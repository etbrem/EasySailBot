#!/usr/bin/python3
import re
import logging
import functools
import random
import string
import urllib
import subprocess

import transmission_ctl
import config

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)


# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logging.getLogger('httpx').setLevel(logging.WARN)



API_TOKEN = config.API_TOKEN
AUTHENTICATED_USER_IDS = config.AUTHENTICATED_USER_IDS



def execute_shell(cmd):
    return subprocess.check_output(cmd, shell=True).decode()

def random_string(length=5, alphabet=string.ascii_letters):
    return ''.join(random.choice(alphabet) for _ in range(length))

def iter_torrent_reprs():
    return transmission_ctl.iter_torrents(translation=transmission_ctl.torrent_repr)


# TODO: Cleanup aiogram code completely
__STATE_I__ = 0

def State():
    global __STATE_I__
    __STATE_I__ += 1
    return __STATE_I__


class Commands(object):
    _start = State()
    _authenticate = State()

    _main_menu = State()
    _process_main_menu_choice = State()
    _cancel = State()
    
    storage_stats = State()
    add_tv_show = State()
    add_movie = State()
    list_torrents = State()
    start_torrent = State()
    stop_torrent = State()
    delete_torrent = State()

def iter_commands():

    for field_name in dir(Commands):
        if field_name.startswith("_"):
            continue
    
        field_value = getattr(Commands, field_name, None)
        if isinstance(field_value, int):

            yield field_name

def to_camel_case(text):
    ret = ""

    for part in text.split("_"):
        if not part:
            continue

        ret += part[0].upper() + part[1:] + " "

    return ret[:-1]




COMMANDS_BY_NAMES = {to_camel_case(cmd): cmd for cmd in iter_commands()}

COMMANDS_KEYBOARD_BUTTONS = [[cmd] for cmd in COMMANDS_BY_NAMES]

MAIN_MENU_MARKUP = ReplyKeyboardMarkup(COMMANDS_KEYBOARD_BUTTONS, resize_keyboard=True, selective=True)
REMOVE_MARKUP = ReplyKeyboardRemove()


PASSWORD = ''

def get_password():
    global PASSWORD
    return PASSWORD

def new_password():
    global PASSWORD
    
    PASSWORD = random_string(alphabet=string.digits)
    
    logging.critical("="*30)
    logging.critical(f"PASSWORD: {PASSWORD}")
    logging.critical("="*30)

    return PASSWORD

async def reply(update: Update, text: str, reply_markup=REMOVE_MARKUP):
    await update.message.reply_text(text, reply_markup=reply_markup)

async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Conversation's entry point
    """
     
    if update.message.from_user.id in AUTHENTICATED_USER_IDS:
        await reply(update, f'User authenticated')
        return await _main_menu(update)

    
    msg = f"UserID {update.message.from_user.id} needs to authenticate with a password"
    logging.info(msg)
    await reply(update, msg)
    
    new_password()
    return Commands._authenticate

async def _authenticate(update, context):
    text = update.message.text

    password = get_password()
    success = text == password

    logging.info(f"PASSWORD ({password}) ATTEMPT {success}: USERID={update.message.from_user.id} '{text}'")

    if not success:
        new_password()
        return ConversationHandler.END
        
    return await _main_menu(update)

async def _cancel(update, context):
    await reply(update, 'Cancelled.')

    # Don't serve main menu to prevent canceling during before authentication
    return Commands._start


async def _main_menu(update: Update, context=None):
    await reply(update, "Enter command:", reply_markup=MAIN_MENU_MARKUP)
    return Commands._process_main_menu_choice


async def _process_main_menu_choice(update: Update, context):
    """
    Process user name
    """

    choice = COMMANDS_BY_NAMES.get(update.message.text, None)

    logging.info(f"{update.message.from_user.id} chose: {choice}")

    if choice is None:
        return await _main_menu(update)

    else:
        scope = globals()
        callback = scope.get(choice, None)

        if callback is not None:
            return await callback(update, context)

        return await _main_menu(update)


async def list_torrents(update, context):
    for torrent_repr in transmission_ctl.iter_torrents(translation=transmission_ctl.torrent_status_repr):
        await reply(update, torrent_repr)
        
    return await _main_menu(update)
    
async def storage_stats(update, context):
    await reply(update, execute_shell("df -h | head -n 1; df -h | grep /plex/media"))
    return await _main_menu(update)

    
    
async def prompt_magnet(update: Update):
    await reply(update, "Enter magnet url (or type 'cancel'):")
   
def create_magnet_handler(state, callback):
    @functools.wraps(callback)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text

        if text.strip().lower() == 'cancel':
            return await _main_menu(update)

        if not text.lower().startswith("magnet:"):
            await prompt_magnet(update)
            return state
            
        ret = callback(text)
        
        try:
            dn = urllib.parse.parse_qs(urllib.parse.urlsplit(text).query)['dn'][0]
        except:
            dn = text[:30] + " ..."
        
        await reply(update, f"{callback.__name__}('{dn}') = {ret}")
        return await _main_menu(update)
        
    return wrapper

add_tv_show = create_magnet_handler(Commands.add_tv_show, lambda magnet: transmission_ctl.add_torrent_to_dir(magnet, config.DIR_TV_SHOW))
add_movie = create_magnet_handler(Commands.add_movie, lambda magnet: transmission_ctl.add_torrent_to_dir(magnet, config.DIR_MOVIE))

async def prompt_torrent(update: Update):
    keyboard = [['Cancel']] + [[t] for t in iter_torrent_reprs()]

    markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True, selective=True)        
    await reply(update, "Choose torrent:", reply_markup=markup)
    

def create_torrent_handler(state, callback):

    @functools.wraps(callback)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        choice = update.message.text
        
        if choice.strip().lower() == 'cancel':
            return await _main_menu(update)

        if choice not in list(iter_torrent_reprs()):
            await prompt_torrent(update)
            return state
            
        regex_torrent_id = r'^(\d+):.+?'
        match = re.match(regex_torrent_id, choice)
        
        if not match:
            await reply(update, "Internal error?")
            await prompt_torrent(update)
            return state
        
        torrent_id = int(match.group(1))
        ret = callback(torrent_id)
   
        await reply(update, f"{callback.__name__}({torrent_id}) = {ret}")
        return await _main_menu(update)
        
    return wrapper

start_torrent = create_torrent_handler(Commands.start_torrent, transmission_ctl.start_torrent)
stop_torrent = create_torrent_handler(Commands.stop_torrent, transmission_ctl.stop_torrent)
delete_torrent = create_torrent_handler(Commands.delete_torrent, transmission_ctl.delete_torrent)

if __name__ == '__main__':
    application = Application.builder().token(API_TOKEN).build()

    scope = globals()
    states = {}

    for cmd in ['_authenticate', '_main_menu', '_process_main_menu_choice'] + \
                list(iter_commands()):

        cmd_enum = getattr(Commands, cmd, None)
        cmd_callback = scope.get(cmd, None)

        if cmd_enum is None or cmd_callback is None:
            continue

        states[cmd_enum] = [MessageHandler(filters.Regex('.*'), cmd_callback)]

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", _start)],

        states=states,

        fallbacks=[CommandHandler("cancel", _cancel)],
    )  

    application.add_handler(conv_handler)
    application.run_polling(allowed_updates=Update.ALL_TYPES)

