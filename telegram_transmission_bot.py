#!/usr/bin/python3

import re
import logging
import functools
import random
import string
import urllib
import subprocess
import types

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


######################################################################
# State utils
######################################################################
__STATE_I__ = 0

def State():
    global __STATE_I__
    __STATE_I__ += 1
    return __STATE_I__

STATES = {}
CALLBACKS = {}

def make_state(state_name):
    global STATES

    state = STATES.get(state_name)
    
    if state is None:
        state = State()

        STATES[state_name] = state

    return state

COMMANDS_LAYOUT = [
            ['add_tv_show', 'add_movie'], 
            ['start_torrent','stop_torrent', 'delete_torrent'],
            ['list_torrents', 'list_torrent_files'],
            ['enable_all_torrent_files', 'toggle_all_torrent_files', 'toggle_torrent_file'],
            ['storage_stats']
 ] 

INTERNAL_STATES = ['_start', '_authenticate', '_main_menu',
            '_process_main_menu_choice',
            '_cancel']
for state in INTERNAL_STATES:
    make_state(state)

def get_scope():
    scope = globals().copy()
    scope.update(CALLBACKS)
    return scope

######################################################################
# Misc utils
######################################################################

def execute_shell(cmd):
    return subprocess.check_output(cmd, shell=True).decode()

def random_string(length=5, alphabet=string.ascii_letters):
    return ''.join(random.choice(alphabet) for _ in range(length))

def to_camel_case(text):
    ret = ""

    for part in text.split("_"):
        if not part:
            continue

        ret += part[0].upper() + part[1:] + " "

    return ret[:-1]

def iter_torrent_reprs():
    return transmission_ctl.iter_torrents(transformation=transmission_ctl.torrent_repr)


######################################################################
# Telegram bot utils
######################################################################

REMOVE_MARKUP = ReplyKeyboardRemove()

def repr_action(update, text):
    user_id = update.message.from_user.id
    msg = f'UserID {user_id} {text}'
    return msg

def log_on_call(enter_msg=None, exit_msg=None):

    def decorator(func):
        return func  # Disable for now

        if enter_msg is None and exit_msg is None:
            l_enter_msg = f'entered {func.__name__}'
            l_exit_msg = f'exited {func.__name__}'
        else:
            l_enter_msg = enter_msg
            l_exit_msg = exit_msg

        @functools.wraps(func)
        async def wrapper(update, *args, **kwargs):
            if l_enter_msg:
                msg = repr_action(update, l_enter_msg)
                logging.info(msg)
            
            ret = await func(update, *args, **kwargs)

            if l_exit_msg:
                msg = repr_action(update, l_exit_msg)
                logging.info(msg)

            return ret

        return wrapper
    return decorator

def is_cancel(update):
    try:
        return update.message.text.strip().lower() == 'cancel'
    except:
        return False

def cancelable(func):

    @functools.wraps(func)
    async def wrapper(update, context, *args, **kwargs):

        if is_cancel(update):
            logging.info(repr_action(update, 'canceled'))
            return await _main_menu(update)

        return await func(update, context, *args, **kwargs)

    return wrapper



async def reply(update: Update, text: str, reply_markup=REMOVE_MARKUP):
    await update.message.reply_text(text, reply_markup=reply_markup)

async def multi_reply(update: Update, label: str, 
                      values, with_index=True,
                      reply_markup=REMOVE_MARKUP):

    # Check if values is wanted iterable
    if isinstance(values, (list, map, types.GeneratorType)):

        for i, v in enumerate(values):
            sublabel = f'[{i}]' if with_index else ''
            await reply(update, f"{label}{sublabel} = {v}", reply_markup=reply_markup)

    else:
        await reply(update, f"{label} = {values}", reply_markup=reply_markup)


######################################################################
# Authentication and main menu utils
######################################################################

COMMANDS = [cmd for row in COMMANDS_LAYOUT for cmd in row]
COMMANDS_BY_NAMES = {to_camel_case(cmd): cmd for cmd in COMMANDS}

COMMANDS_LAYOUT_CAMELCASE = [[]] * len(COMMANDS_LAYOUT)

for i, row in enumerate(COMMANDS_LAYOUT):
    COMMANDS_LAYOUT_CAMELCASE[i] = [to_camel_case(cmd) for cmd in row]


