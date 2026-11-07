#!/usr/bin/env python3
"""Turn local XSPF video samples and a MIDI arrangement into a split-screen song."""
from __future__ import annotations

import argparse
from collections import defaultdict, deque, OrderedDict
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET

import cv2
import mido
import numpy as np
from scipy import signal
from scipy.io import wavfile

SR = 24000


def run(args, **kwargs):
    p = subprocess.run(args, stderr=subprocess.PIPE, **kwargs)
    if p.returncode:
        raise RuntimeError(f"{args[0]} failed ({p.returncode}): {p.stderr.decode(errors='replace')[-3000:]}")
    return p


def midi_notes(path, tracks=None, include_drums=False, channels=None):
    mid = mido.MidiFile(path)
    if mid.type == 2:
        raise ValueError("Type 2 MIDI has independent timelines; convert it to type 0 or 1 first.")
    events = []
    for tid, track in enumerate(mid.tracks):
        tick = 0
        for msg in track:
            tick += msg.time
            events.append((tick, tid, msg))
    events.sort(key=lambda e: e[0])
    active = defaultdict(deque)
    sustained = defaultdict(list)
    pedals = defaultdict(bool)
    notes = []
    tempo, prev, now = 500000, 0, 0.0

    def finish(item, end):
        start, tid, channel, pitch, velocity = item
        if end > start:
            notes.append(dict(start=start, duration=end-start, track=tid,
                              channel=channel, pitch=pitch, velocity=velocity))

    for tick, tid, msg in events:
        now += mido.tick2second(tick-prev, mid.ticks_per_beat, tempo)
        prev = tick
        if msg.type == 'set_tempo':
            tempo = msg.tempo
        if tracks is not None and tid not in tracks:
            continue
        channel = getattr(msg, 'channel', -1)
        if channels is not None and channel not in channels:
            continue
        if channel == 9 and not include_drums:
            continue
        key = (tid, channel, getattr(msg, 'note', -1))
        pedal_key = (tid, channel)
        if msg.type == 'note_on' and msg.velocity:
            active[key].append((now, tid, channel, msg.note, msg.velocity))
        elif msg.type == 'note_off' or (msg.type == 'note_on' and not msg.velocity):
            if active[key]:
                item = active[key].popleft()
                if pedals[pedal_key]:
                    sustained[pedal_key].append(item)
                else:
                    finish(item, now)
        elif msg.type == 'control_change' and msg.control == 64:
            pedals[pedal_key] = msg.value >= 64
            if not pedals[pedal_key]:
                for item in sustained.pop(pedal_key, []):
                    finish(item, now)
        elif msg.type == 'control_change' and msg.control in (120, 123):
            for k in list(active):
                if k[:2] == pedal_key:
                    for item in active.pop(k):
                        finish(item, now)
            for item in sustained.pop(pedal_key, []):
                finish(item, now)
    for items in [*active.values(), *sustained.values()]:
        for item in items:
            finish(item, now)
    return sorted(notes, key=lambda n: (n['start'], n['track'], n['pitch']))


def playlist_paths(path):
    paths = []
    for node in ET.parse(path).findall('.//{*}location'):
        uri = urlparse(node.text or '')
        if uri.scheme not in ('', 'file') or uri.netloc not in ('', 'localhost'):
            raise ValueError(f"Only local playlist locations are supported: {node.text}")
        p = Path(unquote(uri.path))
        if not p.is_absolute():
            p = path.parent / p
        if not p.is_file():
            # Permit moving the playlist and its directory tree together.
            matches = list(path.parent.glob(f'*/Burping_eructation/{p.name}'))
            if len(matches) != 1:
                raise FileNotFoundError(f"Missing/ambiguous playlist clip: {p}")
            p = matches[0]
        if p.resolve() not in paths:
            paths.append(p.resolve())
    if not paths:
        raise ValueError('No local clips found in playlist')
    return paths


