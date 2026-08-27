from ytmusicapi import YTMusic

ytmusic = YTMusic("browser.json")

songs = ytmusic.get_liked_songs()
for song in songs["tracks"]:
    print(song["title"])
