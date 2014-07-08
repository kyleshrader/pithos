# -*- coding: utf-8; tab-width: 4; indent-tabs-mode: nil; -*-
### BEGIN LICENSE
# Copyright (C) 2010 Kevin Mehall <km@kevinmehall.net>
# Copyright (C) 2012 Christopher Eby <kreed@kreed.org>
#This program is free software: you can redistribute it and/or modify it
#under the terms of the GNU General Public License version 3, as published
#by the Free Software Foundation.
#
#This program is distributed in the hope that it will be useful, but
#WITHOUT ANY WARRANTY; without even the implied warranties of
#MERCHANTABILITY, SATISFACTORY QUALITY, or FITNESS FOR A PARTICULAR
#PURPOSE.  See the GNU General Public License for more details.
#
#You should have received a copy of the GNU General Public License along
#with this program.  If not, see <http://www.gnu.org/licenses/>.
### END LICENSE

from .blowfish import Blowfish
# from Crypto.Cipher import Blowfish
from xml.dom import minidom
import re
import json
import logging
import time
import urllib.request, urllib.parse, urllib.error
import codecs
import os
import sys
import urllib.request
import threading
import string
import shutil

# This is an implementation of the Pandora JSON API using Android partner
# credentials.
# See http://pan-do-ra-api.wikia.com/wiki/Json/5 for API documentation.

HTTP_TIMEOUT = 30
USER_AGENT = 'pithos'

RATE_BAN = 'ban'
RATE_LOVE = 'love'
RATE_NONE = None

API_ERROR_API_VERSION_NOT_SUPPORTED = 11
API_ERROR_COUNTRY_NOT_SUPPORTED = 12
API_ERROR_INSUFFICIENT_CONNECTIVITY = 13
API_ERROR_READ_ONLY_MODE = 1000
API_ERROR_INVALID_AUTH_TOKEN = 1001
API_ERROR_INVALID_LOGIN = 1002
API_ERROR_LISTENER_NOT_AUTHORIZED = 1003
API_ERROR_PARTNER_NOT_AUTHORIZED = 1010
API_ERROR_PLAYLIST_EXCEEDED = 1039

PLAYLIST_VALIDITY_TIME = 60*60*3

NAME_COMPARE_REGEX = re.compile(r'[^A-Za-z0-9]')

class PandoraError(IOError):
    def __init__(self, message, status=None, submsg=None):
        self.status = status
        self.message = message
        self.submsg = submsg

class PandoraAuthTokenInvalid(PandoraError): pass
class PandoraNetError(PandoraError): pass
class PandoraAPIVersionError(PandoraError): pass
class PandoraTimeout(PandoraNetError): pass

def pad(s, l):
    return s + b'\0' * (l - len(s))

