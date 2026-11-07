from pathlib import Path
import tempfile
import json
from types import SimpleNamespace
import unittest
import wave

import numpy as np

from sneeze_compilation import (
    HOP_SAMPLES, SAMPLE_RATE, WINDOW_SECONDS, cached_scores, command,
    concatenate, discover, events_from_scores, export_clip, merge_intervals,
    probe, score_blocks,
)


class FakeScores:
    def __init__(self, scores):
        self.scores = scores

    def numpy(self):
        return self.scores


class WindowModel:
    """Unpadded frames, labelled with their actual first decoded audio sample."""
    def __call__(self, audio):
        size = round(WINDOW_SECONDS*SAMPLE_RATE)
        count = max(0, 1+(len(audio)-size)//HOP_SAMPLES)
        scores = np.array([[audio[i*HOP_SAMPLES]] for i in range(count)])
        return FakeScores(scores), None, None


class SneezeTests(unittest.TestCase):
    def test_discovery_only_selects_originals_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'vod').mkdir()
            original = root/'vod'/'original.mp4'
            original.touch()
            (root/'vod'/'sneeze.mp4').touch()
            (root/'original.json').touch()
            self.assertEqual(discover([root, original]), [original])

    def test_merge_tracks_max_score_and_chain_overlap(self):
        self.assertEqual(merge_intervals([(2, 3, .8), (0, 1, .4), (1, 2, .6)]),
                         [[0, 3, .8]])

    def test_padding_clamps_and_merges_without_replaying_overlap(self):
        scores = [.9, 0, 0, 0, .6]
        events = events_from_scores(scores, 10, 3, .5, .5, .75, 0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], 10)
        self.assertEqual(events[0][1], 13)
        self.assertEqual(events[0][2], .9)

    def test_no_matches_is_empty(self):
        self.assertEqual(events_from_scores([.1]*10, 0, 4.8, .35, .5, .75, .3), [])

    def test_chunk_boundaries_final_partial_and_resume(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'audio.wav'
            samples = (np.arange(round(5.2*SAMPLE_RATE)) % 24000).astype('<i2')
            with wave.open(str(path), 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(samples.tobytes())
            core = 4*HOP_SAMPLES
            blocks = list(score_blocks(path, WindowModel(), 0, 0, 5.2, core))
            scores = [s for _, values in blocks for s in values]
            self.assertEqual(len(scores), 11)
            expected = samples[np.arange(11)*HOP_SAMPLES]/32768
            np.testing.assert_allclose(scores, expected, atol=1/32768)
            resumed = list(score_blocks(path, WindowModel(), 0, 0, 5.2, core, core))
            np.testing.assert_allclose([s for _, values in resumed for s in values], scores[4:])
            self.assertEqual(blocks[-1][0], round(5.2*SAMPLE_RATE))

    def test_short_audio_is_reported_instead_of_successful_empty_scan(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'short.wav'
            with wave.open(str(path), 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(np.zeros(SAMPLE_RATE, '<i2').tobytes())
            with self.assertRaisesRegex(RuntimeError, 'Audio ended'):
                list(score_blocks(path, WindowModel(), 0, 0, 5, 10*HOP_SAMPLES))

    def test_cache_resumes_interruption_and_reuses_scores_without_model(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root/'audio.wav'
            with wave.open(str(source), 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(np.ones(round(5.2*SAMPLE_RATE), '<i2').tobytes())
            args = SimpleNamespace(chunk_seconds=1.92, start=0, work_dir=root/'work')
            calls = []

            def interrupt(audio):
                calls.append(True)
                if len(calls) == 2:
                    raise RuntimeError('test interruption')
                return WindowModel()(audio)

            with self.assertRaisesRegex(RuntimeError, 'test interruption'):
                cached_scores(source, 5.2, args, lambda: (interrupt, 0))
            scores, key = cached_scores(source, 5.2, args, lambda: (WindowModel(), 0))
            self.assertEqual(len(scores), 11)

            def forbidden_load():
                self.fail('Complete cache should not load a model')

            self.assertEqual(cached_scores(source, 5.2, args, forbidden_load)[0], scores)
            # Simulate an interrupted final decoder exit; retry checks last block.
            path = args.work_dir/'scores'/f'{key}.json'
            data = json.loads(path.read_text())
            data['complete'] = False
            path.write_text(json.dumps(data))
            self.assertEqual(cached_scores(source, 5.2, args, lambda: (WindowModel(), 0))[0], scores)

    def test_real_video_export_and_compilation(self):
        with tempfile.TemporaryDirectory(prefix="sneeze 'test ") as d:
            root = Path(d)
            source = root/'original.mp4'
            command(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                     'testsrc2=size=320x180:rate=24:duration=3', '-f', 'lavfi', '-i',
                     'sine=frequency=440:sample_rate=44100:duration=3',
                     '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)])
            args = SimpleNamespace(width=320, height=180, fps=24, encoder='cpu')
            clips = [root/'one.mp4', root/'two.mp4']
            export_clip(source, clips[0], .5, 1.5, args)
            export_clip(source, clips[1], 2, 3, args)
            # Normalized clips can be concatenated, even with apostrophes in paths.
            output = root/'joined.mp4'
            concatenate(clips, output)
            self.assertAlmostEqual(probe(output), 2, delta=.15)
            command(['ffmpeg', '-v', 'error', '-i', str(output), '-f', 'null', '-'])
            with self.assertRaises(FileExistsError):
                concatenate(clips, output)


if __name__ == '__main__':
    unittest.main()
