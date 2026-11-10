from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import mido
import numpy as np
from scipy.io import wavfile

import burp_song as bs


BURP = 53  # 'Burping, eructation' in YAMNet's list
COUGH = 42


def constant_model(audio):
    """Stand-in for YAMNet: one frame per 0.48 s step; every frame a confident burp, a faint cough."""
    count = max(0, 1+(len(audio)-bs.WINDOW_SAMPLES)//bs.HOP_SAMPLES)
    scores = np.zeros((count, 521))
    scores[:, BURP], scores[:, COUGH] = .9, .2
    return scores


def save_midi(path, *tracks):
    mid = mido.MidiFile(ticks_per_beat=480)
    mid.tracks.extend(mido.MidiTrack(t) for t in tracks)
    mid.save(path)
    return path


class FixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = tempfile.TemporaryDirectory()
        cls.fixture = Path(cls.fixture_dir.name)/'fixture.mp4'
        bs.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                'color=c=blue:size=320x180:rate=24:duration=3', '-f', 'lavfi', '-i',
                'sine=frequency=220:sample_rate=48000:duration=3',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(cls.fixture)])

    @classmethod
    def tearDownClass(cls):
        cls.fixture_dir.cleanup()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)


class MidiAndAudioTests(FixtureTest):
    def test_tempo_change_and_overlapping_notes(self):
        tempo = [mido.MetaMessage('set_tempo', tempo=500000),
                 mido.MetaMessage('set_tempo', tempo=1000000, time=480)]
        notes = [mido.Message('note_on', note=60, velocity=90),
                 mido.Message('note_on', note=64, velocity=90, time=240),
                 mido.Message('note_off', note=60, time=720),
                 mido.Message('note_off', note=64)]
        result = bs.midi_notes(save_midi(self.tmp/'a.mid', tempo, notes))
        self.assertEqual(len(result), 2)
        self.assertAlmostEqual(result[0]['duration'], 1.5)
        self.assertAlmostEqual(result[1]['start'], .25)
        self.assertAlmostEqual(result[1]['duration'], 1.25)

    def test_sustain_pedal_and_drums_skipped_by_default(self):
        track = [mido.Message('control_change', control=64, value=127),
                 mido.Message('note_on', note=60, velocity=100),
                 mido.Message('note_on', channel=9, note=36, velocity=100),
                 mido.Message('note_off', note=60, time=480),
                 mido.Message('note_off', channel=9, note=36),
                 mido.Message('control_change', control=64, value=0, time=480)]
        path = save_midi(self.tmp/'b.mid', track)
        result = bs.midi_notes(path)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]['duration'], 1)
        self.assertEqual(len(bs.midi_notes(path, include_drums=True)), 2)

    def test_repeated_same_pitch_notes(self):
        track = [mido.Message('note_on', note=60),
                 mido.Message('note_on', note=60, time=120),
                 mido.Message('note_off', note=60, time=120),
                 mido.Message('note_on', note=60, velocity=0, time=120)]
        notes = bs.midi_notes(save_midi(self.tmp/'c.mid', track))
        self.assertEqual([round(n['duration'], 3) for n in notes], [.25, .25])

    def test_pitch_estimate(self):
        audio = .5*np.sin(2*math.pi*220*np.arange(bs.SR)/bs.SR)
        self.assertAlmostEqual(bs.estimate_pitch(audio), 57, delta=.15)
        self.assertIsNone(bs.estimate_pitch(np.zeros(bs.SR)))

    def test_clip_choice_prefers_similar_length(self):
        clips = [dict(duration=.2, pitch=60), dict(duration=1., pitch=60)]
        self.assertIs(bs.select_clip(dict(duration=.22, pitch=60), clips), clips[0])

    def test_pitch_shift_and_stretch(self):
        path = self.tmp/'tone.wav'
        wavfile.write(path, bs.SR, (.5*np.sin(2*math.pi*220*np.arange(bs.SR//2)/bs.SR)).astype(np.float32))
        clip = dict(key='tone', wav=str(path), duration=.5, pitch=57)
        audio = bs.note_audio(dict(duration=.25, pitch=69), clip, self.tmp)
        self.assertEqual(len(audio), bs.SR//4)
        interior = audio[1000:-1000]
        peak = np.argmax(np.abs(np.fft.rfft(interior*np.hanning(len(interior)))))
        self.assertAlmostEqual(peak*bs.SR/len(interior), 440, delta=12)

    def test_extreme_notes_stay_in_rubberband_limits(self):
        # Issue #5: a 20 ms note on a 2.5 s clip needed tempo 127 (Rubber Band allows 0.01 to 100).
        path = self.tmp/'long.wav'
        wavfile.write(path, bs.SR, (.5*np.sin(2*math.pi*220*np.arange(5*bs.SR//2)/bs.SR)).astype(np.float32))
        clip = dict(key='long', wav=str(path), duration=2.5, pitch=57)
        for duration, pitch in [(.0196, 60), (.00055, 60), (.0001, 60), (300, 60), (1, 127), (1, 0)]:
            with self.subTest(duration=duration, pitch=pitch):
                audio = bs.note_audio(dict(duration=duration, pitch=pitch), clip, self.tmp)
                self.assertEqual(len(audio), round(duration*bs.SR))
        self.assertEqual(len(bs.note_audio(dict(duration=1e-6, pitch=60), clip, self.tmp)), 0)

    def test_drums_get_clips_by_register_at_their_own_speed(self):
        clips = [dict(duration=.4, pitch=40), dict(duration=.4, pitch=60), dict(duration=.4, pitch=80)]

        def drum(start, key, duration=.01):
            return dict(start=start, duration=duration, track=0, channel=9, pitch=key, velocity=100)
        melody = dict(start=0, duration=1, track=0, channel=0, pitch=60, velocity=100)
        kick, snare, cymbal, again, double = drum(0, 36), drum(0, 38), drum(0, 49), drum(.1, 36), drum(.1, 36)
        notes = [melody, kick, snare, cymbal, again, double]
        bs.place_drums(notes, clips)
        self.assertEqual([kick['clip'], snare['clip'], cymbal['clip']], [0, 1, 2])
        self.assertEqual(len(notes), 5)  # the doubled kick plays once
        self.assertNotIn('clip', melody)
        self.assertAlmostEqual(kick['duration'], .1)    # until the next kick
        self.assertAlmostEqual(cymbal['duration'], .4)  # the whole clip, not the 10 ms written
        self.assertEqual((cymbal['pitch'], cymbal['tempo']), (80, 1.0))

    def test_make_song_with_drums(self):
        clip = self.tmp/'clips'/'burp-0001.mp4'
        clip.parent.mkdir()
        shutil.copyfile(self.fixture, clip)
        midi = save_midi(self.tmp/'drums.mid', [
            mido.Message('note_on', note=60), mido.Message('note_on', channel=9, note=36),
            mido.Message('note_off', channel=9, note=36, time=10), mido.Message('note_off', note=60, time=470)])
        output = self.tmp/'drums.mp4'
        bs.make_song([clip], midi, output, self.tmp/'cache', drums=True)
        self.assertTrue(output.stat().st_size > 0)

    def test_make_song_end_to_end(self):
        clip = self.tmp/'clips'/'burp-0001.mp4'
        clip.parent.mkdir()
        shutil.copyfile(self.fixture, clip)
        midi = save_midi(self.tmp/'song.mid', [
            mido.Message('note_on', note=60), mido.Message('note_on', note=64),
            mido.Message('note_off', note=60, time=480), mido.Message('note_off', note=64)])
        output = self.tmp/'song.mp4'
        bs.make_song([clip], midi, output, self.tmp/'cache')
        info = json.loads(bs.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_type,width',
                                  '-of', 'json', str(output)], capture_output=False,
                                 stdout=bs.subprocess.PIPE).stdout)
        self.assertAlmostEqual(float(info['format']['duration']), .5, delta=.1)
        self.assertEqual({s['codec_type'] for s in info['streams']}, {'video', 'audio'})
        self.assertEqual(max(s.get('width', 0) for s in info['streams']), bs.WIDTH)


class DetectionTests(FixtureTest):
    def test_find_sounds_merges_overlapping_windows(self):
        burps = bs.find_sounds([.1, .9, .8, .1, .1, .5], .35)
        self.assertEqual(len(burps), 2)
        self.assertAlmostEqual(burps[0][0], bs.HOP_SECONDS)
        self.assertAlmostEqual(burps[0][1], 2*bs.HOP_SECONDS+bs.WINDOW_SECONDS)
        self.assertEqual(burps[0][2], .9)
        self.assertEqual(bs.find_sounds([.1, .2], .35), [])

    def test_scores_cover_whole_video_across_chunks(self):
        whole = bs.sound_scores(self.fixture, constant_model, BURP, chunk_seconds=60)
        chunked = bs.sound_scores(self.fixture, constant_model, BURP, chunk_seconds=.96)
        self.assertEqual(len(whole), math.ceil(3/bs.HOP_SECONDS))
        self.assertEqual(len(chunked), len(whole))

    def test_several_sounds_use_the_highest_score(self):
        cough = bs.sound_scores(self.fixture, constant_model, [COUGH])
        both = bs.sound_scores(self.fixture, constant_model, [COUGH, BURP])
        self.assertTrue(all(abs(s-.2) < 1e-6 for s in cough))
        self.assertTrue(all(abs(s-.9) < 1e-6 for s in both))

    def test_sound_names_match_and_make_stable_folders(self):
        names = bs.sound_names()
        self.assertEqual(len(names), 521)
        self.assertEqual(names[BURP], bs.DEFAULT_SOUND)
        self.assertEqual(bs.resolve_sounds(['cough', 'BURPING, ERUCTATION', 'Cough']),
                         ['Burping, eructation', 'Cough'])
        with self.assertRaisesRegex(ValueError, 'Unknown sound'):
            bs.resolve_sounds(['Belch of doom'])
        self.assertEqual(bs.sounds_folder(['Cough', 'Burping, eructation']), 'burping-eructation+cough')
        self.assertLessEqual(len(bs.sounds_folder(names[:40])), 80)


class TwitchTests(FixtureTest):
    def test_list_vods_filters_dates_dedupes_and_skips_live(self):
        dates = {1: (2026, 9, 30, 23, 59), 2: (2026, 10, 1, 0, 0), 3: (2026, 10, 31, 23, 59),
                 4: (2026, 11, 1, 0, 0), 5: (2026, 10, 15, 0, 0)}

        class Client:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                pass

            def extract_info(self, url, download=False):
                if self.options.get('extract_flat'):
                    return {'entries': [{'id': f'v{i}'} for i in [5, 4, 3, 3, 2, 1]]}
                vod = int(url.rsplit('/', 1)[-1])
                stamp = datetime(*dates[vod], tzinfo=timezone.utc).timestamp()
                return dict(id=f'v{vod}', timestamp=stamp, is_live=vod == 5)

        with patch('yt_dlp.YoutubeDL', side_effect=Client):
            selected = bs.list_vods('streamer', date(2026, 10, 1), date(2026, 10, 31))
            everything = bs.list_vods('streamer')
        self.assertEqual([v['id'] for v in selected], ['2', '3'])
        self.assertEqual([v['id'] for v in everything], ['1', '2', '3', '4'])

    def vod(self):
        return dict(id='123', date='2026-10-01', title='test', url='https://www.twitch.tv/videos/123')

    def fake_download(self, vod, folder, cookies=None):
        folder.mkdir(parents=True, exist_ok=True)
        return Path(shutil.copyfile(self.fixture, folder/f"{vod['id']}.mp4"))

    def test_download_vod_passes_options_to_yt_dlp(self):
        fixture, seen = self.fixture, {}

        class Client:
            def __init__(self, options):
                seen.update(options)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                pass

            def download(self, urls):
                shutil.copyfile(fixture, seen['outtmpl'].replace('%(ext)s', 'mp4'))
                return 0

        with patch('yt_dlp.YoutubeDL', side_effect=Client):
            path = bs.download_vod(self.vod(), self.tmp/'vods', 'firefox')
        self.assertEqual(path.name, '123.mp4')
        self.assertFalse(seen['quiet'])
        self.assertEqual(seen['cookiesfrombrowser'], ('firefox',))

    def test_process_vod_saves_clips_then_deletes_vod(self):
        root = self.tmp/'work'
        with patch.object(bs, 'download_vod', side_effect=self.fake_download), \
             patch.object(bs, 'load_yamnet', return_value=(constant_model, bs.sound_names())):
            clips = bs.process_vod(self.vod(), root, .35)
        self.assertTrue(clips and all(c.is_file() for c in clips))
        self.assertFalse((root/'vods'/'123.mp4').exists())
        self.assertTrue((root/'clips'/'burping-eructation'/'123'/'done.json').exists())
        with patch.object(bs, 'download_vod', side_effect=AssertionError('must not download again')):
            self.assertEqual(bs.process_vod(self.vod(), root, .35), clips)

    def test_other_sounds_get_their_own_clips(self):
        root = self.tmp/'work'
        with patch.object(bs, 'download_vod', side_effect=self.fake_download), \
             patch.object(bs, 'load_yamnet', return_value=(constant_model, bs.sound_names())):
            coughs = bs.process_vod(self.vod(), root, .35, ['Cough'])
            both = bs.process_vod(self.vod(), root, .35, ['Burping, eructation', 'Cough'])
        self.assertEqual(coughs, [])  # the stand-in's coughs are too faint
        self.assertTrue(both and all('burping-eructation+cough' in str(c) for c in both))

    def test_failed_clip_keeps_vod_for_retry(self):
        root = self.tmp/'work'
        with patch.object(bs, 'download_vod', side_effect=self.fake_download), \
             patch.object(bs, 'load_yamnet', return_value=(constant_model, bs.sound_names())), \
             patch.object(bs, 'cut_clip', side_effect=RuntimeError('encoder broke')):
            with self.assertRaisesRegex(RuntimeError, 'encoder broke'):
                bs.process_vod(self.vod(), root, .35)
        self.assertTrue((root/'vods'/'123.mp4').exists())
        self.assertFalse((root/'clips'/'burping-eructation'/'123'/'done.json').exists())


class YamnetNumpyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from yamnet_numpy import YAMNet
        cls.model = YAMNet()
        cls.names = bs.sound_names()

    def top(self, waveform, count=3):
        scores = self.model(waveform.astype(np.float32))
        self.assertEqual(scores.shape[1], 521)
        mean = scores.mean(axis=0)
        return [self.names[i] for i in mean.argsort()[::-1][:count]]

    def test_recognizes_silence_and_a_tone(self):
        seconds = np.arange(3*16000)/16000
        self.assertEqual(self.top(np.zeros(len(seconds)), 1), ['Silence'])
        self.assertIn('Dial tone', self.top(.5*np.sin(2*np.pi*440*seconds)))

    def test_one_row_per_hop_like_yamnet(self):
        for samples, rows in ((100, 1), (15600, 1), (15601, 2), (15600+7680*4, 5)):
            self.assertEqual(len(self.model(np.zeros(samples, np.float32))), rows)

    @unittest.skipUnless(bs.tensorflow_installed(), 'TensorFlow not installed')
    def test_matches_tensorflow(self):
        bs._model = None
        try:
            tensorflow_model, _ = bs.load_yamnet('tensorflow')
        finally:
            bs._model = None
        rng = np.random.default_rng(1)
        seconds = np.arange(5*16000)/16000
        for waveform in (rng.normal(0, .1, 40000), .3*np.sin(2*np.pi*(200+100*seconds)*seconds)):
            waveform = waveform.astype(np.float32)
            np.testing.assert_allclose(self.model(waveform), tensorflow_model(waveform), atol=5e-4)


class EncoderTests(unittest.TestCase):
    def setUp(self):
        bs._encoder = None
        self.addCleanup(setattr, bs, '_encoder', None)

    def test_cpu_only_skips_probing(self):
        with patch.object(bs.subprocess, 'run', side_effect=AssertionError('no probe')):
            self.assertEqual(bs.pick_encoder('cpu')['id'], 'cpu')

    def test_falls_back_to_cpu_when_no_gpu_encoder_works(self):
        failed = bs.subprocess.CompletedProcess([], 1)
        with patch.object(bs.subprocess, 'run', return_value=failed):
            self.assertEqual(bs.pick_encoder()['id'], 'cpu')

    def test_picks_first_working_gpu_encoder(self):
        def fake_run(cmd, **kwargs):
            return bs.subprocess.CompletedProcess(cmd, 0 if 'h264_amf' in cmd else 1)
        with patch.object(bs.subprocess, 'run', side_effect=fake_run):
            self.assertEqual(bs.pick_encoder()['id'], 'amf')

    def test_cpu_encoder_settings_produce_a_playable_clip(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d)/'in.mp4'
            bs.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=320x180:duration=1',
                    '-f', 'lavfi', '-i', 'sine=duration=1', '-shortest', str(source)])
            bs.cut_clip(source, .2, .7, Path(d)/'clip.mp4', bs.CPU_ENCODER)
            self.assertGreater((Path(d)/'clip.mp4').stat().st_size, 0)


class GuiFormTests(unittest.TestCase):
    def setUp(self):
        import burp_gui
        self.gui = burp_gui
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.midi = save_midi(self.tmp/'s.mid', [mido.Message('note_on', note=60),
                                                  mido.Message('note_off', note=60, time=480)])

    def test_form_becomes_script_arguments(self):
        argv, output = self.gui.build_argv(' EmmyTheAxolotl ', '2026-10-01', '', .4, str(self.midi),
                                           str(self.tmp/'song'), download_all=True, cpu_only=True)
        self.assertEqual(output, self.tmp/'song.mp4')
        self.assertEqual(argv[0], 'emmytheaxolotl')
        self.assertIn('--from-date', argv)
        self.assertNotIn('--to-date', argv)
        self.assertNotIn('--all', argv)  # only applies without dates
        self.assertIn('--cpu-only', argv)
        self.assertNotIn('--drums', argv)
        self.assertEqual(argv[argv.index('--confidence')+1], '0.40')
        bs.parse_args(argv)  # the script accepts what the form builds
        argv, _ = self.gui.build_argv('x', '', '', .35, str(self.midi), str(self.tmp/'o.mp4'), drums=True)
        self.assertTrue(bs.parse_args(argv).drums)

    def test_form_problems_are_explained(self):
        cases = [('', '', '', 'streamer'), ('x', '10/01/2026', '', 'From date'),
                 ('x', '2026-10-31', '2026-10-01', 'on or before')]
        for user, start, end, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.gui.build_argv(user, start, end, .35, str(self.midi), str(self.tmp/'o.mp4'))
        with self.assertRaisesRegex(ValueError, 'MIDI'):
            self.gui.build_argv('x', '', '', .35, '', str(self.tmp/'o.mp4'))
        with self.assertRaisesRegex(ValueError, 'at least one sound'):
            self.gui.build_argv('x', '', '', .35, str(self.midi), str(self.tmp/'o.mp4'), sounds=[])

    def test_chosen_sounds_are_passed_on(self):
        argv, _ = self.gui.build_argv('x', '', '', .35, str(self.midi), str(self.tmp/'o.mp4'),
                                      sounds=['Laughter', 'Burping, eructation'])
        self.assertEqual([argv[i+1] for i, a in enumerate(argv) if a == '--sound'],
                         ['Burping, eructation', 'Laughter'])
        self.assertEqual(bs.parse_args(argv).sounds, ['Burping, eructation', 'Laughter'])


class CommandLineTests(FixtureTest):
    def setUp(self):
        super().setUp()
        self.midi = save_midi(self.tmp/'song.mid', [mido.Message('note_on', note=60),
                                                     mido.Message('note_off', note=60, time=480)])
        self.base = ['streamer', '--midi', str(self.midi), '--work-dir', str(self.tmp/'work'),
                     '--output', str(self.tmp/'out.mp4')]

    def test_no_dates_with_existing_clips_skips_twitch(self):
        clip = self.tmp/'work'/'streamer'/'clips'/'burping-eructation'/'1'/'clip-0001.mp4'
        clip.parent.mkdir(parents=True)
        shutil.copyfile(self.fixture, clip)
        with patch.object(bs, 'list_vods', side_effect=AssertionError('no network')), \
             patch.object(bs, 'make_song') as make_song:
            bs.main(self.base)
        self.assertEqual(make_song.call_args.args[0], [clip])

    def test_no_dates_without_clips_downloads_everything(self):
        with patch.object(bs, 'list_vods', return_value=[]) as list_vods:
            with self.assertRaisesRegex(ValueError, 'No clips'):
                bs.main(self.base)
        self.assertEqual(list_vods.call_args.args[1:3], (None, None))

    def test_date_range_is_passed_through(self):
        with patch.object(bs, 'list_vods', return_value=[]) as list_vods:
            with self.assertRaises(ValueError):
                bs.main(self.base+['--from-date', '2026-10-01', '--to-date', '2026-10-31'])
        self.assertEqual(list_vods.call_args.args[1:3], (date(2026, 10, 1), date(2026, 10, 31)))

    def test_sound_choices(self):
        self.assertEqual(bs.parse_args(self.base).sounds, [bs.DEFAULT_SOUND])
        args = bs.parse_args(self.base+['--sound', 'cough', '--sound', 'Laughter'])
        self.assertEqual(args.sounds, ['Cough', 'Laughter'])
        with patch('sys.stdout') as out, self.assertRaises(SystemExit):
            bs.parse_args(['--list-sounds'])
        self.assertIn('Burping, eructation', ''.join(c.args[0] for c in out.write.call_args_list))

    def test_search_prints_channels_without_other_arguments(self):
        channels = [dict(login='emmytheaxolotl', name='emmytheaxolotl', image=None, followers=4573, live=False)]
        with patch.object(bs, 'search_channels', return_value=channels) as search, \
             patch('sys.stdout') as out, self.assertRaises(SystemExit):
            bs.parse_args(['--search', 'emmy'])
        search.assert_called_once_with('emmy')
        self.assertIn('4,573 followers', ''.join(c.args[0] for c in out.write.call_args_list))

    def test_https_trusts_certifi(self):
        import certifi
        import ssl
        with patch('ssl.create_default_context', wraps=ssl.create_default_context) as create:
            bs.https_context()
        create.assert_called_once_with(cafile=certifi.where())

    def test_bad_arguments_are_rejected(self):
        for extra in (['--confidence', '0'], ['--confidence', '1.5'],
                      ['--from-date', '2026-10-31', '--to-date', '2026-10-01'],
                      ['--clips-dir', '.', '--all'], ['--sound', 'Belch of doom']):
            with self.subTest(extra=extra), self.assertRaises(SystemExit), \
                 patch('sys.stderr'):
                bs.parse_args(self.base+extra)


if __name__ == '__main__':
    unittest.main()
