#!/usr/bin/python3

from bot_utils import *
from stream_utils import *


SERVER = HTTPTorrentServer()
FILE_CONVERTER = FileConverter(ffmpeg_path=getattr(config, 'FFMPEG_PATH', 'ffmpeg'))


######################################################################
# Menu objects
######################################################################

MAIN_MENU = AuthenticatedTorrentMenu(
    name='main', 
    add_to_authenticated_users=False,
    authenticated_user_ids=config.AUTHENTICATED_USER_IDS,
    layout=[
    ['add_tv_show', 'add_movie'], 
    ['start_torrent','stop_torrent', 'delete_torrent'],
    ['list_torrents', 'list_torrent_files'],
    ['disable_all_torrent_files', 'toggle_torrent_files'],
    ['more', 'exit']
])

SECOND_MENU = TorrentMenu(
    name='second',
    layout=[
        ['convert_videos', 'cast_videos'],
        ['get_my_ID', 'storage_stats'],
        ['admin_menu', 'back']
])

CAST_MENU = TorrentMenu(
    name='cast',
    layout=[
    ['UPNP_discover', 'control_UPNP_device'],
    ['start_file_server', 'stop_file_server', 'status_file_server'],
    ['back']
])

ADMIN_MENU = AuthenticatedMenu(
    name='admin',
    password_authentication=False,
    authenticated_user_ids=getattr(config, 'ADMIN_USER_IDS', []),
    layout=[
    ['get_password', 'set_password'],
    ['list_admins', 'add_admin'],
    ['list_authenticated_users', 'add_authenticated_user'],
    ['back']
])

CONVERTION_MENU = FileConvertionMenu(FILE_CONVERTER, 
                                     name='fileconvert',
                                     on_complete=SECOND_MENU._main_menu)

STATES = {}


######################################################################
# Main menu callbacks
######################################################################

##############################
# Basic command handlers
@MAIN_MENU.callback(menu_on_exit=True)
async def list_torrents(update, context):
    for torrent_repr in iter_torrent_reprs(status=True):
        await reply(update, torrent_repr)

##############################
# Enter different menus
@MAIN_MENU.callback(state_name='more', prefix_menu=False)
async def enter_second_menu(update, context):
    return await SECOND_MENU._start(update, context)

@MAIN_MENU.callback(state_name='exit')
async def exit_main_menu(update, context):
    await reply(update, 'Bye!', reply_markup=REMOVE_MARKUP)
    return ConversationHandler.END

##############################
# Magnet command handlers
MAIN_MENU.create_magnet_handler('add_tv_show', lambda update, magnet: transmission_utils.add_magnet(magnet, config.DIR_TV_SHOWS))
MAIN_MENU.create_magnet_handler('add_movie', lambda update, magnet: transmission_utils.add_magnet(magnet, config.DIR_MOVIES))

##############################
# Torrent command handlers
MAIN_MENU.create_torrent_handler('start_torrent', lambda update, torrent_id: transmission_utils.start_torrent(torrent_id))
MAIN_MENU.create_torrent_handler('stop_torrent', lambda update, torrent_id: transmission_utils.stop_torrent(torrent_id))
MAIN_MENU.create_torrent_handler('delete_torrent', lambda update, torrent_id: transmission_utils.delete_torrent(torrent_id))

MAIN_MENU.create_torrent_handler('list_torrent_files',

    # Map torrent files to their representation
    lambda update, torrent_id: map(
        repr,
        iter_torrent_files(torrent_id)
        )
)
MAIN_MENU.create_torrent_handler('disable_all_torrent_files',
    lambda update, torrent_id: transmission_utils.update_torrent_files( torrent_id, update_cb = lambda tf: {'selected': False} )
)

MAIN_MENU.create_torrent_handler('toggle_torrent_files',

    # Save chosen torrent and create new list to store chosen files in the userdata
    lambda update, torrent_id: MAIN_MENU.get_userdata(update).update(
        {
            'toggle_torrent_files_chosen_torrent': torrent_id,
            'toggle_torrent_files_chosen_files': set()
        }
    ),
    on_complete=lambda update, context: MAIN_MENU.get_callback('toggle_torrent_files_prompt_files')(update, context)
)

@MAIN_MENU.callback()
async def toggle_torrent_files_prompt_files(update, context):
    torrent_id = MAIN_MENU.get_userdata(update).get('toggle_torrent_files_chosen_torrent')

    if torrent_id is None:
        return await MAIN_MENU.get_callback('toggle_torrent_files')(update, context)

    await MAIN_MENU.prompt_torrent_files(update, torrent_id, prepend_layout=[["Cancel", "Done"]])
    return 'toggle_torrent_files_choose_files'