class Pandora(object):
    def __init__(self):
        self.opener = urllib.request.build_opener()
        pass

    def pandora_encrypt(self, s):
        return b''.join([codecs.encode(self.blowfish_encode.encrypt(pad(s[i:i+8], 8)), 'hex_codec') for i in range(0, len(s), 8)])

    def pandora_decrypt(self, s):
        return b''.join([self.blowfish_decode.decrypt(pad(codecs.decode(s[i:i+16], 'hex_codec'), 8)) for i in range(0, len(s), 16)]).rstrip(b'\x08')

    def json_call(self, method, args={}, https=False, blowfish=True):
        url_arg_strings = []
        if self.partnerId:
            url_arg_strings.append('partner_id=%s'%self.partnerId)
        if self.userId:
            url_arg_strings.append('user_id=%s'%self.userId)
        if self.userAuthToken:
            url_arg_strings.append('auth_token=%s'%urllib.parse.quote_plus(self.userAuthToken))
        elif self.partnerAuthToken:
            url_arg_strings.append('auth_token=%s'%urllib.parse.quote_plus(self.partnerAuthToken))

        url_arg_strings.append('method=%s'%method)
        protocol = 'https' if https else 'http'
        url = protocol + self.rpcUrl + '&'.join(url_arg_strings)

        if self.time_offset:
            args['syncTime'] = int(time.time()+self.time_offset)
        if self.userAuthToken:
            args['userAuthToken'] = self.userAuthToken
        elif self.partnerAuthToken:
            args['partnerAuthToken'] = self.partnerAuthToken
        data = json.dumps(args).encode('utf-8')

        logging.debug(url)
        logging.debug(data)

        if blowfish:
            data = self.pandora_encrypt(data)

        try:
            req = urllib.request.Request(url, data, {'User-agent': USER_AGENT, 'Content-type': 'text/plain'})
            response = self.opener.open(req, timeout=HTTP_TIMEOUT)
            text = response.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            logging.error("HTTP error: %s", e)
            raise PandoraNetError(str(e))
        except urllib.error.URLError as e:
            logging.error("Network error: %s", e)
            if e.reason.strerror == 'timed out':
                raise PandoraTimeout("Network error", submsg="Timeout")
            else:
                raise PandoraNetError("Network error", submsg=e.reason.strerror)

        logging.debug(text)

        tree = json.loads(text)

        if tree['stat'] == 'fail':
            code = tree['code']
            msg = tree['message']
            logging.error('fault code: ' + str(code) + ' message: ' + msg)

            if code == API_ERROR_INVALID_AUTH_TOKEN:
                raise PandoraAuthTokenInvalid(msg)
            elif code == API_ERROR_COUNTRY_NOT_SUPPORTED:
                 raise PandoraError("Pandora not available", code,
                    submsg="Pandora is not available outside the United States.")
            elif code == API_ERROR_API_VERSION_NOT_SUPPORTED:
                raise PandoraAPIVersionError(msg)
            elif code == API_ERROR_INSUFFICIENT_CONNECTIVITY:
                raise PandoraError("Out of sync", code,
                    submsg="Correct your system's clock. If the problem persists, a Pithos update may be required")
            elif code == API_ERROR_READ_ONLY_MODE:
                raise PandoraError("Pandora maintenance", code,
                    submsg="Pandora is in read-only mode as it is performing maintenance. Try again later.")
            elif code == API_ERROR_INVALID_LOGIN:
                raise PandoraError("Login Error", code, submsg="Invalid username or password")
            elif code == API_ERROR_LISTENER_NOT_AUTHORIZED:
                raise PandoraError("Pandora Error", code,
                    submsg="A Pandora One account is required to access this feature. Uncheck 'Pandora One' in Settings.")
            elif code == API_ERROR_PARTNER_NOT_AUTHORIZED:
                raise PandoraError("Login Error", code,
                    submsg="Invalid Pandora partner keys. A Pithos update may be required.")
            elif code == API_ERROR_PLAYLIST_EXCEEDED:
                raise PandoraError("Playlist Error", code,
                    submsg="You have requested too many playlists. Try again later.")
            else:
                raise PandoraError("Pandora returned an error", code, "%s (code %d)"%(msg, code))

        if 'result' in tree:
            return tree['result']

    def set_audio_quality(self, fmt):
        self.audio_quality = fmt

    def set_url_opener(self, opener):
        self.opener = opener

    def connect(self, client, user, password):
        self.partnerId = self.userId = self.partnerAuthToken = None
        self.userAuthToken = self.time_offset = None

        self.rpcUrl = client['rpcUrl']
        self.blowfish_encode = Blowfish(client['encryptKey'].encode('utf-8'))
        self.blowfish_decode = Blowfish(client['decryptKey'].encode('utf-8'))

        partner = self.json_call('auth.partnerLogin', {
            'deviceModel': client['deviceModel'],
            'username': client['username'], # partner username
            'password': client['password'], # partner password
            'version': client['version']
            },https=True, blowfish=False)

        self.partnerId = partner['partnerId']
        self.partnerAuthToken = partner['partnerAuthToken']

        pandora_time = int(self.pandora_decrypt(partner['syncTime'].encode('utf-8'))[4:14])
        self.time_offset = pandora_time - time.time()
        logging.info("Time offset is %s", self.time_offset)

        user = self.json_call('auth.userLogin', {'username': user, 'password': password, 'loginType': 'user'}, https=True)
        self.userId = user['userId']
        self.userAuthToken = user['userAuthToken']

        self.get_stations(self)

    def get_stations(self, *ignore):
        stations = self.json_call('user.getStationList')['stations']
        self.quickMixStationIds = None
        self.stations = [Station(self, i) for i in stations]

        if self.quickMixStationIds:
            for i in self.stations:
                if i.id in self.quickMixStationIds:
                    i.useQuickMix = True

    def save_quick_mix(self):
        stationIds = []
        for i in self.stations:
            if i.useQuickMix:
                stationIds.append(i.id)
        self.json_call('user.setQuickMix', {'quickMixStationIds': stationIds})

    def search(self, query):
        results = self.json_call('music.search', {'searchText': query})

        l =  [SearchResult('artist', i) for i in results['artists']]
        l += [SearchResult('song',   i) for i in results['songs']]
        l.sort(key=lambda i: i.score, reverse=True)

        return l

    def add_station_by_music_id(self, musicid):
        d = self.json_call('station.createStation', {'musicToken': musicid})
        station = Station(self, d)
        self.stations.append(station)
        return station

    def get_station_by_id(self, id):
        for i in self.stations:
            if i.id == id:
                return i

    def add_feedback(self, trackToken, rating):
        logging.info("pandora: addFeedback")
        rating_bool = True if rating == RATE_LOVE else False
        feedback = self.json_call('station.addFeedback', {'trackToken': trackToken, 'isPositive': rating_bool})
        return feedback['feedbackId']

    def delete_feedback(self, stationToken, feedbackId):
        self.json_call('station.deleteFeedback', {'feedbackId': feedbackId, 'stationToken': stationToken})

