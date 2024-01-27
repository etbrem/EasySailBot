#/usr/bin/python3
import re
import logging
import functools
import random
import string
import urllib
import subprocess


import aiogram.utils.markdown as md
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Text
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ParseMode
from aiogram.utils import executor


import transmission_ctl

def execute_shell(cmd):
    return subprocess.check_output(cmd, shell=True).decode()

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)


AUTHENTICATED_USER_IDS = []
PASSWORD = ''

API_TOKEN = ''
bot = Bot(token=API_TOKEN)


# For example use simple MemoryStorage for Dispatcher.
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)



# States
class Commands(StatesGroup):
    _authenticate = State()
    _main_menu = State()
    
    storage_stats = State()
    add_tv_show = State()
    add_movie = State()
    list_torrents = State()
    start_torrent = State()
    stop_torrent = State()
    delete_torrent = State()

def random_string(length=5, alphabet=string.ascii_letters):
    return ''.join(random.choice(alphabet) for _ in range(length))

def enumerate_torrent_reprs():
    return transmission_ctl.iter_torrents(translation=transmission_ctl.torrent_repr)

    
def enumerate_commands():

    for field_name in dir(Commands):
        if field_name.startswith("_"):
            continue
    
        field_value = getattr(Commands, field_name, None)

        if isinstance(field_value, State):
            yield field_name

def to_camel_case(text):
    ret = ""

    for part in text.split("_"):
        if not part:
            continue

        ret += part[0].upper() + part[1:] + " "

    return ret[:-1]


COMMANDS_BY_NAMES = {to_camel_case(cmd): cmd for cmd in enumerate_commands()}

REMOVE_MARKUP = types.ReplyKeyboardRemove()
MAIN_MENU_MARKUP = types.ReplyKeyboardMarkup(resize_keyboard=True, selective=True)

for cmd in COMMANDS_BY_NAMES.keys():
    MAIN_MENU_MARKUP.add(cmd)


async def serve_main_menu(message):
    await Commands._main_menu.set()
    await message.answer("Enter command:", reply_markup=MAIN_MENU_MARKUP)

@dp.message_handler(commands='start')
async def cmd_start(message: types.Message):
    """
    Conversation's entry point
    """
    global PASSWORD
     
    PASSWORD = random_string(alphabet=string.digits)
    PASSWORD = PASSWORD[0].upper() + PASSWORD[1:]

    if message.from_user.id in AUTHENTICATED_USER_IDS:
        return await serve_main_menu(message)
    
    logging.critical("="*30)
    logging.critical(f"PASSWORD: {PASSWORD}")
    logging.critical("="*30)

    await message.answer("Authenticate:", reply_markup=REMOVE_MARKUP)
    await Commands.next()


@dp.message_handler(state=Commands._authenticate)
async def process_authenticate(message: types.Message, state: FSMContext):
    logging.info(f"PASSWORD ({PASSWORD}) ATTEMPT {bool(message.text == PASSWORD)}:  {message.from_user.id} '{message.text}'")
    if message.text != PASSWORD:
        return await state.finish()
        
    await serve_main_menu(message)


# You can use state '*' if you need to handle all states
@dp.message_handler(state='*', commands='cancel')
@dp.message_handler(Text(equals='cancel', ignore_case=True), state='*')
async def cancel_handler(message: types.Message, state: FSMContext):
    """
    Allow user to cancel any action
    """
    current_state = await state.get_state()
    if current_state is None:
        return

    logging.info('Cancelling state %r', current_state)
    # Cancel state and inform user about it
    #await state.finish()
    # And remove keyboard (just in case)
    await message.reply('Cancelled.', reply_markup=REMOVE_MARKUP)
    await serve_main_menu(message)



@dp.message_handler(state=Commands._main_menu)
async def process_main_menu_choice(message: types.Message, state: FSMContext):
    """
    Process user name
    """

    choice = COMMANDS_BY_NAMES.get(message.text, None)

    logging.info(f"CHOICE: {choice}")

    if choice is None:
        await message.reply("Enter command:", reply_markup=MAIN_MENU_MARKUP)
    else:
        scope = globals().copy()
        scope.update(locals())

        await getattr(Commands, choice).set()        
        
        prompt_callback = scope.get(choice, None)
        if prompt_callback is not None:
            await prompt_callback(message)
        


async def list_torrents(message):
    for torrent_repr in transmission_ctl.iter_torrents(translation=transmission_ctl.torrent_status_repr):
        await message.answer(torrent_repr)
        
    await serve_main_menu(message)
    
async def storage_stats(message):
    await message.answer(execute_shell("df -h | head -n 1; df -h | grep /plex/media"))
    await serve_main_menu(message)
    
    
async def prompt_magnet(message: types.Message):
    await message.answer("Enter magnet url (or type 'cancel'):", reply_markup=REMOVE_MARKUP)
   
def create_magnet_handler(state, callback):
    @dp.message_handler(state=state)
    @functools.wraps(callback)
    async def foo(message: types.Message):
        if not message.text.lower().startswith("magnet:"):
            return await prompt_magnet(message)
            
        ret = callback(message.text)
        
        try:
            dn = urllib.parse.parse_qs(urllib.parse.urlsplit(message.text).query)['dn'][0]
        except:
            dn = message.text[:30] + " ..."
        
        await message.reply(f"{callback._name_}('{dn}') = {ret}")
        
        await serve_main_menu(message)
        
    return foo

add_tv_show = prompt_magnet
add_tv_show_handler = create_magnet_handler(Commands.add_tv_show, transmission_ctl.add_tv_show)
add_movie = prompt_magnet
add_movie_handler = create_magnet_handler(Commands.add_movie, transmission_ctl.add_movie)   

async def prompt_torrent(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, selective=True)
    markup.add("Cancel")
    
    for torrent_repr in enumerate_torrent_reprs():
        markup.add(torrent_repr)
        
    await message.answer("Choose torrent:", reply_markup=markup)
    

def create_torrent_handler(state, callback):
    @dp.message_handler(state=state)
    @functools.wraps(callback)
    async def foo(message):
        choice = message.text
        
        if choice not in list(enumerate_torrent_reprs()):
            await message.reply("Invalid input")
            return await prompt_torrent(message)
            
        regex_torrent_id = r'^(\d+):.+?'
        match = re.match(regex_torrent_id, choice)
        
        if not match:
            await message.reply("Internal error?")
            return await prompt_torrent(message)
        
        torrent_id = int(match.group(1))
        ret = callback(torrent_id)
   
        await message.reply(f"{callback._name_}({torrent_id}) = {ret}")
        await serve_main_menu(message) 
        
    return foo

start_torrent = prompt_torrent
start_torrent_handler = create_torrent_handler(Commands.start_torrent, transmission_ctl.start_torrent)
stop_torrent = prompt_torrent
stop_torrent_handler = create_torrent_handler(Commands.stop_torrent, transmission_ctl.stop_torrent)
delete_torrent = prompt_torrent
delete_torrent_handler = create_torrent_handler(Commands.delete_torrent, transmission_ctl.delete_torrent)

if _name_ == '_main_':
    executor.start_polling(dp, skip_updates=True)
