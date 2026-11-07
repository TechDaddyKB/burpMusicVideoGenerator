import math
from pathlib import Path
import tempfile
import unittest

import mido
import numpy as np
from scipy.io import wavfile

from burp_song import SR, estimate_pitch, midi_notes, note_audio, playlist_paths, select_clip


class RendererTests(unittest.TestCase):
    def read_notes(self, midi_tracks, **kwargs):
        with tempfile.TemporaryDirectory() as d:
            mid = mido.MidiFile(ticks_per_beat=480)
            mid.tracks.extend(midi_tracks)
            path = Path(d) / 'test.mid'
            mid.save(path)
            return midi_notes(path, **kwargs)

    def test_global_tempo_and_polyphony(self):
        tempo = mido.MidiTrack([
            mido.MetaMessage('set_tempo', tempo=500000),
            mido.MetaMessage('set_tempo', tempo=1000000, time=480)])
        notes = mido.MidiTrack([
            mido.Message('note_on', note=60, velocity=90),
            mido.Message('note_on', note=64, velocity=90, time=240),
            mido.Message('note_off', note=60, time=720),
            mido.Message('note_off', note=64)])
        result = self.read_notes([tempo, notes], tracks={1})
        self.assertEqual(len(result), 2)
        self.assertAlmostEqual(result[0]['duration'], 1.5)
        self.assertAlmostEqual(result[1]['start'], .25)
        self.assertAlmostEqual(result[1]['duration'], 1.25)

    def test_sustain_and_default_drum_exclusion(self):
        notes = mido.MidiTrack([
            mido.Message('control_change', control=64, value=127),
            mido.Message('note_on', note=60, velocity=100),
            mido.Message('note_on', channel=9, note=36, velocity=100),
            mido.Message('note_off', note=60, time=480),
            mido.Message('note_off', channel=9, note=36),
            mido.Message('control_change', control=64, value=0, time=480)])
        result = self.read_notes([notes])
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]['duration'], 1)
        self.assertEqual(len(self.read_notes([notes], include_drums=True)), 2)

    def test_repeated_same_pitch_note_pairing(self):
        track = mido.MidiTrack([
            mido.Message('note_on', note=60),
            mido.Message('note_on', note=60, time=120),
            mido.Message('note_off', note=60, time=120),
            mido.Message('note_on', note=60, velocity=0, time=120)])
        notes = self.read_notes([track])
        self.assertEqual(len(notes), 2)
        self.assertAlmostEqual(notes[0]['duration'], .25)
        self.assertAlmostEqual(notes[1]['duration'], .25)

    def test_pitch_estimate(self):
        audio = .5*np.sin(2*math.pi*220*np.arange(SR)/SR)
        self.assertAlmostEqual(estimate_pitch(audio), 57, delta=.15)
        self.assertIsNone(estimate_pitch(np.zeros(SR)))

    def test_duration_selection(self):
        clips = [dict(duration=.2, pitch=60), dict(duration=1., pitch=60)]
        self.assertIs(select_clip(dict(duration=.22, pitch=60), clips), clips[0])

    def test_independent_pitch_and_duration_with_ffmpeg(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            path = cache/'tone.wav'
            wavfile.write(path, SR, (.5*np.sin(2*math.pi*220*np.arange(SR//2)/SR)).astype(np.float32))
            clip = dict(key='test-tone', wav=str(path), duration=.5, pitch=57)
            audio = note_audio(dict(duration=.25, pitch=69), clip, cache)
            self.assertEqual(len(audio), SR//4)
            interior = audio[1000:-1000]
            peak = np.argmax(np.abs(np.fft.rfft(interior*np.hanning(len(interior)))))
            self.assertAlmostEqual(peak*SR/len(interior), 440, delta=12)

    def test_relative_escaped_playlist(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            (directory/'a b.mp4').touch()
            path = directory/'test.xspf'
            path.write_text('<playlist xmlns="http://xspf.org/ns/0/"><trackList>'
                            '<track><location>a%20b.mp4</location></track>'
                            '</trackList></playlist>')
            self.assertEqual(playlist_paths(path), [(directory/'a b.mp4').resolve()])


if __name__ == '__main__':
    unittest.main()
