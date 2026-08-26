from __future__ import annotations
"""Metadata normalization layer for downloaded audio files.

Only targeted fields are rewritten to keep provider-acquired metadata intact
where possible while enforcing cross-player consistency.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class PostprocessResult:
    """Counts returned after metadata post-processing.

    Fields reflect how many files were successfully updated by each stage.
    """

    artist_normalized: int = 0
    rating_normalized: int = 0
    wmp_normalized: int = 0


class MetadataNormalizer:
    """Normalizes selected metadata fields while preserving others.

    Scope:
    - Artist/year/genre harmonization.
    - Liked-song rating normalization.
    - Windows Media Player compatibility rewrite for MP3 artwork/tags.
    """

    def normalize_artist_year_genre(
        self,
        song_path: Path,
        intended_artist: str,
        intended_album: str,
        intended_year: str | None,
        intended_genre: str | None,
    ) -> bool:
        """Normalize artist/year/genre tags for one audio file.

        Behavior is format-aware:
        - MP3: ID3 frames (`TPE1`, `TPE2`, `TYER`, `TDRC`, `TCON`).
        - M4A: common MP4 atoms (`\xa9ART`, `aART`, `\xa9day`, `\xa9gen`).
        - FLAC/OPUS/OGG: Vorbis-style text fields.
        """
        intended_artist = intended_artist.strip()
        if not intended_artist:
            return False

        intended_album = intended_album.strip()
        if not intended_album:
            intended_album = "Singles and Misc"

        intended_year = self._normalize_year_value(intended_year)
        intended_genre = intended_genre.strip() if isinstance(intended_genre, str) else None
        if intended_genre == "":
            intended_genre = None

        ext = song_path.suffix.lower()

        try:
            if ext == ".mp3":
                from mutagen.id3 import ID3, ID3NoHeaderError, TALB, TCON, TDRC, TPE1, TPE2, TYER

                try:
                    tags = ID3(str(song_path))
                except ID3NoHeaderError:
                    tags = ID3()

                # Replace artist fields rather than appending, preventing mixed values.
                tags.delall("TPE1")
                tags.add(TPE1(encoding=3, text=[intended_artist]))
                tags.delall("TPE2")
                tags.add(TPE2(encoding=3, text=[intended_artist]))
                tags.delall("TALB")
                tags.add(TALB(encoding=3, text=[intended_album]))

                if intended_year is not None:
                    # Write both TYER and TDRC for broader reader compatibility.
                    tags.delall("TYER")
                    tags.add(TYER(encoding=3, text=[intended_year]))
                    tags.delall("TDRC")
                    tags.add(TDRC(encoding=3, text=[intended_year]))

                if intended_genre is not None:
                    tags.delall("TCON")
                    tags.add(TCON(encoding=3, text=[intended_genre]))

                tags.save(str(song_path), v2_version=3)
                return True

            from mutagen import File

            audio_file = File(str(song_path), easy=False)
            if audio_file is None:
                return False

            if ext == ".m4a":
                audio_file["\xa9ART"] = [intended_artist]
                audio_file["aART"] = [intended_artist]
                audio_file["\xa9alb"] = [intended_album]
                if intended_year is not None:
                    audio_file["\xa9day"] = [intended_year]
                if intended_genre is not None:
                    audio_file["\xa9gen"] = [intended_genre]
                audio_file.save()
                return True

            if ext in {".flac", ".opus", ".ogg"}:
                audio_file["ARTIST"] = [intended_artist]
                audio_file["ALBUMARTIST"] = [intended_artist]
                audio_file["ALBUM"] = [intended_album]
                if intended_year is not None:
                    audio_file["DATE"] = [intended_year]
                    audio_file["YEAR"] = [intended_year]
                if intended_genre is not None:
                    audio_file["GENRE"] = [intended_genre]
                audio_file.save()
                return True

            return False
        except Exception:
            # Tag write failures are isolated per file to keep batch resilient.
            return False

    def normalize_liked_rating(self, song_path: Path, is_liked: bool) -> bool:
        """Apply a 5-star equivalent rating for liked songs only.

        Current mapping:
        - MP3: POPM rating 255.
        - FLAC/OPUS/OGG: RATING=100.
        """
        if not is_liked:
            return False

        ext = song_path.suffix.lower()

        try:
            if ext == ".mp3":
                from mutagen.id3 import ID3, ID3NoHeaderError, POPM

                try:
                    tags = ID3(str(song_path))
                except ID3NoHeaderError:
                    tags = ID3()

                tags.delall("POPM")
                tags.add(POPM(email="ytmfever@local", rating=255, count=0))
                tags.save(str(song_path), v2_version=3)
                return True

            from mutagen import File

            audio_file = File(str(song_path), easy=False)
            if audio_file is None:
                return False

            if ext in {".flac", ".opus", ".ogg"}:
                audio_file["RATING"] = ["100"]
                audio_file.save()
                return True

            return False
        except Exception:
            return False

    def normalize_mp3_for_wmp(self, song_path: Path, cover_path: Path) -> bool:
        """Rewrite MP3 APIC/ID3 version for better Windows Media Player support.

        WMP compatibility can be improved by ensuring:
        - One clear front-cover APIC frame.
        - ID3 saved in v2.3 format.
        """
        if song_path.suffix.lower() != ".mp3":
            return False

        try:
            from mutagen.id3 import APIC, ID3, ID3NoHeaderError
        except Exception:
            return False

        try:
            try:
                tags = ID3(str(song_path))
            except ID3NoHeaderError:
                tags = ID3()

            if cover_path.exists():
                image_data = cover_path.read_bytes()
                # Replace any existing images to avoid readers picking stale artwork.
                tags.delall("APIC")
                tags.add(
                    APIC(
                        encoding=3,
                        mime="image/jpeg",
                        type=3,
                        desc="Front cover",
                        data=image_data,
                    )
                )

            tags.save(str(song_path), v2_version=3)
            return True
        except Exception:
            return False

    def postprocess_changed_files(
        self,
        changed_files: list[Path],
        album_cover_path: Path,
        intended_artist: str,
        intended_album: str,
        intended_year: str | None,
        intended_genre: str | None,
        is_liked: bool,
    ) -> PostprocessResult:
        """Apply all post-download metadata normalizations to changed files.

        Order is intentional:
        1. Normalize artist/year/genre identity first.
        2. Apply liked-rating markers.
        3. Run WMP-oriented MP3 rewrite as final save pass.
        """
        result = PostprocessResult()

        for file_path in changed_files:
            if self.normalize_artist_year_genre(
                file_path,
                intended_artist,
                intended_album,
                intended_year,
                intended_genre,
            ):
                result.artist_normalized += 1

            if self.normalize_liked_rating(file_path, is_liked):
                result.rating_normalized += 1

            if self.normalize_mp3_for_wmp(file_path, album_cover_path):
                result.wmp_normalized += 1

        return result

    @staticmethod
    def _normalize_year_value(value: object) -> str | None:
        """Extract canonical 4-digit year from arbitrary value."""
        if value is None:
            return None
        value_str = str(value).strip()
        if not value_str:
            return None

        import re

        match = re.search(r"\b(19|20)\d{2}\b", value_str)
        return match.group(0) if match else None
