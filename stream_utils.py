import os
import re
import json
import time
import threading
import functools
from http.server import HTTPServer, BaseHTTPRequestHandler

import bs4  # python3 -m pip install beautifulsoup4
import requests  # python3 -m pip install requests
import magic  # python3 -m pip install python-magic
from dlna_cast.ssdp import discover as upnp_discover, \
                            Device as UPNPDevice  # python3 -m pip install dlna-cast

from bot_utils import *


LOGGER = logging.getLogger(__name__)


######################################################################
# Misc
######################################################################

def clock_to_seconds(clockstr):
    hours, minutes, seconds = clockstr.split(":")

    if '.' in seconds:
        seconds = seconds.split(".")[0]

    return int(seconds) + int(minutes) *(60) + int(hours) *(60*60)

def seconds_to_clock(seconds):
    hours = int(seconds / (60*60))
    seconds -= hours * (60 * 60)
    minutes = int(seconds / (60))
    seconds -= minutes * (60)
    return f'{hours:02}:{minutes:02}:{seconds:02}'


######################################################################
# HTTP based torrent/file server
######################################################################

def make_href(*subdirs):
    subdirs_str = ''

    for subdir in subdirs:
        subdirs_str += f'/{subdir}'

    return f'{subdirs_str}'

##############################
# HTTP Handler
class HTTPTorrentServerHandler(BaseHTTPRequestHandler):
    '''
    HTTP torrent server handler which serves torrent files accessed by /TorrentFile/{torrent_id}/{file_id}

    It also has registries for other mapped files and NOTIFY callbacks

    It supports the "Content-Range" HTTP header
    '''

    HREF_AVTRANSPORT = 'AVTransport'
    HREF_FILE = 'File'
    HREF_TORRENT = 'TorrentFile'

    regex_torrent_file = re.compile(fr'^/*{HREF_TORRENT}\/(\d+)(?:/(\d+)/)?.*?')  # http://host/TorrentFile/1/0/ for torrent id 1 file id 0
    regex_header_range = re.compile(r'^(\d+)-(\d+)?$')

    protocol_version = 'HTTP/1.1'

    OVERRIDE_MIMETYPES = {
        'video/x-matroska': 'video/webm'
    }

    def __init__(self, NOTIFY_callbacks, file_mappings, *args, **kwargs):
        self.NOTIFY_callbacks = NOTIFY_callbacks
        self.file_mappings = file_mappings
        super().__init__(*args, **kwargs)

    def _get_content_range_numbers(self):
        ''' Extract "Range" HTTP header start and end if they exist '''

        range_str = self.headers.get('Range')
        if not range_str:
            return
        if not range_str.startswith('bytes='):
            return
        
        range_str = range_str[len('bytes='):]

        # TODO: Case last 100 bytes: bytes=-100
        
        m = self.regex_header_range.match(range_str)
        if not m:
            return
        
        start, end = m.groups()
        start = int(start)

        if end is not None:
            end = int(end)

        return start, end

    def _get_content_range(self, size):
        ''' Extract "Range" HTTP header start and end normalized to some size '''

        ret = self._get_content_range_numbers()

        if ret is None:
            return
        start, end = ret

        if end is None:
            end = size - 1

        elif end > size - 1:
            end = size - 1

        return (start, end)

    def _serve_file_part(self, fp, start, size):
        with open(fp, 'rb') as f:
            f.seek(start)
            self.wfile.write(f.read(size))

    def _url_to_torrent_file_id(self):
        ''' Extract (torrent_id,file_id) from the url '''

        match = self.regex_torrent_file.match(self.path)
        if not match:
            return (None, None)

        torrent_id = int(match.group(1))
        file_id = match.group(2)

        if file_id is not None:
            file_id = int(file_id)

        return torrent_id, file_id

    def guess_mimetype(self, filepath, use_data=True):
        if use_data:
            with open(filepath, 'rb') as f:
                guessed = magic.from_buffer(f.read(1024), mime=True)
        else:
            guessed = magic.from_file(filepath, mime=True)

        return self.OVERRIDE_MIMETYPES.get(guessed, guessed)

    def _url_to_torrent_fileinfo(self):
        ''' Extract (torrent_id,file_id) from the url and get file information '''

        torrent_id, file_id = self._url_to_torrent_file_id()

        LOGGER.info(f'Torrent:{torrent_id} File:{file_id}')
        if torrent_id is None or file_id is None:
            return

        tf = transmission_utils.get_torrent_file(torrent_id, file_id)
        fp = transmission_utils.torrent_file_to_path(tf)

        size = os.path.getsize(fp)
        content_type = self.guess_mimetype(fp)
        return tf, fp, size, content_type

    def _url_to_mapped_fileinfo(self):
        ''' Get file mapped to the url and get file information '''
        
        fp = self.file_mappings.get(self.path)
        
        if not fp:
            return
        
        size = os.path.getsize(fp)
        content_type = self.guess_mimetype(fp)
        return fp, size, content_type

    def _send_default_headers(self, filename='none', size=0, content_type='text/xml',
                              content_range=None, connection=UNINITIALIZED, status_code=None):
        LOGGER.debug(f'Sending headers for {filename} with type {content_type} and size {size} connection={connection} range={content_range}')

        if status_code is not None:
            self.send_response(status_code)
        elif content_range:
            self.send_response(206)
        else:
            self.send_response(200)

        self.send_header("Content-Type", content_type)

        if content_range:
            start, end = content_range
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.send_header("Content-Length", f'{end - start + 1}')
        else:
            self.send_header("Content-Length", f'{size}')

        self.send_header("TransferMode.DLNA.ORG", "Streaming")
        self.send_header('ContentFeatures.DLNA.ORG', 'DLNA.ORG_OP=01;DLNA.ORG_FLAGS=01700000000000000000000000000000')
        self.send_header("Accept-Ranges", "bytes")

        if isinstance(connection, UninitializedClass):
            connection = self.headers.get('Connection')

        if connection is not None:
            self.send_header('Connection', connection)

        self.end_headers()

    def _serve_torrents(self):
        ''' Serve a list of torrents in json format:  {'torrents': ["1: What If Season 2", "2: Percy Jackson...", ...] } '''

        torrent_reprs = [transmission_utils.torrent_status_repr(t) for t in transmission_utils.iter_torrents()]
        response = json.dumps({'torrents': torrent_reprs}, indent=2).encode('latin1')
        self._send_default_headers('torrents.json', len(response), content_type='application/json')
        self.wfile.write(response)

    def _serve_torrent_files(self, torrent_id):
        ''' Serve a list of a specific torrent's files in json format:  {'files': ["1.0: What If s02e01.mp4", "1.1: What If s02e01.srt", ...] } '''

        try:
            file_reprs = [repr(tf) for tf in transmission_utils.iter_torrent_files(torrent_id)]
        except:
            return self._serve_torrents()

        response = json.dumps({'files': file_reprs}, indent=2).encode('latin1')
        self._send_default_headers('files.json', len(response), content_type='application/json')
        self.wfile.write(response)

    def do_NOTIFY(self):
        url = self.path
        cb = self.NOTIFY_callbacks.get(url)

        if cb:
            LOGGER.info(f'NOTFIY callback for {url}')
            cb(self)

        else:
            LOGGER.info(f'NOTFIY callback DOESNT EXIST for {url}')
            self._send_default_headers('nocallback')

    def do_HEAD(self):
        ret = self._url_to_torrent_fileinfo()

        if ret is not None:
            tf, fp, size, content_type = ret

        else:

            ret = self._url_to_mapped_fileinfo()
            
            if ret is not None:
                fp, size, content_type = ret        

            else:            
                self._send_default_headers('nofile', status_code=404)
                return
            
        LOGGER.info(f'HEAD FILE {fp}')

        fn = os.path.basename(fp)

        content_range = self._get_content_range(size)
        self._send_default_headers(fn, size,
                                   content_type=content_type,
                                   content_range=content_range)

    def do_GET(self):
        ret = self._url_to_torrent_fileinfo()

        if ret is not None:
            tf, fp, size, content_type = ret

        else:

            ret = self._url_to_mapped_fileinfo()
            if ret is not None:
                fp, size, content_type = ret

            else:
                torrent_id, file_id = self._url_to_torrent_file_id()

                if torrent_id is None:
                    self._serve_torrents()
                    return
                self._serve_torrent_files(torrent_id)
                return
        
        fn = os.path.basename(fp)
        
        content_range = self._get_content_range(size)

        if content_range is None:
            start = 0
            end = size - 1
        else:
            start, end = content_range


        content_type = self.guess_mimetype(fp)

        LOGGER.info(f'GET FILE {content_type} {fp} CONTENT RANGE: {start} {end}')

        self._send_default_headers(fn, size,
                                   content_type=content_type,
                                   content_range=content_range)
        try:
            part_size = end + 1 - start
            self._serve_file_part(fp, start, part_size)
        except:
            pass


