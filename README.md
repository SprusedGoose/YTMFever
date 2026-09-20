# YTMFever

## Working in the codebase

#### Activate Virtual Environment

Start by doing `source venv/bin/activate` from the project root directory to activate the virtual environment with all dependencies.

#### Authenticate via Browser

Then, go to a web browser and make a search query in youtube.  
Open Developer Tools and go to the "Network" tab.
Here, you should see a request using POST method returning status 200, right click this request -> Copy Value -> Copy Request Headers.

Go back to the project terminal you enabled the virtual environment in, and run `ytmusicapi browser`.  Follow instructions.

#### Running the Project

As long as the project has been authenticated with the ytmusicapi, and the virtual environment is activated with dependencies, you can run main.py via `venv/bin/python main.py`.

## Options

Options are currently only implemented as variables at the top of main.py.  These include;
- should_get_date : determines whether or not to send an extra request to get and apply a song's upload date.  By default this is True (on).
- download_limit : how many songs to download when the script runs.  By default this is 10.
- seconds_between_downloads : how long should wait between downloads.  Could error out if spamming ytdlp too much.  By default this is 10.
- reset_cache: whether or not the cache should be reset every time.  By default this is False.
- MUSIC_OUTPUT_PATH : location music will be installed to.  By default this is $HOME/Music/Jellyfin.
- ARTIST_CACHE_PATH

## Further development

For further development, I (Teagan) recommend implementing a user interface to interface with these variables via command-line arguments rather than in the file itself.  I would also recommend figuring out how to deal with browser authentication in code as opposed to the command line.  Ultimately the code works just by detecting if the browser.json file exists however, so anything that can generate that with the new headers should be fine - even a simple fstring written to the file.