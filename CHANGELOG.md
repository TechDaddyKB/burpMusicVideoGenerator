# Changelog

## Unreleased

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
