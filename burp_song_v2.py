#!/usr/bin/env python3
"""Download Twitch broadcasts by date, extract burps, and render a MIDI clip song.

Without dates, reuse local burp clips without contacting Twitch.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import logging
import math
from pathlib import Path
import re
import shutil
import tempfile
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import burp_song as song
from sneeze_compilation import (
    atomic_json, cached_scores, events_from_scores, export_clip, load_yamnet,
    probe, source_signature,
)

LOG = logging.getLogger('burp_song_v2')
BURP_CLASS = 'Burping, eructation'
VIDEO_SUFFIXES = {'.mp4', '.mkv', '.webm', '.ts', '.mov'}


def iso_date(value):
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Use YYYY-MM-DD') from exc


def vod_id(value):
    match = re.fullmatch(r'v?(\d+)', str(value))
    if not match:
        raise ValueError(f'Invalid Twitch broadcast ID: {value!r}')
    return match[1]


def public_record(info, username):
    identity = vod_id(info['id'])
    owner = info.get('uploader_id')
    if owner and owner.lower() != username:
        raise ValueError(f'Broadcast {identity} belongs to {owner}, not {username}')
    if info.get('is_live') or info.get('live_status') in ('is_live', 'is_upcoming'):
        raise ValueError(f'Broadcast {identity} is still live/upcoming; retry once archived')
    timestamp = info.get('timestamp')
    if timestamp is not None:
        started = datetime.fromtimestamp(float(timestamp), timezone.utc).date()
    elif info.get('upload_date'):
        started = datetime.strptime(info['upload_date'], '%Y%m%d').date()
    else:
        raise ValueError(f'No broadcast date for {identity}; refusing to guess its date')
    return dict(id=identity, username=username, url=f'https://www.twitch.tv/videos/{identity}',
                title=info.get('title') or '', date=started.isoformat(),
                duration=info.get('duration'))


def ydl_options(args):
    options = dict(quiet=True, socket_timeout=30, retries=3)
    if args.cookies_from_browser:
        options['cookiesfrombrowser'] = (args.cookies_from_browser,)
    return options


def discover_broadcasts(args):
    from yt_dlp import YoutubeDL
    url = f'https://www.twitch.tv/{args.username}/videos?filter=archives&sort=time'
    # Flat entries have no date in the current Twitch extractor. Fetch each VOD's
    # actual metadata, rather than filtering undated entries with --dateafter.
    with YoutubeDL(dict(ydl_options(args), extract_flat=True)) as listing:
        result = listing.extract_info(url, download=False)
        if not result or 'entries' not in result:
            raise RuntimeError('Twitch did not return a broadcast listing')
        entries = list(result['entries'])
    records, seen = [], set()
    with YoutubeDL(dict(ydl_options(args), noplaylist=True)) as details:
        for entry in entries:
            if not entry:
                raise RuntimeError('Incomplete Twitch listing; retry instead of silently omitting a VOD')
            identity = vod_id(entry['id'])
            if identity in seen:
                continue
            seen.add(identity)
            info = details.extract_info(f'https://www.twitch.tv/videos/{identity}', download=False)
            if not info:
                raise RuntimeError(f'Cannot inspect broadcast {identity}')
            record = public_record(info, args.username)
            if args.from_date <= date.fromisoformat(record['date']) <= args.to_date:
                records.append(record)
    records.sort(key=lambda r: (r['date'], int(r['id'])))
    return records[:args.max_vods] if args.max_vods else records


def local_original(directory):
    files = sorted(p for p in directory.glob('original.*')
                   if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES)
    if len(files) > 1:
        raise ValueError(f'Multiple original videos in {directory}; select one before retrying')
    return files[0] if files else None


def validate_original(source, record):
    duration = probe(source)
    expected = record.get('duration')
    if expected and duration+max(5, float(expected)*.005) < float(expected):
        raise ValueError(f'Full video appears truncated: {source} ({duration:.1f}s versus {expected}s)')
    return duration


def obtain_video(record, root, args):
    """Return source and whether it is a download owned by this v2 workflow."""
    directory = root/record['id']
    directory.mkdir(parents=True, exist_ok=True)
    source = local_original(directory)
    ownership = directory/'download-owned.json'
    if source:
        validate_original(source, record)
        owned = ownership.exists() and json.loads(ownership.read_text()).get('source') == source_signature(source)
        return source, owned
    if args.reuse_dir:
        base = args.reuse_dir
        candidates = [base/record['id'], base/f"v{record['id']}",
                      base/args.username/record['id'], base/args.username/f"v{record['id']}"]
        for candidate in candidates:
            source = local_original(candidate)
            if source:
                validate_original(source, record)
                LOG.info('Using existing full video without taking deletion ownership: %s', source)
                return source, False
    from yt_dlp import YoutubeDL
    options = dict(ydl_options(args), quiet=False, noplaylist=True,
                   format=args.download_format, merge_output_format='mkv',
                   outtmpl=str(directory/'original.%(ext)s'),
                   continuedl=True, overwrites=False, fragment_retries=10,
                   skip_unavailable_fragments=False)
    with YoutubeDL(options) as downloader:
        if downloader.download([record['url']]) != 0:
            raise RuntimeError(f"Download failed: {record['url']}")
    source = local_original(directory)
    if source is None:
        raise RuntimeError(f'No completed download found in {directory}')
    validate_original(source, record)
    atomic_json(ownership, dict(source=source_signature(source), url=record['url']))
    return source, True


def remove_owned_video(source, root, record):
    """Remove only our recorded download, and only if its identity hasn't changed."""
    directory = root/record['id']
    marker = directory/'download-owned.json'
    if source.parent.resolve() != directory.resolve() or not marker.exists():
        raise ValueError(f'Refusing to delete a video not owned by v2: {source}')
    if json.loads(marker.read_text()).get('source') != source_signature(source):
        raise ValueError(f'Video changed since download; preserving it: {source}')
    source.unlink()
    marker.unlink()
    LOG.info('Deleted completed full download after saving burp clips: %s', source)