class Station(object):
    def __init__(self, pandora, d):
        self.pandora = pandora

        self.id = d['stationId']
        self.idToken = d['stationToken']
        self.isCreator = not d['isShared']
        self.isQuickMix = d['isQuickMix']
        self.name = d['stationName']
        self.useQuickMix = False

        if self.isQuickMix:
            self.pandora.quickMixStationIds = d.get('quickMixStationIds', [])

    def transformIfShared(self):
        if not self.isCreator:
            logging.info("pandora: transforming station")
            self.pandora.json_call('station.transformSharedStation', {'stationToken': self.idToken})
            self.isCreator = True

    def get_playlist(self):
        logging.info("pandora: Get Playlist")
        playlist = self.pandora.json_call('station.getPlaylist', {'stationToken': self.idToken}, https=True)
        songs = []
        for i in playlist['items']:
            if 'songName' in i: # check for ads
                i['stationName'] = self.name
                songs.append(Song(self.pandora, i))
        return songs

    @property
    def info_url(self):
        return 'http://www.pandora.com/stations/'+self.idToken

    def rename(self, new_name):
        if new_name != self.name:
            self.transformIfShared()
            logging.info("pandora: Renaming station")
            self.pandora.json_call('station.renameStation', {'stationToken': self.idToken, 'stationName': new_name})
            self.name = new_name

    def delete(self):
        logging.info("pandora: Deleting Station")
        self.pandora.json_call('station.deleteStation', {'stationToken': self.idToken})

