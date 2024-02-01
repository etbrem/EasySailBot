#!/usr/bin/python3

from bot_utils import *

######################################################################
# Menus
######################################################################

MAIN_MENU = AuthenticatedTorrentMenu(
    name='main', 
    add_to_authenticated_users=False,
    authenticated_user_ids=config.AUTHENTICATED_USER_IDS,
    layout=[
    ['add_tv_show', 'add_movie'], 
    ['start_torrent','stop_torrent', 'delete_torrent'],
    ['list_torrents', 'list_torrent_files'],
    ['disable_all_torrent_files', 'toggle_all_torrent_files', 'toggle_torrent_file'],
    ['test_menu', 'exit']
])

TEST_MENU = TorrentMenu(
    name='secret',
    layout=[
        ['admin_panel', 'get_my_ID'],
        ['not_implemented', 'storage_stats'],
        ['this_is_a_test', 'echo_test'],
        ['back_to_main']
])

ADMIN_MENU = AuthenticatedMenu(
    name='admin',
    password_authentication=False,
    authenticated_user_ids=getattr(config, 'ADMIN_USER_IDS', []),
    layout=[
    ['get_password', 'set_password'],
    ['list_admins', 'add_admin'],
    ['list_authenticated_users', 'add_authenticated_user'],
    ['back_to_main']
])

######################################################################
# Commands implementation
######################################################################

######################################################################
# Main menu

# 
# Basic command handlers
@log_on_call()
@MAIN_MENU.callback(menu_on_exit=True)
async def list_torrents(update, context):
    for torrent_repr in MAIN_MENU.iter_torrent_reprs(status=True):
        await reply(update, torrent_repr)

@MAIN_MENU.callback(state_name='exit')
async def exit_main_menu(update, context):
    await reply(update, 'Bye!', reply_markup=REMOVE_MARKUP)
    return ConversationHandler.END

#
# Magnet command handlers
MAIN_MENU.create_magnet_handler('add_tv_show', lambda magnet: transmission_ctl.add_magnet(magnet, config.DIR_TV_SHOWS))
MAIN_MENU.create_magnet_handler('add_movie', lambda magnet: transmission_ctl.add_magnet(magnet, config.DIR_MOVIES))

# 
# Torrent command handlers
MAIN_MENU.create_torrent_handler('start_torrent', transmission_ctl.start_torrent)
MAIN_MENU.create_torrent_handler('stop_torrent', transmission_ctl.stop_torrent)
MAIN_MENU.create_torrent_handler('delete_torrent', transmission_ctl.delete_torrent)

MAIN_MENU.create_torrent_handler('list_torrent_files',

    # Map sorted torrent files to their representation
    lambda torrent_id: map(
        repr,
        sorted(transmission_ctl.iter_torrent_files(torrent_id), key=lambda tf: tf.name)
        )
)
MAIN_MENU.create_torrent_handler('disable_all_torrent_files',
    lambda torrent_id: transmission_ctl.update_torrent_files( torrent_id, update_cb = lambda tf: {'selected': False} )
)
MAIN_MENU.create_torrent_handler('toggle_all_torrent_files',
    lambda torrent_id: transmission_ctl.update_torrent_files( torrent_id, update_cb = lambda tf: {'selected': not tf.selected} )
)


# 
# Torrent file command handlers
MAIN_MENU.create_torrent_file_handler('toggle_torrent_file',

    lambda torrent_file: transmission_ctl.update_torrent_files( 
                            torrent_file.torrent_id, 
                            update_cb = lambda tf: {'selected': not tf.selected},
                            filter_cb = lambda tf: tf.file_id == torrent_file.file_id
                        )
)

@MAIN_MENU.callback(prefix_menu=False)
async def test_menu(update, context):
    return await TEST_MENU._start(update, context)



######################################################################
# Test menu

@TEST_MENU.callback(prefix_menu=False)
async def admin_panel(update, context):
    return await ADMIN_MENU._start(update, context)

