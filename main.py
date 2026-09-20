import os
from pathlib import Path
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, APIC, TXXX
import shutil
import requests
import yt_dlp
from ytmusicapi import YTMusic, OAuthCredentials


MUSIC_OUTPUT_PATH = Path(f"{Path.home()}/Music/JellyfinMusic")
ARTIST_CACHE_PATH = Path("cache/artist_cache.txt")
SONG_CACHE_PATH = Path("cache/song_cache.txt")

download_limit = 10
reset_cache = False
should_get_date = True

downloaded_since_last_time_limit_reached = 0

def main() -> None:
    

    print(f"Output Path: {MUSIC_OUTPUT_PATH}" )
    if not os.path.exists(MUSIC_OUTPUT_PATH):
        os.mkdir(MUSIC_OUTPUT_PATH)

    if reset_cache:
        shutil.rmtree("cache")

    if not os.path.exists(Path("cache")):
        os.mkdir(Path("cache"))

    if not os.path.exists(ARTIST_CACHE_PATH):
        cache = open(ARTIST_CACHE_PATH, "w")
        cache.write("")
        cache.close()
    if not os.path.exists(SONG_CACHE_PATH):
        cache = open(SONG_CACHE_PATH, "w")
        cache.write("")
        cache.close()

    #ytmusic = YTMusic("oauth.json", oauth_credentials=OAuthCredentials(client_id=OAUTH_CLIENT_ID, client_secret=OAUTH_CLIENT_SECRET))
    ytmusic = YTMusic("browser.json")

    #iterate_through_liked_songs(ytmusic)


    if downloaded_since_last_time_limit_reached < download_limit:
        iterate_through_artist_discographies(ytmusic)


def iterate_through_artist_discographies(ytmusic):
    global downloaded_since_last_time_limit_reached
    print("Iterating through artist discographies...")

    artist_cache = open(ARTIST_CACHE_PATH, "r")
    song_cache = open(SONG_CACHE_PATH, "r+")

    artists = list(line.strip("\n") for line in artist_cache.readlines())
    existing_songs = list(line.strip("\n") for line in song_cache.readlines())

    for artist in artists:
        song_playlist = None
        try:
            song_playlist = ytmusic.get_artist(artist)['songs']['browseId']
        except:
            raise Exception("Artist is a user, skipping")
            continue

        if song_playlist != None:
            for song in ytmusic.get_playlist(song_playlist)['tracks']:
                print(song['title'])

                if song['videoId'] not in existing_songs: # second check because artist loop - would happen anyways
                    print(song)
                    try:
                        if should_get_date:
                            download_song(song, ytmusic.get_song(song['videoId']))
                        else:
                            download_song(song)
                    except: 
                        raise Exception(f"Failed to download song: {song['title']}")
                    downloaded_since_last_time_limit_reached += 1
                    song_cache.write(song['videoId'] + "\n")
                    existing_songs.append(song['videoId'])

                    if downloaded_since_last_time_limit_reached >= download_limit:
                        downloaded_since_last_time_limit_reached = 0
                        print(f"Finished downloading {download_limit} songs.")
                        return


def iterate_through_liked_songs(ytmusic):
    global downloaded_since_last_time_limit_reached
    print("Iterating through liked songs...")

    artist_cache = open(ARTIST_CACHE_PATH, "r+")
    song_cache = open(SONG_CACHE_PATH, "r+")
    
    liked_songs = ytmusic.get_liked_songs()['tracks']
    existing_artists = list(line.strip("\n") for line in artist_cache.readlines())
    existing_songs = list(line.strip("\n") for line in song_cache.readlines())

    
    for song in liked_songs:
        #print(str(song['title']) + " - " + str(song['videoId']))
        if song['videoId'] == None:
            continue

        song_id = str(song['videoId'])
    
        if song_id not in existing_songs:

            for artist in song['artists']:
                print("-----------" + str(artist['name']) + " - " + str(artist['id']) + "-----------")
                if artist['id'] == None:
                    print("Artist not found")
                    continue

                if song_id not in existing_songs: # second check because artist loop - would happen anyways
                    print(song['title'])
                    print(song)
                    try:
                        if should_get_date:
                            download_song(song, ytmusic.get_song(song_id))
                        else:
                            download_song(song)
                    except: 
                        raise Exception(f"Failed to download song: {song['title']}")
                    downloaded_since_last_time_limit_reached += 1
                    song_cache.write(song_id + "\n")
                    existing_songs.append(song_id)

                    if downloaded_since_last_time_limit_reached >= download_limit:
                        artist_id = str(artist['id'])
                        if artist_id not in existing_artists:
                            artist_cache.write(artist_id + "\n")
                            existing_artists.append(artist_id)

                        downloaded_since_last_time_limit_reached = 0
                        print(f"Finished downloading {download_limit} songs.")
                        return

                artist_id = str(artist['id'])
                if artist_id not in existing_artists:
                    artist_cache.write(artist_id + "\n")
                    existing_artists.append(artist_id)
        print("\n")

    artist_cache.close()
    song_cache.close()


