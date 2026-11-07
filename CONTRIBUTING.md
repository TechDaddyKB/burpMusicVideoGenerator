# Contributing

Use Python 3.12, FFmpeg/ffprobe, and the environment setup in the README.

Before proposing a change:

```bash
source .venv/bin/activate
python -m unittest discover -v
uv pip check --python .venv/bin/python
git diff --check
```

Keep tests independent of live Twitch accounts and model downloads. Generate small
audio/video fixtures in temporary directories and substitute the downloader/model
where needed. Test cleanup failures and retry behavior when changing VOD handling.
Preserve the distinction between newly downloaded sources and pre-existing footage.

Keep generated media, credentials, environments, and personal filesystem paths out
of commits. Include a concrete description of behavior changes, test results, and
an entry in CHANGELOG.md. GitHub Actions runs the unit/media tests; authenticated
downloads and optional GPU encoding need explicitly documented manual validation.

`burp_song_v2.py` is the main entry point. `burp_song.py` implements MIDI playback
and the video grid. `sneeze_compilation.py` supplies the existing streaming audio,
cache, model-class selection, and clip-export helpers; importing it does not run
sneeze detection. Keep these modules together when distributing the tool.