##############################
# HTTP server
class HTTPTorrentServer(HTTPServer):
    '''
    HTTP Torrent server main object to easily start and stop the server

    This shares objects with initialized HTTPTorrentServerHandler() objects
    '''

    def __init__(self, *args, server_address=(getattr(config, 'SERVER_IP', ''), getattr(config, 'SERVER_PORT', 0)), RequestHandlerClass=HTTPTorrentServerHandler,
                 timeout=10, **kwargs):
        self.should_run = self.started = False
        self.timeout=timeout
        self.threads = []

        # Share objects with RequestHandlerClass
        self.NOTIFY_callbacks = {}
        self.file_mappings = {}
        new_cls = functools.partial(RequestHandlerClass, self.NOTIFY_callbacks, self.file_mappings)

        super().__init__(*args, server_address=server_address,
                         RequestHandlerClass=new_cls,
                         **kwargs)

    def start_threads(self):
        if self.started:
            return
        LOGGER.info("STARTING file server threads")

        self.started = self.should_run = True
        for i in range(10):
            thread = threading.Thread(target=self._serve_while_should_run)
            thread.daemon = True
            thread.start()
            self.threads.append(thread)

    def stop_threads(self):
        self.should_run = False
        LOGGER.info("STOPPING file server threads")
        
        ip, port = self.server_address
        if ip in ('', None, '0.0.0.0'):
            ip = '127.0.0.1'
        
        # Trigger requests for awaiting self.handle_request() calls
        try:
            for i in range(22):
                requests.get(f'http://{ip}:{port}/', timeout=4)
        except:
            pass

        for thread in self.threads:
            thread.join()

        self.threads = []
        self.started = False

    def _serve_while_should_run(self):
        while self.should_run:
            self.handle_request()

    def register_NOTIFY_callback(self, href, callback):
        if href in self.NOTIFY_callbacks:
            LOGGER.warning(f"Overwriting {href} callback")
        LOGGER.info(f"Registered callback {href} to {callback}")
        self.NOTIFY_callbacks[href] = callback

    def unregister_NOTIFY_callback(self, href):
        if href not in self.NOTIFY_callbacks:
            LOGGER.info(f"No callback registered for {href}")
            return
        LOGGER.info(f"Unregistered callback {href}")
        del self.NOTIFY_callbacks[href]
    
    def register_file_mapping(self, href, filepath):
        if not filepath:
            LOGGER.warning(f"No path specified for {href}")
            return

        if href in self.file_mappings:
            LOGGER.warning(f"Overwriting {href} filepath")

        LOGGER.info(f"Registered {href} to file {filepath}")
        self.file_mappings[href] = filepath

    def unregister_file_mapping(self, href):
        if href not in self.file_mappings:
            LOGGER.info(f"No file registered for {href}")
            return
        
        LOGGER.info(f"Unregistered {href} to file {self.file_mappings[href]}")
        del self.file_mappings[href]
    