def clip_manifest_path(record, root, args):
    config = dict(version=1, id=record['id'], threshold=args.burp_threshold,
                  width=args.width, height=args.height, fps=args.fps, encoder=args.encoder,
                  scan_seconds=args.scan_seconds, chunk_seconds=args.chunk_seconds)
    key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:20]
    return root/record['id']/f'burps-{key}.json', config


def extract_burps(record, root, args, get_model):
    manifest, config = clip_manifest_path(record, root, args)
    if manifest.exists():
        saved = json.loads(manifest.read_text())
        if saved.get('complete') and saved['config'] == config:
            clips = [Path(e['clip']) for e in saved['events']]
            if all(p.is_file() for p in clips):
                LOG.info('Reusing %d completed burp clips from broadcast %s', len(clips), record['id'])
                # Recover interruption between publishing the manifest and cleanup.
                original = local_original(root/record['id'])
                if (original and not args.keep_vods and args.scan_seconds is None
                        and (root/record['id']/'download-owned.json').exists()):
                    remove_owned_video(original, root, record)
                return clips
    source, owned = obtain_video(record, root, args)
    duration = validate_original(source, record)
    if args.scan_seconds:
        duration = min(duration, args.scan_seconds)
    scan_args = SimpleNamespace(start=0, chunk_seconds=args.chunk_seconds, work_dir=root/'analysis')
    scores, key = cached_scores(source, duration, scan_args, get_model, label=BURP_CLASS)
    events = events_from_scores(scores, 0, duration, args.burp_threshold, 0, 0, 0)
    rows, clips = [], []
    for number, (start, end, score) in enumerate(events, 1):
        clip_key = hashlib.sha256(f'{key}:{start:.9f}:{end:.9f}:{config}'.encode()).hexdigest()[:20]
        clip = root/record['id']/'Burping_eructation'/f'burp-{clip_key}.mp4'
        LOG.info('Broadcast %s: exporting burp %d/%d', record['id'], number, len(events))
        export_clip(source, clip, start, end, args)
        rows.append(dict(clip=str(clip), start=start, end=end, score=score))
        clips.append(clip)
    atomic_json(manifest, dict(complete=True, config=config, broadcast=record,
                              source=source_signature(source), events=rows))
    # A bounded test scan leaves most footage unanalyzed, so retain its source.
    if owned and not args.keep_vods and args.scan_seconds is None:
        remove_owned_video(source, root, record)
    return clips


