from __future__ import annotations
"""Configuration model for the YTMfever runtime.

Centralizing settings in a dataclass makes path behavior explicit and keeps
operational defaults in one place.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AppConfig:
    """Runtime configuration for YTMfever.

    Attributes:
    - auth_path: YTMusic browser auth file used by ytmusicapi.
    - output_root: Root directory for Jellyfin-friendly media structure.
    - ytdl_config_dir: Directory passed to ytmdl via `--ytdl-config`.
    - max_songs_per_artist: Upper bound when expanding artist song lists.
    """

    project_root: Path = Path(__file__).resolve().parent.parent
    auth_path: Path = Path("browser.json")
    output_root: Path = Path("JellyfinMusic")
    ytdl_config_dir: Path = Path(".yt-dlp")
    max_songs_per_artist: int = 500

    def __post_init__(self) -> None:
        """Resolve relative runtime paths from the project root."""
        self.project_root = self.project_root.resolve()

        if not self.auth_path.is_absolute():
            self.auth_path = self.project_root / self.auth_path

        if not self.output_root.is_absolute():
            self.output_root = self.project_root / self.output_root

        if not self.ytdl_config_dir.is_absolute():
            self.ytdl_config_dir = self.project_root / self.ytdl_config_dir

    @property
    def ytdl_config_file(self) -> Path:
        """Full path to the yt-dlp config file created/updated at runtime."""
        return self.ytdl_config_dir / "yt-dlp.conf"
