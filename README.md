# Burp Music Video Generator

**Have you ever wanted to make your favorite streamer's burps into a song? Well,
try this.** At last, a practical answer to the question nobody at the conservatory
was brave enough to ask.

Give it a Twitch username, a date range, and a MIDI file. It finds archived
broadcasts, downloads them, extracts likely burps, and plays your arrangement using
those clips. Chords become split screens. A questionable bodily function becomes
a questionable artistic decision. Everybody wins.

**From indigestion to orchestration.**

## What it does

- Finds available Twitch archive broadcasts between inclusive UTC start dates.
- Downloads and processes one broadcast at a time.
- Detects candidate burps locally with YAMNet and saves individual video clips.
- Deletes its new full downloads after their clips and detection records are saved.
- Reuses existing clips when no dates are supplied, with no Twitch requests.
- Matches each MIDI note to a clip, adjusts pitch and duration, and mixes the song.
- Shows simultaneous notes in a split screen, with no generated title/note labels.

Text already in the broadcast image stays in the image. We removed the renderer's
labels; we did not invent a magic eraser for chat, game HUDs, or the streamer's
very important "drink water" reminder.

## Requirements

- **Python 3.12**. Use this version for the included TensorFlow dependency range.
- **FFmpeg and ffprobe**, with the `rubberband` audio filter and `libx264` encoder.
- Disk space for one full broadcast, the retained clips, and rendering caches.
- A MIDI file supplied by you. The software does not include songs or streamer footage.

CPU processing works. No API key or paid AI service is needed. YAMNet weights may
download on first detection use. NVIDIA encoding is optional, not required.

Linux is the tested platform. Other platforms need compatible TensorFlow wheels
and an FFmpeg build with those filters/encoders; they have not been validated here.

## Setup

Clone your copy of this repository, then enter its directory:

```bash
git clone https://github.com/YOUR_USERNAME/burpMusicVideoGenerator.git
cd burpMusicVideoGenerator
```

Install FFmpeg using your system's package manager. For example:

```bash
# Ubuntu / Debian
sudo apt-get update
sudo apt-get install -y ffmpeg

# Arch Linux
sudo pacman -S --needed ffmpeg
```

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if you don't
already have it, then create the Python environment:

```bash
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
source .venv/bin/activate
python burp_song_v2.py --help
```

Already have Python 3.12 and prefer pip? The old-fashioned digestive tract works too:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

You can inspect FFmpeg's capabilities with `ffmpeg -filters` and `ffmpeg -encoders`.
Look for `rubberband` and `libx264` respectively.

## Download broadcasts and make a song

```bash
python burp_song_v2.py emmytheaxolotl \
  --from-date 2026-10-01 --to-date 2026-10-31 \
  --midi /path/to/song.mid \
  --output artifacts/burp-symphony.mp4
```

Replace the username, dates, and MIDI path with yours. Dates select broadcasts by
their **start date in UTC**, including both endpoints. A broadcast starting on the
last date is included in full, even if it ends the next day. Both dates must be
supplied together.

The tool searches the streamer's archive listing, equivalent to:

```text
https://www.twitch.tv/USERNAME/videos?filter=archives&sort=time
```

It can only discover broadcasts that remain available there. Expired or deleted
VODs cannot be retrieved. A date/metadata/download failure stops the run instead
of quietly declaring an incomplete collection finished.

Check the selection before moving any video bytes:

```bash
python burp_song_v2.py emmytheaxolotl \
  --from-date 2026-10-01 --to-date 2026-10-31 --list-only
```

## Use clips you already have

Omit both dates to skip Twitch discovery, downloads, and sound detection:

```bash
python burp_song_v2.py emmytheaxolotl \
  --midi /path/to/song.mid \
  --output artifacts/encore.mp4
```

By default it looks in its own streamer workspace, `sound_results/USERNAME`,
`./USERNAME`, and the current directory if its name matches the streamer.
Select another library explicitly with `--clips-dir`:

```bash
python burp_song_v2.py emmytheaxolotl \
  --clips-dir /path/to/existing/clips \
  --midi /path/to/song.mid \
  --seconds 15 --output artifacts/short-burp-story.mp4
```

The clip tree should contain `Burping_eructation` folders, for example:

```text
my-clips/
  some-broadcast/
    Burping_eructation/
      burp_001.mp4
      burp_002.mp4
```

Every MP4 in matching folders is eligible. No MIDI file, sample video, or generated
clip is bundled with the repository.

## The full-video disappearing act

For each new download, the program finishes extracting all detected clips and
saves a complete detection record **before deleting that full video**. It keeps
the clips, scores, and metadata. The next song can use the clips alone.