def download_song(song_data, data_from_get_song=None):
    url = f"www.youtube.com/watch?v={str(song_data['videoId'])}"

    # Deal with data necessary for song path (and some metadata)

    artists_by_name = []
    for artist in song_data['artists']:
        artists_by_name.insert(0,artist['name'])

    if str(song_data['album']) != "None":
        mp3_path = f"{MUSIC_OUTPUT_PATH}/{artists_by_name[0]}/{str(song_data['album']['name'])}/{str(song_data['title'])}.mp3"
    else:
        mp3_path = f"{MUSIC_OUTPUT_PATH}/{artists_by_name[0]}/{str(song_data['title'])}.mp3"

    # Download song

    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': mp3_path.removesuffix(".mp3"),
        'quiet': True,
        'no_warnings': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    
    # Deal with processing data for metadata
    
    artists_listed = ""
    if len(artists_by_name) > 1:
        for artist in artists_by_name:
            artists_listed += artist + "; "
        artists_listed = artists_listed.removesuffix("; ")
    else: artists_listed = artists_by_name[0]

    album_listed = ""
    if str(song_data['album']) != "None":
        album_listed = str(song_data['album']['name'])

    rating = ""
    if str(song_data['likeStatus']) == 'LIKE':
        rating = "5"
    
    # Apply new metadata

    audio = MP3(mp3_path, ID3=ID3)

    audio["TIT2"] = TIT2(encoding=3, text=str(song_data['title']))
    audio["TPE1"] = TPE1(encoding=3, text=artists_listed)
    audio["TALB"] = TALB(encoding=3, text=album_listed)
    if data_from_get_song != None:
        audio["TDRC"] = TDRC(encoding=3, text=str(data_from_get_song['microformat']['microformatDataRenderer']['uploadDate'].split('T')[0]))
    if rating != "":
        audio["TXXX"] = TXXX(encoding=3, desc="RATING", text=rating)

    response = requests.get(song_data['thumbnails'][-1]['url'], stream=True)

    if response.status_code == 200:
        with open("most_recent_cover.png", "wb") as file:
            for chunk in response.iter_content(1024):
                file.write(chunk)

    with open("most_recent_cover.png", "rb") as img_file:
        audio.tags.add(
            APIC(
                encoding=3,
                mime="image/png",
                type=3,
                desc="Cover Art",
                data=img_file.read(),
            )
        )

    audio.save()

    # Copy mp3s with metadata preserved for other artists
    if len(artists_by_name) > 1:
        for artist in artists_by_name:
            if artist == artists_by_name[0]:
                continue
            mp3_folder_to_path = f"{MUSIC_OUTPUT_PATH}/{artist}/{str(song_data['album']['name'])}"
            if str(song_data['album']) != "None":
                mp3_path_to = f"{mp3_folder_to_path}/{str(song_data['title'])}.mp3"
            else:
                mp3_path_to = f"{mp3_folder_to_path}/{str(song_data['title'])}.mp3"
            os.makedirs(mp3_folder_to_path, exist_ok=True)
            shutil.copy2(mp3_path, mp3_path_to)



if __name__ == "__main__":
    main()