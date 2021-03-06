#!/usr/bin/env python

import os
import subprocess
import sys
from datetime import datetime
from xml.dom import minidom
try:
    import plistlib
except:
    pass

import mutagen
from lrucache import LRUCache

import config

# Something to strip
TRIBUNE_CR = ' Copyright Tribune Media Services, Inc.'

TV_RATINGS = {'TV-Y7': 'x1', 'TV-Y': 'x2', 'TV-G': 'x3', 'TV-PG': 'x4', 
              'TV-14': 'x5', 'TV-MA': 'x6', 'TV-NR': 'x7',
              'TVY7': 'x1', 'TVY': 'x2', 'TVG': 'x3', 'TVPG': 'x4', 
              'TV14': 'x5', 'TVMA': 'x6', 'TVNR': 'x7',
              'Y7': 'x1', 'Y': 'x2', 'G': 'x3', 'PG': 'x4',
              '14': 'x5', 'MA': 'x6', 'NR': 'x7', 'UNRATED': 'x7'}

MPAA_RATINGS = {'G': 'G1', 'PG': 'P2', 'PG-13': 'P3', 'PG13': 'P3',
                'R': 'R4', 'X': 'X5', 'NC-17': 'N6', 'NC17': 'N6',
                'NR': 'N8', 'UNRATED': 'N8'}

STAR_RATINGS = {'1': 'x1', '1.5': 'x2', '2': 'x3', '2.5': 'x4',
                '3': 'x5', '3.5': 'x6', '4': 'x7',
                '*': 'x1', '**': 'x3', '***': 'x5', '****': 'x7'}

HUMAN = {'mpaaRating': {'G1': 'G', 'P2': 'PG', 'P3': 'PG-13', 'R4': 'R',
                        'X5': 'X', 'N6': 'NC-17', 'N8': 'Unrated'},
         'tvRating': {'x1': 'TV-Y7', 'x2': 'TV-Y', 'x3': 'TV-G',
                      'x4': 'TV-PG', 'x5': 'TV-14', 'x6': 'TV-MA',
                      'x7': 'Unrated'},
         'starRating': {'x1': '1', 'x2': '1.5', 'x3': '2', 'x4': '2.5',
                        'x5': '3', 'x6': '3.5', 'x7': '4'}}

BOM = '\xef\xbb\xbf'

tivo_cache = LRUCache(50)
mp4_cache = LRUCache(50)
dvrms_cache = LRUCache(50)

mswindows = (sys.platform == "win32")

def tag_data(element, tag):
    for name in tag.split('/'):
        new_element = element.getElementsByTagName(name)
        if not new_element:
            return ''
        element = new_element[0]
    if not element.firstChild:
        return ''
    return element.firstChild.data

def _vtag_data(element, tag):
    for name in tag.split('/'):
        new_element = element.getElementsByTagName(name)
        if not new_element:
            return []
        element = new_element[0]
    elements = element.getElementsByTagName('element')
    return [x.firstChild.data for x in elements if x.firstChild]

def _tag_value(element, tag):
    item = element.getElementsByTagName(tag)
    if item:
        value = item[0].attributes['value'].value
        name = item[0].firstChild.data
        return name[0] + value[0]