@MAIN_MENU.callback()
async def toggle_torrent_files_choose_files(update, context):
    text = get_text(update)

    userid = get_userid(update)
    userdata = MAIN_MENU.get_userdata(userid)

    torrent_id = userdata.get('toggle_torrent_files_chosen_torrent')
    torrent_files = userdata.get('toggle_torrent_files_chosen_files')
    
    if text.strip().lower() == 'done':
        ret = transmission_utils.update_torrent_files(torrent_id,
            filter_cb=lambda tf: tf.file_id in torrent_files,
            update_cb=lambda tf: {'selected': not tf.selected}
        )

        MAIN_MENU.del_userdata_entries(userid,
                                       'toggle_torrent_files_chosen_torrent',
                                       'toggle_torrent_files_chosen_files')

        await multi_reply(update, 'Updated torrent files', ret)
        return await MAIN_MENU._main_menu(update, context)
    
    torrent_id2, file_id = MAIN_MENU.choice_to_torrent_file_id(text)

    if file_id is not None and torrent_id2 == torrent_id:
        torrent_files.add(file_id)

    return await toggle_torrent_files_prompt_files(update, context)


######################################################################
# "More" menu callbacks
######################################################################

##############################
# Basic commands
@SECOND_MENU.callback(menu_on_exit=True)
async def get_my_ID(update, context):
    await reply(update, 'Your UserID is:')
    await reply(update, f'{get_userid(update)}')

@SECOND_MENU.callback(menu_on_exit=True)
async def storage_stats(update, context):
    for path in [config.DIR_MOVIES, config.DIR_TV_SHOWS]:
        free_size = get_free_size(path)
        used_size = get_used_size(path)
        total_size = used_size + free_size

        total_size_str = transmission_utils.repr_size(total_size)
        used_size_str = transmission_utils.repr_size(used_size)
        free_size_str = transmission_utils.repr_size(free_size)

        msg = f'Path: {path}\n'
        msg += f'Total: {total_size_str} \n'
        msg += f'Used: {used_size_str} {int(100*used_size/total_size)}%\n'
        msg += f'Available: {free_size_str} {int(100*free_size/total_size)}%'
        await reply(update, msg)

##############################
# Enter different menus
@SECOND_MENU.callback(prefix_menu=False)
async def admin_menu(update, context):
    return await ADMIN_MENU._start(update, context)

@SECOND_MENU.callback(prefix_menu=False)
async def convert_videos(update, context):
    return await CONVERTION_MENU._start(update, context)

@SECOND_MENU.callback(prefix_menu=False)
async def cast_videos(update, context):
    return await CAST_MENU._start(update, context)

@SECOND_MENU.callback(state_name='back', prefix_menu=False)
async def back_to_main_from_second(update, context):
    return await MAIN_MENU._main_menu(update, context)


######################################################################
# Admin menu callbacks
######################################################################

##############################
# Enter different menus
@ADMIN_MENU.callback(state_name='back', prefix_menu=False)
async def back_to_second_from_admin(update, context):
    return await SECOND_MENU._main_menu(update, context)

##############################
# Basic command handlers
@ADMIN_MENU.callback(menu_on_exit=True)
async def list_admins(update, context):
    await multi_reply(update, 'Admins', ADMIN_MENU.authenticated_user_ids, with_index=True)

@ADMIN_MENU.callback(menu_on_exit=True)
async def list_authenticated_users(update, context):
    await multi_reply(update, 'Authenticated users', MAIN_MENU.authenticated_user_ids, with_index=True)

@ADMIN_MENU.callback(menu_on_exit=True)
async def get_password(update, callback):
    await reply(update, MAIN_MENU.password)

@ADMIN_MENU.callback()
async def set_password(update, callback):
    await reply(update, 'Enter new password:', reply_markup=REMOVE_MARKUP)
    return '_process_set_password'

@ADMIN_MENU.callback(menu_on_exit=True)
async def _process_set_password(update, context):
    text = get_text(update)
    MAIN_MENU.password = text
    await reply(update, 'Password set')

@ADMIN_MENU.callback()
async def add_admin(update, context):
    await reply(update, 'Enter user id:', reply_markup=REMOVE_MARKUP)
    return '_process_add_admin'

@ADMIN_MENU.callback(menu_on_exit=True)
async def _process_add_admin(update, context):
    text = get_text(update)

    try:
        new_user_id = int(text)
    except:
        return await reply(update, f'Error casting to int: {text}')

    try:
        ADMIN_MENU.authenticated_user_ids.add(new_user_id)
    except:
        return await reply(update, f'Error adding to authenticated_user_ids: {text}')

    await multi_reply(update, 'Admin users', ADMIN_MENU.authenticated_user_ids, with_index=True)

@ADMIN_MENU.callback()
async def add_authenticated_user(update, context):
    await reply(update, 'Enter user id:', reply_markup=REMOVE_MARKUP)
    return '_process_add_authenticated_user'