MAIN_MENU_MARKUP = ReplyKeyboardMarkup(COMMANDS_LAYOUT_CAMELCASE, resize_keyboard=True, selective=True)

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

async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ Conversation's entry point """
     
    if update.message.from_user.id in config.AUTHENTICATED_USER_IDS:
        logging.info(repr_action(update, 'authenticated'))
        return await _main_menu(update)

    msg = repr_action(update, 'requires a password')
    logging.info(msg)
    await reply(update, msg)
    
    new_password()
    return STATES.get('_authenticate')

async def _authenticate(update, context):
    ''' Check if the password was correct '''

    text = update.message.text

    password = get_password()
    success = text == password

    msg = repr_action(update, f"'{text}' =='{PASSWORD}' {success}")
    logging.info(msg)

    if not success:
        new_password()
        return ConversationHandler.END
        
    return await _main_menu(update)

async def _cancel(update, context):
    await reply(update, 'Cancelled.')

    # Don't serve main menu to prevent canceling during before authentication
    return STATES.get('_start')

async def _main_menu(update: Update, context=None):
    ''' Send the commands menu then process the choice '''

    await reply(update, "Enter command:", reply_markup=MAIN_MENU_MARKUP)
    return STATES.get('_process_main_menu_choice')


async def _process_main_menu_choice(update: Update, context):
    """ Process chosen command """

    choice = COMMANDS_BY_NAMES.get(update.message.text, None)

    msg = repr_action(update, f'chose {choice}')
    logging.info(msg)

    if choice is None:
        return await _main_menu(update)

    else:
        scope = get_scope()
        callback = scope.get(choice, None)

        if callback is not None:
            return await callback(update, context)

        return await _main_menu(update)
    

######################################################################
# Prompt and choice handler utils
######################################################################

async def prompt_magnet(update: Update):
    await reply(update, "Enter magnet url (or type 'cancel'):")
   

async def prompt_torrent(update: Update):
    keyboard = [['Cancel']] + [[t] for t in iter_torrent_reprs()]

    markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True, selective=True)        
    await reply(update, "Choose torrent:", reply_markup=markup)

async def prompt_torrent_files(update, torrent_id):
    sorted_by_name = sorted(transmission_ctl.iter_torrent_files(torrent_id), key=lambda tf: tf.name)
    keyboard = [["Cancel"]] + [[str(tf)] for tf in sorted_by_name]

    markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True, selective=True)        
    await reply(update, "Choose file:", reply_markup=markup)

def choice_to_torrent_id(choice):
    if choice not in list(iter_torrent_reprs()):
        return

    regex_torrent_id = r'^(\d+):.*'
    match = re.match(regex_torrent_id, choice)
    
    if not match:
        return

    torrent_id = int(match.group(1))
    return torrent_id
    
def choice_to_torrent_file_id(choice):
    regex_torrent_id = r'^(\d+).(\d+):.*'
    match = re.match(regex_torrent_id, choice)
    
    if not match:
        return

    try:
        torrent_id = int(match.group(1))
        file_id = int(match.group(2))

    except:
        return

    return (torrent_id, file_id)

def choice_to_torrent_file(choice):
    tf = choice_to_torrent_file_id(choice)

    if tf is None:
        return None

    torrent_id, file_id = tf

    for other in transmission_ctl.iter_torrent_files(torrent_id):
        if other.file_id == file_id:
            return other


######################################################################
# Handler creation utils
######################################################################

def create_magnet_handler(state_name, callback):
    state = make_state(state_name)

    @log_on_call(f'entered {state_name}', f'exited {state_name}')
    @cancelable
    @functools.wraps(callback)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text

        if not text.lower().startswith("magnet:"):
            await prompt_magnet(update)
            return state
            
        ret = callback(text)
        
        try:
            dn = urllib.parse.parse_qs(urllib.parse.urlsplit(text).query)['dn'][0]
        except:
            dn = text[:30] + " ..."
        
        label = f"{state_name}('{dn}')"

        msg = repr_action(update, label)
        logging.info(msg)

        await multi_reply(update, label, ret)
        return await _main_menu(update)
    
    CALLBACKS[state_name] = wrapper
    
    return wrapper
    