######################################################################
# File convertion
######################################################################


class FileConverter(object):
    ''' 
    Handle file convertions with ffmpeg binary

    Saves .metadata_json files to represent the output file's metadata
    '''

    COMMAND_TEMPLATE =  '''{ffmpeg_path} -y -i "{input_path}" {codec_switches} "{output_path}" '''

    DEFAULT_CODEC_SWITCHES = '-map 0 -map_chapters 0 -scodec mov_text -vcodec libx264 -pix_fmt yuv420p -profile:v baseline'

    METADATA_EXTENSION = '.metadata_json'

    @classmethod
    def output_to_metadata_path(cls, output_path):
        return f'{output_path}{cls.METADATA_EXTENSION}'

    @classmethod
    def iter_convertion_metadata_files(cls, *paths):
        if not paths:
            paths = [config.DIR_MOVIES, config.DIR_TV_SHOWS]

        for path in paths:
            # TODO: glob
            for root, folders, files in os.walk(path):
                for f in files:
                    
                    if f.endswith(cls.METADATA_EXTENSION):
                        yield os.path.join(root, f)
    @classmethod
    def iter_convertion_metadatas(cls,
                                  filter_cb=lambda md: os.path.exists(md.get('converted_file')),
                                  *paths):
        for fp in cls.iter_convertion_metadata_files(*paths):
            with open(fp, 'r') as metadata_file:
                metadata = json.loads(metadata_file.read())

            if not filter_cb(metadata):
                continue
                
            yield metadata

    def __init__(self, ffmpeg_path='ffmpeg'):
        self.ffmpeg_path = ffmpeg_path
        self.threads = []
        self.running_identifiers = set()
        self.convertions = []

    def _convert_file_thread(self, metadata):
        LOGGER.info(f'STARTING CONVERTION THREAD FOR {metadata}')

        input_path = metadata.get('original_file')
        output_path = metadata.get('converted_file')

        if not input_path or not output_path:
            LOGGER.error(f'CONVERSION REQUIRES original_file({input_path}) and converted_file({output_path})')
            return

        self.convertions.append(metadata)

        codec_switches = metadata.get('ffmpeg_codec_switches')
        identifier = metadata.get('identifier')

        if identifier:
            self.running_identifiers.add(identifier)

        cmd = self.COMMAND_TEMPLATE.format(ffmpeg_path=self.ffmpeg_path,
                                           input_path=input_path,
                                           codec_switches=codec_switches,
                                           output_path=output_path)
        try:
            output = execute_shell(cmd)
            # TODO: Save video metadata from ffmpeg output

            LOGGER.info(f'Finished converting {input_path} -> {output_path} ({identifier}):\n{output}')
        except:
            LOGGER.info(f'Failed converting {input_path} -> {output_path} ({identifier}) !!!')
        finally:
            if identifier:
                self.running_identifiers.remove(identifier)
    
    def start_conversion_thread(self, metadata):
        t = threading.Thread(target=self._convert_file_thread, args=[metadata])
        self.threads.append(t)
        t.start()

    def convert_file(self, filepath, output_path=None, codec_switches=DEFAULT_CODEC_SWITCHES,
                     **metadatas):
        
        if not filepath or not os.path.isfile(filepath):
            LOGGER.error(f'No such file {filepath}')
            return
        
        if output_path is None:
            output_path = f'{filepath}_converted.mp4'

        metadata_path = self.output_to_metadata_path(output_path)

        with open(metadata_path, 'w') as metadata_file:
            metadata = {
                'original_file': filepath,
                'converted_file': output_path,
                'ffmpeg_codec_switches': codec_switches,
                'identifier': random_identifier(),
                'time': time.time(),
            }

            for k, v in metadatas.items():
                metadata[k] = v

            metadata_file.write(json.dumps(metadata, indent=2))

        self.start_conversion_thread(metadata)
        return metadata