- A failed extraction preserves its full video for retry.
- `--keep-vods` retains new full downloads while you experiment.
- `--reuse-dir /path/to/old/vods` reuses `VOD_ID/original.*` or
  `vVOD_ID/original.*` there. Those pre-existing full videos are preserved.
- `--scan-seconds 1800` scans only the first 30 minutes of each VOD and retains its
  full download because the rest has not been analyzed.
- A completed scan with no burps saves an empty record and may still delete its
  v2-owned full download. No song is rendered if the entire selection yields no clips.
- Rerunning the same settings reuses completed clips and scan checkpoints. Changing
  extraction settings after deleting a VOD may require downloading it again.

Deleted full videos can only be restored by downloading them again while they are
available. This is why `--keep-vods` exists: sometimes the encore needs more gas.

## Useful knobs

| Option | What it changes |
| --- | --- |
| `--burp-threshold .50` | Raises detection selectivity; default `.35`. |
| `--max-vods 1` | Processes only the first selected broadcast for a trial. |
| `--scan-seconds 1800` | Limits detection to 30 minutes per VOD; keeps full downloads. |
| `--start 30 --seconds 15` | Renders MIDI seconds 30–45 as a preview. |
| `--channels 2,3` | Uses selected zero-based MIDI channels. |
| `--tracks 1,2` | Uses selected zero-based MIDI tracks. |
| `--octave -1` | Lowers the arrangement an octave. |
| `--include-drums` | Treats percussion channel 9 as pitched notes; off by default. |
| `--width 640 --height 360 --fps 24` | Makes a smaller preview. Default is 1280×720/30. |
| `--work-dir /path/to/work` | Changes where clips, temporary VODs and caches live. |
| `--keep-vods` | Retains new full downloads after extraction. |
| `--cookies-from-browser chrome` | Uses a browser session when Twitch access requires it. |
| `--encoder nvenc` | Uses NVIDIA encoding for extracted clips; CPU remains the song encoder. |

An optional yt-dlp format selector can reduce download size:

```bash
python burp_song_v2.py emmytheaxolotl \
  --from-date 2026-10-01 --to-date 2026-10-31 \
  --download-format 'bv*[height<=720]+ba/b[height<=720]' \
  --midi /path/to/song.mid --output artifacts/720p-gas.mp4
```

## Output and limitations

The MP4 has an adjacent JSON report listing note-to-clip assignments. The default
working directory is `burp-song-v2-work/USERNAME/`, containing broadcast metadata,
retained `Burping_eructation` samples, extraction records, detector scores,
generated playlists, and a song-rendering cache. Existing output videos/reports
are refused; use a new output filename for each attempt.

Detection can mistake voices or game audio for burps. Scores are not calibrated
probabilities. Pitch matching uses one estimated pitch per clip, so a wandering
burp stays a wandering burp. Background music and speech are transformed too.
Large pitch shifts or stretches can sound rough. This is a burp orchestra, not
Vienna Philharmonic with a stomach ache.

Source frames are cached at 640×360, and grid tiles rearrange as notes start and
stop. MIDI program sounds, pitch bends, and expression are not reproduced.
The cache can grow to several GB; remove the `song-cache` directory when no render
is running to reclaim space. Preserve the extracted clips for future songs.

## Tests and development

```bash
source .venv/bin/activate
python -m unittest discover -v
uv pip check --python .venv/bin/python
```

The suite uses generated audio/video and mocked Twitch responses. It tests MIDI
timing, pitch/duration changes, split screens without labels, date filtering,
download options, retry behavior, and full-video cleanup. It makes no live Twitch
requests and does not download YAMNet weights. Live media downloads and optional
GPU/browser-cookie paths require separate checks.

GitHub Actions runs the same tests on Linux with Python 3.12 and FFmpeg on pushes
and pull requests. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Publish your GitHub repository

This is a command-line program, so publishing means pushing this source repository;
it does not need a hosted web server.

The local Git repository is already initialized on `main`. Create an **empty**
GitHub repository without an autogenerated README, license, or `.gitignore`, then:

```bash
git remote add origin https://github.com/YOUR_USERNAME/burpMusicVideoGenerator.git
git push -u origin main
```

The repository ignores videos, audio, MIDI inputs, generated work folders, virtual
environments, caches, logs, and common credential files. Inputs and output clips
stay on your machine. It has no GitHub remote configured until you add one.

## Credits

Built with [yt-dlp](https://github.com/yt-dlp/yt-dlp),
[YAMNet](https://www.tensorflow.org/hub/tutorials/yamnet), FFmpeg/Rubber Band,
NumPy, SciPy, Mido, and OpenCV. The model and dependencies retain their own licenses.
Bring your own MIDI and footage you can use. Bring your own sense of humor, too;
we spent the entire budget on the bass section.