def from_moov(full_path):
    if full_path in mp4_cache:
        return mp4_cache[full_path]

    metadata = {}
    len_desc = 0

    try:
        mp4meta = mutagen.File(unicode(full_path, 'utf-8'))
        assert(mp4meta)
    except:
        mp4_cache[full_path] = {}
        return {}

    # The following 1-to-1 correspondence of atoms to pyTivo
    # variables is TV-biased
    keys = {'tvnn': 'callsign', 'tven': 'episodeNumber',
            'tvsh': 'seriesTitle'}

    for key, value in mp4meta.items():
        if type(value) == list:
            value = value[0]
        if key == 'stik':
            metadata['isEpisode'] = ['false', 'true'][value == 'TV Show']
        elif key in keys:
            metadata[keys[key]] = value
        # These keys begin with the copyright symbol \xA9
        elif key == '\xa9day':
            if len(value) == 4:
                value += '-01-01T16:00:00Z'
            metadata['originalAirDate'] = value
            #metadata['time'] = value
        elif key in ['\xa9gen', 'gnre']:
            for k in ('vProgramGenre', 'vSeriesGenre'):
                if k in metadata:
                    metadata[k].append(value)
                else:
                    metadata[k] = [value]
        elif key == '\xa9nam':
            if 'tvsh' in mp4meta:
                metadata['episodeTitle'] = value
            else:
                metadata['title'] = value

        # Description in desc, cmt, and/or ldes tags. Keep the longest.
        elif key in ['desc', '\xa9cmt', 'ldes'] and len(value) > len_desc:
            metadata['description'] = value
            len_desc = len(value)

        # A common custom "reverse DNS format" tag
        elif (key == '----:com.apple.iTunes:iTunEXTC' and
              ('us-tv' in value or 'mpaa' in value)):
            rating = value.split("|")[1].upper()
            if rating in TV_RATINGS and 'us-tv' in value:
                metadata['tvRating'] = TV_RATINGS[rating]
            elif rating in MPAA_RATINGS and 'mpaa' in value:
                metadata['mpaaRating'] = MPAA_RATINGS[rating]

        # Actors, directors, producers, AND screenwriters may be in a long
        # embedded XML plist.
        elif (key == '----:com.apple.iTunes:iTunMOVI' and
              'plistlib' in sys.modules):
            items = {'cast': 'vActor', 'directors': 'vDirector',
                     'producers': 'vProducer', 'screenwriters': 'vWriter'}
            data = plistlib.readPlistFromString(value)
            for item in items:
                if item in data:
                    metadata[items[item]] = [x['name'] for x in data[item]]

    mp4_cache[full_path] = metadata
    return metadata

def from_dvrms(full_path):
    if full_path in dvrms_cache:
        return dvrms_cache[full_path]

    metadata = {}

    try:
        meta = mutagen.File(unicode(full_path, 'utf-8'))
        assert(meta)
    except:
        dvrms_cache[full_path] = {}
        return {}

    keys = {'title': ['Title'],
            'description': ['Description', 'WM/SubTitleDescription'],
            'episodeTitle': ['WM/SubTitle'],
            'callsign': ['WM/MediaStationCallSign'],
            'displayMajorNumber': ['WM/MediaOriginalChannel'],
            'originalAirDate': ['WM/MediaOriginalBroadcastDateTime'],
            'rating': ['WM/ParentalRating'],
            'credits': ['WM/MediaCredits'], 'genre': ['WM/Genre']}

    for tagname in keys:
        for tag in keys[tagname]:
            try:
                if tag in meta:
                    value = str(meta[tag][0])
                    if value:
                        metadata[tagname] = value
            except:
                pass

    if 'episodeTitle' in metadata and 'title' in metadata:
        metadata['seriesTitle'] = metadata['title']
    if 'genre' in metadata:
        value = metadata['genre'].split(',')
        metadata['vProgramGenre'] = value
        metadata['vSeriesGenre'] = value
        del metadata['genre']
    if 'credits' in metadata:
        value = [x.split('/') for x in metadata['credits'].split(';')]
        if len(value) > 3:
            metadata['vActor'] = [x for x in (value[0] + value[3]) if x]
            metadata['vDirector'] = [x for x in value[1] if x]
        del metadata['credits']
    if 'rating' in metadata:
        rating = metadata['rating']
        if rating in TV_RATINGS:
            metadata['tvRating'] = TV_RATINGS[rating]
        del metadata['rating']

    dvrms_cache[full_path] = metadata
    return metadata

