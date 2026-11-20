#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Shaghayegh Mastalipour

"""HoSub Studio 2.0 — Single-command offline launcher (zero third-party dependencies)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser

from hosub_app import __app_name__, __version__
from hosub_app.server import create_server


def _pick_free_port(host: str, preferred: int) -> int:
    for p in [preferred, *range(preferred + 1, preferred + 25)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    return preferred


def _open_app_window(url: str) -> None:
    """Open HoSub Studio in a frameless desktop-style Chrome/Edge `--app` window on Windows,
    falling back gracefully to the system's default browser."""
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
        for exe in candidates:
            if exe.is_file():
                try:
                    subprocess.Popen([str(exe), f"--app={url}", "--window-size=1440,900"])
                    return
                except Exception:
                    pass
    elif sys.platform == "darwin":
        for browser in ("google-chrome", "chromium"):
            b = shutil.which(browser)
            if b:
                subprocess.Popen([b, f"--app={url}"])
                return
    webbrowser.open(url)


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{__app_name__} v{__version__}")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Preferred TCP port (default: 8765)")
    parser.add_argument("--strict-port", action="store_true", help="Do not auto-increment if port is busy")
    parser.add_argument("--no-browser", action="store_true", help="Run server without opening a browser window")
    args = parser.parse_args()

    app_root = Path(__file__).resolve().parent
    port = args.port if args.strict_port else _pick_free_port(args.host, args.port)
    httpd, state = create_server(app_root, host=args.host, port=port)

    display_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{display_host}:{port}"
    eng = state.tools.status()
    stats = state.lexicon.stats()

    print(f"\n  {__app_name__} v{__version__}  (100% Offline · Zero External Packages)")
    print(f"  -----------------------------------------------------------------")
    print(f"  Studio URL   : {url}")
    print(f"  Dictionaries : {stats['zh_words']:,} Mandarin HSK · {stats['zh_chars']:,} Hanzi · "
          f"{stats['ko_words']:,} Korean TOPIK + Full Hangul · {stats['custom_words']:,} Custom")
    print(f"  FFmpeg       : {eng['ffmpeg_path'] or 'Not detected (place in tools/)'}")
    print(f"  Whisper.cpp  : {eng['whisper_path'] or 'Not detected (place in tools/)'}")
    print(f"  GGML Model   : {Path(eng['model_path']).name if eng['model_path'] else 'Not detected (place in models/)'}")
    print(f"  -----------------------------------------------------------------")
    print("  Keep this window open while editing. Press Ctrl+C to quit.\n", flush=True)

    if not args.no_browser:
        threading.Timer(0.45, _open_app_window, args=(url,)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down HoSub Studio cleanly. Goodbye!")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