downloads = {}
temp_dir = os.path.join(os.path.expanduser('~'),'Pithos','Temp')
music_dir = os.path.join(os.path.expanduser('~'),'Pithos','Music')
class Song(object):
    def __init__(self, pandora, d):
        self.pandora = pandora

        self.album = d['albumName']
        self.artist = d['artistName']
        self.audioUrlMap = d['audioUrlMap']
        self.trackToken = d['trackToken']
        self.rating = RATE_LOVE if d['songRating'] == 1 else RATE_NONE # banned songs won't play, so we don't care about them
        self.stationId = d['stationId']
        self.songName = d['songName']
        self.songDetailURL = d['songDetailUrl']
        self.songExplorerUrl = d['songExplorerUrl']
        self.artRadio = d['albumArtUrl']
        self.stationName = d['stationName']

        self.bitrate = None
        self.is_ad = None  # None = we haven't checked, otherwise True/False
        self.tired=False
        self.message=''
        self.start_time = None
        self.finished = False
        self.playlist_time = time.time()
        self.feedbackId = None

        self.downloaded = False
        self.download()

    def get_download_url(self):
        quality = self.pandora.audio_quality
        try:
            q = self.audioUrlMap[quality]
        except KeyError:
            logging.warn("Unable to use audio format %s. Using %s", quality, list(self.audioUrlMap.keys())[0])
            q = list(self.audioUrlMap.values())[0]['audioUrl']
        logging.info("Using audio quality %s: %s %s", quality, q['bitrate'], q['encoding'])
        audiourl = q['audioUrl']
        return audiourl

    def resolve_filename(self):
        return os.path.join(self.get_folders_path(), self.get_song_filename())

    def get_artist_folder(self):
        return self.make_safe(self.artist)

    def get_album_folder(self):
        return self.make_safe(self.album)

    def get_song_filename(self):
        return self.make_safe(self.songName + '.mp4')

    def get_station_folder(self):
        return self.make_safe(self.stationName)

    def get_folders_path(self):
        station_dir = self.get_station_folder()
        artist_dir = self.get_artist_folder()
        album_dir = self.get_album_folder()
        return os.path.join(station_dir, artist_dir, album_dir)

    def get_temp_dir(self):
        global temp_dir
        return temp_dir

    def get_music_dir(self):
        global music_dir
        return music_dir

    def get_stored_filename(self):
        return os.path.join(self.get_music_dir(), self.resolve_filename())

    def get_temp_filename(self):
        return os.path.join(self.get_temp_dir(), self.resolve_filename())

    def is_stored(self):
        return os.path.exists(self.get_stored_filename())

    def download(self):
        # If stored, return stored filename
        stored_filename = self.get_stored_filename()
        if os.path.isfile(stored_filename):
            self.file_name = stored_filename
            self.downloaded = True
            return
        # Create required folders if not already created
        folders_path = os.path.join(self.get_temp_dir(),self.get_folders_path())
        if not os.path.exists(folders_path):
            os.makedirs(folders_path)
        # Get URL to download from
        audiourl = self.get_download_url()
        # Get the temp file path
        temp_filename = self.get_temp_filename()
        # Download song in seperate process
        def runInThread():
            try:
                tmp_filename, headers = urllib.request.urlretrieve(audiourl, temp_filename, reporthook=self.dlProgress)
            except Exception:
                import traceback
                print(traceback.format_exc())
                print('Download Failed\n')
                self.file_name = None
            else:
                self.file_name = tmp_filename
            return
        thread = threading.Thread(target=runInThread)
        thread.start()

    def dlProgress(self, count, blockSize, totalSize):
        percent = int(count*blockSize*100/totalSize)
        global downloads
        if percent >= 100:
            self.downloaded = True
            downloads.pop(self.songName, None)
            print('Finished Downloading %s' % self.resolve_filename())
        else:
            downloads[self.songName] = percent
        dl_strings = []
        for key, value in downloads.items():
            dl_strings.append(key+"...%d%%"%value)
        dl_string = ', '.join(dl_strings)
        sys.stdout.write("%s\r" % dl_string)
        sys.stdout.flush()
        sys.stdout.write('\x1b[2K')

    def delete_temp(self):
        # Delete the file
        file_name = self.get_temp_filename()
        if os.path.isfile(file_name):
            os.remove(file_name)
        # Delete the album folder if empty
        album_folder = os.path.join(self.get_temp_dir(), self.get_station_folder(), self.get_artist_folder(), self.get_album_folder())
        if os.path.exists(album_folder):
            if not os.listdir(album_folder):
                os.rmdir(album_folder)
        # Delete the artist folder if empty
        artist_folder = os.path.join(self.get_temp_dir(), self.get_station_folder(), self.get_artist_folder())
        if os.path.exists(artist_folder):
            if not os.listdir(artist_folder):
                os.rmdir(artist_folder)
        # Delete the station folder if empty
        station_folder = os.path.join(self.get_temp_dir(), self.get_station_folder())
        if os.path.exists(station_folder):
            if not os.listdir(station_folder):
                os.rmdir(station_folder)

    def store(self):
        # Move from temp to music
        stored_dirs = os.path.join(self.get_music_dir(), self.get_folders_path())
        if not os.path.exists(stored_dirs):
            os.makedirs(stored_dirs)
            if not os.path.isfile(self.get_stored_filename()):
                shutil.copy(self.get_temp_filename(), self.get_stored_filename())
        self.delete_temp()

    def make_safe(self, filename):
        valid_chars = "&+-_.() %s%s" % (string.ascii_letters, string.digits)
        return ''.join(c for c in filename if c in valid_chars)

    @property
    def title(self):
        if not hasattr(self, '_title'):
            # the actual name of the track, minus any special characters (except dashes) is stored
            # as the last part of the songExplorerUrl, before the args.
            explorer_name = self.songExplorerUrl.split('?')[0].split('/')[-1]
            clean_expl_name = NAME_COMPARE_REGEX.sub('', explorer_name).lower()
            clean_name = NAME_COMPARE_REGEX.sub('', self.songName).lower()

            if clean_name == clean_expl_name:
                self._title = self.songName
            else:
                try:
                    xml_data = urllib.urlopen(self.songExplorerUrl)
                    dom = minidom.parseString(xml_data.read())
                    attr_value = dom.getElementsByTagName('songExplorer')[0].attributes['songTitle'].value

                    # Pandora stores their titles for film scores and the like as 'Score name: song name'
                    self._title = attr_value.replace('{0}: '.format(self.songName), '', 1)
                except:
                    self._title = self.songName
        return self._title

    @property
    def audioUrl(self):
        import time
        while not self.downloaded:
            time.sleep(1)
        return 'file://'+self.file_name

    @property
    def station(self):
        return self.pandora.get_station_by_id(self.stationId)

    def rate(self, rating):
        if self.rating != rating:
            self.station.transformIfShared()
            if rating == RATE_NONE:
                if not self.feedbackId:
                    # We need a feedbackId, get one by re-rating the song. We
                    # could also get one by calling station.getStation, but
                    # that requires transferring a lot of data (all feedback,
                    # seeds, etc for the station).
                    opposite = RATE_BAN if self.rating == RATE_LOVE else RATE_LOVE
                    self.feedbackId = self.pandora.add_feedback(self.trackToken, opposite)
                self.pandora.delete_feedback(self.station.idToken, self.feedbackId)
            else:
                self.feedbackId = self.pandora.add_feedback(self.trackToken, rating)
            self.rating = rating

    def set_tired(self):
        if not self.tired:
            self.pandora.json_call('user.sleepSong', {'trackToken': self.trackToken})
            self.tired = True

    def bookmark(self):
        self.pandora.json_call('bookmark.addSongBookmark', {'trackToken': self.trackToken})

    def bookmark_artist(self):
        self.pandora.json_call('bookmark.addArtistBookmark', {'trackToken': self.trackToken})

    @property
    def rating_str(self):
        return self.rating

    def is_still_valid(self):
        return (time.time() - self.playlist_time) < PLAYLIST_VALIDITY_TIME

class SearchResult(object):
    def __init__(self, resultType, d):
        self.resultType = resultType
        self.score = d['score']
        self.musicId = d['musicToken']

        if resultType == 'song':
            self.title = d['songName']
            self.artist = d['artistName']
        elif resultType == 'artist':
            self.name = d['artistName']

