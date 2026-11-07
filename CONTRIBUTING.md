# Contributing

Use Python 3.12, FFmpeg, and the setup in the README.

Before proposing a change:

```bash
source .venv/bin/activate
python -m unittest discover -v
git diff --check
```

Everything lives in `burp_song.py`, in this order: GPU encoder detection, Twitch
search and download, burp detection, MIDI-to-audio, video rendering, and the
command line. `burp_gui.py` is the desktop app; it builds the same command-line
arguments and calls `burp_song.main()`. Tests are in `test_burp_song.py`.

Keep tests independent of live Twitch accounts and model downloads: generate small
audio/video files in temporary folders and substitute the downloader or model.
When changing VOD handling, check that a full VOD is deleted only after its clips
are saved, and kept when something fails.

Keep generated media, credentials, virtual environments, and personal file paths
out of commits. Describe behavior changes and test results, and add an entry to
CHANGELOG.md.
