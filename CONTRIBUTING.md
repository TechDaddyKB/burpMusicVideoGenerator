# Contributing

Use Python 3.12, FFmpeg, and the setup in the README.

Before proposing a change:

```bash
source .venv/bin/activate
python -m unittest discover -v
git diff --check
```

## Layout

- `burp_song.py`: everything the tool does, in this order: GPU encoder detection,
  Twitch search and download, sound detection, MIDI-to-audio, video rendering, and
  the command line.
- `yamnet_numpy.py` + `yamnet_weights.npz`: YAMNet on NumPy. `tools/export_yamnet.py`
  regenerates the weights from Google's SavedModel (needs TensorFlow).
- `burp_gui.py`: the desktop app; it builds command-line arguments and calls
  `burp_song.main()`. `burp_app.py` is the packaged builds' entry point.
- `test_burp_song.py`: the tests.
- `build/`: release builds. `build/ffmpeg/build.sh` (our FFmpeg), `build/burp.spec`
  (PyInstaller), `build/linux/package.sh` (tar.gz, AppImage, .deb, Flatpak) and
  `build/release-notes.md`.

Keep tests independent of live Twitch accounts and model downloads: generate small
audio/video files in temporary folders and substitute the downloader or model.
When changing VOD handling, check that a full VOD is deleted only after its clips
are saved, and kept when something fails.

Keep generated media, credentials, virtual environments, and personal file paths
out of commits. Describe behavior changes and test results, and add an entry to
CHANGELOG.md.

## Releases

Push a tag such as `v0.4.0`. `.github/workflows/release.yml` audits the dependencies,
builds FFmpeg (cached by the build script's hash), builds both editions for
Windows, macOS (Apple silicon) and Linux, and the standard edition for Intel Macs, checks each one renders a video, signs the Windows files
and publishes the GitHub release. Running the workflow by hand builds everything
without publishing. To add a new platform to an existing release, run it by hand
with `release_tag` set (e.g. `v0.3.2`): it uploads only the files that release lacks
and updates `SHA256SUMS.txt`, and refuses if the app code differs from the tag.

Windows signing uses Azure Artifact Signing through OpenID Connect, in the
**signing** environment (release tags only). Its variables are the same as Cuebot's
(see Cuebot's docs/CODE_SIGNING.md); the Azure app registration has a federated
credential for this repository's `signing` environment.

## Dependency updates

Dependabot covers the `requirements*.txt` files and GitHub Actions; `pip-audit`
blocks releases with known vulnerabilities. It can't see the source archives pinned
in `build/ffmpeg/build.sh` or the YAMNet archive in `models/` (checksum in `burp_song.py`).
Check FFmpeg, x264, Rubber Band, nv-codec-headers, AMF, oneVPL, libva, libdrm and
Vulkan-Headers for security releases now and then: update the URL and SHA-256
(`sha256sum` the new archive) and let the release workflow rebuild.