######################################################################
# File convertion menu
######################################################################


class FileConvertionMenu(TorrentMenu):
    DEFAULT_LAYOUT = [
        ['convert_torrent_file', 'delete_file_convertion'],
        ['list_converted_files', 'list_active_convertions'],
        ['back']
    ]
    
    def __init__(self, file_converter: FileConverter, *args, on_complete=None, layout=DEFAULT_LAYOUT, **kwargs):
        super().__init__(*args, layout=layout, **kwargs)

        self.file_converter = file_converter
        self.on_complete = on_complete

        self.create_torrent_file_handler('convert_torrent_file', self._convert_torrent_file_cb)

        process_delete_file_cb = self.cancelable(self._delete_file_convertion_process_choice)
        self.register_callback('_delete_file_convertion_process_choice', process_delete_file_cb)

    async def back(self, update, context):
        if self.on_complete:
            return await call_callback(self.on_complete, update, context)
        
        return ConversationHandler.END

    async def _convert_torrent_file_cb(self, update, torrent_file):
        # on_complete in self.create_torrent_file_handler() call will make this return to self._main_menu

        if torrent_file.completed < torrent_file.size or not torrent_file.size:
            await reply(update, f'Warning: Torrent file download not complete: {torrent_file.completed} / {torrent_file.size}')

        fp = transmission_utils.torrent_file_to_path(torrent_file)
        if not fp:
            await reply(update, f'Invalid input')
            return

        metadata = self.file_converter.convert_file(fp,
                                                    torrent_id=torrent_file.torrent_id,
                                                    file_id=torrent_file.file_id)
        if metadata:
            await multi_reply(update, f'Started convertion', metadata)
        else:
            await reply(update, 'Failed to start convertion')

    async def list_converted_files(self, update, context):
        for metadata in self.file_converter.iter_convertion_metadatas():
            identifier = metadata.get('identifier')

            if not identifier:
                metadata['active'] = 'unknown'
            elif identifier in self.file_converter.running_identifiers:
                metadata['active'] = True
            else:
                metadata['active'] = False

            await reply(update, f'Converted file metadata:\n{json.dumps(metadata, indent=2)}')
        return await self._main_menu(update, context)

    async def delete_file_convertion(self, update, context):
        userdata = self.get_userdata(update)

        convertions = list(m for m in self.file_converter.iter_convertion_metadatas()
                           if m.get('identifier', 'FAKEIFDSFDSFD') not in self.file_converter.running_identifiers)
        userdata['ConvertedFiles_list'] = convertions

        await self.prompt_list(update, 'Choose converted file:', [c.get("converted_file") for c in convertions])
        return self.prefix_menu('_delete_file_convertion_process_choice')

    async def _delete_file_convertion_process_choice(self, update, context):
        userdata = self.get_userdata(update)
        convertions = userdata.get('ConvertedFiles_list', [])

        i = self.choice_to_number(get_text(update))

        if i is not None and 0 <= i < len(convertions):
            convertion = convertions[i]

            output_path = convertion.get('converted_file')
            metadata_path = self.file_converter.output_to_metadata_path(output_path)
            
            try:
                os.unlink(output_path)

            except:
                msg = repr_action(update, f'failed to delete data file {output_path}')
                LOGGER.info(msg)
                await reply(update, msg)

            else:
                msg = repr_action(update, f'deleted data file {output_path}')
                LOGGER.info(msg)
                await reply(update, msg)

            try:
                os.unlink(metadata_path)

            except:
                msg = repr_action(update, f'failed to delete metadata file {metadata_path}')
                LOGGER.info(msg)
                await reply(update, msg)

            else:
                msg = repr_action(update, f'deleted metadata file {output_path}')
                LOGGER.info(msg)
                await reply(update, msg)

        return await self._main_menu(update, context)
            
    async def list_active_convertions(self, update, context):
        identifiers = self.file_converter.running_identifiers.copy()
        for convertion in self.file_converter.convertions:

            if convertion.get('identifier') in identifiers:
                await reply(update, f'File convertion:\n{json.dumps(convertion, indent=2)}')

        return await self._main_menu(update, context)


