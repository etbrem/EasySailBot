# TelegramTransmissionBot
Control your Transmission torrent client using a telegram bot

`python3 transmission_telegram_bot.py`

## Configuration

In telegram_transmission_bot.py set the variables `API_TOKEN` and `AUTHENTICATED_USER_IDS`.  

`API_TOKEN` is your bot's [API token](https://core.telegram.org/bots/features#creating-a-new-bot).  
`AUTHENTICATED_USER_IDS` is your telegram user id, run the bot and look at the logs to find yours to skip password authentication.  

In transmission_ctl.py set the variables `DIR_TV_SHOWS` and `DIR_MOVIES`  

```python3
DIR_TV_SHOWS = '/plex/media/TV Shows'
DIR_MOVIES = '/plex/media/Movies'
```


## Requirements
`python3 -m pip install transmissionrpc python-telegram-bot`  

[Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe)

Transmission client [listening on port 9091](https://github.com/transmission/transmission/blob/main/docs/Web-Interface.md) (or change create_transmission_rpc() in transmission_ctl.py)  
Make sure to check the "Web interface" when [installing Transmission](https://transmissionbt.com/download).  

The "Storage Stats" command needs the linux binaries `df`, `head` and `grep`  
