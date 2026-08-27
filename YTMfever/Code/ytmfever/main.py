import os
from pathlib import Path
import yt_dlp


MUSIC_OUTPUT_PATH = Path("JellyfinMusic")
cache_path = Path("ytmfever/cache.txt")


def main() -> None:
    print(f"Output Path: {MUSIC_OUTPUT_PATH}" )

    if not os.path.exists(cache_path):
        cache = open(cache_path, "w")
        cache.write("")
        cache.close()

    download_video("www.youtube.com/watch?v=kFCYIh9S-Eo")









def download_video(url: str):
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': f"{MUSIC_OUTPUT_PATH}/%(title)s.%(ext)s",
        'quiet': True,
        'no_warnings': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    print(url)
    cache = open(cache_path, "a")
    cache.write(f"{url}\n")    


if __name__ == "__main__":
    main()