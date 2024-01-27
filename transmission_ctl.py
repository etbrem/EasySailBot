import math

import transmissionrpc



def convert_size(size_bytes, tag=True):
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

TRANSMISSION_RPC_OBJECT = None



DIR_TV_SHOWS = '/plex/media/TV Shows'
DIR_MOVIES = '/plex/media/Movies'


def create_transmission_rpc():
    return transmissionrpc.Client('127.0.0.1')


def get_transmission_rpc():
    global TRANSMISSION_RPC_OBJECT

    if TRANSMISSION_RPC_OBJECT is None:
        TRANSMISSION_RPC_OBJECT = create_transmission_rpc()

    return TRANSMISSION_RPC_OBJECT


def add_torrent_to_dir(magnet, download_dir):
    print("add_torrent_to_dir", download_dir, magnet)

    tc = get_transmission_rpc()

    torrent = tc.add_torrent(magnet, download_dir=download_dir)
    return torrent_repr(torrent)


def add_tv_show(magnet):
    return add_torrent_to_dir(magnet, DIR_TV_SHOWS)


def add_movie(magnet):
    return add_torrent_to_dir(magnet, DIR_MOVIES)


def iter_torrents(translation=lambda t: t):
    for torrent in get_transmission_rpc().get_torrents():
        yield translation(torrent)

def get_torrent_size(torrent):
    return sum(f['size'] for f in torrent.files().values())


def torrent_repr(torrent):
    return '{id}: {name}'.format(id=torrent.id, name=torrent.name)
    
def torrent_status_repr(torrent):
    torrent_id = torrent.id
    torrent_name = torrent.name
            
    try:
        torrent_percent = str(int(torrent.progress)) + "%"
    except:
        torrent_percent = "?? %"
    
    try:
        torrent_status = torrent.status
    except:
        torrent_status = "Unknown status"
    
    try:
        size = get_torrent_size(torrent)
        done = float(convert_size(size, tag=False)) * (torrent.progress/100.0)
        
        torrent_size = f"{done} / {convert_size(size)}"
    except:
        torrent_size = convert_size(get_torrent_size(torrent))
    
    return '{torrent_id}: {torrent_status} {torrent_percent} {torrent_size}\n{torrent_name}'.format(**locals())

def get_torrent(torrent_id):
    tc = get_transmission_rpc()

    return tc.get_torrent(int(torrent_id))


def start_torrent(torrent_id):
    torrent = get_torrent(torrent_id)
    return torrent.start()


def stop_torrent(torrent_id):
    torrent = get_torrent(torrent_id)
    return torrent.stop()



def delete_torrent(torrent_id):
    torrent_id = int(torrent_id)

    torrent = get_torrent(torrent_id)

    tc = get_transmission_rpc()
    return tc.remove_torrent(torrent_id, delete_data=True)