@ADMIN_MENU.callback(menu_on_exit=True)
async def _process_add_authenticated_user(update, context):
    text = get_text(update)

    try:
        new_user_id = int(text)
    except:
        return await reply(update, f'Error casting to int: {text}')

    try:
        MAIN_MENU.authenticated_user_ids.add(new_user_id)
    except:
        return await reply(update, f'Error adding to authenticated_user_ids: {text}')

    await multi_reply(update, 'Authenticated users', MAIN_MENU.authenticated_user_ids, with_index=True)



######################################################################
# Casting menu callbacks
######################################################################

##############################
# Enter different menus
@CAST_MENU.callback(state_name='back', prefix_menu=False)
async def back_to_second_from_cast(update, context):
    return await SECOND_MENU._start(update, context)

##############################
# UPnP commands
@CAST_MENU.callback(menu_on_exit=True)
async def UPNP_discover(update, context):
    await reply(update, 'Please wait..')
    devices = list(iter_UPNP_devices())
    await multi_reply(update, 'UPNP device', devices, with_index=True)  

@CAST_MENU.callback()
async def control_UPNP_device(update, context):
    userdata = CAST_MENU.get_userdata(update)
    await reply(update, 'Please wait..')
    devices = list(iter_UPNP_devices())
    userdata['UPNP_devices'] = devices
    await CAST_MENU.prompt_list(update, 'Select device:', devices)
    return '_control_UPNP_device_process_device_choice'

@CAST_MENU.callback(prefix_menu=False)
async def _control_UPNP_device_process_device_choice(update, context):
    userid = get_userid(update)
    userdata = CAST_MENU.get_userdata(userid)
    
    i = CAST_MENU.choice_to_number(get_text(update))
    devices = userdata.get("UPNP_devices", [])

    if i is not None and 0 <= i < len(devices):
        device=devices[i]
        upnp_cast_menu = UPNPTorrentCastMenu(SERVER, FILE_CONVERTER, device, name=f'upnpcast_{userid}', on_complete=_control_UPNP_device_exit_cast_menu)
        userdata['UPNPTorrentCastMenu'] = upnp_cast_menu
        STATES.update(upnp_cast_menu.create_message_handlers())
        
        return await _control_UPNP_device_enter_cast_menu(update, context)

    return await CAST_MENU._start(update, context)

@CAST_MENU.callback(prefix_menu=False)
async def _control_UPNP_device_enter_cast_menu_existing(update, context):
    upnp_cast_menu = CAST_MENU.get_userdata(update).get('UPNPTorrentCastMenu')

    if upnp_cast_menu:
        return await upnp_cast_menu._process_main_menu_choice(update, context)

    return await CAST_MENU._main_menu(update, context)

@CAST_MENU.callback(prefix_menu=False)
async def _control_UPNP_device_exit_cast_menu(update, context):
    userdata = CAST_MENU.get_userdata(update)
    upnp_cast_menu = userdata.get('UPNPTorrentCastMenu')

    if upnp_cast_menu:

        states = upnp_cast_menu.create_message_handlers()
        for state in states:
            del conv_handler.states[state]

        del upnp_cast_menu
        CAST_MENU.del_userdata_entries(update, 'UPNPTorrentCastMenu')
    
    return await CAST_MENU._start(update, context)

@CAST_MENU.callback(prefix_menu=False)
async def _control_UPNP_device_enter_cast_menu(update, context):
    upnp_cast_menu = CAST_MENU.get_userdata(update).get('UPNPTorrentCastMenu')

    if upnp_cast_menu is not None:
        return await upnp_cast_menu._start(update, context)

    return await CAST_MENU._start(update, context)

##############################
# File server commands

@CAST_MENU.callback(menu_on_exit=True)
async def start_file_server(update, context):
    if SERVER.started:
        await reply(update, 'Server already started')

    SERVER.start_threads()

    msg = repr_action(update, 'STARTED file server')
    LOGGER.info(msg)
    await reply(update, msg)

@CAST_MENU.callback(menu_on_exit=True)
async def stop_file_server(update, context):
    if not SERVER.started:
        await reply(update, 'Server not running')
    
    SERVER.stop_threads()

    msg = repr_action(update, 'STOPPED file server')
    LOGGER.info(msg)
    await reply(update, msg)

@CAST_MENU.callback(menu_on_exit=True)
async def status_file_server(update, context):
    status = 'ON' if SERVER.started else 'OFF'
    msg = repr_action(update, f'file server status {status} on {SERVER.server_address}')
    LOGGER.info(msg)
    await reply(update, msg)


######################################################################
# Bot creation
######################################################################

if __name__ == '__main__':
    application = Application.builder().token(config.API_TOKEN).build()

    STATES.update(menus_to_states(MAIN_MENU, SECOND_MENU, ADMIN_MENU, CAST_MENU, CONVERTION_MENU))

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('.*'), MAIN_MENU._start)],

        states=STATES,

        fallbacks=[CommandHandler("cancel", MAIN_MENU._cancel)],
    )  

    application.add_handler(conv_handler)
    application.run_polling(allowed_updates=Update.ALL_TYPES)
