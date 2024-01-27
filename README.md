# TelegramTransmissionBot
Control your Transmission torrent client using a telegram bot

# Configuration

In telegram_transmission_bot.py set the variables `API_TOKEN` and `AUTHENTICATED_USER_IDS`.  
`API_TOKEN` is your bot's [API token](https://core.telegram.org/bots/features#creating-a-new-bot).  
`AUTHENTICATED_USER_IDS` is your telegram user id, run the bot and looking at the logs to find yours to skip password authentication.  

In transmission_ctl.py set the variables `DIR_TV_SHOWS` and `DIR_MOVIES`  
```python
DIR_TV_SHOWS = '/plex/media/TV Shows'
DIR_MOVIES = '/plex/media/Movies'
```


## Requirements
`pip install transmissionrpc`
