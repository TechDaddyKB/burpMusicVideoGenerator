#!/usr/bin/env python3
"""Find candidate sneezes in downloaded full videos and join them into an MP4."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import numpy as np

LOG = logging.getLogger('sneeze_compilation')
SAMPLE_RATE = 16000
HOP_SAMPLES = 7680
HOP_SECONDS = HOP_SAMPLES / SAMPLE_RATE
# 0.96 s of mel features, with the final 25 ms STFT window extending the waveform.
WINDOW_SECONDS = .975
MODEL_URL = 'https://tfhub.dev/google/yamnet/1'
VIDEO_SUFFIXES = {'.mp4', '.mkv', '.webm', '.ts', '.mov'}


def command(args, **kwargs):
    result = subprocess.run(args, stderr=subprocess.PIPE, **kwargs)
    if result.returncode:
        error = result.stderr.decode(errors='replace')[-3000:]
        raise RuntimeError(f'{args[0]} exited {result.returncode}: {error}')
    return result


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
        json.dump(value, f, indent=2)
        f.write('\n')
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def discover(inputs):
    """Directories select only original.*, never old short clips/compilations."""
    sources = set()
    for value in inputs:
        path = Path(value).expanduser().resolve()
        if path.is_dir():
            sources.update(p.resolve() for p in path.rglob('original.*')
                           if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES)
        elif path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES:
            sources.add(path)
        else:
            raise ValueError(f'Not a video or input directory: {path}')
    if not sources:
        raise ValueError('No full videos found. Directories are searched for original.*; '
                         'pass other video filenames explicitly.')
    return sorted(sources)


def probe(path):
    raw = command(['ffprobe', '-v', 'error', '-show_entries',
                   'format=duration:stream=codec_type', '-of', 'json', str(path)],
                  stdout=subprocess.PIPE).stdout
    info = json.loads(raw)
    streams = {s['codec_type'] for s in info['streams']}
    if not {'video', 'audio'}.issubset(streams):
        raise ValueError(f'Video and audio streams required: {path}')
    duration = float(info['format']['duration'])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f'Invalid duration: {path}')
    return duration


def load_yamnet(label='Sneeze'):
    # Existing detector environment already contains these optional dependencies.
    os.environ.setdefault('TF_USE_LEGACY_KERAS', '1')
    os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '2')
    import tensorflow_hub as hub
    LOG.info('Loading YAMNet (weights download on first use if not cached)')
    model = hub.load(MODEL_URL)
    with open(model.class_map_path().numpy().decode(), encoding='utf-8') as f:
        classes = list(csv.DictReader(f))
    indices = [i for i, item in enumerate(classes) if item['display_name'] == label]
    if len(indices) != 1:
        raise ValueError(f'Model has no unique {label} class')
    return model, indices[0]


def read_up_to(stream, count):
    data = bytearray()
    while len(data) < count:
        part = stream.read(count-len(data))
        if not part:
            break
        data.extend(part)
    return data


def score_blocks(source, model, class_index, start, duration, core_samples, resume_samples=0):
    """Hop-aligned blocks with future context; yield each score exactly once.

    Every block owns core_samples of the timeline. The extra three hops are
    lookahead, preventing artificial zero-padding at internal chunk boundaries.
    """
    remaining = duration-resume_samples/SAMPLE_RATE
    if remaining <= 0:
        return
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-ss', f'{start+resume_samples/SAMPLE_RATE:.9f}',
           '-i', str(source), '-t', f'{remaining:.9f}', '-map', '0:a:0', '-vn',
           '-ac', '1', '-ar', str(SAMPLE_RATE), '-f', 'f32le', '-']
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err)
        carry = bytearray()
        offset = resume_samples
        try:
            while offset/SAMPLE_RATE < duration:
                target = (core_samples+3*HOP_SAMPLES)*4
                carry.extend(read_up_to(proc.stdout, target-len(carry)))
                if not carry:
                    break
                if len(carry) % 4:
                    raise RuntimeError('Truncated float audio from FFmpeg')
                waveform = np.frombuffer(carry, dtype='<f4').copy()
                count = min(core_samples//HOP_SAMPLES,
                            math.ceil(min(len(waveform), duration*SAMPLE_RATE-offset)/HOP_SAMPLES))
                if len(waveform) < core_samples and offset+len(waveform) < duration*SAMPLE_RATE-SAMPLE_RATE//10:
                    raise RuntimeError('Audio ended before the requested scan interval')
                # YAMNet does not return every trailing hop automatically. Complete
                # the last frame explicitly, then keep only real timeline starts.
                needed = (count-1)*HOP_SAMPLES+round(WINDOW_SECONDS*SAMPLE_RATE)
                if len(waveform) < needed:
                    waveform = np.pad(waveform, (0, needed-len(waveform)))
                scores, _, _ = model(waveform)
                values = np.asarray(scores.numpy())[:, class_index]
                if len(values) < count:
                    raise RuntimeError('Model returned fewer frames than expected')
                if not np.all(np.isfinite(values[:count])):
                    raise RuntimeError('Model returned invalid scores')
                next_offset = min(offset+core_samples, round(duration*SAMPLE_RATE))
                yield next_offset, values[:count].tolist()
                offset = next_offset
                carry = carry[core_samples*4:]
            proc.stdout.close()
            status = proc.wait()
            if status:
                err.seek(0)
                raise RuntimeError(f'FFmpeg decoder exited {status}: '
                                   f'{err.read().decode(errors="replace")[-3000:]}')
            if offset < round(duration*SAMPLE_RATE)-SAMPLE_RATE//10:
                raise RuntimeError('Audio ended before the requested scan interval')
        finally:
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
            proc.wait()


def source_signature(path):
    stat = path.stat()
    return dict(path=str(path), size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def cached_scores(source, duration, args, get_model, label='Sneeze'):
    core = max(1, int(args.chunk_seconds/HOP_SECONDS))*HOP_SAMPLES
    config = dict(version=1, source=source_signature(source), model=MODEL_URL,
                  label=label, start=args.start, duration=duration, core_samples=core)
    key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:24]
    path = args.work_dir/'scores'/f'{key}.json'
    data = dict(config=config, next_sample=0, complete=False, scores=[])
    if path.exists():
        data = json.loads(path.read_text())
        if data['config'] != config:
            raise ValueError(f'Score cache mismatch: {path}')
    if not data['complete']:
        if data['next_sample'] >= round(duration*SAMPLE_RATE):
            # A decoder error/interruption after the final checkpoint must rerun
            # that block, rather than treating an unchecked decoder exit as success.
            data['next_sample'] = max(0, ((round(duration*SAMPLE_RATE)-1)//core)*core)
            data['scores'] = data['scores'][:data['next_sample']//HOP_SAMPLES]
        model, index = get_model()
        for next_sample, scores in score_blocks(source, model, index, args.start, duration,
                                                core, data['next_sample']):
            data['scores'].extend(scores)
            data['next_sample'] = next_sample
            # Mark complete only after the decoder has exited successfully below.
            atomic_json(path, data)
            LOG.info('%s: %.1f / %.1f minutes scanned', source.parent.name,
                     next_sample/SAMPLE_RATE/60, duration/60)
        data['complete'] = True
        atomic_json(path, data)
    else:
        LOG.info('Reusing scanned scores: %s', source)
    return data['scores'], key


def merge_intervals(events, gap=0):
    merged = []
    for start, end, score in sorted(events):
        if merged and start <= merged[-1][1]+gap+1e-7:
            merged[-1][1] = max(merged[-1][1], end)
            merged[-1][2] = max(merged[-1][2], score)
        else:
            merged.append([start, end, score])
    return merged


def events_from_scores(scores, start, duration, threshold, before, after, gap):
    end_of_scan = start+duration
    frames = [(start+i*HOP_SECONDS, min(end_of_scan, start+i*HOP_SECONDS+WINDOW_SECONDS), float(s))
              for i, s in enumerate(scores) if s >= threshold and i*HOP_SECONDS < duration]
    events = merge_intervals(frames, gap)
    # Merge again after padding, so repeated sneezes don't replay the same footage.
    padded = [(max(start, a-before), min(end_of_scan, b+after), score) for a,b,score in events]
    return merge_intervals(padded)


def export_clip(source, target, start, end, args):
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix='.encode-') as tmp:
        temp = Path(tmp)/'clip.mp4'
        codec = (['-c:v', 'h264_nvenc', '-preset', 'p4', '-cq', '21', '-b:v', '0']
                 if args.encoder == 'nvenc' else
                 ['-c:v', 'libx264', '-preset', 'fast', '-crf', '21'])
        command(['ffmpeg', '-nostdin', '-v', 'error', '-ss', f'{start:.9f}', '-i', str(source),
                 '-t', f'{end-start:.9f}', '-map', '0:v:0', '-map', '0:a:0',
                 '-vf', f'scale={args.width}:{args.height}:force_original_aspect_ratio=decrease,'
                        f'pad={args.width}:{args.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={args.fps}',
                 *codec, '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '160k',
                 '-ar', '48000', '-ac', '2', '-map_metadata', '-1',
                 '-movflags', '+faststart', str(temp)])
        probe(temp)
        os.link(temp, target)


def concatenate(clips, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.sneeze-join-') as tmp:
        directory = Path(tmp)
        # Safe fixed names keep the concat list independent of input path quoting.
        for i, clip in enumerate(clips):
            (directory/f'clip-{i:06}.mp4').symlink_to(clip.resolve())
        listing = directory/'clips.txt'
        listing.write_text(''.join(f"file 'clip-{i:06}.mp4'\n" for i in range(len(clips))))
        temp = directory/'compilation.mp4'
        command(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'concat', '-safe', '1',
                 '-i', str(listing), '-map', '0:v:0', '-map', '0:a:0', '-c', 'copy',
                 '-movflags', '+faststart', str(temp)])
        probe(temp)
        os.link(temp, output)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('inputs', nargs='*', default=['.'], help='Directories containing original.* or video files')
    p.add_argument('--output', type=Path, default=Path('sneeze-compilation.mp4'))
    p.add_argument('--work-dir', type=Path, default=Path('sneeze_results'))
    p.add_argument('--threshold', type=float, default=.70, help='YAMNet score cutoff (default .70)')
    p.add_argument('--before', type=float, default=.5, help='Seconds of lead-in')
    p.add_argument('--after', type=float, default=.75, help='Seconds of follow-through')
    p.add_argument('--merge-gap', type=float, default=.3, help='Merge nearby detections before padding')
    p.add_argument('--chunk-seconds', type=float, default=60, help='Bounded inference chunk size')
    p.add_argument('--start', type=float, default=0, help='Start time within each source')
    p.add_argument('--seconds', type=float, help='Scan at most this many seconds per source')
    p.add_argument('--max-videos', type=int, help='Scan only first N videos for a trial')
    p.add_argument('--detect-only', action='store_true', help='Save timestamps without rendering video')
    p.add_argument('--width', type=int, default=1280)
    p.add_argument('--height', type=int, default=720)
    p.add_argument('--fps', type=int, default=30)
    p.add_argument('--encoder', choices=['cpu', 'nvenc'], default='cpu')
    args = p.parse_args()
    numeric = [args.threshold, args.before, args.after, args.merge_gap, args.chunk_seconds, args.start]
    if not all(math.isfinite(x) for x in numeric) or (args.seconds is not None and not math.isfinite(args.seconds)):
        p.error('Numeric options must be finite')
    if not 0 < args.threshold <= 1:
        p.error('threshold must be greater than 0 and at most 1')
    if min(args.before, args.after, args.merge_gap, args.start) < 0:
        p.error('Padding, merge gap and start must be nonnegative')
    if args.chunk_seconds < HOP_SECONDS or (args.seconds is not None and args.seconds <= 0):
        p.error('chunk-seconds must be >= .48; seconds must be positive')
    if args.max_videos is not None and args.max_videos < 1:
        p.error('max-videos must be positive')
    if min(args.width, args.height) < 32 or args.width % 2 or args.height % 2 or args.fps < 1:
        p.error('Use positive fps and even dimensions of at least 32')
    args.output = args.output.expanduser().resolve()
    args.work_dir = args.work_dir.expanduser().resolve()
    if args.output.suffix.lower() != '.mp4':
        p.error('output must end with .mp4')
    if not args.detect_only and args.output.exists():
        p.error('Output exists; choose a new --output (sources and existing videos are never overwritten)')
    return args


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    args = parse_args()
    for executable in ('ffmpeg', 'ffprobe'):
        if not shutil.which(executable):
            raise RuntimeError(f'{executable} is required on PATH')
    sources = discover(args.inputs)
    if args.max_videos:
        sources = sources[:args.max_videos]
    LOG.info('Found %d full video(s); score threshold %.3f', len(sources), args.threshold)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    loaded = []

    def get_model():
        if not loaded:
            loaded.append(load_yamnet())
        return loaded[0]

    # Unique run reports preserve earlier review/editing work across reruns.
    with tempfile.NamedTemporaryFile(prefix='detections-', suffix='.csv', dir=args.work_dir,
                                     mode='w', newline='', delete=False) as f:
        report = Path(f.name)
        writer = csv.DictWriter(f, fieldnames=['source', 'start_seconds', 'end_seconds',
                                              'yamnet_score', 'clip'])
        writer.writeheader()
        clips, records = [], []
        for source in sources:
            full_duration = probe(source)
            duration = full_duration-args.start
            if duration <= 0:
                LOG.info('Skipping %s: start is past end of video', source)
                continue
            if args.seconds is not None:
                duration = min(duration, args.seconds)
            scores, source_key = cached_scores(source, duration, args, get_model)
            events = events_from_scores(scores, args.start, duration, args.threshold,
                                        args.before, args.after, args.merge_gap)
            LOG.info('%s: %d candidate sneeze segment(s)', source.parent.name, len(events))
            for i, (start, end, score) in enumerate(events, 1):
                settings = f'{source_key}:{start:.9f}:{end:.9f}:{args.width}:{args.height}:{args.fps}:{args.encoder}:v1'
                clip_key = hashlib.sha256(settings.encode()).hexdigest()[:20]
                clip = args.work_dir/'clips'/f'sneeze-{clip_key}.mp4'
                row = dict(source=str(source), start_seconds=f'{start:.6f}', end_seconds=f'{end:.6f}',
                           yamnet_score=f'{score:.6f}', clip=str(clip))
                writer.writerow(row)
                f.flush()
                records.append(row)
                if not args.detect_only:
                    LOG.info('Exporting %s %d/%d: %.2f–%.2f seconds', source.parent.name, i, len(events), start, end)
                    export_clip(source, clip, start, end, args)
                    clips.append(clip)
    atomic_json(report.with_suffix('.json'), dict(
        sources=[str(s) for s in sources], threshold=args.threshold,
        before=args.before, after=args.after, merge_gap=args.merge_gap, start=args.start,
        seconds=args.seconds, detect_only=args.detect_only, output=str(args.output), events=records))
    LOG.info('Detection report: %s', report)
    if args.detect_only:
        LOG.info('Detection complete: %d candidate segment(s); no video rendered', len(records))
    elif not clips:
        LOG.info('No candidate sneezes met the threshold; no compilation created')
    else:
        concatenate(clips, args.output)
        LOG.info('Created %s with %d candidate segment(s)', args.output, len(clips))


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit('Interrupted; rerun the same command to resume completed scan chunks and clips.')
    except (RuntimeError, ValueError, OSError, ImportError) as exc:
        raise SystemExit(str(exc))
