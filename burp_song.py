#!/usr/bin/env python3
"""Turn a Twitch streamer's burps into a music video played from a MIDI file.

1. Download the streamer's archived broadcasts (VODs), one at a time.
2. Find burps with Google's YAMNet sound classifier and save each as a clip.
3. Delete the full VOD once its clips are saved.
4. Play every MIDI note with a burp clip, pitch-shifted and stretched to fit,
   showing notes that sound together side by side, and save it as an MP4.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict, defaultdict, deque
from datetime import date, datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import cv2
import mido
import numpy as np
from scipy import signal
from scipy.io import wavfile

LOG = logging.getLogger('burp_song')

# Detection (YAMNet works on 16 kHz mono audio, scoring 0.975 s windows every 0.48 s).
YAMNET_URL = 'https://tfhub.dev/google/yamnet/1'
BURP_CLASS = 'Burping, eructation'
DETECT_RATE = 16000
HOP_SAMPLES = 7680
WINDOW_SAMPLES = 15600
HOP_SECONDS = HOP_SAMPLES / DETECT_RATE
WINDOW_SECONDS = WINDOW_SAMPLES / DETECT_RATE

# Rendering.
SR = 24000
WIDTH, HEIGHT, FPS = 1280, 720, 30
TILE_W, TILE_H = 640, 360


def run(cmd, **kwargs):
    result = subprocess.run(cmd, stderr=subprocess.PIPE, **kwargs)
    if result.returncode:
        error = result.stderr.decode(errors='replace')[-3000:]
        raise RuntimeError(f'{cmd[0]} failed ({result.returncode}): {error}')
    return result


# --- Video encoder (GPU when available) ----------------------------------------

def encoder_options():
    """Hardware H.264 encoders to try, best first, then the CPU encoder.

    Each has: global options (before the inputs), a filter to get frames into the
    encoder's format, and the encoder options.
    """
    options = [
        ('nvenc', 'NVIDIA NVENC', [], 'format=yuv420p', ['-c:v', 'h264_nvenc', '-preset', 'p4', '-cq', '23', '-b:v', '0']),
        ('qsv', 'Intel Quick Sync', [], 'format=nv12', ['-c:v', 'h264_qsv', '-global_quality', '23']),
        ('amf', 'AMD AMF', [], 'format=nv12', ['-c:v', 'h264_amf', '-rc', 'cqp', '-qp_i', '23', '-qp_p', '23']),
        ('videotoolbox', 'Apple VideoToolbox', [], 'format=yuv420p', ['-c:v', 'h264_videotoolbox', '-q:v', '65']),
    ]
    # VAAPI covers AMD and Intel GPUs on Linux; try each GPU's render device.
    for device in sorted(Path('/dev/dri').glob('renderD*')):
        options.append((f'vaapi:{device}', f'VAAPI ({device.name})', ['-vaapi_device', str(device)],
                        'format=nv12,hwupload', ['-c:v', 'h264_vaapi', '-qp', '23']))
    options.append(('vulkan', 'Vulkan', ['-init_hw_device', 'vulkan'], 'format=nv12,hwupload',
                    ['-c:v', 'h264_vulkan', '-qp', '23']))
    return [dict(id=i, name=n, global_args=g, filter=f, args=a) for i, n, g, f, a in options]


CPU_ENCODER = dict(id='cpu', name='CPU (libx264)', global_args=[], filter='format=yuv420p',
                   args=['-c:v', 'libx264', '-preset', 'fast', '-crf', '21'])
_encoder = None


def pick_encoder(choice='auto'):
    """Return the first encoder that can actually encode a test video here."""
    global _encoder
    if choice == 'cpu':
        return CPU_ENCODER
    if _encoder is None:
        _encoder = CPU_ENCODER
        for option in encoder_options():
            test = ['ffmpeg', '-v', 'error', '-nostdin', *option['global_args'], '-f', 'lavfi',
                    '-i', 'color=c=black:size=320x240:rate=30:duration=0.2',
                    '-vf', option['filter'], *option['args'], '-f', 'null', '-']
            try:
                if subprocess.run(test, capture_output=True, timeout=20).returncode == 0:
                    _encoder = option
                    break
            except (OSError, subprocess.TimeoutExpired):
                pass
        LOG.info('Video encoder: %s', _encoder['name'])
    return _encoder


# --- Twitch -------------------------------------------------------------------

def ydl_options(cookies_from_browser=None, **extra):
    options = dict(quiet=True, socket_timeout=30, retries=3, **extra)
    if cookies_from_browser:
        options['cookiesfrombrowser'] = (cookies_from_browser,)
    return options


# Twitch's public web client ID (the one twitch.tv and yt-dlp use; not a secret).
TWITCH_CLIENT_ID = 'kimne78kx3ncx6brgo4mv6wki5h1ko'
SEARCH_QUERY = '''query($q: String!) { searchFor(userQuery: $q, platform: "web",
  options: {targets: [{index: CHANNEL}]}) { channels { edges { item { ... on User {
  login displayName profileImageURL(width: 70) followers { totalCount } stream { id } } } } } } }'''


def search_channels(query):
    """Search Twitch channels by name. Uses Twitch's unofficial web API, which may change."""
    import urllib.request
    body = json.dumps(dict(query=SEARCH_QUERY, variables=dict(q=query))).encode()
    request = urllib.request.Request('https://gql.twitch.tv/gql', body, {
        'Client-Id': TWITCH_CLIENT_ID, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.load(response)
    edges = (((data.get('data') or {}).get('searchFor') or {}).get('channels') or {}).get('edges') or []
    return [dict(login=u['login'], name=u['displayName'], image=u.get('profileImageURL'),
                 followers=(u.get('followers') or {}).get('totalCount'), live=bool(u.get('stream')))
            for u in (edge.get('item') or {} for edge in edges) if u.get('login')]


def list_vods(username, from_date=None, to_date=None, cookies_from_browser=None):
    """Return the streamer's archived broadcasts that started within the dates (UTC)."""
    from yt_dlp import YoutubeDL
    url = f'https://www.twitch.tv/{username}/videos?filter=archives&sort=time'
    with YoutubeDL(ydl_options(cookies_from_browser, extract_flat=True)) as ydl:
        listing = ydl.extract_info(url, download=False) or {}
    ids = []
    for entry in listing.get('entries') or []:
        match = re.fullmatch(r'v?(\d+)', str((entry or {}).get('id', '')))
        if match and match[1] not in ids:
            ids.append(match[1])
    vods = []
    # The listing has no dates, so look each broadcast up individually.
    with YoutubeDL(ydl_options(cookies_from_browser, noplaylist=True)) as ydl:
        for vod_id in ids:
            info = ydl.extract_info(f'https://www.twitch.tv/videos/{vod_id}', download=False)
            if info.get('is_live') or info.get('live_status') in ('is_live', 'is_upcoming'):
                LOG.info('Skipping %s: still live', vod_id)
                continue
            if info.get('timestamp') is not None:
                started = datetime.fromtimestamp(float(info['timestamp']), timezone.utc).date()
            else:
                started = datetime.strptime(info['upload_date'], '%Y%m%d').date()
            if (from_date and started < from_date) or (to_date and started > to_date):
                continue
            vods.append(dict(id=vod_id, date=started.isoformat(), title=info.get('title') or '',
                             url=f'https://www.twitch.tv/videos/{vod_id}'))
    vods.sort(key=lambda v: (v['date'], int(v['id'])))
    return vods


def download_vod(vod, folder, cookies_from_browser=None):
    from yt_dlp import YoutubeDL
    folder.mkdir(parents=True, exist_ok=True)
    # Clips are rendered at 720p, so a bigger download only wastes time and disk.
    options = ydl_options(cookies_from_browser, quiet=False, noplaylist=True,
                          format='best[height<=720]/bv*[height<=720]+ba/best',
                          merge_output_format='mkv', continuedl=True,
                          outtmpl=str(folder/f"{vod['id']}.%(ext)s"))
    with YoutubeDL(options) as ydl:
        if ydl.download([vod['url']]) != 0:
            raise RuntimeError(f"Download failed: {vod['url']}")
    files = [p for p in folder.glob(f"{vod['id']}.*") if p.suffix not in ('.part', '.ytdl')]
    if len(files) != 1:
        raise RuntimeError(f"Expected one downloaded video for {vod['id']}, found {len(files)}")
    return files[0]


# --- Burp detection -----------------------------------------------------------

_model = None


def load_yamnet():
    """Load YAMNet once (weights download on first use) and find its burp class."""
    global _model
    if _model is None:
        import csv
        os.environ.setdefault('TF_USE_LEGACY_KERAS', '1')
        os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '2')
        import tensorflow as tf
        import tensorflow_hub as hub
        gpus = tf.config.list_physical_devices('GPU')
        for gpu in gpus:
            # Take GPU memory as needed, leaving room for the video encoder.
            tf.config.experimental.set_memory_growth(gpu, True)
        LOG.info('Loading YAMNet on %s', 'the GPU' if gpus else 'the CPU')
        model = hub.load(YAMNET_URL)
        with open(model.class_map_path().numpy().decode(), encoding='utf-8') as f:
            names = [row['display_name'] for row in csv.DictReader(f)]
        _model = model, names.index(BURP_CLASS)
    return _model


def burp_scores(video, model, class_index, chunk_seconds=60):
    """Score the whole video's audio, one 0.48 s step at a time, in bounded chunks."""
    chunk = max(1, round(chunk_seconds/HOP_SECONDS))*HOP_SAMPLES
    lookahead = WINDOW_SAMPLES  # so windows at a chunk's end see real audio, not padding
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-i', str(video), '-map', '0:a:0', '-vn',
           '-ac', '1', '-ar', str(DETECT_RATE), '-f', 'f32le', '-']
    scores, buffer = [], np.empty(0, np.float32)
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err)
        try:
            while True:
                wanted = chunk+lookahead-len(buffer)
                data = proc.stdout.read(wanted*4)
                buffer = np.concatenate([buffer, np.frombuffer(data[:len(data)//4*4], '<f4')])
                last = len(data) < wanted*4
                if not len(buffer):
                    break
                frames = math.ceil(len(buffer)/HOP_SAMPLES) if last else chunk//HOP_SAMPLES
                needed = (frames-1)*HOP_SAMPLES+WINDOW_SAMPLES
                waveform = np.pad(buffer, (0, max(0, needed-len(buffer))))
                result = np.asarray(model(waveform)[0].numpy())[:frames, class_index]
                scores.extend(float(s) for s in result)
                LOG.info('%s: scanned %.0f minutes', video.name, len(scores)*HOP_SECONDS/60)
                if last:
                    break
                buffer = buffer[chunk:]
        finally:
            proc.stdout.close()
            code = proc.wait()
        if code:
            err.seek(0)
            raise RuntimeError(f'ffmpeg could not read {video}: {err.read().decode(errors="replace")[-3000:]}')
    return scores


def find_burps(scores, confidence):
    """Merge overlapping windows that score at least `confidence` into (start, end, peak)."""
    burps = []
    for i, score in enumerate(scores):
        if score < confidence:
            continue
        start, end = i*HOP_SECONDS, i*HOP_SECONDS+WINDOW_SECONDS
        if burps and start <= burps[-1][1]:
            burps[-1] = (burps[-1][0], end, max(burps[-1][2], score))
        else:
            burps.append((start, end, score))
    return burps


def cut_clip(video, start, end, target, encoder=CPU_ENCODER):
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix('.tmp.mp4')
    run(['ffmpeg', '-nostdin', '-v', 'error', '-y', *encoder['global_args'],
         '-ss', f'{start:.3f}', '-i', str(video),
         '-t', f'{end-start:.3f}', '-map', '0:v:0', '-map', '0:a:0',
         '-vf', f'scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,'
                f'pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},{encoder["filter"]}',
         *encoder['args'], '-c:a', 'aac', '-b:a', '160k', '-ar', '48000', '-ac', '2', str(temp)])
    temp.replace(target)


def process_vod(vod, root, confidence, cookies_from_browser=None, encoder=CPU_ENCODER):
    """Download one VOD, save its burp clips, then delete the VOD. Returns the clips."""
    folder = root/'clips'/vod['id']
    done = folder/'done.json'
    if done.exists():
        LOG.info('Already processed %s; reusing its clips', vod['id'])
        return existing_clips(folder)
    LOG.info('Processing %s %s %s', vod['date'], vod['url'], vod['title'])
    video = download_vod(vod, root/'vods', cookies_from_browser)
    burps = find_burps(burp_scores(video, *load_yamnet()), confidence)
    LOG.info('%s: found %d burps', vod['id'], len(burps))
    clips = []
    for number, (start, end, score) in enumerate(burps, 1):
        clip = folder/f'burp-{number:04}.mp4'
        cut_clip(video, start, end, clip, encoder)
        clips.append(clip)
    folder.mkdir(parents=True, exist_ok=True)
    done.write_text(json.dumps(dict(vod=vod, confidence=confidence, burps=[
        dict(clip=c.name, start=s, end=e, score=sc) for c, (s, e, sc) in zip(clips, burps)]), indent=2))
    video.unlink()
    LOG.info('Deleted full VOD %s', video.name)
    return clips


def existing_clips(folder):
    return sorted(p for p in Path(folder).rglob('*.mp4') if not p.name.endswith('.tmp.mp4'))


# --- MIDI to audio ------------------------------------------------------------

def midi_notes(path, include_drums=False):
    """Return notes as dicts of start/duration (seconds), track, channel, pitch, velocity."""
    mid = mido.MidiFile(path)
    if mid.type == 2:
        raise ValueError('Type 2 MIDI files are not supported; convert to type 0 or 1.')
    events = []
    for track_id, track in enumerate(mid.tracks):
        tick = 0
        for msg in track:
            tick += msg.time
            events.append((tick, track_id, msg))
    events.sort(key=lambda e: e[0])
    active, sustained, pedals, notes = defaultdict(deque), defaultdict(list), defaultdict(bool), []
    tempo, previous, now = 500000, 0, 0.0

    def finish(item, end):
        start, track_id, channel, pitch, velocity = item
        if end > start:
            notes.append(dict(start=start, duration=end-start, track=track_id,
                              channel=channel, pitch=pitch, velocity=velocity))

    for tick, track_id, msg in events:
        now += mido.tick2second(tick-previous, mid.ticks_per_beat, tempo)
        previous = tick
        if msg.type == 'set_tempo':
            tempo = msg.tempo
        channel = getattr(msg, 'channel', -1)
        if channel == 9 and not include_drums:  # General MIDI percussion
            continue
        key, pedal = (track_id, channel, getattr(msg, 'note', -1)), (track_id, channel)
        if msg.type == 'note_on' and msg.velocity:
            active[key].append((now, track_id, channel, msg.note, msg.velocity))
        elif msg.type in ('note_on', 'note_off'):
            if active[key]:
                item = active[key].popleft()
                if pedals[pedal]:
                    sustained[pedal].append(item)
                else:
                    finish(item, now)
        elif msg.type == 'control_change' and msg.control == 64:  # sustain pedal
            pedals[pedal] = msg.value >= 64
            if not pedals[pedal]:
                for item in sustained.pop(pedal, []):
                    finish(item, now)
    for items in [*active.values(), *sustained.values()]:
        for item in items:
            finish(item, now)
    return sorted(notes, key=lambda n: (n['start'], n['track'], n['pitch']))


def estimate_pitch(audio):
    """Median autocorrelation pitch (MIDI number) over voiced windows, or None."""
    estimates = []
    size = 4096
    for start in range(0, max(1, len(audio)-size+1), 2048):
        x = audio[start:start+size].astype(float)
        if len(x) < 1024 or np.max(np.abs(x)) < .005:
            continue
        x = (x-x.mean())*np.hanning(len(x))
        corr = signal.fftconvolve(x, x[::-1], mode='full')[len(x)-1:]
        lo, hi = int(SR/600), min(int(SR/45), len(corr)-1)
        peaks, _ = signal.find_peaks(corr[lo:hi])
        if not len(peaks) or corr[0] <= 0:
            continue
        lag = int(peaks[np.argmax(corr[lo:hi][peaks])]+lo)
        if corr[lag]/corr[0] > .3:
            estimates.append(69+12*math.log2((SR/lag)/440))
    return float(np.median(estimates)) if estimates else None


def analyze_clip(path, cache, fallback_pitch=48):
    """Extract a clip's trimmed, normalized audio and estimate its pitch (cached)."""
    stat = path.stat()
    key = hashlib.sha256(f'{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest()[:24]
    meta, wav = cache/f'{key}.json', cache/f'{key}.wav'
    if not meta.exists() or not wav.exists():
        raw = run(['ffmpeg', '-v', 'error', '-i', str(path), '-vn', '-ac', '1', '-ar', str(SR),
                   '-f', 'f32le', '-'], stdout=subprocess.PIPE).stdout
        audio = np.frombuffer(raw, '<f4').copy()
        if not len(audio) or np.max(np.abs(audio)) < .0001:
            raise ValueError(f'Silent clip: {path}')
        # Trim quiet lead-in/tail: keep 25 ms around windows above 8% of the loudest.
        window = SR//100
        energy = np.sqrt(np.mean(np.pad(audio, (0, -len(audio) % window)).reshape(-1, window)**2, axis=1))
        loud = np.flatnonzero(energy > max(.002, float(energy.max())*.08))
        begin = max(0, loud[0]*window-SR//40)
        end = min(len(audio), (loud[-1]+1)*window+SR//40)
        audio = audio[begin:end]
        pitch = estimate_pitch(audio)
        audio *= .8/max(.001, float(np.max(np.abs(audio))))
        wavfile.write(wav, SR, audio)
        meta.write_text(json.dumps(dict(offset=begin/SR, duration=len(audio)/SR, pitch=pitch)))
    data = json.loads(meta.read_text())
    return dict(key=key, path=str(path), wav=str(wav), offset=data['offset'],
                duration=data['duration'], pitch=data['pitch'] if data['pitch'] is not None else fallback_pitch)


def select_clip(note, clips):
    # Prefer a clip of similar length; pitch distance breaks near-ties.
    return min(clips, key=lambda c: abs(math.log(c['duration']/note['duration']))
               + .025*abs(c['pitch']-note['pitch']))


def note_audio(note, clip, cache):
    """Pitch-shift and stretch a clip's audio to one note's pitch and length (cached)."""
    length = round(note['duration']*SR)
    ratio = 2**((note['pitch']-clip['pitch'])/12)
    key = hashlib.sha256(f"{clip['key']}:{length}:{ratio:.8f}".encode()).hexdigest()[:24]
    path = cache/f'note-{key}.wav'
    if not path.exists():
        fade = min(.008, note['duration']/4)
        filters = (f"rubberband=tempo={clip['duration']/note['duration']:.9f}:pitch={ratio:.9f},"
                   f"apad,atrim=duration={note['duration']:.9f},"
                   f"afade=t=in:d={fade},afade=t=out:st={note['duration']-fade}:d={fade}")
        run(['ffmpeg', '-v', 'error', '-y', '-i', clip['wav'], '-af', filters,
             '-ar', str(SR), '-c:a', 'pcm_f32le', str(path)])
    return wavfile.read(path)[1][:length]


# --- Video --------------------------------------------------------------------

class Frames:
    """Decoded clip frames, kept on disk and memory-mapped, a few clips at a time."""

    def __init__(self, cache):
        self.cache, self.loaded = cache, OrderedDict()

    def get(self, clip):
        key = clip['key']
        if key not in self.loaded:
            path = self.cache/f'frames-{key}-{FPS}.bgr'
            if not path.exists():
                temp = path.with_suffix('.tmp')
                run(['ffmpeg', '-v', 'error', '-y', '-i', clip['path'], '-an', '-vf',
                     f'fps={FPS},scale={TILE_W}:{TILE_H}:force_original_aspect_ratio=decrease,'
                     f'pad={TILE_W}:{TILE_H}:(ow-iw)/2:(oh-ih)/2',
                     '-pix_fmt', 'bgr24', '-f', 'rawvideo', str(temp)])
                temp.replace(path)
            frames = np.memmap(path, np.uint8, 'r').reshape(-1, TILE_H, TILE_W, 3)
            if not len(frames):
                raise ValueError(f"No video frames in {clip['path']}")
            self.loaded[key] = frames
            if len(self.loaded) > 8:
                self.loaded.popitem(last=False)
        self.loaded.move_to_end(key)
        return self.loaded[key]


def render_video(notes, clips, audio_path, output, seconds, cache, encoder=CPU_ENCODER):
    frames = Frames(cache)
    cmd = ['ffmpeg', '-v', 'error', '-y', *encoder['global_args'], '-f', 'rawvideo', '-pixel_format', 'bgr24',
           '-video_size', f'{WIDTH}x{HEIGHT}', '-framerate', str(FPS), '-i', '-',
           '-i', str(audio_path), '-map', '0:v', '-map', '1:a', '-vf', encoder['filter'], *encoder['args'],
           '-c:a', 'aac', '-b:a', '192k', '-t', f'{seconds:.3f}', '-movflags', '+faststart', str(output)]
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=err)
        try:
            active, cursor = [], 0
            for frame in range(math.ceil(seconds*FPS)):
                t = frame/FPS
                active = [n for n in active if n['start']+n['duration'] > t]
                while cursor < len(notes) and notes[cursor]['start'] <= t:
                    if notes[cursor]['start']+notes[cursor]['duration'] > t:
                        active.append(notes[cursor])
                    cursor += 1
                active.sort(key=lambda n: (n['track'], n['pitch'], n['start']))
                canvas = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
                # Split the screen into a grid with one tile per sounding note.
                cols = min(max(1, math.ceil(math.sqrt(len(active)*WIDTH/HEIGHT))), max(1, len(active)))
                rows = math.ceil(len(active)/cols) if active else 1
                for i, n in enumerate(active):
                    clip = clips[n['clip']]
                    source = frames.get(clip)
                    # Play the clip's video at the same stretched speed as its audio.
                    source_time = clip['offset']+(t-n['start'])/n['duration']*clip['duration']
                    image = source[min(len(source)-1, int(source_time*FPS))]
                    x0, x1 = (i % cols)*WIDTH//cols, (i % cols+1)*WIDTH//cols
                    y0, y1 = (i//cols)*HEIGHT//rows, (i//cols+1)*HEIGHT//rows
                    scale = min((x1-x0)/TILE_W, (y1-y0)/TILE_H)
                    w, h = max(1, int(TILE_W*scale)), max(1, int(TILE_H*scale))
                    x, y = x0+(x1-x0-w)//2, y0+(y1-y0-h)//2
                    canvas[y:y+h, x:x+w] = cv2.resize(image, (w, h))
                proc.stdin.write(canvas.tobytes())
                if frame % (FPS*10) == 0:
                    LOG.info('Video %.0f / %.0f seconds', t, seconds)
            proc.stdin.close()
            if proc.wait():
                err.seek(0)
                raise RuntimeError(f'ffmpeg failed: {err.read().decode(errors="replace")[-3000:]}')
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def make_song(clip_paths, midi, output, cache, seconds=None, encoder=CPU_ENCODER):
    notes = midi_notes(midi)
    end = max((n['start']+n['duration'] for n in notes), default=0)
    if seconds:
        end = min(end, seconds)
    notes = [dict(n, duration=min(n['start']+n['duration'], end)-n['start'])
             for n in notes if n['start'] < end]
    if not notes:
        raise ValueError(f'No playable notes in {midi}')
    cache.mkdir(parents=True, exist_ok=True)
    LOG.info('Analyzing %d clips', len(clip_paths))
    clips = [analyze_clip(Path(p), cache) for p in clip_paths]
    mix = np.zeros(math.ceil(end*SR), np.float32)
    for i, note in enumerate(notes):
        clip = select_clip(note, clips)
        note['clip'] = clips.index(clip)
        audio = note_audio(note, clip, cache)
        start = round(note['start']*SR)
        length = min(len(audio), len(mix)-start)
        mix[start:start+length] += audio[:length]*(note['velocity']/127)
        if i % 100 == 0:
            LOG.info('Audio: note %d / %d', i+1, len(notes))
    peak = float(np.max(np.abs(mix)))
    if peak > 0:
        mix *= .94/peak
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='burp-render-', dir=output.parent) as work:
        work = Path(work)
        wavfile.write(work/'mix.wav', SR, mix)
        render_video(notes, clips, work/'mix.wav', work/'song.mp4', end, cache, encoder)
        (work/'song.mp4').replace(output)
    LOG.info('Created %s (%d notes, %.1f seconds)', output, len(notes), end)


# --- Command line -------------------------------------------------------------

def iso_date(value):
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('use YYYY-MM-DD') from exc


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('username', help='Twitch username, for example emmytheaxolotl')
    p.add_argument('--midi', type=Path, required=True, help='MIDI file to play')
    p.add_argument('--output', type=Path, default=Path('burp-song.mp4'), help='Output MP4 (default: burp-song.mp4)')
    p.add_argument('--from-date', type=iso_date, help='First broadcast date to download, YYYY-MM-DD (UTC)')
    p.add_argument('--to-date', type=iso_date, help='Last broadcast date to download, YYYY-MM-DD (UTC)')
    p.add_argument('--confidence', type=float, default=.35,
                   help='Burp detection score from 0 to 1; higher finds fewer, surer burps (default: .35)')
    p.add_argument('--all', action='store_true',
                   help='Without dates: download the whole VOD archive even if clips already exist')
    p.add_argument('--clips-dir', type=Path, help='Use the MP4 clips in this folder instead of downloading')
    p.add_argument('--work-dir', type=Path, default=Path('burp-work'),
                   help='Where clips, temporary VODs and caches go (default: burp-work)')
    p.add_argument('--seconds', type=float, help='Render only the first N seconds of the song, for a preview')
    p.add_argument('--cpu-only', action='store_true',
                   help='Encode video on the CPU even if a GPU encoder is available')
    p.add_argument('--cookies-from-browser', choices=['firefox', 'chrome', 'chromium', 'brave', 'opera', 'edge'],
                   help="Use this browser's Twitch login (for subscriber-only VODs)")
    args = p.parse_args(argv)
    args.username = args.username.lower()
    if not re.fullmatch(r'[a-z0-9_]{1,25}', args.username):
        p.error('give a Twitch username, not a URL')
    if not args.midi.is_file():
        p.error(f'MIDI file not found: {args.midi}')
    if args.output.suffix.lower() != '.mp4':
        p.error('--output must end in .mp4')
    if args.output.exists():
        p.error(f'{args.output} already exists; choose another --output')
    if not 0 < args.confidence <= 1:
        p.error('--confidence must be greater than 0 and at most 1')
    if args.from_date and args.to_date and args.from_date > args.to_date:
        p.error('--from-date must be on or before --to-date')
    if args.seconds is not None and args.seconds <= 0:
        p.error('--seconds must be positive')
    if args.clips_dir and (args.from_date or args.to_date or args.all):
        p.error('--clips-dir uses existing clips; leave out --from-date, --to-date and --all')
    return args


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    args = parse_args(argv)
    for tool in ('ffmpeg', 'ffprobe'):
        if not shutil.which(tool):
            raise RuntimeError(f'{tool} is required; install FFmpeg first')
    root = args.work_dir/args.username
    encoder = pick_encoder('cpu' if args.cpu_only else 'auto')
    if args.clips_dir:
        clips = existing_clips(args.clips_dir)
    elif args.from_date or args.to_date or args.all or not existing_clips(root/'clips'):
        vods = list_vods(args.username, args.from_date, args.to_date, args.cookies_from_browser)
        LOG.info('Found %d broadcasts', len(vods))
        clips = []
        for vod in vods:
            clips += process_vod(vod, root, args.confidence, args.cookies_from_browser, encoder)
    else:
        clips = existing_clips(root/'clips')
        LOG.info('Using %d existing clips from %s', len(clips), root/'clips')
    if not clips:
        raise ValueError('No burp clips found, so there is nothing to play. '
                         'Try other dates or a lower --confidence.')
    make_song(clips, args.midi, args.output, root/'cache', args.seconds, encoder)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit('Interrupted. Rerun the same command to continue where it stopped.')
    except (RuntimeError, ValueError, OSError, ImportError) as exc:
        raise SystemExit(str(exc))
