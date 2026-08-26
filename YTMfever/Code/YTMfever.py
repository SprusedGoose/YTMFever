"""YTMfever entrypoint.

The implementation lives under the `ytmfever` package.
This script wires configuration + services and runs the downloader.

Design notes:
- Keep this file intentionally tiny so orchestration logic stays testable
    inside package classes.
- `main()` is the single runtime composition point for dependency wiring.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ytmfever import AppConfig, MetadataNormalizer, YTMFeverDownloader, YTMusicLibraryService


def parse_args() -> argparse.Namespace:
    """Parse command-line options for run modes."""
    parser = argparse.ArgumentParser(
        description="Download and normalize tracks from your YTMusic library.",
    )
    parser.add_argument(
        "--small-batch",
        action="store_true",
        help="Run a limited batch of downloads instead of a full library pass.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Maximum number of tracks to download in small-batch mode (default: 10).",
    )
    return parser.parse_args()


def main() -> None:
    """Initialize dependencies and execute the downloader.

    The downloader is assembled explicitly so each component can be swapped
    independently in future (for example, alternate metadata normalizers or
    alternate music-source services).
    """
    config = AppConfig()

    if not validate_auth_path(config.auth_path):
        return

    service = YTMusicLibraryService(
        auth_path=str(config.auth_path),
        max_songs_per_artist=config.max_songs_per_artist,
    )
    normalizer = MetadataNormalizer()
    downloader = YTMFeverDownloader(config, service, normalizer)
    args = parse_args()

    if args.small_batch:
        batch_size = max(1, args.batch_size)
        print(f"Running small batch mode (up to {batch_size} track downloads)...")
        downloader.run(max_downloads=batch_size)
    else:
        downloader.run()


def validate_auth_path(auth_path: Path) -> bool:
    """Validate YTMusic auth file existence and JSON parseability."""
    if not auth_path.exists():
        print(f"Auth file not found: {auth_path}")
        print("Create or restore browser.json before running downloads.")
        return False

    if not auth_path.is_file():
        print(f"Auth path is not a file: {auth_path}")
        return False

    try:
        json.loads(auth_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Auth file is not valid JSON: {auth_path}")
        print(f"Reason: {exc}")
        return False

    return True


if __name__ == "__main__":
    main()
