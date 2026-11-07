<p align="center"><img src="assets/icon.png" width="128" alt=""></p>

# Burp Music Video Generator

[![Tests](https://github.com/TechDaddyKB/burpMusicVideoGenerator/actions/workflows/tests.yml/badge.svg)](https://github.com/TechDaddyKB/burpMusicVideoGenerator/actions/workflows/tests.yml)
[![CodeQL](https://github.com/TechDaddyKB/burpMusicVideoGenerator/actions/workflows/codeql.yml/badge.svg)](https://github.com/TechDaddyKB/burpMusicVideoGenerator/actions/workflows/codeql.yml)
[![Latest release](https://img.shields.io/github/v/release/TechDaddyKB/burpMusicVideoGenerator)](https://github.com/TechDaddyKB/burpMusicVideoGenerator/releases/latest)

**Have you ever wanted to make your favorite streamer's burps into a song? Well,
try this.** At last, a practical answer to the question nobody at the conservatory
was brave enough to ask.

Give it a Twitch username and a MIDI file. It downloads the streamer's archived
broadcasts, finds their burps, and plays your song with them: each burp is
pitch-shifted and stretched to fit its note, and notes that sound together share
a split screen. A questionable bodily function becomes a questionable artistic
decision. Everybody wins.

**From indigestion to orchestration.**

## Download

Grab the [latest release](https://github.com/TechDaddyKB/burpMusicVideoGenerator/releases/latest).
No Python, no FFmpeg, no assembly required: everything it needs is in the box, like a
burp waiting for its cue.

| You're on | Get |
| --- | --- |
| Windows 10/11 | `…-windows-x64.zip`: unzip, run `burp-music-video-generator.exe` |
| macOS (Apple silicon) | `…-macos-arm64.dmg`: drag to Applications |
| Ubuntu, Debian, Mint, Pop!_OS | `…_amd64.deb` |
| Any Linux | `…-linux-x64.AppImage` (one file), `.flatpak` (sandboxed) or `.tar.gz` (plain folder) |

### Standard or TensorFlow edition?

Every download comes in two flavors. **Most people want standard** (no `-tensorflow` in the name).

| | Standard | TensorFlow |
| --- | --- | --- |
| Size | ~100 MB | ~500 MB to 1 GB |
| Burp detector | Our NumPy copy of Google's YAMNet | Google's original TensorFlow YAMNet |
| Same results? | Yes: same model and weights, scores within 0.0001 of TensorFlow's | It's the reference |
| Detection on a GPU | No; the CPU does about a minute of video per second | Linux + NVIDIA GPU with CUDA 12 and cuDNN 9 installed; CPU elsewhere |
| Video encoding on a GPU | Yes | Yes |

Detection is rarely the slow part (downloading is), so the TensorFlow edition only
pays off on a Linux box with an NVIDIA card chewing through a mountain of broadcasts.
It's the same burp either way; one just arrives in a bigger truck.

### First launch

- **Windows** builds are code-signed. A brand-new release can still trigger SmartScreen's
  "Windows protected your PC" until the signature earns some reputation: **More info** > **Run anyway**.
- **macOS** builds aren't notarized yet. Right-click the app and choose **Open** the first
  time (or allow it under System Settings > Privacy & Security).
- **AppImage:** `chmod +x` it first. **Flatpak:** `flatpak install --user <file>`; needs Flathub for the runtime.

## How it works

1. Lists the streamer's archived broadcasts (VODs) on Twitch, filtered by your dates.
2. Downloads one VOD at a time.
3. Finds burps with Google's [YAMNet](https://www.kaggle.com/models/google/yamnet)
   sound classifier and saves each one as a short video clip. Burps are the
   default, but you can pick any of the 521 sounds YAMNet knows, or several.
4. Deletes the full VOD once its clips are saved. Your hard drive thanks you.
5. Plays every MIDI note with the clip closest in length, pitch-shifted and
   stretched to fit, and saves the result as an MP4.

## The app

Search for a streamer and click them, choose the sounds to detect (burps are already
selected), optionally set a date range, drag the confidence slider, choose a MIDI file
and where to save the video, then press **Make the video**. The log shows progress,
and **Play video** opens the result.

Downloads save videos to your Videos folder (Movies on a Mac) and keep clips in your
user data folder. Run from source, both go next to the code (`outputs/` and `burp-work/`).

Streamer search uses Twitch's public website API, which Twitch could change. If
search stops working, type the exact username and press Search to use it anyway.

## GPU acceleration

Video encoding uses your GPU automatically. At startup it test-encodes with each of
these and uses the first that works, otherwise the CPU:

| GPU | Encoder |
| --- | --- |
| NVIDIA | NVENC |
| Intel | Quick Sync |
| AMD (Windows) | AMF |
| Apple | VideoToolbox |
| AMD or Intel (Linux) | VAAPI |
| Most others | Vulkan |

The downloads include our own FFmpeg build ([build/ffmpeg/build.sh](build/ffmpeg/build.sh)) with
every one of those, plus x264 and Rubber Band, and nothing else: no codecs it doesn't
use, no network code. It loads your GPU driver at run time, so keep drivers current.
**Use CPU only** in the app (or `--cpu-only`) skips the GPU.

## Run from source

Needs **Python 3.12** and **FFmpeg** with the `rubberband` filter and `libx264`
(`ffmpeg -filters | grep rubberband`). Tkinter comes with Python from python.org,
uv and Homebrew; on Debian/Ubuntu add `sudo apt-get install python3-tk`.

```bash
git clone https://github.com/TechDaddyKB/burpMusicVideoGenerator.git
cd burpMusicVideoGenerator
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
source .venv/bin/activate
python burp_gui.py
```

For the TensorFlow detector, install `requirements-tensorflow.txt` instead
(`tensorflow[and-cuda]` for NVIDIA GPUs on Linux). It uses Google's YAMNet model
from `models/`, checked against a pinned checksum.

## Command line

Download October's broadcasts and make a song:

```bash
python burp_song.py emmytheaxolotl --midi song.mid \
  --from-date 2026-10-01 --to-date 2026-10-31 \
  --output burp-symphony.mp4
```

The Linux downloads take the same arguments:
`burp-music-video-generator emmytheaxolotl --midi song.mid …`.

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
| `--search NAME` | Search Twitch for channels by name, then exit. |
| `--confidence N` | How sure the detector must be, from 0 to 1 (default `.35`). Higher means fewer, surer clips. Other sounds may need a different value. |
| `--all` | Without dates, download the whole archive even when clips already exist. |
| `--clips-dir DIR` | Use the MP4 clips in another folder instead of downloading. |
| `--seconds N` | Render only the first N seconds, for a quick preview. |
| `--work-dir DIR` | Where clips, the VOD being processed, and caches go (default `burp-work`). |
| `--engine NAME` | `numpy`, `tensorflow`, or `auto` (TensorFlow if installed). |
| `--cpu-only` | Encode video on the CPU even when a GPU encoder is available. |
| `--cookies-from-browser NAME` | Use your browser's Twitch login, for subscriber-only VODs. |

Only VODs still listed on the streamer's
[archive page](https://www.twitch.tv/emmytheaxolotl/videos?filter=archives&sort=time)
can be downloaded. Broadcasts that are still live are skipped; we don't burp-shame
works in progress.

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
  not true probabilities. Science marches on; so does indigestion.
- Each clip gets one estimated pitch, so a wandering burp stays a wandering burp.
- Big pitch shifts or stretches can sound rough.
- MIDI drums (channel 10) are skipped, and instrument sounds, pitch bends and
  expression aren't reproduced.
- Anything already on screen in the broadcast (chat, game HUDs) stays in the video.

This is a burp orchestra, not the Vienna Philharmonic with a stomach ache.

## Security and updates

- **CodeQL** scans the Python code and the workflows on every push and weekly.
- **Dependabot** opens pull requests for outdated Python packages and GitHub Actions,
  and security alerts as soon as an advisory is published.
- **pip-audit** checks every dependency for known vulnerabilities on every push, and
  again before each release is built: a vulnerable package stops the release.
- The FFmpeg sources, YAMNet model and GitHub Actions are pinned by SHA-256 or commit.

## Tests

```bash
python -m unittest discover -v
```

The tests use generated audio and video, a stand-in for YAMNet and fake Twitch
responses, so they make no network requests. With TensorFlow installed they also
check that the NumPy YAMNet matches TensorFlow's. GitHub Actions runs them on every
push. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Credits

Built with [yt-dlp](https://github.com/yt-dlp/yt-dlp), FFmpeg, x264, Rubber Band,
NumPy, SciPy, Pillow and Mido, which keep their own licenses. The bundled FFmpeg is
GPL; its exact sources are listed in `ffmpeg/SOURCES.txt` in each download.
YAMNet and its weights (`yamnet_weights.npz`) are by Google, under the Apache License
2.0; `yamnet_sounds.txt` lists its sound classes from the
[AudioSet ontology](https://research.google.com/audioset/ontology/index.html).

Bring your own MIDI and footage you can use. Bring your own sense of humor, too;
we spent the entire budget on the bass section.

## License

This project's code is released under the [MIT License](LICENSE). It does not
cover MIDI files, broadcasts, or clips you process with it.