def from_eyetv(full_path):
    keys = {'TITLE': 'title', 'SUBTITLE': 'episodeTitle',
            'DESCRIPTION': 'description', 'YEAR': 'movieYear',
            'EPISODENUM': 'episodeNumber'}
    metadata = {}
    path, name = os.path.split(unicode(full_path, 'utf-8'))
    eyetvp = [x for x in os.listdir(path) if x.endswith('.eyetvp')][0]
    eyetvp = os.path.join(path, eyetvp)
    eyetv = plistlib.readPlist(eyetvp)
    if 'epg info' in eyetv:
        info = eyetv['epg info']
        for key in keys:
            if info[key]:
                metadata[keys[key]] = info[key]
        if info['SUBTITLE']:
            metadata['seriesTitle'] = info['TITLE']
        if info['ACTORS']:
            metadata['vActor'] = [x.strip() for x in info['ACTORS'].split(',')]
        if info['DIRECTOR']:
            metadata['vDirector'] = [info['DIRECTOR']]

        for ptag, etag, ratings in [('tvRating', 'TV_RATING', TV_RATINGS),
                              ('mpaaRating', 'MPAA_RATING', MPAA_RATINGS),
                              ('starRating', 'STAR_RATING', STAR_RATINGS)]:
           x = info[etag].upper()
           if x and x in ratings:
               metadata[ptag] = ratings[x]

        # movieYear must be set for the mpaa/star ratings to work
        if (('mpaaRating' in metadata or 'starRating' in metadata) and
            'movieYear' not in metadata):
            metadata['movieYear'] = eyetv['info']['start'].year
    return metadata

def from_text(full_path):
    metadata = {}
    path, name = os.path.split(unicode(full_path, 'utf-8'))
    title, ext = os.path.splitext(name)

    for metafile in [os.path.join(path, title) + '.properties',
                     os.path.join(path, 'default.txt'), full_path + '.txt',
                     os.path.join(path, '.meta', 'default.txt'),
                     os.path.join(path, '.meta', name) + '.txt']:
        if os.path.exists(metafile):
            sep = ':='[metafile.endswith('.properties')]
            for line in file(metafile, 'U'):
                if line.startswith(BOM):
                    line = line[3:]
                if line.strip().startswith('#') or not sep in line:
                    continue
                key, value = [x.strip() for x in line.split(sep, 1)]
                if not key or not value:
                    continue
                if key.startswith('v'):
                    if key in metadata:
                        metadata[key].append(value)
                    else:
                        metadata[key] = [value]
                else:
                    metadata[key] = value

    for rating, ratings in [('tvRating', TV_RATINGS),
                            ('mpaaRating', MPAA_RATINGS),
                            ('starRating', STAR_RATINGS)]:
        x = metadata.get(rating, '').upper()
        if x in ratings:
            metadata[rating] = ratings[x]

    return metadata

def basic(full_path):
    base_path, name = os.path.split(full_path)
    title, ext = os.path.splitext(name)
    mtime = os.stat(unicode(full_path, 'utf-8')).st_mtime
    if (mtime < 0):
        mtime = 0
    originalAirDate = datetime.fromtimestamp(mtime)

    metadata = {'title': title,
                'originalAirDate': originalAirDate.isoformat()}
    ext = ext.lower()
    if ext in ['.mp4', '.m4v', '.mov']:
        metadata.update(from_moov(full_path))
    elif ext in ['.dvr-ms', '.asf', '.wmv']:
        metadata.update(from_dvrms(full_path))
    elif 'plistlib' in sys.modules and base_path.endswith('.eyetv'):
        metadata.update(from_eyetv(full_path))
    metadata.update(from_text(full_path))

    return metadata