@TEST_MENU.callback(menu_on_exit=True)
async def get_my_ID(update, context):
    await reply(update, 'Your UserID is:')
    await reply(update, f'{get_userid(update)}')

@log_on_call()
@TEST_MENU.callback(menu_on_exit=True)
async def storage_stats(update, context):
    await reply(update, execute_shell("df -h | head -n 1; df -h | grep /plex/media"))

@TEST_MENU.callback()
async def echo_test(update, context):
    await reply(update, f'Enter text:', reply_markup=REMOVE_MARKUP)
    return '_echo_test_handler'

@TEST_MENU.callback(menu_on_exit=True)
async def _echo_test_handler(update, context):
    await reply(update, update.message.text)

TEST_MENU.create_torrent_handler('this_is_a_test', 
    lambda torrent_id: f'You selected torrent {str(torrent_id)}',
    on_exit_lambda=lambda menu: menu.get_callbacks().get('after_torrent_selection') or menu._main_menu
    )
@TEST_MENU.callback()
async def after_torrent_selection(update, context):
    await reply(update, f'Enter something to reverse:', reply_markup=REMOVE_MARKUP)
    return '_reverse_echo_test_handler'

@TEST_MENU.callback(menu_on_exit=True)
async def _reverse_echo_test_handler(update, context):
    text = update.message.text
    await reply(update, text[::-1])

@TEST_MENU.callback(prefix_menu=False)
async def back_to_main(update, context):
    return await MAIN_MENU._main_menu(update)


######################################################################
# Admin menu

@ADMIN_MENU.callback(state_name='back_to_main', prefix_menu=False)
async def back_to_main_from_admin(update, context):
    return await MAIN_MENU._main_menu(update)

@ADMIN_MENU.callback(menu_on_exit=True)
async def get_password(update, callback):
    await reply(update, MAIN_MENU.password)

@ADMIN_MENU.callback()
async def set_password(update, callback):
    await reply(update, 'Enter new password:', reply_markup=REMOVE_MARKUP)
    return '_process_set_password'

@ADMIN_MENU.callback(menu_on_exit=True)
async def _process_set_password(update, context):
    text = update.message.text
    MAIN_MENU.password = text
    await reply(update, 'Password set')

@ADMIN_MENU.callback(menu_on_exit=True)
async def list_admins(update, context):
    await multi_reply(update, 'Admins', ADMIN_MENU.authenticated_user_ids, with_index=True)

@ADMIN_MENU.callback(menu_on_exit=True)
async def list_authenticated_users(update, context):
    await multi_reply(update, 'Authenticated users', MAIN_MENU.authenticated_user_ids, with_index=True)

@ADMIN_MENU.callback()
async def add_admin(update, context):
    await reply(update, 'Enter user id:', reply_markup=REMOVE_MARKUP)
    return '_process_add_admin'

@ADMIN_MENU.callback(menu_on_exit=True)
async def _process_add_admin(update, context):
    text = update.message.text

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
    text = update.message.text

    try:
        new_user_id = int(text)
    except:
        return await reply(update, f'Error casting to int: {text}')

    try:
        MAIN_MENU.authenticated_user_ids.add(new_user_id)
    except:
        return await reply(update, f'Error adding to authenticated_user_ids: {text}')

    await multi_reply(update, 'Authenticated users', MAIN_MENU.authenticated_user_ids, with_index=True)


if __name__ == '__main__':
    application = Application.builder().token(config.API_TOKEN).build()

    states = menus_to_states(MAIN_MENU, TEST_MENU, ADMIN_MENU)

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('.*'), MAIN_MENU._start)],

        states=states,

        fallbacks=[CommandHandler("cancel", MAIN_MENU._cancel)],
    )  

    application.add_handler(conv_handler)
    application.run_polling(allowed_updates=Update.ALL_TYPES)