######################################################################
# UPnP control
######################################################################


def iter_UPNP_devices(filter_cb=lambda device: device.find_action("SetAVTransportURI")):
    ''' By default get only UPNP devices which support casting '''
    for device in upnp_discover(timeout=10):
        if filter_cb(device):
            yield device


class UPNPDeviceControl(object):
    def __init__(self, server: HTTPTorrentServer, device: UPNPDevice):
        self.server = server
        self.device = device

        self.cast_state = ''

        self.avtransport_href = ''
        self.avtransport_sid = ''
        self.avtransport_timeout = self.avtransport_start = 0

        self.video_href = ''
        self.video_didl_metadata = ''

    def __del__(self):
        self.unregister_video_file()
        self.unsubscribe_avtransport()

    def make_url(self, postfix=''):
        ip = self.device.iface_ip  # Use the IP of the interface connected to the device
        port = self.server.server_address[1]
        return f'http://{ip}:{port}{postfix}'
    
    def get_action(self, action, default=lambda *args, **kwargs: None):
        action = self.device.find_action(action)

        if action is None:
            LOGGER.warn(f'No such action {action}')
            return default
        
        return action

    def send_play(self, InstanceID=0, Speed=None):
        self.subscribe_avtransport()

        LOGGER.info(f"SENDING PLAY to {self.device}")

        if Speed is None:
            Speed = 1

        LOGGER.info(f"SENDING PLAY with speed {Speed} to {self.device}")
        return self.get_action('Play')(InstanceID=InstanceID, Speed=Speed)

    def send_pause(self, InstanceID=0):
        self.subscribe_avtransport()

        LOGGER.info(f"SENDING PAUSE to {self.device}")
        return self.get_action('Pause')(InstanceID=InstanceID)

    def send_stop(self, InstanceID=0):
        self.subscribe_avtransport()

        LOGGER.info(f"SENDING STOP to {self.device}")
        return self.get_action('Stop')(InstanceID=InstanceID)

    def send_mute(self, InstanceID=0, Channel='Master', DesiredMute=1):
        self.subscribe_avtransport()

        LOGGER.info(f"SENDING MUTE {DesiredMute} to {self.device}")
        return self.get_action('Mute')(InstanceID=InstanceID, Channel=Channel, DesiredMute=DesiredMute)

    def send_uri(self, url, InstanceID=0, CurrentURIMetaData=''):
        self.subscribe_avtransport()

        LOGGER.info(f"SENDING URI {url} to {self.device}")
        return self.get_action('SetAVTransportURI')(
                                            InstanceID=InstanceID,
                                            CurrentURI=url,
                                            CurrentURIMetaData=CurrentURIMetaData
                                            )
    
    def get_position_info(self, InstanceID=0):
        self.subscribe_avtransport()

        LOGGER.info(f"GETTING POSITION INFO from {self.device}")
        return self.get_action('GetPositionInfo')(InstanceID=InstanceID) or {}
    
    def get_protocol_info(self, InstanceID=0):
        self.subscribe_avtransport()

        LOGGER.info(f"GETTING PROTOCOL INFO from {self.device}")
        return self.get_action('GetProtocolInfo')(InstanceID=InstanceID) or {}

    def get_volume(self, InstanceID=0, Channel='Master'):
        self.subscribe_avtransport()

        LOGGER.info(f"GETTING VOLUME from {self.device}")
        ret = self.get_action('GetVolume')(InstanceID=InstanceID, Channel=Channel)

        if ret:
            return ret.get('CurrentVolume')

    def set_volume(self, DesiredVolume=0, InstanceID=0, Channel='Master'):
        self.subscribe_avtransport()

        LOGGER.info(f"SETTING VOLUME on {self.device}")
        return self.get_action('SetVolume')(InstanceID=InstanceID, Channel=Channel, DesiredVolume=DesiredVolume)

    def send_seek(self, location, relative=False, InstanceID=0, Unit='REL_TIME'):
        self.subscribe_avtransport()

        # pi = self.get_position_info()
        # pi.get('RelTime')
        if isinstance(location, int):
            location = seconds_to_clock(location)

        LOGGER.info(f"SEEKING TO {location} on {self.device}")
        return self.get_action('Seek')(InstanceID=InstanceID, Unit=Unit, Target=location) or {}

    def play_file(self, filepath):
        self.unregister_video_file()
        self.send_stop()

        video_href = make_href(HTTPTorrentServerHandler.HREF_FILE,
                                random_identifier(),
                                'video.mp4')

        self.cast_state = 'registered'
        self.register_video_file(video_href, filepath)
        self.resubscribe_avtransport()

    def play_torrent_file(self, torrent_file):
        self.unregister_video_file()
        self.send_stop()

        fp = transmission_utils.torrent_file_to_path(torrent_file)

        video_href = make_href(HTTPTorrentServerHandler.HREF_TORRENT,
                                torrent_file.torrent_id, torrent_file.file_id,
                                'video.mp4')

        self.cast_state = 'registered'
        self.register_video_file(video_href, fp)
        self.resubscribe_avtransport()

    def register_video_file(self, video_href, filepath):
        self.video_href = video_href
        self.server.register_file_mapping(video_href, filepath)

    def unregister_video_file(self):
        if not self.video_href:
            return
        self.server.unregister_file_mapping(self.video_href)
        self.video_href = ''

    def resubscribe_avtransport(self):
        self.unsubscribe_avtransport()
        self.subscribe_avtransport()

    def subscribe_avtransport(self):
        if self.avtransport_href:
            return

        if not self.server.started:
            self.server.start_threads()

        self.avtransport_href = make_href(HTTPTorrentServerHandler.HREF_AVTRANSPORT, random_identifier())

        url = self.make_url(self.avtransport_href)
        self.server.register_NOTIFY_callback(self.avtransport_href, self.AVTransport_cb)
        self.avtransport_sid, self.avtransport_timeout = self.device.AVTransport.subscribe(url)

        self.avtransport_start = time.time()
        
    def unsubscribe_avtransport(self):
        if not self.avtransport_href:
            return
        
        self.server.unregister_NOTIFY_callback(self.avtransport_href)
        ret = self.device.AVTransport.cancel_subscription(self.avtransport_sid)
        self.avtransport_sid = self.avtransport_timeout = self.avtransport_href = ''

        return ret
    
    def AVTransport_cb(self, httphandler: HTTPTorrentServerHandler):
        # TODO: Refactor

        url = httphandler.path
        LOGGER.info(f'NOTIFY callback for {url} in state {self.cast_state}')

        try:
            size = int(httphandler.headers.get('Content-Length'))
            data = httphandler.rfile.read(size)
            
            xml_soup = bs4.BeautifulSoup(data, 'xml')
            lastchange = bs4.BeautifulSoup(xml_soup.find('LastChange').text, 'xml')
            instance = lastchange.find("InstanceID", val=0)
            assert instance
        except:
            httphandler._send_default_headers('error_callback', 0)
            return

        httphandler._send_default_headers('callback', 0)

        curr_transport_actions = instance.find("CurrentTransportActions")
        transport_state = instance.find("TransportState") 
        
        if self.cast_state == 'registered' and self.video_href:
            self.cast_state = 'sent_uri'
            url = self.make_url(self.video_href)
            self.send_uri(url)

        if self.cast_state == 'sent_uri' and \
            transport_state and transport_state.attrs.get('val') == 'STOPPED' and \
            curr_transport_actions and "Play" in curr_transport_actions.attrs.get('val'):
            self.cast_state = 'sent_play'
            self.send_play()    



