# Changelog

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
