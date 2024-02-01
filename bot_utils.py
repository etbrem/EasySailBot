import re
import sys
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
import logging

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logging.getLogger('httpx').setLevel(logging.WARN)

LOGGER = logging.getLogger(__name__)

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


######################################################################
# Telegram bot utils
######################################################################

REMOVE_MARKUP = ReplyKeyboardRemove()

def flatten_layout(layout):
    return [x for row in layout for x in row]

def map_layout(callback, layout):
    return [[callback(x) for x in row] for row in layout]

def get_userid(update):
    return update.message.from_user.id

def repr_action(update, text):
    user_id = get_userid(update)
    msg = f'UserID {user_id} {text}'
    return msg

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

def is_cancel(update):
    try:
        return update.message.text.strip().lower() == 'cancel'
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
    INTERNAL_STATES = ['_start', '_main_menu',
                    '_process_main_menu_choice', '_cancel']

    def transform_cmd_name(self, cmd_name):
        return to_camel_case(cmd_name)

    def __init__(self, name=None, layout=None, states=None, callbacks=None):
        self.name = f'Menu_{name or random_string(8)}'
        self.states = states or set()
        self.callbacks = callbacks or {}
        self.text_to_states = {}

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

    async def _main_menu(self, update: Update, context=None):
        ''' Send the commands menu then process the choice '''
        await reply(update, "Enter command:", reply_markup=self.create_markup())
        return self.prefix_menu('_process_main_menu_choice')

    async def _process_main_menu_choice(self, update: Update, context):
        """ Process chosen command """
        # TODO: Namespace -> conversationMenu -> menu with mapped text + callbacks
        choice = self.get_text_mappings().get(update.message.text, None)

        msg = repr_action(update, f'chose {choice}')
        LOGGER.info(msg)

        if choice is None:
            return await self._main_menu(update)

        else:
            callback = self.get_callbacks().get(choice, None)

            if callback is not None:
                return await callback(update, context)

            return await self._main_menu(update)

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

        text = update.message.text

        userid = get_userid(update)
        password = self.get_password(userid)
        success = text == password

        msg = repr_action(update, f"authentication attempt {success}: '{text}'")
        LOGGER.info(msg)

        if not success:
            self.new_password(userid)
            return ConversationHandler.END
        
        if self.add_to_authenticated_users:
            self.authenticated_user_ids.add(userid)

        return await self._main_menu(update)


class TorrentMenu(Menu):

    @staticmethod
    def iter_torrent_reprs(status=False):
        if status:
            cb = transmission_ctl.torrent_status_repr
        else:
            cb = transmission_ctl.torrent_repr
        return map(cb, transmission_ctl.iter_torrents())

    @staticmethod
    def iter_torrent_files(torrent_id):
        # Torrent files sorted by file name
        return sorted(transmission_ctl.iter_torrent_files(torrent_id), key=lambda tf: tf.name)

    ###############
    # Prompt helpers
    @classmethod
    async def prompt_magnet(menu, update: Update):
        await reply(update, "Enter magnet url (or type 'cancel'):", reply_markup=REMOVE_MARKUP)

    @classmethod
    async def prompt_torrent(menu, update: Update):
        keyboard = [['Cancel']] + [[t] for t in menu.iter_torrent_reprs()]

        markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True, selective=True)        
        await reply(update, "Choose torrent:", reply_markup=markup)

    @classmethod
    async def prompt_torrent_files(menu, update, torrent_id):
        keyboard = [["Cancel"]] + [[str(tf)] for tf in menu.iter_torrent_files(torrent_id)]

        markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True, selective=True)        
        await reply(update, "Choose file:", reply_markup=markup)

    ###############
    # Choice helpers 
    @classmethod
    def choice_to_torrent_id(menu, choice):
        if choice not in list(menu.iter_torrent_reprs()):
            return

        regex_torrent_id = r'^(\d+):.*'
        match = re.match(regex_torrent_id, choice)
        
        if not match:
            return

        torrent_id = int(match.group(1))
        return torrent_id
        
    @classmethod
    def choice_to_torrent_file_id(menu, choice):
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

    @classmethod
    def choice_to_torrent_file(menu, choice):
        tf = menu.choice_to_torrent_file_id(choice)

        if tf is None:
            return None

        torrent_id, file_id = tf

        for other in transmission_ctl.iter_torrent_files(torrent_id):
            if other.file_id == file_id:
                return other

    ###############
    # Handler creation

    '''
    Handlers are in charge of prompting for input/selection
    and passing the data onto a callback to handle the request.

    # TODO: init_state() before wrapper() so prompt doesnt always fail first time (currently a feature)
    '''


    def create_magnet_handler(menu, state_name, callback, on_exit_lambda=lambda menu: menu._main_menu):
        ''' Prompt for magnet URL and call callback() with the URL '''

        @log_on_call(f'entered {state_name}', f'exited {state_name}')
        @menu.callback(state_name=state_name, menu_on_exit=False)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            text = update.message.text

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

            ret = callback(text)
            await multi_reply(update, label, ret)

            on_exit = on_exit_lambda(menu)
            return await on_exit(update, context)
        
        return wrapper
        
    def create_torrent_handler(menu, state_name, callback, on_exit_lambda=lambda menu: menu._main_menu):
        ''' Prompt for torrent ID and call callback() with ID '''


        @log_on_call(f'entered {state_name} torrent selection', f'exited {state_name} torrent selection')
        @menu.callback(state_name=state_name)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):

            choice = update.message.text
            torrent_id = menu.choice_to_torrent_id(choice)

            if torrent_id is None:
                await menu.prompt_torrent(update)
                return state_name    

            label = f'{state_name}({torrent_id})'
            
            msg = repr_action(update, label)
            LOGGER.info(msg)

            ret = callback(torrent_id)
            await multi_reply(update, label, ret)

            on_exit = on_exit_lambda(menu)
            return await on_exit(update, context)

        return wrapper

    def create_torrent_file_handler(menu, state_name, callback, on_exit_lambda=lambda menu: menu._main_menu):
        ''' Prompt for torrent ID, then prompt for torrent file ID and call callback() with torrent file object '''

        prompt_state = state_name

        process_state_name = f'_{state_name}_torrent_file_choice_handler'
        process_state = process_state_name

        @log_on_call(f'entered {state_name} torrent selection', f'exited {state_name} torrent selection')
        @menu.callback(state_name=state_name)
        async def _prompt_torrent_files(update, context):
            choice = update.message.text

            torrent_id = menu.choice_to_torrent_id(choice)

            if torrent_id is None:
                await menu.prompt_torrent(update)
                return prompt_state

            await menu.prompt_torrent_files(update, torrent_id)
            return process_state

        @log_on_call(f'entered {state_name} file selection', f'exited {state_name} file selection')
        @menu.callback(state_name=process_state_name)
        async def _process_torrent_file_choice(update, context):
            choice = update.message.text

            tf = menu.choice_to_torrent_file(choice)
            if tf is None:
                await reply(update, 'Error choosing torrent file')
                return await menu._main_menu(update)

            label = f"{state_name}({tf.torrent_id}.{tf.file_id})"

            msg = repr_action(update, label)
            LOGGER.info(msg)

            ret = callback(tf)
            await multi_reply(update, label, ret)

            on_exit = on_exit_lambda(menu)
            return await on_exit(update, context)
        
        return _prompt_torrent_files, _process_torrent_file_choice


class AuthenticatedTorrentMenu(AuthenticatedMenu, TorrentMenu):
    pass


def menus_to_states(*menus):
    states = {}
    for menu in menus:
        states.update(menu.create_message_handlers())
    return states