######################################################################
# UPNP casting menu
######################################################################


class UPNPTorrentCastMenu(TorrentMenu):
    DEFAULT_LAYOUT = [
        ['play', 'pause', 'stop'],
        ['volume_up', 'volume_down'],
        ['seek_back', 'seek_forward', 'seek_time'],
        ['cast_torrent_file', 'cast_converted_file'],
        ['back'],
    ]

    def __init__(self, server: HTTPTorrentServer, file_converter: FileConverter, device: UPNPDevice, *args, on_complete=None, layout=DEFAULT_LAYOUT, **kwargs):
        super().__init__(*args, layout=layout, **kwargs)

        self.on_complete = on_complete

        self.controller = UPNPDeviceControl(server, device)
        self.file_converter = file_converter
        
        self.volume_inc = 3
        self.time_inc = 30
        self.muted = False

        self.create_torrent_file_handler('cast_torrent_file', lambda update, tf: self.play_torrent_file(tf))

        _cast_converted_file_process_choice_cb = self.cancelable(self._cast_converted_file_process_choice)
        self.register_callback('_cast_converted_file_process_choice', _cast_converted_file_process_choice_cb)

    async def back(self, update, context):
        self.controller.unsubscribe_avtransport()
        self.controller.unregister_video_file()
        
        if self.on_complete:
            return await call_callback(self.on_complete, update, context)
        
        return ConversationHandler.END

    async def cast_converted_file(self, update, context):
        userdata = self.get_userdata(update)

        convertions = list(m for m in self.file_converter.iter_convertion_metadatas()
                           if m.get('identifier', 'FAKEIFDSFDSFD') not in self.file_converter.running_identifiers)
        userdata['ConvertedFiles_list'] = convertions
        
        await self.prompt_list(update, 'Choose converted file:', [c.get('converted_file') for c in convertions])
        return self.prefix_menu('_cast_converted_file_process_choice')

    async def _cast_converted_file_process_choice(self, update, context):
        userdata = self.get_userdata(update)
        convertions = userdata.get('ConvertedFiles_list', [])

        i = self.choice_to_number(get_text(update))

        if i is not None and 0 <= i < len(convertions):
            convertion = convertions[i]

            converted_file = convertion.get('converted_file')
            if os.path.isfile(converted_file):
                msg = repr_action(update, f'casting file {converted_file}')
                LOGGER.info(msg)
                await reply(update, msg)

                self.controller.play_file(converted_file)

        return await self._main_menu(update, context)

    async def play(self, update, context):
        self.controller.send_play()

    async def pause(self, update, context):
        self.controller.send_pause()

    async def stop(self, update, context):
        self.controller.send_stop()

    async def volume_up(self, update, context):
        volume = self.controller.get_volume() or 0
        volume += self.volume_inc
        self.controller.set_volume(DesiredVolume=volume)

    async def volume_down(self, update, context):
        volume = self.controller.get_volume() or 0
        volume -= self.volume_inc
        self.controller.set_volume(DesiredVolume=volume)

    async def toggle_mute(self, update, context):
        # TODO: This doesn't work
        self.controller.send_mute(DesiredMute=int(self.muted))
        self.muted = not self.muted

    async def seek_back(self, update, context):
        # TODO: This doesn't work
        RelTime = self.controller.get_position_info().get('RelTime') or '00:00:00'

        seconds = clock_to_seconds(RelTime)
        seconds -= self.time_inc
        
        location = seconds_to_clock(seconds)
        self.controller.send_seek(location)

    async def seek_forward(self, update, context):
        # TODO: This doesn't work
        RelTime = self.controller.get_position_info().get('RelTime') or '00:00:00'

        seconds = clock_to_seconds(RelTime)
        seconds += self.time_inc
        
        location = seconds_to_clock(seconds)
        self.controller.send_seek(location)

    