def create_torrent_handler(state_name, callback):
    state = make_state(state_name)

    @log_on_call(f'entered {state_name} torrent selection', f'exited {state_name} torrent selection')
    @cancelable
    @functools.wraps(callback)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):

        choice = update.message.text
        torrent_id = choice_to_torrent_id(choice)

        if torrent_id is None:
            await prompt_torrent(update)
            return state    

        label = f'{state_name}({torrent_id})'
        
        msg = repr_action(update, label)
        logging.info(msg)

        ret = callback(torrent_id)

        await multi_reply(update, label, ret)
        return await _main_menu(update)

    CALLBACKS[state_name] = wrapper
    return wrapper


def create_torrent_file_handler(state_name, callback):
    prompt_state = make_state(state_name)

    process_state_name = f'_{state_name}_torrent_file_choice_handler'
    process_state = make_state(process_state_name)

    @log_on_call(f'entered {state_name} torrent selection', f'exited {state_name} torrent selection')
    @cancelable
    async def _prompt_torrent_files(update, context):
        choice = update.message.text

        torrent_id = choice_to_torrent_id(choice)

        if torrent_id is None:
            await prompt_torrent(update)
            return prompt_state

        await prompt_torrent_files(update, torrent_id)
        return process_state

    @log_on_call(f'entered {state_name} file selection', f'exited {state_name} file selection')
    @cancelable
    @functools.wraps(callback)
    async def _process_torrent_file_choice(update, context):
        choice = update.message.text

        tf = choice_to_torrent_file(choice)
        if tf is None:
            await reply(update, 'Error choosing torrent file')
            return await _main_menu(update)

        label = f"{state_name}({tf.torrent_id}.{tf.file_id})"

        msg = repr_action(update, label)
        logging.info(msg)

        ret = callback(tf)
        
        await multi_reply(update, label, ret)
        return await _main_menu(update)
    
    CALLBACKS[state_name] = _prompt_torrent_files
    CALLBACKS[process_state_name] = _process_torrent_file_choice

    return _prompt_torrent_files, _process_torrent_file_choice


######################################################################
# Commands implementation
######################################################################

@log_on_call()
async def list_torrents(update, context):
    for torrent_repr in transmission_ctl.iter_torrents(transformation=transmission_ctl.torrent_status_repr):
        await reply(update, torrent_repr)
        
    return await _main_menu(update)
    
@log_on_call()
async def storage_stats(update, context):
    await reply(update, execute_shell("df -h | head -n 1; df -h | grep /plex/media"))
    return await _main_menu(update)


create_magnet_handler('add_tv_show', lambda magnet: transmission_ctl.add_torrent_to_dir(magnet, config.DIR_TV_SHOWS))
create_magnet_handler('add_movie', lambda magnet: transmission_ctl.add_torrent_to_dir(magnet, config.DIR_MOVIES))

create_torrent_handler('start_torrent', transmission_ctl.start_torrent)
create_torrent_handler('stop_torrent', transmission_ctl.stop_torrent)
create_torrent_handler('delete_torrent', transmission_ctl.delete_torrent)
create_torrent_handler('list_torrent_files',

    # Map sorted torrent files to their representation
    lambda torrent_id: map(
        repr,
        sorted(transmission_ctl.iter_torrent_files(torrent_id), key=lambda tf: tf.name)
        )
    )

create_torrent_handler('enable_all_torrent_files',
    # TODO: Do this in one request
    lambda torrent_id: map(transmission_ctl.toggle_torrent_file, transmission_ctl.iter_torrent_files(torrent_id))
    )
create_torrent_handler('toggle_all_torrent_files',
    # TODO: Do this in one request
    lambda torrent_id: map(transmission_ctl.toggle_torrent_file, transmission_ctl.iter_torrent_files(torrent_id))
    )

create_torrent_file_handler('toggle_torrent_file', transmission_ctl.toggle_torrent_file)



if __name__ == '__main__':
    application = Application.builder().token(config.API_TOKEN).build()

    scope = get_scope()
    states = {}

    for state in STATES:

        state_enum = make_state(state)
        state_callback = scope.get(state, None)

        if state_enum is None or state_callback is None:
            logging.error(f'Error registering {state} ({state_enum}) = {state_callback}')
            continue

        states[state_enum] = [MessageHandler(filters.Regex('.*'), state_callback)]

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('.*'), _start)],

        states=states,

        fallbacks=[CommandHandler("cancel", _cancel)],
    )  

    application.add_handler(conv_handler)
    application.run_polling(allowed_updates=Update.ALL_TYPES)

