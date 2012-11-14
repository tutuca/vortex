import logging
import os
import re

import mutagen

from django.conf import settings
from django.core.files import File

from vortex.musique.models import Artist, Album, Song


LOGGER = logging.getLogger(__name__)

if settings.TITLECASE_ARTIST_AND_ALBUM_NAMES:
    from vortex.musique.utils import titlecase
else:
    titlecase = lambda x: x


def get_mutagen_audio_options():
    """Build the list of options to give mutagen.File for reading
    audio tags according to the supported formats in the config.
    """

    formats = settings.SUPPORTED_FORMATS

    from mutagen.easyid3 import EasyID3FileType
    audio_options = [EasyID3FileType]
    for fmt in formats:
        if fmt == 'mp3':
            from mutagen.mp3 import EasyMP3
            audio_options.append(EasyMP3)
        elif fmt == 'mp4' or fmt == 'm4a':
            from mutagen.easymp4 import EasyMP4
            audio_options.append(EasyMP4)
        elif fmt == 'ogg':
            from mutagen.oggvorbis import OggVorbis
            from mutagen.oggspeex import OggSpeex
            from mutagen.oggflac import OggFLAC
            audio_options += [OggVorbis, OggSpeex, OggFLAC]
        elif fmt == 'flac':
            from mutagen.flac import FLAC
            audio_options.append(FLAC)
        elif fmt == 'wma':
            from mutagen.asf import ASF
            audio_options.append(ASF)
        else:
            LOGGER.info('"%s" support not implemented yet' % fmt)

    return audio_options


def update():
    """Import into the library all files in the directory structure
    rooted at `DROPBOX`.

    The files that are successfully imported are moved into the
    `MEDIA_ROOT` folder. The others are left in place, except for those
    whose name matches one in `DUMMY_FILES`, which are deleted.
    """

    mutagen_options = get_mutagen_audio_options()
    pattern = '|'.join(settings.DUMMY_FILES)
    regex = re.compile(r'%s' % pattern)
    for root, dirs, files in os.walk(settings.DROPBOX, topdown=False):
        for name in files:
            if re.match(regex, name) or name.endswith(
                                            ('jpg', 'jpeg', 'gif', 'png')):
                #FIXME: keep images for cover image
                try:
                    os.remove(os.path.join(root, name))
                except Exception:
                    pass
            else:
                filename = os.path.join(root, name).decode('utf-8')
                import_file(filename, mutagen_options)
        try:
            if root != settings.DROPBOX:
                os.rmdir(root)
        except Exception:
            pass


def import_file(filename, mutagen_options):
    """Import the song referred to by filename into the library

    Arguments:
        filename - the filename of the audio file to import
        mutagen_options - list of options needed by mutagen.File
    """

    try:
        if filename.rsplit('.')[-1].lower() == 'wma':
            info = get_wma_info(filename)
        else:
            info = get_song_info(filename, mutagen_options)
    except ValueError:
        handle_import_error(filename, 'mutagen error')
        return

    artist_name = titlecase(info['artist'])
    artist_path = os.path.join(artist_name[0].upper(), artist_name)
    artist, created = Artist.objects.get_or_create(name=artist_name,
                                                   filepath=artist_path)

    album_path = os.path.join(artist_path, titlecase(info['album']))
    album, created = Album.objects.get_or_create(
                                            title=titlecase(info['album']),
                                            artist=artist,
                                            filepath=album_path)

    filetype = filename.rsplit('.')[-1].lower()
    original_path = filename.replace(
                        settings.DROPBOX, '', 1).lstrip(os.path.sep)

    #TODO: Handle Unknown Title by Unknown Artist
    try:
        song, created = Song.objects.get_or_create(
                            title=info['title'],
                            artist=artist,
                            album=album,
                            track=info['track'],
                            defaults={'filefield': File(open(filename, 'rb')),
                                      'bitrate': info['bitrate'],
                                      'filetype': filetype,
                                      'original_path': original_path,
                                      'first_save': True})
    except Exception, msg:
        handle_import_error(filename, msg)
        return

    if not created:
        # Song already exists, keep only if better bitrate
        if song.bitrate >= info['bitrate']:
            #FIXME: uncomment when Unknown songs are handled correctly
            #os.remove(filename)
            handle_import_error(filename,
                                '%s by %s already exists'
                                    % (info['title'], info['artist']))
            return
        else:
            song.bitrate = info['bitrate']
            song.filefield = File(open(filename, 'rb'))
            song.original_path = original_path
            song.first_save = True
            song.save()

    os.remove(filename)


def get_wma_info(filename):
    """Returns a dictionnary describing attributes of the WMA audio
    file referred to by `filename`.
    """

    try:
        audio = mutagen.File(filename)
    except Exception, msg:
        handle_import_error(filename, msg)
        raise ValueError
    if audio is None:
        raise ValueError

    title = audio.get('Title', [u'Unknown Title'])[0]

    artist = audio.get('Author', [None])[0]
    if not artist:
        artist = audio.get('WM/AlbumArtist', [None])[0]
        if artist:
            artist = artist.value
        else:
            artist = u'Unknown Artist'

    album = audio.get('WM/AlbumTitle', [None])[0]
    if album:
        album = album.value
    else:
        album = u'Unknown Album'

    track = audio.get('WM/TrackNumber', [None])[0]
    if track:
        track = unicode(track.value)
        if len(track) == 1:
            track = u'0' + track
    else:
        track = u''

    bitrate = audio.info.bitrate

    return {'title': title, 'artist': artist, 'album': album,
            'track': track, 'bitrate': bitrate}


def get_tag_field(container, tag_name):
    """Helper function to retrieve an appropriate value for the tag
    with name `tag_name` from the mutagen audio container.
    """

    tag = container.get(tag_name, [u'Unknown %s' % tag_name.capitalize()])[0]
    tag = tag or u'Unknown %s' % tag_name.capitalize()
    return tag.replace('/', '-')


def get_song_info(filename, mutagen_options):
    """Returns a dictionnary describing attributes of the audio file
    referred to by `filename`.

    Arguments:
        filename - the full path of the audio file
        mutagen_options - list of options needed by mutagen.File
    """

    try:
        audio = mutagen.File(filename, options=mutagen_options)
    except Exception, msg:
        handle_import_error(filename, msg)
        raise ValueError
    if audio is None:
        raise ValueError

    title = get_tag_field(audio, 'title')
    artist = get_tag_field(audio, 'artist')
    album = get_tag_field(audio, 'album')
    track = audio.get('tracknumber', [u''])[0]

    try:
        track = re.match(r'\d+', track).group()
        if len(track) == 1:
            track = u'0' + track
    except AttributeError:
        track = u''

    try:
        bitrate = audio.info.bitrate
    except AttributeError:
        # Flac files have no bitrate attribute
        bitrate = 0

    return {'title': title, 'artist': artist, 'album': album,
            'track': track, 'bitrate': bitrate}


def handle_import_error(filename, error_msg=None):
    """Write an error message in the log."""

    log_msg = 'Problem importing file %s' % filename
    if error_msg:
        log_msg = '%s (%s)' % (log_msg, error_msg)
    LOGGER.info(log_msg)
