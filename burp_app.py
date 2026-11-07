#!/usr/bin/env python3
"""Entry point for the packaged app: opens the window, or runs the command line when given arguments.

    burp-music-video-generator                       # the app
    burp-music-video-generator emmytheaxolotl --midi song.mid   # the command line
"""
import os
from pathlib import Path
import sys


def bundle_dir():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def main():
    # Packaged builds carry their own ffmpeg; use it before anything on the system.
    ffmpeg_dir = bundle_dir()/'ffmpeg'
    if ffmpeg_dir.is_dir():
        os.environ['PATH'] = str(ffmpeg_dir)+os.pathsep+os.environ.get('PATH', '')
    # The bundled OpenSSL looks for CA certificates where the build machine kept them, which
    # differs between distributions; use certifi's for anything that relies on the default.
    if getattr(sys, 'frozen', False) and 'SSL_CERT_FILE' not in os.environ:
        import certifi
        os.environ['SSL_CERT_FILE'] = certifi.where()
    if len(sys.argv) > 1:
        import burp_song
        try:
            burp_song.main()
        except KeyboardInterrupt:
            raise SystemExit('Interrupted. Rerun the same command to continue where it stopped.')
        except (RuntimeError, ValueError, OSError, ImportError) as exc:
            raise SystemExit(str(exc))
    else:
        import burp_gui
        burp_gui.main()


if __name__ == '__main__':
    main()
