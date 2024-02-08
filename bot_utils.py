import os
import re
import time
import functools
import random
import string
import urllib
import subprocess
import types
import inspect
import logging

import cachetools  # python3 -m pip install cachetools

# python3 -m pip install python-telegram-bot
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)


import config
import transmission_utils


# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logging.getLogger('httpx').setLevel(logging.WARN)

LOGGER = logging.getLogger(__name__)


######################################################################
# Misc utils
######################################################################

class UninitializedClass(object):
    pass

# Used to identify uninitialized parameters with  isinstance(param, UninitializedClass)  or  param is UNINITIALIZED 
# This is so param=None can be handled seperately without being considered an uninitialized parameter
UNINITIALIZED = UninitializedClass()

def get_used_size(start_path = '.'):
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(start_path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            # skip if it is symbolic link
            if not os.path.islink(fp):
                total_size += os.path.getsize(fp)

    return total_size

def get_free_size(start_path = '.'):
    return transmission_utils.get_transmission_rpc().free_space(start_path)

def execute_shell(cmd):
    LOGGER.info(f'Executing shell command: {cmd}')
    return subprocess.check_output(cmd, shell=True).decode()

def random_string(length=8, alphabet=string.ascii_letters):
    return ''.join(random.choice(alphabet) for _ in range(length))

def random_identifier():
    # Making a not completely random identifier helps to correlate to events
    return f'{time.time()}_{random_string()}'

def to_camel_case(text):
    ret = ""

    for part in text.split("_"):
        if not part:
            continue

        ret += part[0].upper() + part[1:] + " "

    return ret[:-1]

def flatten_layout(layout):
    return [x for row in layout for x in row]

def map_layout(callback, layout):
    return [[callback(x) for x in row] for row in layout]

async def call_callback(callback, *args, **kwargs):

    if isinstance(callback, (int, str)):
        return callback

    if inspect.iscoroutinefunction(callback):
        return await callback(*args, **kwargs)

    return callback(*args, **kwargs)

class TimeoutDefaultDict(cachetools.TTLCache):
    def __init__(self, maxsize, ttl, *args, default_factory=dict, reset_on_access=True, **kwargs):
        super().__init__(maxsize, ttl, *args, **kwargs)
        self.default_factory = default_factory
        self.reset_on_access = reset_on_access

    def reset_expiration(self, key):
        # TODO: Don't rely on TTLCache API

        root = self._TTLCache__root
        curr = root.next
        while curr is not root:
            if curr.key == key:
                with self.timer as time:
                    curr.expires = time + self.ttl
                return curr.expires
            curr = curr.next

    def __setitem__(self, key, value):
        ret = super().__setitem__(key, value)

        if self.reset_on_access:
            self.reset_expiration(key)
            
        return ret

    def __getitem__(self, key):
        try:
            ret = super().__getitem__(key)
        except KeyError:
            self.__setitem__(key, self.default_factory())
            return super().__getitem__(key)

        else:
            if self.reset_on_access:
                self.reset_expiration(key)
            return ret

def new_userdata_storage(timeout=None, maxsize=128, reset_on_access=True):
    if timeout is None:
        timeout = 5 * 60

    return TimeoutDefaultDict(maxsize=maxsize, ttl=timeout, default_factory=dict, reset_on_access=reset_on_access)

def log_on_call(enter_msg=None, exit_msg=None):
    # TODO: Figure out

    def decorator(func):

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
                LOGGER.info(msg)
            
            ret = await func(update, *args, **kwargs)

            if l_exit_msg:
                msg = repr_action(update, l_exit_msg)
                LOGGER.info(msg)

            return ret

        return wrapper
    return decorator


######################################################################
# Telegram bot utils
######################################################################

REMOVE_MARKUP = ReplyKeyboardRemove()

def get_text(update):
    try:
        return update.message.text
    except:
        return ''

def get_userid(update):
    return update.message.from_user.id

def repr_action(update, text):
    user_id = get_userid(update)
    msg = f'UserID {user_id} {text}'
    return msg

def is_cancel(update):
    try:
        return get_text(update).strip().lower() == 'cancel'
    except:
        return False

async def reply(update: Update, text: str, reply_markup=None):
    await update.message.reply_text(text, reply_markup=reply_markup)

async def multi_reply(update: Update, label: str, 
                      values, with_index=False,
                      reply_markup=None):

    # Check if values is wanted iterable
    if isinstance(values, (list, map, types.GeneratorType)):

        for i, v in enumerate(values):
            sublabel = f'[{i}]' if with_index else ''
            await reply(update, f"{label}{sublabel} = {v}", reply_markup=reply_markup)

    else:
        await reply(update, f"{label} = {values}", reply_markup=reply_markup)



######################################################################
# Menu utils
######################################################################
class Menu(object):
    '''
    A simple menu starts off with prompting the user to choose a command then processes the choice:
        -> _start() -> _main_menu() -> [user selects] -> _process_main_menu_choice()

        These and _cancel() are the default INTERNAL_STATES of a menu

    A choice is a mapping between the text on the button to a state in the menu:
        choice = menu.get_text_mappings().get(update.message.text, None)

    The layout of the menu is specified as a matrix of layout[row][column]
    where each entry is the name of the callback to call

    Each state in the menu is handled by a callback which must register itself (or be a member of the menu class):

        @menu.callback()  # Unless specified use func.__name__ as the state_name
        async def hello_world(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
            await reply(update, 'Hello World!')

            return '_main_menu'  # This enters the "_main_menu" state 
                                 # but calls _main_menu() (and prompt_commands()) only after the user sends another message
                                 # so the user won't see the prompt until they send a message

        @menu.callback(state_name='hello_world2', menu_on_exit=True)
        async def hello_world2(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
            await reply(update, 'Hello World!')
            await reply(update, 'Returning to main menu')

    When registering menus in the BOT each MENU prefixes a unique string to all its states' names,
    so the bot can handle several menus without overwriting state callbacks with the same name.

    For callbacks registered in the MENU with the @menu.callback(...) decorator this is done automatically to any returned string,
        unless  @menu.callback(... prefix_menu=False ...)  is specified
    otherwise  return menu.prefix_menu(next_state_name)
    or  return the value of a callback which does prefix_menu

    
    Each menu has a userdata storage which is a dict of dicts indexed by the userid
    When an entry in the userdata storage isn't referenced for 5 minutes it gets deleted
    '''

    INTERNAL_STATES = ['_start', '_main_menu',
                    '_process_main_menu_choice', '_cancel']

    def __init__(self, name=None, layout=None, states=None, callbacks=None,
                 userdata_timeout=getattr(config, 'USERDATA_TIMEOUT', 5 * 60)):
        
        self.name = f'Menu_{name or random_string(8)}'
        self.states = states or set()
        self.callbacks = callbacks or {}
        self.text_to_states = {}

        self._userdatas = new_userdata_storage(timeout=userdata_timeout)

        self.layout = layout
        if self.layout is not None:
            for cmd in flatten_layout(self.layout):

                self.register_text_mapping(self.transform_cmd_name(cmd), cmd)

                cb = getattr(self, cmd, None)
                if cb is not None:
                    self.register_callback(cmd, cb)

        for state_name in self.INTERNAL_STATES:
            self.register_state(state_name)

            cb = getattr(self, state_name, None)
            if cb is not None:

                setattr(self, state_name, cb)
                self.register_callback(state_name, cb)

    def transform_cmd_name(self, cmd_name):
        return to_camel_case(cmd_name)
    
    def register_state(self, state_name):
        self.states.add(state_name)

    def register_text_mapping(self, text, state_name):
        self.register_state(state_name)
        self.text_to_states[text] = state_name

    def get_text_mappings(self):
        return self.text_to_states

    def register_callback(self, state_name, callback):
        self.register_state(state_name)
        self.callbacks[state_name] = callback

    def get_callbacks(self):
        return self.callbacks

    def get_callback(self, name, default=UNINITIALIZED):
        if isinstance(default, UninitializedClass):
            default = self._main_menu
        
        return self.get_callbacks().get(name, default)

    def get_userdata(self, userid):
        if isinstance(userid, Update):
            userid = get_userid(userid)

        return self._userdatas[userid]

    def del_userdata_entries(self, userid, *keys):
        userdata = self.get_userdata(userid)

        for key in keys:
            if key in userdata:
                del userdata[key]

    def prefix_menu(self, label):
        prefix = f'{self.name}_'
        if not label.startswith(prefix):
            return prefix + label
        return label

    def prefix_menu_retval(self, callback):

        @functools.wraps(callback)
        async def wrapper(*args, **kwargs):
            ret = await callback(*args, *kwargs)

            if isinstance(ret, str):
                return self.prefix_menu(ret)

            return ret
        return wrapper

    def create_message_handlers(self, prepend_menu_name=True):

        callbacks = self.get_callbacks()
        states = {}

        for state_name in self.states:

            state_callback = callbacks.get(state_name, None)

            if isinstance(state_callback, (int, str)):
                l_state_callback = state_callback

                def _foo(*args, **kwargs):
                    return l_state_callback
                state_callback = _foo

            if prepend_menu_name:
                state_name = self.prefix_menu(state_name)

            LOGGER.info(f'{self.name} registered callback {state_name}')
            states[state_name] = [MessageHandler(filters.Regex('.*'), state_callback)]

        return states

    def create_markup(self):
        markup = ReplyKeyboardMarkup(map_layout(self.transform_cmd_name, self.layout), resize_keyboard=True, selective=True)
        return markup

    async def _start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """ Conversation's entry point """ 

        return await self._main_menu(update, context)

    async def prompt_command(self, update):
        await reply(update, "Enter command:", reply_markup=self.create_markup())

    async def _main_menu(self, update: Update, context=None):
        ''' Send the commands menu then enter process choice state '''

        await self.prompt_command(update)
        return self.prefix_menu('_process_main_menu_choice')

    async def _process_main_menu_choice(self, update: Update, context):
        """ Process chosen command """
        # TODO: Namespace -> conversationMenu -> menu with mapped text + callbacks
        choice = self.get_text_mappings().get(get_text(update), None)

        msg = repr_action(update, f'chose {choice}')
        LOGGER.info(msg)

        if choice is None:
            return await self._main_menu(update, context)

        else:
            callback = self.get_callbacks().get(choice, None)

            if callback is not None:
                return await callback(update, context)

            return await self._main_menu(update, context)

    async def _cancel(self, update, context):
        await reply(update, 'Cancelled.')

        # Don't serve main menu to prevent canceling during before authentication
        return self.prefix_menu('_start')

    ###############
    # Decorators
    def register(self, state_name=None):
        def decorator(func):
            l_state_name = state_name or func.__name__
            self.register_callback(l_state_name, func)
            return func
        return decorator

    def menu_on_exit(self, func):

        @functools.wraps(func)
        async def wrapper(update, context, *args, **kwargs):
            await func(update, context, *args, **kwargs)
            return await self._main_menu(update, context)
        return wrapper

    def cancelable(self, func):

        @functools.wraps(func)
        async def wrapper(update, context, *args, **kwargs):

            if is_cancel(update):
                LOGGER.info(repr_action(update, 'canceled'))
                return await self._main_menu(update, context)

            return await func(update, context, *args, **kwargs)

        return wrapper

    def callback(self, state_name=None, menu_on_exit=False, prefix_menu=True):
        ''' A command is a cancelable (registered) callback which may return to menu when done  '''

        def decorator(func):
            l_state_name = state_name or func.__name__

            do_nothing = lambda func: func

            l_menu_on_exit = self.menu_on_exit if menu_on_exit else do_nothing
            l_prefix_menu = self.prefix_menu_retval if prefix_menu else do_nothing

            @functools.wraps(func)
            @self.register(state_name=l_state_name)
            @l_prefix_menu
            @l_menu_on_exit
            @self.cancelable
            async def wrapper(*args, **kwargs):
                return await func(*args, **kwargs)

            return wrapper

        return decorator
    
def menus_to_states(*menus):
    states = {}
    for menu in menus:
        states.update(menu.create_message_handlers())
    return states


######################################################################
# Menu with authentication process
######################################################################

class AuthenticatedMenu(Menu):
    INTERNAL_STATES = Menu.INTERNAL_STATES + ['_authenticate']

    def __init__(self, *args,
                password_authentication=True,
                add_to_authenticated_users=True,
                authenticated_user_ids=config.AUTHENTICATED_USER_IDS,
                **kwargs):
        super().__init__(*args, **kwargs)

        self.authenticated_user_ids = set(authenticated_user_ids)

        self.password = random_string(100)
        self.password_authentication = password_authentication
        self.add_to_authenticated_users = add_to_authenticated_users

        if self.password_authentication:
            LOGGER.critical("="*30)
            LOGGER.critical(f"PASSWORD: {self.password}")
            LOGGER.critical("="*30)

    def new_password(self, userid):
        password = self.password = random_string(alphabet=string.digits)
    
        LOGGER.critical("="*30)
        LOGGER.critical(f"PASSWORD for {userid}: {password}")
        LOGGER.critical("="*30)
        return password

    def get_password(self, userid):
        return self.password

    async def _start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """ Conversation's entry point """
        
        userid = get_userid(update)

        if userid in self.authenticated_user_ids:
            LOGGER.info(repr_action(update, 'authenticated'))
            return await self._process_main_menu_choice(update, context)

        msg = repr_action(update, 'not authenticated')
        LOGGER.info(msg)
        await reply(update, msg)

        if not self.password_authentication:
            return ConversationHandler.END

        return self.prefix_menu('_authenticate')

    async def _authenticate(self, update, context):
        ''' Check if the password was correct '''

        text = get_text(update)

        userid = get_userid(update)
        password = self.get_password(userid)
        success = text == password

        msg = repr_action(update, f"authentication attempt {success}: '{text}'")
        LOGGER.info(msg)

        if not success:
            return ConversationHandler.END
        
        if self.add_to_authenticated_users:
            self.authenticated_user_ids.add(userid)

        return await self._main_menu(update, context)


######################################################################
# Menu with torrent selection handlers
######################################################################


def iter_torrent_reprs(status=False):
    if status:
        cb = transmission_utils.torrent_status_repr
    else:
        cb = transmission_utils.torrent_repr
    return map(cb, transmission_utils.iter_torrents())

def iter_torrent_files(torrent_id):
    # Torrent files sorted by file name
    return sorted(transmission_utils.iter_torrent_files(torrent_id), key=lambda tf: tf.name)


class TorrentMenu(Menu):

    ###############
    # Prompt helpers
    @classmethod
    async def prompt_magnet(menu, update: Update):
        await reply(update, "Enter magnet url (or type 'cancel'):", reply_markup=REMOVE_MARKUP)

    @classmethod
    async def prompt_list(menu, update, text, values, prepend_layout=[["Cancel"]],
                          stringify_value = lambda i, value: f'{i}: {value}'):
        keyboard = prepend_layout + [[stringify_value(i, value)] for i, value in enumerate(values)]
        markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True, selective=True)        
        await reply(update, text, reply_markup=markup)

    @classmethod
    async def prompt_torrent(menu, update: Update, prepend_layout=[["Cancel"]]):
        return await menu.prompt_list(update,
                                      'Choose torrent:',
                                      iter_torrent_reprs(),
                                      prepend_layout=prepend_layout,
                                      stringify_value=lambda i,value: str(value))
    @classmethod
    async def prompt_torrent_files(menu, update, torrent_id, prepend_layout=[["Cancel"]]):
        return await menu.prompt_list(update,
                                      'Choose file:',
                                      iter_torrent_files(torrent_id),
                                      prepend_layout=prepend_layout,
                                      stringify_value=lambda i,value: str(value))

    ###############
    # Choice helpers 
    @classmethod
    def choice_to_number(menu, choice):
        regex_selection = r'^(\d+):.*'
        match = re.match(regex_selection, choice)
        
        if not match:
            return

        selection = int(match.group(1))
        return selection
        
    @classmethod
    def choice_to_torrent_id(menu, choice):
        if choice not in list(iter_torrent_reprs()):
            return
        return menu.choice_to_number(choice)

    @classmethod
    def choice_to_torrent_file_id(menu, choice):
        regex_torrent_id = r'^(\d+).(\d+):.*'
        match = re.match(regex_torrent_id, choice)
        
        if not match:
            return (None, None)

        try:
            torrent_id = int(match.group(1))
            file_id = int(match.group(2))

        except:
            return (None, None)

        return (torrent_id, file_id)

    @classmethod
    def choice_to_torrent_file(menu, choice):
        tf = menu.choice_to_torrent_file_id(choice)

        if tf is None:
            return None

        torrent_id, file_id = tf

        for other in transmission_utils.iter_torrent_files(torrent_id):
            if other.file_id == file_id:
                return other

    ###############
    # Handler creation

    '''
    Handlers are in charge of prompting for input/selection
    and passing the data onto a callback to handle the request.

    # TODO: init_state() before wrapper() so prompt doesnt always fail first time (currently a feature)
    '''

    def create_magnet_handler(menu, state_name, callback, on_complete=UNINITIALIZED):
        ''' Prompt for magnet URL and call callback() with the URL 
        !! WARNING: on_complete must be a callback of this menu since everything here prefix_menu=True !!
        '''

        if isinstance(on_complete, UninitializedClass):
            on_complete = menu._main_menu

        @log_on_call(f'entered {state_name}', f'exited {state_name}')
        @menu.callback(state_name=state_name, menu_on_exit=False)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            text = get_text(update)

            if not text.lower().startswith("magnet:"):
                await menu.prompt_magnet(update)
                return state_name
                
            try:
                magnet_name = urllib.parse.parse_qs(urllib.parse.urlsplit(text).query)['dn'][0]
            except:
                magnet_name = text[:30] + " ..."
            
            label = f"{state_name}('{magnet_name}')"

            msg = repr_action(update, label)
            LOGGER.info(msg)
            
            ret = await call_callback(callback, update, text)
            await multi_reply(update, label, ret)

            if on_complete is not None:
                return await call_callback(on_complete, update, context)
        
        return wrapper
        
    def create_torrent_handler(menu, state_name, callback, on_complete=UNINITIALIZED):
        ''' Prompt for torrent ID and call callback() with ID 
        !! WARNING: on_complete must be a callback of this menu since everything here prefix_menu=True !!
        '''

        if isinstance(on_complete, UninitializedClass):
            on_complete = menu._main_menu

        @log_on_call(f'entered {state_name} torrent selection', f'exited {state_name} torrent selection')
        @menu.callback(state_name=state_name)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):

            choice = get_text(update)
            torrent_id = menu.choice_to_torrent_id(choice)

            if torrent_id is None:
                await menu.prompt_torrent(update)
                return state_name    

            label = f'{state_name}({torrent_id})'
            
            msg = repr_action(update, label)
            LOGGER.info(msg)

            ret = await call_callback(callback, update, torrent_id)
            await multi_reply(update, label, ret)

            if on_complete is not None:
                return await call_callback(on_complete, update, context)

        return wrapper

    def create_torrent_file_handler(menu, state_name, callback, on_complete=UNINITIALIZED):
        ''' Prompt for torrent ID, then prompt for torrent file ID and call callback() with torrent file object 
        !! WARNING: on_complete must be a callback of this menu since everything here prefix_menu=True !!
        '''

        if isinstance(on_complete, UninitializedClass):
            on_complete = menu._main_menu

        prompt_state = state_name

        process_state_name = f'_{state_name}_torrent_file_choice_handler'
        process_state = process_state_name

        @log_on_call(f'entered {state_name} torrent selection', f'exited {state_name} torrent selection')
        @menu.callback(state_name=state_name)
        async def _prompt_torrent_files(update, context):
            choice = get_text(update)

            torrent_id = menu.choice_to_torrent_id(choice)

            if torrent_id is None:
                await menu.prompt_torrent(update)
                return prompt_state

            await menu.prompt_torrent_files(update, torrent_id)
            return process_state

        @log_on_call(f'entered {state_name} file selection', f'exited {state_name} file selection')
        @menu.callback(state_name=process_state_name)
        async def _process_torrent_file_choice(update, context):
            choice = get_text(update)

            torrent_file = menu.choice_to_torrent_file(choice)
            if torrent_file is None:
                await reply(update, 'Error choosing torrent file')
                return await menu._main_menu(update, context)

            label = f"{state_name}({torrent_file.torrent_id}.{torrent_file.file_id})"

            msg = repr_action(update, label)
            LOGGER.info(msg)

            ret = await call_callback(callback, update, torrent_file)
            await multi_reply(update, label, ret)

            if on_complete is not None:
                return await call_callback(on_complete, update, context)
        
        return _prompt_torrent_files, _process_torrent_file_choice


class AuthenticatedTorrentMenu(AuthenticatedMenu, TorrentMenu):
    pass

