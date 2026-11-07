# Burp Music Video Generator

**Have you ever wanted to make your favorite streamer's burps into a song? Well,
try this.** At last, a practical answer to the question nobody at the conservatory
was brave enough to ask.

Give it a Twitch username and a MIDI file. It downloads the streamer's archived
broadcasts, finds their burps, and plays your song with them: each burp is
pitch-shifted and stretched to fit its note, and notes that sound together share
a split screen. A questionable bodily function becomes a questionable artistic
decision. Everybody wins.

**From indigestion to orchestration.**

## How it works

1. Lists the streamer's archived broadcasts (VODs) on Twitch, filtered by your dates.
2. Downloads one VOD at a time.
3. Finds burps with Google's [YAMNet](https://www.tensorflow.org/hub/tutorials/yamnet)
   sound classifier and saves each one as a short video clip. Burps are the
   default, but you can pick any of the 521 sounds YAMNet knows, or several.
4. Deletes the full VOD once its clips are saved.
5. Plays every MIDI note with the burp clip closest in length, pitch-shifted and
   stretched to fit, and saves the result as an MP4.

## Requirements

- **Python 3.12**
- **FFmpeg**, with the `rubberband` audio filter and `libx264` encoder
  (check with `ffmpeg -filters | grep rubberband`)
- Disk space for one full VOD at a time, plus the clips
- A MIDI file. No songs or footage are included.

No API key or paid service is needed, and it runs on a CPU. YAMNet's weights
download automatically the first time it looks for burps. Linux is the tested platform.

### GPU acceleration

Video encoding uses your GPU automatically when FFmpeg can. At startup it tries
these in order and uses the first that works, otherwise the CPU:

| GPU | FFmpeg encoder |
| --- | --- |
| NVIDIA | NVENC |
| Intel | Quick Sync |
| AMD (Windows) | AMF |
| Apple | VideoToolbox |
| AMD or Intel (Linux) | VAAPI |
| Most others | Vulkan |

Your FFmpeg build and GPU drivers must support the encoder; the log says which
one was picked.

Burp detection uses TensorFlow, which runs YAMNet on the GPU if it can see one.
For NVIDIA on Linux, install `pip install "tensorflow[and-cuda]"`. AMD needs
TensorFlow's ROCm build and Apple needs `tensorflow-metal`. Otherwise detection
runs on the CPU, which is fine: YAMNet is a small model.

## Setup

```bash
git clone https://github.com/YOUR_USERNAME/burpMusicVideoGenerator.git
cd burpMusicVideoGenerator
```