def from_container(xmldoc):
    metadata = {}

    keys = {'title': 'Title', 'episodeTitle': 'EpisodeTitle',
            'description': 'Description', 'seriesId': 'SeriesId',
            'episodeNumber': 'EpisodeNumber', 'tvRating': 'TvRating',
            'displayMajorNumber': 'SourceChannel', 'callsign': 'SourceStation'}

    details = xmldoc.getElementsByTagName('Details')[0]

    for key in keys:
        data = tag_data(details, keys[key])
        if data:
            if key == 'description':
                data = data.replace(TRIBUNE_CR, '')
            elif key == 'tvRating':
                data = 'x' + data
            elif key == 'displayMajorNumber':
                if '-' in data:
                    data, metadata['displayMinorNumber'] = data.split('-')
            metadata[key] = data

    return metadata

def from_details(xml):
    metadata = {}

    xmldoc = minidom.parse(xml)
    showing = xmldoc.getElementsByTagName('showing')[0]
    program = showing.getElementsByTagName('program')[0]

    items = {'description': 'program/description',
             'title': 'program/title',
             'episodeTitle': 'program/episodeTitle',
             'episodeNumber': 'program/episodeNumber',
             'seriesId': 'program/series/uniqueId',
             'seriesTitle': 'program/series/seriesTitle',
             'originalAirDate': 'program/originalAirDate',
             'isEpisode': 'program/isEpisode',
             'movieYear': 'program/movieYear',
             'partCount': 'partCount',
             'partIndex': 'partIndex',
             'time': 'time'}

    for item in items:
        data = tag_data(showing, items[item])
        if data:
            if item == 'description':
                data = data.replace(TRIBUNE_CR, '')
            metadata[item] = data

    vItems = ['vActor', 'vChoreographer', 'vDirector',
              'vExecProducer', 'vProgramGenre', 'vGuestStar',
              'vHost', 'vProducer', 'vWriter']

    for item in vItems:
        data = _vtag_data(program, item)
        if data:
            metadata[item] = data

    sb = showing.getElementsByTagName('showingBits')
    if sb:
        metadata['showingBits'] = sb[0].attributes['value'].value

    for tag in ['starRating', 'mpaaRating', 'colorCode']:
        value = _tag_value(program, tag)
        if value:
            metadata[tag] = value

    rating = _tag_value(showing, 'tvRating')
    if rating:
        metadata['tvRating'] = 'x' + rating[1]

    return metadata

def from_tivo(full_path):
    if full_path in tivo_cache:
        return tivo_cache[full_path]

    tdcat_path = config.get_bin('tdcat')
    tivo_mak = config.get_server('tivo_mak')
    if tdcat_path and tivo_mak:
        fname = unicode(full_path, 'utf-8')
        if mswindows:
            fname = fname.encode('iso8859-1')
        tcmd = [tdcat_path, '-m', tivo_mak, '-2', fname]
        tdcat = subprocess.Popen(tcmd, stdout=subprocess.PIPE)
        metadata = from_details(tdcat.stdout)
        tivo_cache[full_path] = metadata
    else:
        metadata = {}

    return metadata

def force_utf8(text):
    if type(text) == str:
        try:
            text = text.decode('utf8')
        except:
            if sys.platform == 'darwin':
                text = text.decode('macroman')
            else:
                text = text.decode('iso8859-1')
    return text.encode('utf-8')

def dump(output, metadata):
    for key in metadata:
        value = metadata[key]
        if type(value) == list:
            for item in value:
                output.write('%s: %s\n' % (key, item.encode('utf-8')))
        else:
            if key in HUMAN and value in HUMAN[key]:
                output.write('%s: %s\n' % (key, HUMAN[key][value]))
            else:
                output.write('%s: %s\n' % (key, value.encode('utf-8')))

if __name__ == '__main__':        
    if len(sys.argv) > 1:
        metadata = {}
        fname = force_utf8(sys.argv[1])
        ext = os.path.splitext(fname)[1].lower()
        if ext == '.tivo':
            config.init([])
            metadata.update(from_tivo(fname))
        elif ext in ['.mp4', '.m4v', '.mov']:
            metadata.update(from_moov(fname))
        elif ext in ['.dvr-ms', '.asf', '.wmv']:
            metadata.update(from_dvrms(fname))
        dump(sys.stdout, metadata)
