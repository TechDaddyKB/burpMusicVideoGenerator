# Changelog

## Unreleased

- Add an Intel Mac download (`macos-x64.dmg`, macOS 11 or later, standard edition
  only: TensorFlow has no Intel Mac builds after 2.16), also attached to 0.3.2.
- The release workflow, run by hand with `release_tag`, adds files that an existing
  release is missing.

## 0.3.2 — 2026-11-07

- Fix "dict() got multiple values for keyword argument 'quiet'" when downloading a
  broadcast: every VOD download failed in 0.3.0 and 0.3.1. A new test runs the real
  download code against a stand-in yt-dlp, which the other tests skipped.

## 0.3.1 — 2026-11-07

- Fix "CERTIFICATE_VERIFY_FAILED" in the Linux downloads on distributions that keep
  CA certificates outside Ubuntu's location (Arch, Fedora, ...): HTTPS now trusts
  certifi's certificate authorities, which are bundled. Affected Twitch search and
  VOD downloads.
- Add `--search NAME` to the command line; the release workflow uses it to check
  HTTPS inside each Linux and macOS package.
- Update numpy to 2.5, scipy to 1.18, TensorFlow to 2.21 and mido to 1.3.3.

## 0.3.0 — 2026-11-07

- Release downloads for Windows (zip, code-signed), macOS (dmg, Apple silicon) and
  Linux (tar.gz, AppImage, .deb, Flatpak) that need no Python or FFmpeg installed,
  in two editions: standard (NumPy YAMNet) and TensorFlow.
- Bundle a purpose-built FFmpeg (`build/ffmpeg/build.sh`): only the formats, codecs
  and filters the app uses, plus NVENC, AMF, Quick Sync, VAAPI, Vulkan, D3D11/DXVA2
  or VideoToolbox depending on the platform.
- Add `yamnet_numpy.py`, YAMNet on NumPy with Google's weights; it matches
  TensorFlow's scores within 0.0001. TensorFlow is now optional
  (`requirements-tensorflow.txt`, `--engine`); `tensorflow-hub` is no longer used.
- Replace OpenCV with Pillow (much smaller downloads); require Pillow 13 for its
  security fixes.
- Add an app icon, CodeQL scanning, Dependabot, and pip-audit checks that block
  releases with vulnerable dependencies.
- Packaged builds save videos to the user's Videos/Movies folder and keep clips in
  the user's data folder. FFmpeg no longer flashes console windows on Windows, and
  render failures report FFmpeg's error.
- Downloads are remuxed to MP4 with yt-dlp's native HLS downloader.
- Detect any of YAMNet's 521 sounds, or several at once, instead of only burps
  (still the default): `--sound NAME` (repeatable), `--list-sounds`, and a
  searchable multi-select list in the app. Clips are now stored per sound choice
  in `clips/<sounds>/<vod-id>/clip-NNNN.mp4`. Bundle `yamnet_sounds.txt`.
- Add `burp_gui.py`, a desktop app (Tkinter) with Twitch streamer search,
  optional dates, a confidence slider, MIDI and output pickers, and a progress log.
- Encode video on the GPU when possible (NVENC, Quick Sync, AMF, VideoToolbox,
  VAAPI or Vulkan, whichever works first), falling back to the CPU. Add `--cpu-only`.
- Let TensorFlow grow GPU memory as needed when YAMNet runs on a GPU.
- Add `search_channels()` using Twitch's public web API.
- Simplify the tool into one script, `burp_song.py`, replacing `burp_song_v2.py`
  and its helper modules `burp_song.py` (old renderer) and `sneeze_compilation.py`.
- New options: `--confidence` (was `--burp-threshold`), `--all`, and dates that
  can be given separately. Without dates, existing clips are used, or the whole
  VOD archive is downloaded when there are none.
- Full VODs are always deleted once their clips are saved. Removed
  `--keep-vods`, `--reuse-dir`, `--scan-seconds`, `--max-vods`, `--list-only`,
  `--download-format`, `--encoder`, track/channel/octave selection, size/fps
  options, XSPF playlists, and the JSON report next to the output.
- Downloads are capped at 720p, the size clips are rendered at.
- Default work folder is now `burp-work/USERNAME/`, with clips in `clips/<vod-id>/`.
- Add the MIT License.
- Ignore temporary render folders from interrupted runs and common test-tool
  and editor leftovers.

## 0.2.0 — 2026-11-07

- Package the v2 generator, required helper modules, tests and requirements in a
  standalone Git repository.
- Add portable setup instructions, examples, generated-media ignore rules, and
  a GitHub Actions test workflow.
- Support username/date-based Twitch archive discovery, burp extraction, and MIDI
  song generation without generated tile labels.
- Reuse existing clips without network requests when dates are omitted.
- Delete completed v2-owned full downloads after successful extraction, while
  retaining failed, partial-scan, explicitly kept and pre-existing source videos.
