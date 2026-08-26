from __future__ import annotations
"""Download orchestration for YTMfever.

This module coordinates end-to-end flow:
- Artist discovery/routing.
- ytmdl command execution.
- Artwork handling.
- Post-download metadata normalization.
"""

import os
import csv
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

from mutagen import File
from mutagen.id3 import ID3, ID3NoHeaderError

from .config import AppConfig
from .metadata import MetadataNormalizer
from .ytmusic_service import YTMusicLibraryService


class YTMFeverDownloader:
    """Coordinates folder routing, downloads, artwork, and post-processing.

    This class intentionally delegates API parsing to `YTMusicLibraryService`
    and tag-writing concerns to `MetadataNormalizer`.
    """

    def __init__(
        self,
        config: AppConfig,
        service: YTMusicLibraryService,
        normalizer: MetadataNormalizer,
    ) -> None:
        """Create downloader with injected config/service/normalizer dependencies."""
        self.config = config
        self.service = service
        self.normalizer = normalizer
        self.audio_exts = {".mp3", ".m4a", ".opus", ".flac", ".ogg", ".wav"}
        self.ytmdl_executable: Path | None = None
        self.ytmdl_env: dict[str, str] | None = None
        self.run_id: str | None = None
        self.run_manifest_rows: list[dict[str, str]] = []

    def run(self, max_downloads: int | None = None) -> None:
        """Entrypoint for full artist discovery and download flow.

        Startup sequence:
        1. Resolve ytmdl executable.
        2. Build runtime environment and ytdl configuration.
        3. Gather library songs and unique artist IDs.
        4. Process each artist independently.
        """
        self.ytmdl_executable = self.resolve_ytmdl_executable()
        if self.ytmdl_executable is None:
            print("ytmdl executable not found. Install ytmdl in this environment first.")
            return

        self.ytmdl_env = self.build_ytmdl_env()
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_manifest_rows = []
        self.ensure_ytdlp_config()
        self.config.output_root.mkdir(parents=True, exist_ok=True)

        songs = self.service.get_songs_in_library()
        artist_ids = self.service.get_artist_ids_for_songs(songs)

        print(f"ytdl config: {self.config.ytdl_config_file.resolve()}")
        print(f"Library songs: {len(songs)}")
        print(f"Artists to process: {len(artist_ids)}")

        successful_downloads = 0
        for artist_id in artist_ids:
            remaining: int | None = None
            if max_downloads is not None:
                remaining = max_downloads - successful_downloads
                if remaining <= 0:
                    break

            successful_downloads += self.download_artist_discography(
                artist_id,
                max_new_downloads=remaining,
            )

        if max_downloads is not None:
            print(
                f"Small batch complete: {successful_downloads}/{max_downloads} track(s) downloaded."
            )

        self.write_run_manifest("small_batch" if max_downloads is not None else "full_run")

    def download_artist_discography(
        self,
        artist_id: str,
        max_new_downloads: int | None = None,
    ) -> int:
        """Download tracks for one artist ID and normalize resulting files.

        Important behavior:
        - Song list is fetched just-in-time for each artist.
        - Existing files are skipped by filename stem check.
        - Only changed/new files are passed to metadata post-processing.
        """
        content = self.service.get_artist_payload(artist_id)
        if content is None:
            return 0

        artist_name_raw = content.get("name", "Unknown Artist")
        if not isinstance(artist_name_raw, str):
            artist_name_raw = "Unknown Artist"

        artist_name = artist_name_raw.strip() or "Unknown Artist"
        artist_dir = self.config.output_root / self.sanitize_for_path(artist_name)
        artist_dir.mkdir(parents=True, exist_ok=True)

        artist_thumb = self.pick_thumbnail_url(content.get("thumbnails"), content.get("thumbnail"))
        self.ensure_image(artist_thumb, artist_dir / "artist.jpg")

        songs = self.service.dedupe_tracks_by_video_id(self.service.get_artist_song_list(content))
        if not songs:
            print(f"Skipping {artist_name}: no songs found")
            return 0

        print(f"Artist: {artist_name} ({len(songs)} songs)")
        downloaded_for_artist = 0

        for track in songs:
            if (
                max_new_downloads is not None
                and downloaded_for_artist >= max_new_downloads
            ):
                break

            track_title = track.get("title", "Unknown Title")
            if not isinstance(track_title, str) or not track_title.strip():
                track_title = "Unknown Title"

            is_liked = bool(track.get("_is_liked"))
            source_artist_name = self.service.get_source_artist_name_for_track(
                track,
                artist_id,
                artist_name,
            )

            album_name_raw = self.service.extract_album_name(track)
            album_name = album_name_raw.strip() or "Singles and Misc"
            album_dir = artist_dir / self.sanitize_for_path(album_name)
            album_dir.mkdir(parents=True, exist_ok=True)

            video_id_raw = track.get("videoId")
            video_id = video_id_raw.strip() if isinstance(video_id_raw, str) else ""

            # Local existence check replaces archive-file logic for direct skip behavior.
            existing_file = self.find_existing_track_file(album_dir, track_title)
            if existing_file is not None:
                print(f"Skipping existing file: {artist_name} - {track_title}")
                self.append_manifest_row(
                    artist_name,
                    album_name,
                    track_title,
                    video_id,
                    "skipped_existing",
                    0,
                    "Track file already present before download attempt",
                    [existing_file],
                )
                continue

            track_thumb = self.pick_thumbnail_url(track.get("thumbnails"))
            self.ensure_image(track_thumb or artist_thumb, album_dir / "folder.jpg")

            intended_year, intended_genre = self.service.get_year_genre_from_ytmusic(track)
            command = self.build_ytmdl_command(track, source_artist_name, album_name, album_dir)
            if command is None:
                self.append_manifest_row(
                    artist_name,
                    album_name,
                    track_title,
                    video_id,
                    "failed_invalid_command",
                    0,
                    "Unable to build ytmdl command",
                    [],
                )
                continue

            success_files: list[Path] = []
            failure_reason = "No output file detected"
            attempt_count = 0

            for attempt in (1, 2):
                attempt_count = attempt

                # Snapshot before running ytmdl so we can detect just-produced files.
                before_snapshot = self.get_audio_snapshot(album_dir)

                try:
                    result = subprocess.run(
                        command,
                        check=False,
                        env=self.ytmdl_env,
                        stdin=subprocess.DEVNULL,
                    )
                except Exception as exc:
                    failure_reason = f"ytmdl invocation failed: {exc}"
                    if attempt == 1:
                        self.cleanup_ytmdl_cache()
                        continue
                    break

                if result.returncode != 0:
                    failure_reason = f"ytmdl exit code: {result.returncode}"
                    if attempt == 1:
                        self.cleanup_ytmdl_cache()
                        continue
                    break

                changed_files = self.get_changed_audio_files(album_dir, before_snapshot)
                valid_files, quarantined = self.filter_valid_outputs(changed_files)

                if quarantined > 0:
                    failure_reason = f"Quarantined {quarantined} unreadable output file(s)"

                existing_after = self.find_existing_track_file(album_dir, track_title)
                if valid_files:
                    success_files = valid_files
                    break
                if existing_after is not None:
                    success_files = [existing_after]
                    break

                failure_reason = "No output file detected after download attempt"
                if attempt == 1:
                    print(f"No output file detected after download attempt: {artist_name} - {track_title}")
                    self.cleanup_ytmdl_cache()
                    continue
                break

            if not success_files:
                self.append_manifest_row(
                    artist_name,
                    album_name,
                    track_title,
                    video_id,
                    "failed_no_output",
                    attempt_count,
                    failure_reason,
                    [],
                )
                continue

            downloaded_for_artist += 1

            report = self.normalizer.postprocess_changed_files(
                success_files,
                album_dir / "folder.jpg",
                source_artist_name,
                album_name,
                intended_year,
                intended_genre,
                is_liked,
            )

            self.append_manifest_row(
                artist_name,
                album_name,
                track_title,
                video_id,
                "downloaded",
                attempt_count,
                "",
                success_files,
            )

            if report.artist_normalized > 0:
                print(f"Artist metadata normalized: {report.artist_normalized} file(s)")
            if report.rating_normalized > 0:
                print(f"Liked-song 5-star rating applied: {report.rating_normalized} file(s)")
            if report.wmp_normalized > 0:
                print(f"WMP compatibility rewrite complete: {report.wmp_normalized} file(s)")

        return downloaded_for_artist

    @staticmethod
    def sanitize_for_path(name: str) -> str:
        """Return a filesystem-safe path component.

        Replaces Windows-invalid characters and trailing-dot variants so folder
        routing works consistently on NTFS.
        """
        import re

        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name).strip().rstrip(".")
        return cleaned or "Unknown"

    @staticmethod
    def pick_thumbnail_url(*thumbnail_lists: object) -> str | None:
        """Pick the largest thumbnail URL from provided thumbnail arrays.

        The largest image is preferred to maximize embedded/sidecar artwork
        quality in downstream media servers.
        """
        candidates: list[dict] = []
        for thumbnails in thumbnail_lists:
            if isinstance(thumbnails, list):
                candidates.extend(
                    thumb for thumb in thumbnails if isinstance(thumb, dict)
                )

        if not candidates:
            return None

        candidates.sort(
            key=lambda thumb: int(thumb.get("width", 0)) * int(thumb.get("height", 0)),
            reverse=True,
        )

        for thumb in candidates:
            url = thumb.get("url")
            if isinstance(url, str) and url.strip():
                return url

        return None

    @staticmethod
    def ensure_image(url: str | None, destination: Path) -> None:
        """Download image if missing. Failures are non-fatal.

        Artwork retrieval should never stop a successful audio download path.
        """
        if not url or destination.exists():
            return

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with urlopen(url, timeout=20) as response:
                destination.write_bytes(response.read())
        except Exception:
            pass

    def resolve_ytmdl_executable(self) -> Path | None:
        """Resolve ytmdl executable from interpreter, local venv, or PATH.

        Resolution order targets the currently active Python environment first,
        then explicit local venv, then global PATH fallback.
        """
        executable_name = "ytmdl.exe" if sys.platform.startswith("win") else "ytmdl"

        local_bin = Path(sys.executable).with_name(executable_name)
        if local_bin.exists():
            return local_bin

        venv_bin = (
            Path(".venv")
            / ("Scripts" if sys.platform.startswith("win") else "bin")
            / executable_name
        )
        if venv_bin.exists():
            return venv_bin.resolve()

        global_bin = shutil.which("ytmdl")
        return Path(global_bin) if global_bin else None

    def resolve_ffmpeg_executable(self) -> Path | None:
        """Resolve ffmpeg executable from PATH or known winget location.

        The winget fallback covers common Windows setups where PATH is not yet
        refreshed for the current shell.
        """
        ffmpeg_name = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"

        ffmpeg_in_path = shutil.which("ffmpeg")
        if ffmpeg_in_path:
            return Path(ffmpeg_in_path)

        winget_ffmpeg = (
            Path.home()
            / "AppData"
            / "Local"
            / "Microsoft"
            / "WinGet"
            / "Packages"
            / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
            / "ffmpeg-8.1.2-full_build"
            / "bin"
            / ffmpeg_name
        )
        return winget_ffmpeg if winget_ffmpeg.exists() else None

    def resolve_deno_executable(self) -> Path | None:
        """Resolve deno executable from PATH or known winget location.

        Deno is used by yt-dlp as a JavaScript runtime for EJS challenges.
        """
        deno_name = "deno.exe" if sys.platform.startswith("win") else "deno"

        deno_in_path = shutil.which("deno")
        if deno_in_path:
            return Path(deno_in_path)

        winget_deno = (
            Path.home()
            / "AppData"
            / "Local"
            / "Microsoft"
            / "WinGet"
            / "Packages"
            / "DenoLand.Deno_Microsoft.Winget.Source_8wekyb3d8bbwe"
            / deno_name
        )
        return winget_deno if winget_deno.exists() else None

    def build_ytmdl_env(self) -> dict[str, str]:
        """Build process env and ensure ffmpeg/deno directories are on PATH.

        We update PATH in-process to avoid requiring user shell re-login.
        """
        env = dict(os.environ)
        path_parts = env.get("PATH", "").split(os.pathsep)

        ffmpeg_executable = self.resolve_ffmpeg_executable()
        deno_executable = self.resolve_deno_executable()

        if ffmpeg_executable is not None:
            ffmpeg_dir = str(ffmpeg_executable.parent)
            if ffmpeg_dir not in path_parts:
                path_parts.append(ffmpeg_dir)

        if deno_executable is not None:
            deno_dir = str(deno_executable.parent)
            if deno_dir not in path_parts:
                path_parts.append(deno_dir)

        env["PATH"] = os.pathsep.join(path_parts)
        return env

    def ensure_ytdlp_config(self) -> None:
        """Ensure yt-dlp config includes EJS/runtime flags expected by yt-dlp.

        The config is created if missing, otherwise merged non-destructively by
        appending only required missing options.
        """
        required_options = [
            "--js-runtimes deno",
            "--remote-components ejs:github",
        ]

        self.config.ytdl_config_dir.mkdir(parents=True, exist_ok=True)

        if not self.config.ytdl_config_file.exists():
            self.config.ytdl_config_file.write_text(
                "\n".join(required_options) + "\n",
                encoding="utf-8",
            )
            return

        existing_lines = [
            line.strip()
            for line in self.config.ytdl_config_file.read_text(encoding="utf-8").splitlines()
        ]
        missing_lines = [
            option for option in required_options if option not in existing_lines
        ]

        if missing_lines:
            merged_lines = existing_lines + missing_lines
            self.config.ytdl_config_file.write_text(
                "\n".join(merged_lines) + "\n",
                encoding="utf-8",
            )

    def build_ytmdl_command(
        self,
        track: dict,
        artist_name: str,
        album_name: str,
        output_dir: Path,
    ) -> list[str] | None:
        """Build command arguments for a single ytmdl invocation.

        Command is intentionally non-interactive (`--nolocal`, `--choice 1`) and
        uses explicit metadata inputs from upstream normalization.
        """
        if self.ytmdl_executable is None:
            return None

        video_id = track.get("videoId")
        if not isinstance(video_id, str) or not video_id.strip():
            return None

        song_title = track.get("title", "Unknown Title")
        if not isinstance(song_title, str) or not song_title.strip():
            song_title = "Unknown Title"

        url = f"https://www.youtube.com/watch?v={video_id.strip()}"

        return [
            str(self.ytmdl_executable),
            "--url",
            url,
            "--song",
            song_title,
            "--artist",
            artist_name,
            "--album",
            album_name,
            "--quiet",
            "--choice",
            "1",
            "--nolocal",
            "--ignore-errors",
            "--on-meta-error",
            "youtube",
            "--ytdl-config",
            str(self.config.ytdl_config_dir),
            "--output-dir",
            str(output_dir),
            "--disable-file",
        ]

    def get_audio_snapshot(self, album_dir: Path) -> dict[Path, int]:
        """Return mtime snapshot for audio files in an album directory.

        Nanosecond mtimes are used to detect new or modified files precisely.
        """
        snapshot: dict[Path, int] = {}

        for file_path in album_dir.iterdir():
            if not file_path.is_file() or file_path.suffix.lower() not in self.audio_exts:
                continue
            try:
                snapshot[file_path] = file_path.stat().st_mtime_ns
            except OSError:
                continue

        return snapshot

    def get_changed_audio_files(
        self,
        album_dir: Path,
        before_snapshot: dict[Path, int],
    ) -> list[Path]:
        """Return changed/created audio files after a ytmdl invocation.

        This scope limits post-processing to files affected by the current track
        download, avoiding unnecessary rewrites.
        """
        after_snapshot = self.get_audio_snapshot(album_dir)
        return [
            file_path
            for file_path, mtime in after_snapshot.items()
            if before_snapshot.get(file_path) != mtime
        ]

    def find_existing_track_file(self, album_dir: Path, track_title: str) -> Path | None:
        """Return already-present audio file path by normalized stem if available."""
        expected_stem = self.sanitize_for_path(track_title).casefold()

        for file_path in album_dir.iterdir():
            if not file_path.is_file() or file_path.suffix.lower() not in self.audio_exts:
                continue
            if file_path.stem.casefold() == expected_stem:
                return file_path

        return None

    def track_file_already_exists(self, album_dir: Path, track_title: str) -> bool:
        """Check for an already-present audio file by normalized stem.

        This supports just-in-time skip decisions without archive files.
        """
        return self.find_existing_track_file(album_dir, track_title) is not None

    def is_audio_output_valid(self, file_path: Path) -> tuple[bool, str]:
        """Return whether produced audio file looks healthy for post-processing."""
        try:
            parsed = File(str(file_path), easy=False)
        except Exception as exc:
            return False, f"mutagen parse failed: {exc}"

        if parsed is None:
            return False, "mutagen could not identify audio file"

        if file_path.suffix.lower() == ".mp3":
            try:
                ID3(str(file_path))
            except ID3NoHeaderError:
                return False, "MP3 missing ID3 header"
            except Exception as exc:
                return False, f"ID3 parse failed: {exc}"

        return True, "ok"

    def quarantine_output(self, file_path: Path, reason: str) -> Path:
        """Move suspicious output file into quarantine area for later inspection."""
        run_bucket = self.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        quarantine_root = self.config.output_root / "_quarantine" / run_bucket / self.sanitize_for_path(reason)

        try:
            relative_path = file_path.relative_to(self.config.output_root)
        except ValueError:
            relative_path = Path(file_path.name)

        destination = quarantine_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            destination = destination.with_name(f"{destination.stem}_dup{destination.suffix}")

        shutil.move(str(file_path), str(destination))
        return destination

    def filter_valid_outputs(self, changed_files: list[Path]) -> tuple[list[Path], int]:
        """Keep healthy outputs and quarantine unreadable/corrupt artifacts."""
        valid_files: list[Path] = []
        quarantined_count = 0

        for file_path in changed_files:
            is_valid, reason = self.is_audio_output_valid(file_path)
            if is_valid:
                valid_files.append(file_path)
                continue

            try:
                quarantined_path = self.quarantine_output(file_path, reason)
                print(f"Quarantined bad output: {quarantined_path}")
            except Exception:
                pass
            quarantined_count += 1

        return valid_files, quarantined_count

    def cleanup_ytmdl_cache(self) -> None:
        """Best-effort cleanup for ytmdl cache artifacts before retry."""
        cache_dir = Path.home() / ".cache" / "ytmdl"
        if not cache_dir.exists():
            return

        for cache_file in cache_dir.iterdir():
            if not cache_file.is_file():
                continue
            try:
                cache_file.unlink()
            except Exception:
                continue

    def append_manifest_row(
        self,
        artist_name: str,
        album_name: str,
        track_title: str,
        video_id: str,
        status: str,
        attempts: int,
        detail: str,
        output_files: list[Path],
    ) -> None:
        """Collect one normalized manifest row for this run."""
        output_values = [
            str(path.relative_to(self.config.output_root)).replace("\\", "/")
            if path.is_relative_to(self.config.output_root)
            else str(path)
            for path in output_files
        ]

        self.run_manifest_rows.append(
            {
                "run_id": self.run_id or "",
                "timestamp_utc": datetime.utcnow().isoformat(timespec="seconds"),
                "artist": artist_name,
                "album": album_name,
                "track_title": track_title,
                "video_id": video_id,
                "status": status,
                "attempts": str(attempts),
                "detail": detail,
                "output_files": "; ".join(output_values),
            }
        )

    def write_run_manifest(self, mode_name: str) -> None:
        """Write per-run CSV manifest with skip/success/failure outcomes."""
        if not self.run_manifest_rows:
            return

        manifest_dir = self.config.output_root / "_manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        run_bucket = self.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        manifest_path = manifest_dir / f"{run_bucket}_{mode_name}.csv"

        headers = [
            "run_id",
            "timestamp_utc",
            "artist",
            "album",
            "track_title",
            "video_id",
            "status",
            "attempts",
            "detail",
            "output_files",
        ]

        with manifest_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            writer.writerows(self.run_manifest_rows)

        print(f"Run manifest written: {manifest_path}")

