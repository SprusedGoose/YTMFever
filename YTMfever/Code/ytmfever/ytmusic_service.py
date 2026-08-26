from __future__ import annotations
"""YTMusic data-access and payload-normalization helpers.

This module isolates all API-shape quirks so downloader orchestration can stay
focused on workflow rather than endpoint-specific parsing.
"""

import re
from collections.abc import Iterable

from ytmusicapi import YTMusic


class YTMusicLibraryService:
    """Handles all data retrieval and shaping from YTMusic API.

    Responsibilities:
    - Fetch library and liked songs.
    - Normalize heterogeneous API payload shapes.
    - Resolve artist and track metadata used by downloader routing.
    - Cache expensive per-track metadata lookups.
    """

    def __init__(self, auth_path: str, max_songs_per_artist: int = 500) -> None:
        """Build API client and initialize metadata lookup cache."""
        self.client = YTMusic(auth_path)
        self.max_songs_per_artist = max_songs_per_artist
        self.song_meta_cache: dict[str, tuple[str | None, str | None]] = {}

    def get_songs_in_library(self) -> list[dict]:
        """Return unique library songs with a `_is_liked` marker.

        Why this exists:
        - `get_library_songs` and `get_liked_songs` may return different payload
          container shapes.
        - A song can appear in both lists; deduping by `videoId` avoids duplicate
          download attempts while preserving liked-state.
        """
        library_songs = self.client.get_library_songs(limit=1000)
        liked_songs = self.client.get_liked_songs(limit=1000)

        # `liked_songs` may be either a top-level dict with `tracks` or a plain list.
        if isinstance(liked_songs, dict):
            liked_tracks = liked_songs.get("tracks", [])
        elif isinstance(liked_songs, list):
            liked_tracks = liked_songs
        else:
            liked_tracks = []

        # Build fast membership set used to mark final merged songs as liked.
        liked_ids: set[str] = set()
        if isinstance(liked_tracks, list):
            for track in liked_tracks:
                if not isinstance(track, dict):
                    continue
                video_id = track.get("videoId")
                if isinstance(video_id, str) and video_id.strip():
                    liked_ids.add(video_id)

        # Merge both sources before dedupe so the first seen ordering is preserved.
        all_songs: list[dict] = []
        if isinstance(library_songs, list):
            all_songs.extend(library_songs)
        if isinstance(liked_tracks, list):
            all_songs.extend(liked_tracks)

        seen_ids: set[str] = set()
        unique_songs: list[dict] = []
        index_by_id: dict[str, int] = {}

        for song in all_songs:
            if not isinstance(song, dict):
                continue
            song_id = song.get("videoId")
            if not isinstance(song_id, str) or not song_id.strip():
                continue

            if song_id not in seen_ids:
                seen_ids.add(song_id)
                song_copy = dict(song)
                song_copy["_is_liked"] = song_id in liked_ids
                unique_songs.append(song_copy)
                index_by_id[song_id] = len(unique_songs) - 1
            elif song_id in liked_ids:
                # If duplicate appears later from liked list, upgrade marker in-place.
                existing_idx = index_by_id.get(song_id)
                if existing_idx is not None:
                    unique_songs[existing_idx]["_is_liked"] = True

        return unique_songs

    def get_artist_ids_for_songs(self, songs: Iterable[dict]) -> list[str]:
        """Collect unique artist IDs across all songs.

        Multi-artist songs are expanded so each contributing artist can be
        processed for discography downloads.
        """
        seen_artist_ids: set[str] = set()
        unique_artist_ids: list[str] = []

        for song in songs:
            for artist_id in self.get_song_artist_ids(song):
                if artist_id not in seen_artist_ids:
                    seen_artist_ids.add(artist_id)
                    unique_artist_ids.append(artist_id)

        return unique_artist_ids

    def get_artist_payload(self, artist_id: str) -> dict | None:
        """Fetch artist payload safely.

        Failures are treated as non-fatal so one bad artist payload does not stop
        the whole batch run.
        """
        try:
            payload = self.client.get_artist(artist_id)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def get_artist_song_list(self, artist_payload: dict) -> list[dict]:
        """Return best available song list for artist payload.

        Selection order:
        1. Expanded playlist using artist songs `browseId` (fuller catalog).
        2. Fallback preview `results` list.
        3. Fallback direct list payload if present.
        """
        songs_payload = artist_payload.get("songs", {})

        if isinstance(songs_payload, dict):
            browse_id = songs_payload.get("browseId")
            if browse_id:
                try:
                    # The playlist route typically yields more than preview cards.
                    playlist = self.client.get_playlist(
                        browse_id,
                        limit=self.max_songs_per_artist,
                    )
                    tracks = playlist.get("tracks", [])
                    if isinstance(tracks, list):
                        return [track for track in tracks if isinstance(track, dict)]
                except Exception:
                    # Continue to preview fallback when playlist expansion fails.
                    pass

            preview_results = songs_payload.get("results", [])
            if isinstance(preview_results, list):
                return [track for track in preview_results if isinstance(track, dict)]

        if isinstance(songs_payload, list):
            return [track for track in songs_payload if isinstance(track, dict)]

        return []

    @staticmethod
    def dedupe_tracks_by_video_id(tracks: Iterable[dict]) -> list[dict]:
        """Deduplicate tracks by video ID while preserving order."""
        seen_video_ids: set[str] = set()
        unique_tracks: list[dict] = []

        for track in tracks:
            video_id = track.get("videoId")
            if isinstance(video_id, str) and video_id and video_id not in seen_video_ids:
                seen_video_ids.add(video_id)
                unique_tracks.append(track)

        return unique_tracks

    @staticmethod
    def extract_album_name(track: dict) -> str:
        """Return album name used for folder routing, with fallback.

        Folder naming must be stable even when track payloads omit album data.
        """
        album = track.get("album")

        if isinstance(album, dict):
            name = album.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()

        if isinstance(album, str) and album.strip():
            return album.strip()

        return "Singles and Misc"

    @staticmethod
    def get_source_artist_name_for_track(
        track: dict,
        current_artist_id: str,
        fallback_artist_name: str,
    ) -> str:
        """Read artist name from the song source entry for the active artist ID.

        This prevents metadata drift when a track contains multiple artists and
        the current artist context should win.
        """
        artists = track.get("artists") if isinstance(track, dict) else None
        if isinstance(artists, list):
            for artist in artists:
                if not isinstance(artist, dict):
                    continue
                if artist.get("id") != current_artist_id:
                    continue
                artist_name = artist.get("name")
                if isinstance(artist_name, str) and artist_name.strip():
                    return artist_name.strip()

        return fallback_artist_name

    def get_year_genre_from_ytmusic(self, track: dict) -> tuple[str | None, str | None]:
        """Extract year/genre from track payload and song-detail fallback.

        Fast path:
        - Use values already present in track payload when available.

        Fallback path:
        - Query `get_song(videoId)` microformat metadata.
        - Cache extracted result to avoid repeated API calls.
        """
        year, genre = self._extract_year_genre_from_track(track)

        video_id = track.get("videoId") if isinstance(track, dict) else None
        if not isinstance(video_id, str) or not video_id.strip():
            return year, genre

        cached = self.song_meta_cache.get(video_id)
        if cached is not None:
            cached_year, cached_genre = cached
            return year or cached_year, genre or cached_genre

        detail_year: str | None = None
        detail_genre: str | None = None

        try:
            song_details = self.client.get_song(video_id)
            if isinstance(song_details, dict):
                micro = song_details.get("microformat", {})
                if isinstance(micro, dict):
                    renderer = micro.get("microformatDataRenderer", {})
                    if isinstance(renderer, dict):
                        detail_year = self._normalize_year_value(
                            renderer.get("releaseDate")
                            or renderer.get("publishDate")
                            or renderer.get("uploadDate")
                        )
                        detail_genre = self._normalize_genre_value(renderer.get("category"))
        except Exception:
            # Missing detail metadata should not block download flow.
            pass

        self.song_meta_cache[video_id] = (detail_year, detail_genre)
        return year or detail_year, genre or detail_genre

    def get_song_artist_ids(self, song: dict) -> list[str]:
        """Extract all possible artist IDs from a track payload.

        IDs may appear in several fields depending on endpoint shape.
        """
        if not isinstance(song, dict):
            return []

        artist_ids: list[str] = []
        seen_ids: set[str] = set()

        self._collect_artist_ids_from_value(song.get("artists"), seen_ids, artist_ids)
        self._collect_artist_ids_from_value(song.get("artist"), seen_ids, artist_ids)
        self._collect_artist_ids_from_value(song.get("author"), seen_ids, artist_ids)

        album = song.get("album")
        if isinstance(album, dict):
            self._collect_artist_ids_from_value(album.get("artists"), seen_ids, artist_ids)

        return artist_ids

    def _collect_artist_ids_from_value(
        self,
        value: object,
        seen_ids: set[str],
        artist_ids: list[str],
    ) -> None:
        """Recursively collect artist identifiers from dict/list values."""
        if isinstance(value, dict):
            for id_key in ("id", "browseId", "channelId"):
                artist_id = value.get(id_key)
                if (
                    isinstance(artist_id, str)
                    and artist_id.strip()
                    and artist_id not in seen_ids
                ):
                    seen_ids.add(artist_id)
                    artist_ids.append(artist_id)
        elif isinstance(value, list):
            for item in value:
                self._collect_artist_ids_from_value(item, seen_ids, artist_ids)

    @staticmethod
    def _normalize_year_value(value: object) -> str | None:
        """Extract a 4-digit year from loosely formatted metadata fields."""
        if value is None:
            return None

        value_str = str(value).strip()
        if not value_str:
            return None

        match = re.search(r"\b(19|20)\d{2}\b", value_str)
        return match.group(0) if match else None

    @staticmethod
    def _normalize_genre_value(value: object) -> str | None:
        """Normalize genre and drop generic placeholder categories."""
        if not isinstance(value, str):
            return None

        genre = value.strip()
        if not genre:
            return None

        if genre.casefold() in {"music", "unknown", "other"}:
            return None

        return genre

    def _extract_year_genre_from_track(self, track: dict) -> tuple[str | None, str | None]:
        """Read year/genre directly from track payload when possible."""
        if not isinstance(track, dict):
            return None, None

        year = self._normalize_year_value(track.get("year"))

        genre = track.get("genre")
        if not isinstance(genre, str) or not genre.strip():
            genre = track.get("category")

        return year, self._normalize_genre_value(genre)
