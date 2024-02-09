# EasySailBo(a)t
Manage and cast your media library using a telegram bot.  

This bot can  
- add movies and TV shows to a Transmission torrent client using magnet URLs
- list/stop/start/delete torrents
- disable specific files in a torrent
- convert torrent videos to mp4 format which will most likely be castable
- cast videos to devices on the network (UPnP)
- control playback on devices on the network (UPnP)

`python3 telegram_transmission_bot.py`


## Setup guide

See Configuration and Requirements for more details

0. Download this github repository to your computer  
1. Python3  
    1. Install python3  
    2. Run `python3 -m pip install requests cachetools transmissionrpc python-telegram-bot dlna-cast beautifulsoup4` to install python requirements  
2. Transmission  
    1. Install Vistual C++ Redistributable (needed for Transmission on windows)  
    2. Install Transmission (select "Web interface" during installation)  
    3. Run Transmission  
    4. Enable Transmission Web interface  
3. Telegram bot token  
    1. Create a new `API_TOKEN` through the [@BotFather](https://telegram.me/BotFather)  
    2. Update `API_TOKEN` in config.py  
4. Video directories  
    1. Update `DIR_TV_SHOWS` in config.py with the directory to store TV shows  
    2. Update `DIR_MOVIES` in config.py with the directory to store movies  
5. Run `python3 telegram_transmission_bot.py`  


## Configuration

In config.py set the variables `API_TOKEN`, `AUTHENTICATED_USER_IDS`, `DIR_TV_SHOWS`, `DIR_MOVIES`.  

`API_TOKEN` is your bot's [API token](https://core.telegram.org/bots/features#creating-a-new-bot).  
`AUTHENTICATED_USER_IDS` is your telegram user id, run the bot and look at the logs to find yours to skip password authentication.  

```python3
DIR_TV_SHOWS = '/plex/media/TV Shows'
DIR_MOVIES = '/plex/media/Movies'

# OR

DIR_TV_SHOWS = r'C:\Users\USER\Videos\TV Shows'
DIR_MOVIES = r'C:\Users\USER\Videos\Movies'
```


## Requirements
`python3 -m pip install requests cachetools transmissionrpc python-telegram-bot dlna-cast beautifulsoup4 python-magic`  

[Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe) (for Transmission on windows)  

Transmission client [listening on port 9091](https://github.com/transmission/transmission/blob/main/docs/Web-Interface.md) (or change create_transmission_rpc() in transmission_ctl.py)  
Make sure to check the "Web interface" when [installing Transmission](https://transmissionbt.com/download).  


In order to convert video files [ffmpeg](https://www.ffmpeg.org/about.html) must be available.  
You can download and extract a zip [from here](https://github.com/BtbN/FFmpeg-Builds/releases) into the same directory as the bot.  