def existing_clips(args):
    if args.clips_dir:
        roots = [args.clips_dir]
    else:
        here = Path.cwd()
        roots = [args.work_dir/args.username, here/'sound_results'/args.username, here/args.username]
        if here.name.lower() == args.username:
            roots.append(here)
    clips = {p.resolve() for root in roots if root.is_dir()
             for p in root.rglob('Burping_eructation/*.mp4') if p.is_file()}
    if not clips:
        raise ValueError('No existing burp clips found; set --clips-dir to a directory '
                         'containing Burping_eructation folders, or supply both dates')
    return sorted(clips)


def render_song(clips, args):
    # A fresh playlist per run avoids overwriting curated user playlists.
    directory = args.work_dir/args.username/'runs'
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, prefix='burps-', suffix='.xspf', delete=False) as f:
        playlist = Path(f.name)
    ns = 'http://xspf.org/ns/0/'
    root = ET.Element('playlist', xmlns=ns, version='1')
    tracks = ET.SubElement(root, 'trackList')
    for clip in clips:
        track = ET.SubElement(tracks, 'track')
        ET.SubElement(track, 'location').text = clip.resolve().as_uri()
    ET.ElementTree(root).write(playlist, encoding='utf-8', xml_declaration=True)
    argv = ['--midi', str(args.midi), '--playlist', str(playlist), '--output', str(args.output),
            '--no-labels', '--cache', str(args.work_dir/args.username/'song-cache'),
            '--width', str(args.width), '--height', str(args.height), '--fps', str(args.fps),
            '--start', str(args.start), '--octave', str(args.octave)]
    for key in ('seconds', 'tracks', 'channels'):
        value = getattr(args, key)
        if value is not None:
            argv.extend([f'--{key}', str(value)])
    if args.include_drums:
        argv.append('--include-drums')
    song.main(argv)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('username', help='Twitch username, for example emmytheaxolotl')
    p.add_argument('--from-date', type=iso_date, help='First broadcast start date, inclusive UTC')
    p.add_argument('--to-date', type=iso_date, help='Last broadcast start date, inclusive UTC')
    p.add_argument('--midi', type=Path, help='MIDI file to render (required except for --list-only)')
    p.add_argument('--output', type=Path, default=Path('burp-song-v2.mp4'))
    p.add_argument('--work-dir', type=Path, default=Path('burp-song-v2-work'))
    p.add_argument('--clips-dir', type=Path, help='Existing clip tree to use when no dates are supplied')
    p.add_argument('--reuse-dir', type=Path, help='Reuse existing full VODs here; these are never deleted')
    p.add_argument('--list-only', action='store_true', help='List matching broadcasts without downloading')
    p.add_argument('--max-vods', type=int, help='Process only first N selected broadcasts for a trial')
    p.add_argument('--burp-threshold', type=float, default=.35)
    p.add_argument('--chunk-seconds', type=float, default=60)
    p.add_argument('--scan-seconds', type=float, help='Test only the first N seconds per VOD; keep full downloads')
    p.add_argument('--keep-vods', action='store_true', help='Keep new full downloads after extraction')
    p.add_argument('--download-format', default='bv*+ba/best', help='yt-dlp format selector')
    p.add_argument('--cookies-from-browser', choices=['firefox', 'chrome', 'chromium', 'brave', 'opera', 'edge'])
    p.add_argument('--encoder', choices=['cpu', 'nvenc'], default='cpu', help='Burp clip encoder')
    p.add_argument('--start', type=float, default=0, help='Song preview start in MIDI seconds')
    p.add_argument('--seconds', type=float, help='Song preview length')
    p.add_argument('--tracks')
    p.add_argument('--channels')
    p.add_argument('--include-drums', action='store_true')
    p.add_argument('--octave', type=int, default=0)
    p.add_argument('--width', type=int, default=1280)
    p.add_argument('--height', type=int, default=720)
    p.add_argument('--fps', type=int, default=30)
    args = p.parse_args(argv)
    args.username = args.username.lower()
    if not re.fullmatch(r'[a-z0-9_]{1,25}', args.username):
        p.error('Supply a Twitch username, without a URL or @')
    if bool(args.from_date) != bool(args.to_date):
        p.error('Supply both --from-date and --to-date, or omit both for existing clips')
    if args.from_date and args.from_date > args.to_date:
        p.error('from-date must be on or before to-date')
    if args.list_only and not args.from_date:
        p.error('list-only requires a date range')
    if args.clips_dir and args.from_date:
        p.error('clips-dir is for existing-clip mode; omit the dates')
    for key in ('work_dir', 'output', 'clips_dir', 'reuse_dir', 'midi'):
        value = getattr(args, key)
        if value is not None:
            setattr(args, key, value.expanduser().resolve())
    if not args.list_only and (not args.midi or not args.midi.is_file()):
        p.error('Supply an existing --midi file')
    if args.output.suffix.lower() != '.mp4':
        p.error('output must end in .mp4')
    if not args.list_only and (args.output.exists() or args.output.with_suffix('.json').exists()):
        p.error('Output or JSON report exists; select a new --output')
    floats = [args.burp_threshold, args.chunk_seconds, args.start]
    floats += [x for x in (args.seconds, args.scan_seconds) if x is not None]
    if not all(math.isfinite(x) for x in floats):
        p.error('Numeric options must be finite')
    if not 0 < args.burp_threshold <= 1 or args.chunk_seconds < .48 or args.start < 0:
        p.error('Use threshold in (0,1], chunk-seconds >= .48, and start >= 0')
    if any(x is not None and x <= 0 for x in (args.seconds, args.scan_seconds, args.max_vods)):
        p.error('seconds, scan-seconds and max-vods must be positive')
    if min(args.width, args.height) < 32 or args.width % 2 or args.height % 2 or args.fps < 1:
        p.error('Use even dimensions >=32 and positive fps')
    if not args.from_date and (args.reuse_dir or args.scan_seconds or args.max_vods):
        p.error('reuse-dir, scan-seconds and max-vods require dates; use seconds for a song preview')
    if not args.list_only:
        # Validate the requested arrangement before spending time/bandwidth on VODs.
        try:
            tracks = set(map(int, args.tracks.split(','))) if args.tracks else None
            channels = set(map(int, args.channels.split(','))) if args.channels else None
            if channels and not channels.issubset(set(range(16))):
                raise ValueError('Channels must be in 0..15')
            if tracks and not tracks.issubset(set(range(len(song.mido.MidiFile(args.midi).tracks)))):
                raise ValueError('Track index out of range')
            notes = song.midi_notes(args.midi, tracks, args.include_drums, channels)
            if not any(n['start']+n['duration'] > args.start for n in notes):
                raise ValueError('No MIDI notes in the requested tracks/channels/time range')
        except (ValueError, EOFError, OSError) as exc:
            p.error(f'Invalid MIDI selection: {exc}')
    return args


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    args = parse_args(argv)
    for executable in ('ffmpeg', 'ffprobe'):
        if not shutil.which(executable):
            raise RuntimeError(f'{executable} is required on PATH')
    if args.from_date:
        records = discover_broadcasts(args)
        if not records:
            raise ValueError('No available archived broadcasts match that date range')
        root = args.work_dir/args.username
        run_manifest = root/'broadcasts'/f'{args.from_date}_{args.to_date}.json'
        atomic_json(run_manifest, dict(username=args.username, from_date=str(args.from_date),
                                       to_date=str(args.to_date), broadcasts=records))
        for record in records:
            print(f"{record['date']} {record['url']} {record['title']}", flush=True)
        if args.list_only:
            return
        loaded = []

        def get_model():
            if not loaded:
                loaded.append(load_yamnet(BURP_CLASS))
            return loaded[0]

        clips = []
        for record in records:
            clips.extend(extract_burps(record, root, args, get_model))
        if not clips:
            raise ValueError('No burps met the threshold; detection records saved, no song rendered')
    else:
        clips = existing_clips(args)
        LOG.info('Using %d existing clips; Twitch discovery, downloads and detection skipped', len(clips))
    render_song(clips, args)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit('Interrupted. Completed clips are retained; unfinished downloads are preserved for retry.')
    except (RuntimeError, ValueError, OSError, ImportError) as exc:
        raise SystemExit(str(exc))
