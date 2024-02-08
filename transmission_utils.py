import os
import math

import transmissionrpc  # python3 -m pip install transmissionrpc

import config


######################################################################
# Misc
######################################################################

def repr_size(size_bytes, tag=True):
   if size_bytes == 0:
       return "0B"
   
   size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
   i = int(math.floor(math.log(size_bytes, 1024)))
   p = math.pow(1024, i)
   s = round(size_bytes / p, 2)

   if tag:
        return "%s %s" % (s, size_name[i])
   else:
        return s


######################################################################
# Transmission RPC
######################################################################

TRANSMISSION_RPC_OBJECT = None

# Until transmissionrpc is updated to support Transmission 4.1.0 (not yet available for download), add new features ourselves 
# Once Transmission 4.1.0 will be installed the code will add torrents with sequential downloading enabled
added_torrent_fields = {

    # Added in Transmission 4.1.0 (rpc-version: 18), can help with streaming while downloading
    # https://github.com/transmission/transmission/blob/main/docs/rpc-spec.md
    'sequentialDownload': ('boolean', 18, None, None, None, 'Download torrent pieces sequentially (first parts first).')
}

transmissionrpc.constants.TORRENT_ARGS['get'].update(added_torrent_fields)
transmissionrpc.constants.TORRENT_ARGS['set'].update(added_torrent_fields)


def create_transmission_rpc():
    return transmissionrpc.Client(getattr(config, 'TRANSMISSION_IP', '127.0.0.1'))

def get_transmission_rpc():
    global TRANSMISSION_RPC_OBJECT

    if TRANSMISSION_RPC_OBJECT is None:
        TRANSMISSION_RPC_OBJECT = create_transmission_rpc()

    return TRANSMISSION_RPC_OBJECT

def add_magnet(magnet, download_dir):
    print("add_magnet", download_dir, magnet)

    tc = get_transmission_rpc()

    torrent = tc.add_torrent(magnet, download_dir=download_dir)

    if 'sequentialDownload' in torrent._fields:
        torrent._fields['sequentialDownload'] = transmissionrpc.utils.Field(True, True)
        torrent._push()

    return torrent_repr(torrent)


######################################################################
# Torrent
######################################################################

def iter_torrents():
    return get_transmission_rpc().get_torrents()

def get_torrent_size(torrent):
    torrent = make_torrent(torrent)
    return sum(f['size'] for f in torrent.files().values() if f['selected'])

def get_torrent_completed(torrent):
    torrent = make_torrent(torrent)
    return sum(f['completed'] for f in torrent.files().values() if f['selected'])

def torrent_repr(torrent):
    if not torrent:
        return ''

    return '{id}: {name}'.format(id=torrent.id, name=torrent.name)
    
def torrent_status_repr(torrent):
    torrent = make_torrent(torrent)

    torrent_id = torrent.id
    torrent_name = torrent.name
    torrent_status = torrent.status

    torrent_completed = get_torrent_completed(torrent)
    torrent_size = get_torrent_size(torrent)

    torrent_completed_str = repr_size(torrent_completed)
    torrent_size_str = repr_size(torrent_size)

    if torrent_size:
        torrent_percent = int(100.0 * torrent_completed / torrent_size )
    else:
        torrent_percent = '??'

    return f'{torrent_id}: {torrent_status} {torrent_percent}% {torrent_completed_str}/{torrent_size_str}\n{torrent_name}'.format(**locals())

def get_torrent(torrent_id):
    tc = get_transmission_rpc()

    return tc.get_torrent(int(torrent_id))

def make_torrent(torrent_or_id):
    if isinstance(torrent_or_id, (int, str)):
        return get_torrent(torrent_or_id)
    return torrent_or_id

def start_torrent(torrent):
    torrent = make_torrent(torrent)
    torrent.start()
    
    torrent = make_torrent(torrent.id)
    return torrent.status != 'stopped'

def stop_torrent(torrent):
    torrent = make_torrent(torrent)
    torrent.stop()

    torrent = make_torrent(torrent.id)
    return torrent.status == 'stopped'

def delete_torrent(torrent):
    torrent = make_torrent(torrent)
    torrent_id = torrent.id
    del torrent

    tc = get_transmission_rpc()
    return tc.remove_torrent(torrent_id, delete_data=True)


######################################################################
# Torrent File
######################################################################

class TorrentFile(object):
    PROPERTY_NAMES = ['selected', 'priority', 'size', 'name', 'completed']

    def __init__(self, torrent_id, file_id, properties):
        self.torrent_id = torrent_id
        self.file_id = file_id
        self._properties = properties
        
        for property_name in self.PROPERTY_NAMES:
            setattr(self, property_name, properties.get(property_name))

        if self.size:
            self.percent = int(100 * float(self.completed) / self.size)
        else:
            self.percent = 100

    def __str__(self):
        return f'{self.torrent_id}.{self.file_id}: {self.name}'

    def __repr__(self):
        selected_status = ' DISABLED' if not self.selected else ''
        return str(self) + f'\n{self.percent}% {repr_size(self.size)}{selected_status}'

def iter_torrent_files(torrent):
    torrent = make_torrent(torrent)
    
    if torrent:
        for file_id, properties in torrent.files().items():
            yield TorrentFile(torrent.id, file_id, properties)

def get_torrent_file(torrent_id, file_id):
    for tf in iter_torrent_files(torrent_id):
        if tf.file_id == file_id:
            return tf

def update_torrent_files(torrent, 
                        filter_cb = lambda torrent_file: True,
                        update_cb = lambda torrent_file: {'selected': not torrent_file.selected}
                        ):
    torrent = make_torrent(torrent)

    file_updates = {}
    for torrent_file in iter_torrent_files(torrent):

        if filter_cb(torrent_file):
            file_updates[torrent_file.file_id] = update_cb(torrent_file)

    get_transmission_rpc().set_files({torrent.id: file_updates})
    return file_updates

def torrent_file_to_path(tf):
    torrent = get_torrent(tf.torrent_id)
    
    download_dir = torrent._fields.get('downloadDir', None)
    if download_dir is None:
        return
    value = download_dir.value
    if value is None:
        return

    return os.path.join(value, tf.name)