def estimate_pitch(audio):
    """Autocorrelation median over voiced windows; None for noisy/unpitched samples."""
    estimates = []
    size = 4096
    for start in range(0, max(1, len(audio)-size+1), 2048):
        x = audio[start:start+size].astype(float)
        if len(x) < 1024 or np.max(np.abs(x)) < .005:
            continue
        x -= x.mean()
        x *= np.hanning(len(x))
        corr = signal.fftconvolve(x, x[::-1], mode='full')[len(x)-1:]
        lo, hi = int(SR/600), min(int(SR/45), len(corr)-1)
        peaks, _ = signal.find_peaks(corr[lo:hi])
        if not len(peaks) or corr[0] <= 0:
            continue
        lag = int(peaks[np.argmax(corr[lo:hi][peaks])] + lo)
        if corr[lag]/corr[0] > .3:
            estimates.append(69 + 12*math.log2((SR/lag)/440))
    return float(np.median(estimates)) if estimates else None


def analyze(path, cache, fallback):
    stat = path.stat()
    key = hashlib.sha256(f'v1:{path}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest()[:24]
    meta = cache / f'{key}.json'
    wav = cache / f'{key}.wav'
    if not meta.exists() or not wav.exists():
        raw = run(['ffmpeg', '-v', 'error', '-i', str(path), '-vn', '-ac', '1',
                   '-ar', str(SR), '-f', 'f32le', '-'], stdout=subprocess.PIPE).stdout
        audio = np.frombuffer(raw, dtype='<f4').copy()
        if not len(audio) or np.max(np.abs(audio)) < .0001:
            raise ValueError(f'Empty/silent audio: {path}')
        # Keep 25 ms padding around energy above 8% of peak window RMS.
        window = max(1, SR//100)
        energy = np.sqrt(np.mean(np.pad(audio, (0, (-len(audio)) % window)).reshape(-1, window)**2, axis=1))
        audible = np.flatnonzero(energy > max(.002, float(energy.max())*.08))
        begin = max(0, int(audible[0]*window)-int(.025*SR)) if len(audible) else 0
        end = min(len(audio), int((audible[-1]+1)*window)+int(.025*SR)) if len(audible) else len(audio)
        audio = audio[begin:end]
        pitch = estimate_pitch(audio)
        audio *= .8 / max(.001, float(np.max(np.abs(audio))))
        wavfile.write(wav, SR, audio)
        data = dict(path=str(path), key=key, offset=begin/SR, duration=len(audio)/SR,
                    estimated_pitch=pitch)
        meta.write_text(json.dumps(data, indent=2))
    data = json.loads(meta.read_text())
    data['pitch'] = data['estimated_pitch'] if data['estimated_pitch'] is not None else fallback
    data['wav'] = str(wav)
    return data


def select_clip(note, clips):
    # Duration dominates; pitch distance breaks near-ties and avoids extreme shifts.
    return min(clips, key=lambda c: abs(math.log(c['duration']/note['duration']))
               + .025*abs(c['pitch']-note['pitch']))


def note_audio(note, clip, cache):
    length = round(note['duration']*SR)
    ratio = 2**((note['pitch']-clip['pitch'])/12)
    key = hashlib.sha256(f"v1:{clip['key']}:{length}:{ratio:.8f}".encode()).hexdigest()[:24]
    path = cache / f'note-{key}.wav'
    if not path.exists():
        fade = min(.008, note['duration']/4)
        filters = (f"rubberband=tempo={clip['duration']/note['duration']:.9f}:pitch={ratio:.9f},"
                   f"apad,atrim=duration={note['duration']:.9f},"
                   f"afade=t=in:d={fade},afade=t=out:st={note['duration']-fade}:d={fade}")
        run(['ffmpeg', '-v', 'error', '-y', '-i', clip['wav'], '-af', filters,
             '-ar', str(SR), '-c:a', 'pcm_f32le', str(path)])
    _, audio = wavfile.read(path)
    return audio[:length]


class Frames:
    def __init__(self, fps, directory):
        self.cache = OrderedDict()
        self.fps = fps
        self.directory = directory

    def get(self, clip):
        key = clip['key']
        if key not in self.cache:
            path = self.directory / f"frames-{key}-{self.fps}.bgr"
            if not path.exists():
                with tempfile.NamedTemporaryFile(dir=self.directory, suffix='.bgr') as tmp:
                    run(['ffmpeg', '-v', 'error', '-y', '-threads', '1', '-i', clip['path'],
                         '-an', '-vf', f'fps={self.fps},scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2',
                         '-pix_fmt', 'bgr24', '-f', 'rawvideo', tmp.name])
                    import os
                    os.link(tmp.name, path)
            frames = np.memmap(path, dtype=np.uint8, mode='r').reshape(-1, 360, 640, 3)
            if not len(frames):
                raise ValueError(f"No video frames in {clip['path']}")
            self.cache[key] = frames
            if len(self.cache) > 8:
                self.cache.popitem(last=False)
        self.cache.move_to_end(key)
        return self.cache[key]


def render_video(notes, clips, audio_path, output, seconds, args):
    frames = Frames(args.fps, args.cache)
    cmd = ['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pixel_format', 'bgr24',
           '-video_size', f'{args.width}x{args.height}', '-framerate', str(args.fps),
           '-i', '-', '-i', str(audio_path), '-map', '0:v', '-map', '1:a',
           '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p',
           '-c:a', 'aac', '-b:a', '192k', '-t', str(seconds), '-movflags', '+faststart', str(output)]
    # File-backed stderr prevents pipe deadlocks during long encodes.
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=err)
        active, cursor = [], 0
        try:
            count = math.ceil(seconds*args.fps)
            for frame in range(count):
                t = frame/args.fps
                active = [n for n in active if n['start']+n['duration'] > t]
                while cursor < len(notes) and notes[cursor]['start'] <= t:
                    n = notes[cursor]
                    if n['start']+n['duration'] > t:
                        active.append(n)
                    cursor += 1
                active.sort(key=lambda n: (n['track'], n['pitch'], n['start']))
                canvas = np.zeros((args.height, args.width, 3), np.uint8)
                cols = math.ceil(math.sqrt(len(active)*args.width/args.height)) if active else 1
                cols = min(max(1, cols), max(1, len(active)))
                rows = math.ceil(len(active)/cols) if active else 1
                for i, n in enumerate(active):
                    c = clips[n['clip']]
                    source = frames.get(c)
                    source_time = c['offset'] + (t-n['start'])/n['duration']*c['duration']
                    img = source[min(len(source)-1, int(source_time*args.fps))]
                    x0, x1 = (i % cols)*args.width//cols, (i % cols+1)*args.width//cols
                    y0, y1 = (i // cols)*args.height//rows, (i // cols+1)*args.height//rows
                    scale = min((x1-x0)/640, (y1-y0)/360)
                    w, h = max(1, int(640*scale)), max(1, int(360*scale))
                    x, y = x0+(x1-x0-w)//2, y0+(y1-y0-h)//2
                    canvas[y:y+h, x:x+w] = cv2.resize(img, (w, h))
                    if not getattr(args, 'no_labels', False):
                        cv2.putText(canvas, f"T{n['track']} MIDI {n['pitch']}",
                                    (x0+5, y0+17), cv2.FONT_HERSHEY_SIMPLEX, .4, (255,255,255), 1)
                proc.stdin.write(canvas.tobytes())
                if frame % (args.fps*10) == 0:
                    print(f'Video {t:.0f}/{seconds:.1f}s', flush=True)
            proc.stdin.close()
            code = proc.wait()
            if code:
                err.seek(0)
                raise RuntimeError(err.read().decode(errors='replace')[-3000:])
        except BaseException:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            raise


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--midi', type=Path, default=Path('Never-Gonna-Give-You-Up-3.mid'))
    p.add_argument('--playlist', type=Path, default=Path('OctoberBurps.xspf'))
    p.add_argument('--output', type=Path, default=Path('burp-song.mp4'))
    p.add_argument('--tracks', help='Comma-separated zero-based MIDI track indices')
    p.add_argument('--channels', help='Comma-separated zero-based MIDI channels (0-15)')
    p.add_argument('--include-drums', action='store_true', help='Treat channel 10 percussion as pitched notes')
    p.add_argument('--inspect', action='store_true', help='List MIDI tracks and exit')
    p.add_argument('--start', type=float, default=0, help='Start time in MIDI seconds')
    p.add_argument('--seconds', type=float, help='Render only this many seconds')
    p.add_argument('--octave', type=int, default=0, help='Transpose by this many octaves')
    p.add_argument('--fallback-note', type=float, default=48, help='Assumed source MIDI pitch when estimation fails')
    p.add_argument('--fps', type=int, default=30)
    p.add_argument('--width', type=int, default=1280)
    p.add_argument('--height', type=int, default=720)
    p.add_argument('--cache', type=Path, default=Path('.burp-song-cache'))
    p.add_argument('--no-labels', action='store_true', help='Draw no track/note text over video tiles')
    args = p.parse_args(argv)
    if args.inspect:
        mid = mido.MidiFile(args.midi)
        for i, t in enumerate(mid.tracks):
            count = sum(m.type == 'note_on' and m.velocity > 0 for m in t)
            print(f'{i:2}: {t.name or "(unnamed)"}: {count} notes')
            for channel in range(16):
                count = sum(m.type == 'note_on' and m.velocity > 0 and m.channel == channel for m in t)
                if count:
                    print(f'    channel {channel:2}: {count} notes' + (' (percussion)' if channel == 9 else ''))
        return
    if args.start < 0 or (args.seconds is not None and args.seconds <= 0):
        p.error('start must be nonnegative and seconds positive')
    if args.fps < 1 or min(args.width, args.height) < 32 or args.width % 2 or args.height % 2:
        p.error('fps must be positive; dimensions must be even and at least 32')
    if not all(shutil.which(x) for x in ('ffmpeg', 'ffprobe')):
        p.error('Install ffmpeg and ffprobe first')
    args.output = args.output.resolve()
    if args.output.suffix.lower() != '.mp4':
        p.error('output must have .mp4 extension')
    report = args.output.with_suffix('.json')
    if args.output.exists() or report.exists():
        p.error('Output or its JSON report already exists; choose a new --output')
    tracks = set(map(int, args.tracks.split(','))) if args.tracks else None
    channels = set(map(int, args.channels.split(','))) if args.channels else None
    if channels and not channels.issubset(set(range(16))):
        p.error('Channels must be between 0 and 15')
    notes = midi_notes(args.midi, tracks, args.include_drums, channels)
    if tracks and not tracks.issubset(set(range(len(mido.MidiFile(args.midi).tracks)))):
        p.error('Track index out of range')
    end = max((n['start']+n['duration'] for n in notes), default=0)
    end = min(end, args.start+args.seconds) if args.seconds else end
    selected = []
    for n in notes:
        a, b = max(n['start'], args.start), min(n['start']+n['duration'], end)
        if b > a:
            selected.append(dict(n, start=a-args.start, duration=b-a, pitch=n['pitch']+12*args.octave))
    notes = selected
    if not notes:
        p.error('No notes in selected tracks/time range')
    args.cache.mkdir(parents=True, exist_ok=True)
    clips = []
    for path in playlist_paths(args.playlist.resolve()):
        clips.append(analyze(path, args.cache, args.fallback_note))
        if len(clips) % 10 == 0:
            print(f'Analyzed {len(clips)} clips', flush=True)
    seconds = end-args.start
    mix = np.zeros(math.ceil(seconds*SR), dtype=np.float32)
    for i, n in enumerate(notes):
        c = select_clip(n, clips)
        n['clip'] = clips.index(c)
        audio = note_audio(n, c, args.cache)
        start = round(n['start']*SR)
        length = min(len(audio), len(mix)-start)
        mix[start:start+length] += audio[:length]*(n['velocity']/127)
        if i % 50 == 0:
            print(f'Audio {i+1}/{len(notes)} notes', flush=True)
    peak = float(np.max(np.abs(mix)))
    if peak > 0:
        mix *= .94/peak
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='burp-render-', dir=args.output.parent) as work:
        work = Path(work)
        wavfile.write(work/'mix.wav', SR, mix)
        render_video(notes, clips, work/'mix.wav', work/'render.mp4', seconds, args)
        # Hard link publishes without overwriting an existing output.
        import os
        os.link(work/'render.mp4', args.output)
    with report.open('x') as f:
        json.dump(dict(midi=str(args.midi.resolve()), playlist=str(args.playlist.resolve()),
                       start=args.start, seconds=seconds, clips=clips, notes=notes), f, indent=2)
    print(f'Created {args.output} ({len(notes)} notes, {seconds:.2f}s)')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        raise SystemExit(str(exc))