Install FFmpeg (`sudo apt-get install ffmpeg` on Ubuntu/Debian,
`sudo pacman -S ffmpeg` on Arch), then the Python packages with
[uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
source .venv/bin/activate
```

Or with plain pip:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## The app

```bash
python burp_gui.py
```

Search for a streamer and click them, choose the sounds to detect (burps are
already selected), optionally set a date range, drag the confidence slider, choose a MIDI file and where to save the video, then press
**Make the video**. The log shows progress, and **Play video** opens the result.
Videos are saved in the `outputs` folder by default.

The app needs Tkinter, which comes with Python from python.org, uv and Homebrew.
On Debian/Ubuntu, install it with `sudo apt-get install python3-tk`.

Streamer search uses Twitch's public website API, which Twitch could change. If
search stops working, type the exact username and press Search to use it anyway.

## Command line

Download October's broadcasts and make a song:

```bash
python burp_song.py emmytheaxolotl --midi song.mid \
  --from-date 2026-10-01 --to-date 2026-10-31 \
  --output burp-symphony.mp4
```

Laughs and coughs instead of burps? Pick any sounds YAMNet knows. A moment counts
if any of them is detected:

```bash
python burp_song.py emmytheaxolotl --midi song.mid --sound Laughter --sound Cough
python burp_song.py --list-sounds    # all 521, e.g. Sneeze, Screaming, Bark, Applause
```

Leave out the dates to reuse the clips you already have. If there aren't any yet,
it downloads the streamer's whole VOD archive instead:

```bash
python burp_song.py emmytheaxolotl --midi another-song.mid --output encore.mp4
```

| Option | What it does |
| --- | --- |
| `--midi FILE` | The song to play. Required. |
| `--output FILE` | The MP4 to create (default `burp-song.mp4`). It won't overwrite an existing file. |
| `--from-date`, `--to-date` | Only download broadcasts that started in this range (`YYYY-MM-DD`, UTC, inclusive). Either one can be used alone. |
| `--sound NAME` | A sound to detect, repeatable (default `"Burping, eructation"`). Names ignore case. |
| `--list-sounds` | Print every sound you can detect. |
| `--confidence N` | How sure the detector must be, from 0 to 1 (default `.35`). Higher means fewer, surer clips. Other sounds may need a different value. |
| `--all` | Without dates, download the whole archive even when clips already exist. |
| `--clips-dir DIR` | Use the MP4 clips in another folder instead of downloading. |
| `--seconds N` | Render only the first N seconds, for a quick preview. |
| `--work-dir DIR` | Where clips, the VOD being processed, and caches go (default `burp-work`). |
| `--cpu-only` | Encode video on the CPU even when a GPU encoder is available. |
| `--cookies-from-browser NAME` | Use your browser's Twitch login, for subscriber-only VODs. |

Only VODs still listed on the streamer's
[archive page](https://www.twitch.tv/emmytheaxolotl/videos?filter=archives&sort=time)
can be downloaded. Broadcasts that are still live are skipped.

## Where things go

```text
burp-work/emmytheaxolotl/
  clips/burping-eructation/<vod-id>/clip-0001.mp4   the clips (kept)
  clips/burping-eructation/<vod-id>/done.json       where each came from and its score
  clips/cough+laughter/...                          other sound choices get their own folder
  vods/                                             the VOD being processed (deleted when done)
  cache/                                            audio and video cache for rendering
```

A full VOD is only deleted after all its clips are saved, so if something fails
you can rerun the same command and it carries on. Broadcasts that are already
done are skipped. Because the VOD is gone, changing `--confidence` later only
affects new broadcasts; delete a broadcast's `<vod-id>` folder to redo it.
Choosing different sounds starts a new clip folder, so those broadcasts are
downloaded again.

The `cache` folder can grow to several GB. It's safe to delete when nothing is
rendering. Keep the `clips` folder: it's what future songs are made from.

## Limitations

- The detector sometimes mistakes voices or game audio for burps, and scores are
  not true probabilities.
- Each clip gets one estimated pitch, so a wandering burp stays a wandering burp.
- Big pitch shifts or stretches can sound rough.
- MIDI drums (channel 10) are skipped, and instrument sounds, pitch bends and
  expression aren't reproduced.
- Anything already on screen in the broadcast (chat, game HUDs) stays in the video.

This is a burp orchestra, not the Vienna Philharmonic with a stomach ache.

## Tests

```bash
python -m unittest discover -v
```

The tests use generated audio and video, a stand-in for YAMNet and fake Twitch
responses, so they make no network requests. GitHub Actions runs them on every
push. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Credits

Built with [yt-dlp](https://github.com/yt-dlp/yt-dlp), YAMNet, FFmpeg and
Rubber Band, NumPy, SciPy, Mido, and OpenCV, which keep their own licenses.
`yamnet_sounds.txt` lists YAMNet's sound classes, from the
[AudioSet ontology](https://research.google.com/audioset/ontology/index.html).
Bring your own MIDI and footage you can use. Bring your own sense of humor, too;
we spent the entire budget on the bass section.

## License

This project's code is released under the [MIT License](LICENSE). It does not
cover MIDI files, broadcasts, or clips you process with it.
