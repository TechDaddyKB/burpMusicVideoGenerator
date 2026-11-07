from datetime import date, datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

import burp_song_v2 as v2
from sneeze_compilation import command, source_signature


class FakeScores:
    def __init__(self, data):
        self.data = data

    def numpy(self):
        return self.data


def constant_model(audio):
    count = max(0, 1+(len(audio)-15600)//7680)
    return FakeScores(np.full((count, 1), .9)), None, None


class V2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = tempfile.TemporaryDirectory()
        cls.fixture = Path(cls.fixture_dir.name)/'fixture.mp4'
        command(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                 'color=c=blue:size=320x180:rate=24:duration=3', '-f', 'lavfi', '-i',
                 'sine=frequency=220:sample_rate=48000:duration=3',
                 '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(cls.fixture)])

    @classmethod
    def tearDownClass(cls):
        cls.fixture_dir.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'work'/'streamer'
        self.root.mkdir(parents=True)
        self.record = dict(id='123', username='streamer', date='2026-10-01',
                           url='https://www.twitch.tv/videos/123', title='fixture', duration=3)
        self.args = SimpleNamespace(username='streamer', burp_threshold=.35,
                                    width=320, height=180, fps=24, encoder='cpu',
                                    chunk_seconds=1.92, scan_seconds=None, keep_vods=False,
                                    reuse_dir=None, cookies_from_browser=None,
                                    download_format='bv*+ba/best')

    def owned_source(self):
        directory = self.root/'123'
        directory.mkdir(exist_ok=True)
        source = directory/'original.mp4'
        shutil.copyfile(self.fixture, source)
        v2.atomic_json(directory/'download-owned.json', dict(source=source_signature(source)))
        return source

    def test_date_selection_inclusive_utc_and_deduplicated(self):
        self.args.from_date, self.args.to_date = date(2026,10,1), date(2026,10,31)
        self.args.max_vods = None
        dates = {1:(2026,9,30,23,59), 2:(2026,10,1,0,0),
                 3:(2026,10,31,23,59), 4:(2026,11,1,0,0)}

        class Client:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def extract_info(self, url, download=False):
                if self.options.get('extract_flat'):
                    return {'entries':[{'id': f'v{i}'} for i in [4,3,3,2,1]]}
                identity = int(url.rsplit('/',1)[-1])
                stamp = datetime(*dates[identity], tzinfo=timezone.utc).timestamp()
                return dict(id=f'v{identity}', timestamp=stamp, uploader_id='streamer')

        with patch('yt_dlp.YoutubeDL', side_effect=Client):
            records = v2.discover_broadcasts(self.args)
        self.assertEqual([r['id'] for r in records], ['2','3'])

    def test_unknown_date_and_wrong_owner_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'No broadcast date'):
            v2.public_record(dict(id='123'), 'streamer')
        with self.assertRaisesRegex(ValueError, 'belongs to'):
            v2.public_record(dict(id='123', uploader_id='other'), 'streamer')

    def test_success_deletes_owned_full_video_after_clips_and_record(self):
        source = self.owned_source()
        with patch.object(v2, 'obtain_video', return_value=(source, True)):
            clips = v2.extract_burps(self.record, self.root, self.args, lambda: (constant_model,0))
        self.assertTrue(clips and all(p.is_file() for p in clips))
        self.assertFalse(source.exists())
        manifest, _ = v2.clip_manifest_path(self.record, self.root, self.args)
        self.assertTrue(json.loads(manifest.read_text())['complete'])
        with patch.object(v2, 'obtain_video', side_effect=AssertionError('Must reuse clips')):
            self.assertEqual(v2.extract_burps(self.record, self.root, self.args, None), clips)

    def test_failed_clip_export_preserves_full_video(self):
        source = self.owned_source()
        with patch.object(v2, 'obtain_video', return_value=(source, True)), \
             patch.object(v2, 'export_clip', side_effect=RuntimeError('test encoder failure')):
            with self.assertRaisesRegex(RuntimeError, 'test encoder failure'):
                v2.extract_burps(self.record, self.root, self.args, lambda: (constant_model,0))
        self.assertTrue(source.exists())
        manifest, _ = v2.clip_manifest_path(self.record, self.root, self.args)
        self.assertFalse(manifest.exists())

    def test_partial_scan_and_its_cached_retry_keep_source(self):
        source = self.owned_source()
        self.args.scan_seconds = 1.5
        with patch.object(v2, 'obtain_video', return_value=(source, True)):
            v2.extract_burps(self.record, self.root, self.args, lambda: (constant_model,0))
        self.assertTrue(source.exists())
        v2.extract_burps(self.record, self.root, self.args, None)
        self.assertTrue(source.exists())

    def test_keep_vods_and_reused_sources_are_preserved(self):
        source = self.owned_source()
        self.args.keep_vods = True
        with patch.object(v2, 'obtain_video', return_value=(source, True)):
            v2.extract_burps(self.record, self.root, self.args, lambda: (constant_model,0))
        self.assertTrue(source.exists())
        # An original without our ownership marker is never taken over.
        (source.parent/'download-owned.json').unlink()
        self.assertEqual(v2.obtain_video(self.record, self.root, self.args), (source,False))

    def test_cleanup_recovery_and_changed_source_guard(self):
        source = self.owned_source()
        with source.open('ab') as f:
            f.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed since download'):
            v2.remove_owned_video(source, self.root, self.record)
        self.assertTrue(source.exists())

    def test_downloader_options_and_ownership(self):
        def download(urls):
            directory = self.root/'123'
            shutil.copyfile(self.fixture, directory/'original.mp4')
            return 0

        with patch('yt_dlp.YoutubeDL') as factory:
            factory.return_value.__enter__.return_value.download.side_effect = download
            source, owned = v2.obtain_video(self.record, self.root, self.args)
        self.assertTrue(owned and source.is_file())
        options = factory.call_args.args[0]
        self.assertTrue(options['continuedl'])
        self.assertFalse(options['skip_unavailable_fragments'])
        self.assertFalse(options['overwrites'])

    def test_no_date_mode_makes_no_twitch_or_model_calls(self):
        midi = Path(self.tmp.name)/'fixture.mid'
        mid = v2.song.mido.MidiFile()
        track = v2.song.mido.MidiTrack()
        track.extend([v2.song.mido.Message('note_on', note=60),
                      v2.song.mido.Message('note_off', note=60, time=480)])
        mid.tracks.append(track)
        mid.save(midi)
        directory = Path(self.tmp.name)/'clips'/'Burping_eructation'
        directory.mkdir(parents=True)
        shutil.copyfile(self.fixture, directory/'burp.mp4')
        with patch.object(v2, 'discover_broadcasts', side_effect=AssertionError('No network')), \
             patch.object(v2, 'load_yamnet', side_effect=AssertionError('No detector')), \
             patch.object(v2.song, 'main') as renderer:
            v2.main(['streamer','--midi',str(midi),'--clips-dir',str(directory.parent),
                     '--work-dir',str(Path(self.tmp.name)/'output'),
                     '--output',str(Path(self.tmp.name)/'song.mp4')])
        self.assertIn('--no-labels', renderer.call_args.args[0])

    def test_real_chord_video_draws_no_tile_text(self):
        from scipy.io import wavfile
        cache = Path(self.tmp.name)/'cache'
        cache.mkdir()
        audio = Path(self.tmp.name)/'audio.wav'
        wavfile.write(audio, 24000, np.zeros(12000,dtype=np.float32))
        clip = dict(key='fixture',path=str(self.fixture),offset=0,duration=.5)
        notes = [dict(start=0,duration=.5,track=0,pitch=p,clip=0) for p in [60,64]]
        args = SimpleNamespace(cache=cache,width=320,height=180,fps=24,no_labels=True)
        output = Path(self.tmp.name)/'clean.mp4'
        with patch.object(v2.song.cv2, 'putText', side_effect=AssertionError('No overlay allowed')):
            v2.song.render_video(notes,[clip],audio,output,.5,args)
        self.assertAlmostEqual(v2.probe(output), .5, delta=.08)
        cap = v2.song.cv2.VideoCapture(str(output))
        ok, frame = cap.read()
        cap.release()
        self.assertTrue(ok)
        # A two-tile wide grid leaves a black band where labels used to be drawn.
        self.assertLess(int(frame[:17].max()), 15)


if __name__ == '__main__':
    unittest.main()
