# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Shaghayegh Mastalipour

"""Zero-dependency local HTTP + Range-streaming server for HoSub Studio 2.0.

Runs entirely on Python 3.9+ standard library (`http.server`, `json`, `mimetypes`, `urllib`) —
never needs `pip install`, virtual environments, or internet access.
"""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import parse_qs, quote, urlparse

from . import __app_name__, __version__
from .ai_roundtrip import (
    apply_subtitle_ai_response,
    apply_vocab_ai_response,
    build_subtitle_ai_prompt,
    build_vocab_ai_prompt,
)
from .audio_cutter import build_target_audio_zip, plan_target_audio_clips
from .exporters import (
    export_ass,
    export_srt,
    export_txt,
    export_vocab_csv,
    export_vocab_json,
    export_vocab_xlsx,
    export_vtt,
    extract_full_audio,
    parse_srt_or_vtt,
    render_hardsub_video,
    render_softsub_video,
)
from .media_tools import AsrJobRunner, ToolManager
from .romanizer import MultilingualLexicon
from .timecage import analyze_wav, repair_overlaps_antidomino, snap_subtitles_to_islands, synthesize_demo_lesson_wav

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac", ".opus"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi"}
_SAFE_STEM_RE = re.compile(r"[^A-Za-z0-9_\-\u0600-\u06FF\u4E00-\u9FFF\uAC00-\uD7A3]+")


def _safe_stem(name: str, fallback: str = "lesson") -> str:
    s = _SAFE_STEM_RE.sub("_", (name or "").strip()).strip("_")
    return s[:60] or fallback


def _build_demo_project(workspace_dir: Path) -> tuple[dict, Path]:
    """Create a realistic multilingual Mandarin + Korean sample lesson with playable audio & word timestamps."""
    cues = [
        {
            "id": 1,
            "start_ms": 400,
            "end_ms": 3600,
            "text": "Welcome back! In Mandarin, we greet everyone in the morning with 早上好.",
            "translation": "خوش آمدید! در زبان چینی، صبح‌ها با عبارت 早上好 احوال‌پرسی می‌کنیم.",
            "locked": False,
            "group_id": 1,
            "words": [
                {"text": "Welcome", "start_ms": 420, "end_ms": 880},
                {"text": "back!", "start_ms": 890, "end_ms": 1220},
                {"text": "In", "start_ms": 1320, "end_ms": 1460},
                {"text": "Mandarin,", "start_ms": 1470, "end_ms": 1950},
                {"text": "we", "start_ms": 1960, "end_ms": 2090},
                {"text": "greet", "start_ms": 2100, "end_ms": 2360},
                {"text": "in", "start_ms": 2370, "end_ms": 2480},
                {"text": "the", "start_ms": 2490, "end_ms": 2600},
                {"text": "morning", "start_ms": 2610, "end_ms": 2860},
                {"text": "with", "start_ms": 2870, "end_ms": 2980},
                {"text": "早上好", "start_ms": 3000, "end_ms": 3560},
            ],
            "_demo_target_ranges": [(3000, 3560)],
        },
        {
            "id": 2,
            "start_ms": 4100,
            "end_ms": 7500,
            "text": "When addressing your teacher, always say 老师好 to show respect.",
            "translation": "هنگام خطاب قرار دادن استاد خود، همیشه برای احترام بگویید 老师好.",
            "locked": False,
            "group_id": 1,
            "words": [
                {"text": "When", "start_ms": 4120, "end_ms": 4400},
                {"text": "addressing", "start_ms": 4410, "end_ms": 4950},
                {"text": "your", "start_ms": 4960, "end_ms": 5180},
                {"text": "teacher,", "start_ms": 5190, "end_ms": 5620},
                {"text": "always", "start_ms": 5650, "end_ms": 5960},
                {"text": "say", "start_ms": 5970, "end_ms": 6160},
                {"text": "老师好", "start_ms": 6180, "end_ms": 6860},
                {"text": "to", "start_ms": 6890, "end_ms": 7020},
                {"text": "show", "start_ms": 7030, "end_ms": 7240},
                {"text": "respect.", "start_ms": 7250, "end_ms": 7480},
            ],
            "_demo_target_ranges": [(6180, 6860)],
        },
        {
            "id": 3,
            "start_ms": 8100,
            "end_ms": 11800,
            "text": "To thank someone, say 谢谢, and reply warmly with 不客气.",
            "translation": "برای تشکر بگویید 谢谢 و با گرمی پاسخ دهید 不客气.",
            "locked": False,
            "group_id": 0,
            "words": [
                {"text": "To", "start_ms": 8120, "end_ms": 8300},
                {"text": "thank", "start_ms": 8310, "end_ms": 8640},
                {"text": "someone,", "start_ms": 8650, "end_ms": 9080},
                {"text": "say", "start_ms": 9100, "end_ms": 9320},
                {"text": "谢谢", "start_ms": 9350, "end_ms": 9920},
                {"text": "and", "start_ms": 10020, "end_ms": 10250},
                {"text": "reply", "start_ms": 10260, "end_ms": 10620},
                {"text": "warmly", "start_ms": 10630, "end_ms": 10940},
                {"text": "with", "start_ms": 10950, "end_ms": 11100},
                {"text": "不客气", "start_ms": 11120, "end_ms": 11760},
            ],
            "_demo_target_ranges": [(9350, 9920), (11120, 11760)],
        },
        {
            "id": 4,
            "start_ms": 12400,
            "end_ms": 16100,
            "text": "Let's compare shopping words: 这个很便宜 means this one is very affordable.",
            "translation": "بیایید واژگان خرید را تمرین کنیم: 这个很便宜 یعنی این خیلی ارزان است.",
            "locked": True,
            "group_id": 0,
            "words": [
                {"text": "Let's", "start_ms": 12420, "end_ms": 12720},
                {"text": "compare", "start_ms": 12730, "end_ms": 13160},
                {"text": "shopping", "start_ms": 13170, "end_ms": 13580},
                {"text": "words:", "start_ms": 13590, "end_ms": 13880},
                {"text": "这个很便宜", "start_ms": 13920, "end_ms": 14880},
                {"text": "means", "start_ms": 14920, "end_ms": 15220},
                {"text": "very", "start_ms": 15230, "end_ms": 15540},
                {"text": "affordable.", "start_ms": 15550, "end_ms": 16060},
            ],
            "_demo_target_ranges": [(13920, 14880)],
        },
        {
            "id": 5,
            "start_ms": 16800,
            "end_ms": 20600,
            "text": "Now in Korean, a polite greeting is 안녕하세요, and thank you is 감사합니다.",
            "translation": "حالا در زبان کره‌ای، سلام محترمانه 안녕하세요 و تشکر رسمی 감사합니다 است.",
            "locked": False,
            "group_id": 0,
            "words": [
                {"text": "Now", "start_ms": 16820, "end_ms": 17050},
                {"text": "in", "start_ms": 17060, "end_ms": 17200},
                {"text": "Korean,", "start_ms": 17210, "end_ms": 17650},
                {"text": "greeting", "start_ms": 17660, "end_ms": 18120},
                {"text": "is", "start_ms": 18130, "end_ms": 18260},
                {"text": "안녕하세요", "start_ms": 18280, "end_ms": 19120},
                {"text": "and", "start_ms": 19180, "end_ms": 19380},
                {"text": "thank", "start_ms": 19390, "end_ms": 19620},
                {"text": "you", "start_ms": 19630, "end_ms": 19780},
                {"text": "is", "start_ms": 19790, "end_ms": 19880},
                {"text": "감사합니다", "start_ms": 19900, "end_ms": 20560},
            ],
            "_demo_target_ranges": [(18280, 19120), (19900, 20560)],
        },
        {
            "id": 6,
            "start_ms": 21200,
            "end_ms": 25100,
            "text": "If a student practices 한국말 in the 도서관, we cheer: 같이 연습해요!",
            "translation": "وقتی دانش‌آموز 한국말 را در 도서관 تمرین می‌کند، می‌گوییم: 같이 연습해요!",
            "locked": False,
            "group_id": 0,
            "words": [
                {"text": "If", "start_ms": 21220, "end_ms": 21380},
                {"text": "student", "start_ms": 21390, "end_ms": 21780},
                {"text": "practices", "start_ms": 21790, "end_ms": 22240},
                {"text": "한국말", "start_ms": 22260, "end_ms": 22880},
                {"text": "in", "start_ms": 22920, "end_ms": 23050},
                {"text": "the", "start_ms": 23060, "end_ms": 23180},
                {"text": "도서관", "start_ms": 23200, "end_ms": 23780},
                {"text": "we", "start_ms": 23820, "end_ms": 23980},
                {"text": "cheer:", "start_ms": 23990, "end_ms": 24220},
                {"text": "같이", "start_ms": 24250, "end_ms": 24580},
                {"text": "연습해요!", "start_ms": 24590, "end_ms": 25060},
            ],
            "_demo_target_ranges": [(22260, 22880), (23200, 23780), (24250, 25060)],
        },
    ]

    demo_wav = workspace_dir / "demo_multilingual_lesson.wav"
    synthesize_demo_lesson_wav(demo_wav, cues, duration_ms=26200)
    for c in cues:
        c.pop("_demo_target_ranges", None)

    wf = analyze_wav(demo_wav)
    project = {
        "title": "Sample Lesson — Mandarin & Korean Code-Switching",
        "is_demo": True,
        "media_name": "demo_multilingual_lesson.wav",
        "media_kind": "audio",
        "media_version": 1,
        "base_lang": "en",
        "target_langs": ["zh", "ko"],
        "translate_to": "fa",
        "glossary": "Pinyin = پین‌یین\npolite greeting = سلام محترمانه",
        "style": {
            "romanization_mode": "inline",    # "none" | "inline" | "stacked"
            "show_translation": True,
            "cc_enabled": True,
            "highlight_target": True,
            "enforce_bidi": True,
            "font_family": "Segoe UI, Noto Sans, sans-serif",
            "font_size": 22,
            "primary_color": "#F8FAFC",
            "target_color": "#F08BA6",
            "roma_color": "#2DD4BF",
            "translation_color": "#E2E8F0",
            "show_box": True,
            "box_color": "#090D16",
            "box_opacity": 76,
            "margin_v": 28,
        },
        "subtitles": cues,
        "vocab_overrides": {},
        "waveform": wf,
    }
    return project, demo_wav


class AppState:
    """Central session state for HoSub Studio."""

    def __init__(self, app_root: Path) -> None:
        self.app_root = app_root.resolve()
        self.static_dir = self.app_root / "hosub_app" / "static"
        self.data_dir = self.app_root / "data"
        self.runtime_dir = self.app_root / ".cache" / "hosub_runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir = self.app_root / "exports"

        self.lock = threading.RLock()
        self.lexicon = MultilingualLexicon(self.data_dir)
        self.tools = ToolManager(self.app_root, self.data_dir / "engine_config.json")
        self.asr = AsrJobRunner(self.tools)

        self.project, self.media_path = _build_demo_project(self.runtime_dir)
        self.wav_cache_path: Path | None = self.media_path

    def load_media_file(self, path: Path, original_name: str, *, clear_subtitles: bool = False) -> dict:
        # Loading new media must never fight a running transcription over the
        # shared WAV cache / project state — cancel it cleanly first.
        self.asr.cancel_and_wait()
        ext = path.suffix.lower()
        kind = "audio" if ext in AUDIO_EXTS else "video"
        wav_cache = self.runtime_dir / "active_16k.wav"
        wf = {"duration_ms": 0, "buckets_per_sec": 40, "peaks": [], "islands": []}

        # Immediately extract waveform so the Premiere-style timeline works even before ASR!
        ff = self.tools.resolve_ffmpeg()
        if ext == ".wav":
            try:
                wf = analyze_wav(path)
                wav_cache = path
            except Exception:
                if ff:
                    subprocess.run(
                        [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
                         "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(wav_cache)],
                        capture_output=True,
                        timeout=300,
                    )
                    if wav_cache.is_file():
                        wf = analyze_wav(wav_cache)
        elif ff:
            subprocess.run(
                [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
                 "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(wav_cache)],
                capture_output=True,
                timeout=300,
            )
            if wav_cache.is_file():
                wf = analyze_wav(wav_cache)

        with self.lock:
            was_demo = bool(self.project.get("is_demo"))
            self.media_path = path
            self.wav_cache_path = wav_cache if wav_cache.is_file() else None
            self.project["title"] = Path(original_name).stem
            self.project["is_demo"] = False
            self.project["media_name"] = original_name
            self.project["media_kind"] = kind
            self.project["media_version"] = int(time.time() * 1000)
            self.project["waveform"] = wf
            if clear_subtitles or was_demo:
                self.project["subtitles"] = []
                self.project["vocab_overrides"] = {}
            return self.snapshot()

    def enrich_subtitles(self) -> list[dict]:
        """Attach live inline & stacked romanization previews to each cue."""
        base = str(self.project.get("base_lang", "en"))
        targets = list(self.project.get("target_langs") or ["zh", "ko"])
        enriched: list[dict] = []
        for idx, sub in enumerate(self.project["subtitles"], start=1):
            sub["id"] = idx
            raw = str(sub.get("text", ""))
            item = dict(sub)
            item["text_inline"] = self.lexicon.annotate_line(
                raw, mode="inline", base_lang=base, target_langs=targets
            )
            item["text_stacked"] = self.lexicon.annotate_line(
                raw, mode="stacked", base_lang=base, target_langs=targets
            )
            enriched.append(item)
        return enriched

    def snapshot(self, include_waveform: bool = True) -> dict:
        with self.lock:
            data = dict(self.project)
            data["subtitles"] = self.enrich_subtitles()
            if not include_waveform:
                data.pop("waveform", None)
            data["has_media"] = bool(self.media_path and self.media_path.is_file())
            data["has_wav"] = bool(self.wav_cache_path and self.wav_cache_path.is_file())
            data["dict_stats"] = self.lexicon.stats()
            data["engine"] = self.tools.status()
            data["version"] = __version__
            return data


def make_handler(state: AppState):
    class HoSubHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:
            # Keep terminal output quiet and readable
            pass

        # --- Response Helpers ---

        def _send_json(self, payload: object, status: int = 200) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _send_error_json(self, message: str, status: int = 400) -> None:
            self._send_json({"ok": False, "error": str(message)}, status=status)

        def _send_download(self, data: bytes, filename: str, mime: str) -> None:
            ascii_fallback = re.sub(r"[^\x20-\x7E]", "_", filename).replace('"', "")
            utf8_name = quote(filename, safe="")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime)
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{utf8_name}',
            )
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _serve_file(self, path: Path, *, allow_range: bool = False) -> None:
            if not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            mime, _ = mimetypes.guess_type(path.name)
            if not mime:
                mime = "application/octet-stream"
            if mime.startswith("text/") or mime in ("application/javascript", "application/json"):
                mime += "; charset=utf-8"

            size = path.stat().st_size
            range_hdr = self.headers.get("Range") if allow_range else None

            if range_hdr and range_hdr.startswith("bytes="):
                m = re.match(r"bytes=(\d*)-(\d*)", range_hdr)
                if m:
                    s_str, e_str = m.groups()
                    start = int(s_str) if s_str else 0
                    end = int(e_str) if e_str else size - 1
                    start = max(0, min(start, size - 1))
                    end = max(start, min(end, size - 1))
                    length = end - start + 1
                    self.send_response(HTTPStatus.PARTIAL_CONTENT)
                    self.send_header("Content-Type", mime)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.send_header("Content-Length", str(length))
                    self.end_headers()
                    with path.open("rb") as f:
                        f.seek(start)
                        rem = length
                        while rem > 0:
                            chunk = f.read(min(65536, rem))
                            if not chunk:
                                break
                            try:
                                self.wfile.write(chunk)
                            except (BrokenPipeError, ConnectionResetError):
                                break
                            rem -= len(chunk)
                    return

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime)
            if allow_range:
                self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with path.open("rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        break

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))

        def _stream_upload_to_file(self, dest: Path) -> int:
            """Stream raw octet-stream body straight to disk so multi-GB videos use ~64 KB RAM."""
            length = int(self.headers.get("Content-Length") or 0)
            dest.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with dest.open("wb") as f:
                rem = length
                while rem > 0:
                    chunk = self.rfile.read(min(262144, rem))
                    if not chunk:
                        break
                    f.write(chunk)
                    written += len(chunk)
                    rem -= len(chunk)
            return written

        # --- GET Routes ---

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            route = parsed.path
            qs = parse_qs(parsed.query)

            try:
                if route == "/api/state":
                    self._send_json({"ok": True, "project": state.snapshot(include_waveform=True)})
                    return

                if route == "/api/media":
                    with state.lock:
                        mp = state.media_path
                    if not mp or not mp.is_file():
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                    self._serve_file(mp, allow_range=True)
                    return

                if route == "/api/asr/status":
                    self._send_json({"ok": True, "asr": state.asr.get_state()})
                    return

                if route == "/api/engine":
                    self._send_json({"ok": True, "engine": state.tools.status()})
                    return

                if route == "/api/vocab":
                    scope = (qs.get("scope") or ["target"])[0]
                    hide_sw = (qs.get("hide_stopwords") or ["1"])[0] != "0"
                    min_freq = int((qs.get("min_freq") or ["1"])[0])
                    with state.lock:
                        cards = state.lexicon.extract_vocabulary(
                            state.project["subtitles"],
                            base_lang=state.project["base_lang"],
                            target_langs=state.project["target_langs"],
                            scope=scope,
                            hide_stopwords=hide_sw,
                            min_freq=min_freq,
                            saved_overrides=state.project["vocab_overrides"],
                        )
                    self._send_json({"ok": True, "cards": cards})
                    return

                if route == "/api/dict/search":
                    q = (qs.get("q") or [""])[0]
                    lang = (qs.get("lang") or ["all"])[0]
                    src = (qs.get("source") or ["all"])[0]
                    with state.lock:
                        items = state.lexicon.search(q, lang=lang, source=src, limit=100)
                        st = state.lexicon.stats()
                    self._send_json({"ok": True, "items": items, "stats": st})
                    return

                if route == "/api/dict/export":
                    with state.lock:
                        tsv = state.lexicon.export_custom_tsv().encode("utf-8")
                    self._send_download(tsv, "hosub_custom_dictionary.tsv", "text/tab-separated-values; charset=utf-8")
                    return

                if route == "/api/audio-clips/preview":
                    cut_mode = (qs.get("cut_mode") or ["switch_span"])[0]
                    pre_pad = int((qs.get("pre_pad_ms") or ["120"])[0])
                    post_pad = int((qs.get("post_pad_ms") or ["180"])[0])
                    dedup = (qs.get("deduplicate") or ["1"])[0] != "0"
                    naming = (qs.get("naming_mode") or ["word_only"])[0]
                    fmt = (qs.get("format") or ["mp3"])[0]
                    with state.lock:
                        dur = int((state.project.get("waveform") or {}).get("duration_ms") or 0)
                        ff_ok = bool(state.tools.resolve_ffmpeg())
                        actual_fmt = fmt if ff_ok else "wav"
                        clips = plan_target_audio_clips(
                            state.project["subtitles"],
                            state.lexicon,
                            base_lang=state.project["base_lang"],
                            target_langs=state.project["target_langs"],
                            cut_mode=cut_mode,
                            pre_pad_ms=pre_pad,
                            post_pad_ms=post_pad,
                            deduplicate=dedup,
                            naming_mode=naming,
                            ext=actual_fmt,
                            media_duration_ms=dur,
                        )
                    self._send_json({"ok": True, "clips": clips, "actual_format": actual_fmt, "ffmpeg_available": ff_ok})
                    return

                if route == "/api/audio-clips/sample":
                    # Instant in-browser audio preview of a single target-language clip!
                    s_ms = int((qs.get("start_ms") or ["0"])[0])
                    e_ms = int((qs.get("end_ms") or ["1000"])[0])
                    with state.lock:
                        mp = state.media_path
                        wc = state.wav_cache_path
                        ff = state.tools.resolve_ffmpeg()
                    from .audio_cutter import _slice_wav_pure_python
                    from .timecage import read_wav_mono_s16

                    wav_src = wc if (wc and wc.is_file()) else (mp if (mp and mp.suffix.lower() == ".wav") else None)
                    if wav_src and wav_src.is_file():
                        samples, sr = read_wav_mono_s16(wav_src)
                        wav_b = _slice_wav_pure_python(samples, sr, s_ms, e_ms, fade_ms=20)
                        self.send_response(HTTPStatus.OK)
                        self.send_header("Content-Type", "audio/wav")
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Content-Length", str(len(wav_b)))
                        self.end_headers()
                        self.wfile.write(wav_b)
                        return
                    if ff and mp and mp.is_file():
                        dur_s = max(0.1, (e_ms - s_ms) / 1000.0)
                        proc = subprocess.run(
                            [ff, "-hide_banner", "-loglevel", "error", "-ss", f"{s_ms/1000:.3f}",
                             "-t", f"{dur_s:.3f}", "-i", str(mp), "-vn", "-f", "wav", "pipe:1"],
                            capture_output=True, timeout=30,
                        )
                        if proc.returncode == 0:
                            self.send_response(HTTPStatus.OK)
                            self.send_header("Content-Type", "audio/wav")
                            self.send_header("Content-Length", str(len(proc.stdout)))
                            self.end_headers()
                            self.wfile.write(proc.stdout)
                            return
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return

                # Static frontend assets
                rel = "index.html" if route in ("", "/") else route.lstrip("/")
                target = (state.static_dir / rel).resolve()
                if not str(target).startswith(str(state.static_dir.resolve())):
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return
                self._serve_file(target, allow_range=False)
            except Exception as exc:
                self._send_error_json(str(exc), status=500)

        # --- POST Routes ---

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            route = parsed.path
            try:
                if route == "/api/project/sync":
                    body = self._read_json()
                    with state.lock:
                        for k in ("title", "base_lang", "translate_to", "glossary"):
                            if k in body and body[k] is not None:
                                state.project[k] = str(body[k])
                        if "target_langs" in body and isinstance(body["target_langs"], list):
                            state.project["target_langs"] = [
                                str(x).strip().lower() for x in body["target_langs"] if str(x).strip()
                            ]
                        if "style" in body and isinstance(body["style"], dict):
                            state.project["style"].update(body["style"])
                        if "subtitles" in body and isinstance(body["subtitles"], list):
                            cleaned = []
                            for i, s in enumerate(body["subtitles"], start=1):
                                s_ms = max(0, int(s.get("start_ms", 0)))
                                e_ms = max(s_ms + 80, int(s.get("end_ms", s_ms + 500)))
                                cleaned.append({
                                    "id": i,
                                    "start_ms": s_ms,
                                    "end_ms": e_ms,
                                    "text": str(s.get("text", "")),
                                    "translation": str(s.get("translation", "")),
                                    "locked": bool(s.get("locked", False)),
                                    "group_id": int(s.get("group_id") or 0),
                                    "words": s.get("words") if isinstance(s.get("words"), list) else [],
                                })
                            state.project["subtitles"] = cleaned
                        subs = state.enrich_subtitles()
                    self._send_json({"ok": True, "subtitles": subs})
                    return

                if route == "/api/project/snap-timecages":
                    with state.lock:
                        islands = (state.project.get("waveform") or {}).get("islands") or []
                        changed = (
                            snap_subtitles_to_islands(state.project["subtitles"], islands)
                            if islands
                            else repair_overlaps_antidomino(state.project["subtitles"])
                        )
                        subs = state.enrich_subtitles()
                    self._send_json({"ok": True, "changed": changed, "subtitles": subs})
                    return

                if route == "/api/project/load-demo":
                    with state.lock:
                        state.project, state.media_path = _build_demo_project(state.runtime_dir)
                        state.wav_cache_path = state.media_path
                        snap = state.snapshot(include_waveform=True)
                    self._send_json({"ok": True, "project": snap})
                    return

                if route == "/api/project/import-json":
                    body = self._read_json()
                    proj = body.get("project")
                    if not isinstance(proj, dict) or "subtitles" not in proj:
                        raise ValueError("Invalid .hosub.json project file.")
                    with state.lock:
                        for k in ("title", "base_lang", "target_langs", "translate_to", "glossary", "subtitles", "vocab_overrides"):
                            if k in proj:
                                state.project[k] = proj[k]
                        if isinstance(proj.get("style"), dict):
                            state.project["style"].update(proj["style"])
                        state.project["is_demo"] = False
                        snap = state.snapshot(include_waveform=True)
                    self._send_json({"ok": True, "project": snap})
                    return

                if route == "/api/media/upload":
                    raw_name = self.headers.get("X-Filename") or "media.mp4"
                    from urllib.parse import unquote
                    orig_name = Path(unquote(raw_name)).name
                    ext = Path(orig_name).suffix.lower()
                    if ext not in AUDIO_EXTS and ext not in VIDEO_EXTS:
                        raise ValueError(f"Unsupported media extension '{ext}'.")
                    clear_subs = (self.headers.get("X-Clear-Subtitles") or "0") == "1"
                    # Clean prior uploaded media files
                    for old in state.runtime_dir.glob("uploaded_media.*"):
                        old.unlink(missing_ok=True)
                    dest = state.runtime_dir / f"uploaded_media{ext}"
                    self._stream_upload_to_file(dest)
                    snap = state.load_media_file(dest, orig_name, clear_subtitles=clear_subs)
                    self._send_json({"ok": True, "project": snap})
                    return

                if route == "/api/media/open-local":
                    # Instant zero-copy opening for large local files when running on the user's PC!
                    body = self._read_json()
                    fp = Path(str(body.get("path") or "").strip().strip('"')).expanduser()
                    if not fp.is_file():
                        raise ValueError(f"File not found: {fp}")
                    if fp.suffix.lower() not in AUDIO_EXTS | VIDEO_EXTS:
                        raise ValueError(f"Unsupported media type: {fp.suffix}")
                    snap = state.load_media_file(
                        fp.resolve(), fp.name, clear_subtitles=bool(body.get("clear_subtitles", False))
                    )
                    self._send_json({"ok": True, "project": snap})
                    return

                if route == "/api/subtitles/import":
                    body = self._read_json()
                    cues = parse_srt_or_vtt(str(body.get("content") or ""))
                    if not cues:
                        raise ValueError("No valid subtitle timestamps found in the file.")
                    with state.lock:
                        state.project["subtitles"] = cues
                        state.project["is_demo"] = False
                        if body.get("filename"):
                            state.project["title"] = Path(str(body["filename"])).stem
                        subs = state.enrich_subtitles()
                    self._send_json({"ok": True, "count": len(subs), "subtitles": subs})
                    return

                if route == "/api/asr/start":
                    body = self._read_json()
                    with state.lock:
                        mp = state.media_path
                        if not mp or not mp.is_file():
                            raise ValueError("Load a video or audio file first.")
                        base_lang = str(body.get("base_lang") or state.project["base_lang"])
                        target_langs = list(body.get("target_langs") or state.project["target_langs"])
                        state.project["base_lang"] = base_lang
                        state.project["target_langs"] = target_langs

                    def _on_done(cues: list[dict], wf: dict) -> None:
                        with state.lock:
                            state.project["subtitles"] = cues
                            state.project["waveform"] = wf
                            state.project["is_demo"] = False
                            state.wav_cache_path = state.runtime_dir / "active_16k.wav"

                    state.asr.start(
                        media_path=mp,
                        wav_cache_path=state.runtime_dir / "active_16k.wav",
                        base_lang=base_lang,
                        target_langs=target_langs,
                        asr_lang_mode=str(body.get("asr_lang_mode") or "auto"),
                        lesson_vocab_hint=str(body.get("lesson_vocab_hint") or ""),
                        snap_timecages=bool(body.get("snap_timecages", True)),
                        on_complete=_on_done,
                    )
                    self._send_json({"ok": True, "asr": state.asr.get_state()})
                    return

                if route == "/api/asr/cancel":
                    state.asr.cancel()
                    self._send_json({"ok": True, "asr": state.asr.get_state()})
                    return

                if route == "/api/engine/save":
                    body = self._read_json()
                    st = state.tools.save(body)
                    self._send_json({"ok": True, "engine": st})
                    return

                # --- AI Round-Trip (Subtitles & Vocabulary) ---

                if route == "/api/ai/subtitles-prompt":
                    body = self._read_json()
                    with state.lock:
                        res = build_subtitle_ai_prompt(
                            state.project["subtitles"],
                            task=str(body.get("task") or "proofread_and_translate"),
                            base_lang=state.project["base_lang"],
                            target_langs=state.project["target_langs"],
                            translate_to=str(body.get("translate_to") or state.project["translate_to"]),
                            preserve_target_script=bool(body.get("preserve_target_script", True)),
                            glossary_text=str(body.get("glossary") if "glossary" in body else state.project["glossary"]),
                            batch_start=int(body.get("batch_start") or 0),
                            batch_size=int(body.get("batch_size") or 80),
                        )
                    self._send_json({"ok": True, **res})
                    return

                if route == "/api/ai/subtitles-apply":
                    body = self._read_json()
                    with state.lock:
                        report = apply_subtitle_ai_response(
                            state.project["subtitles"],
                            str(body.get("response") or ""),
                            state.lexicon,
                            task=str(body.get("task") or "proofread_and_translate"),
                            base_lang=state.project["base_lang"],
                            target_langs=state.project["target_langs"],
                            enforce_bidi=bool(state.project["style"].get("enforce_bidi", True)),
                        )
                        subs = state.enrich_subtitles()
                    self._send_json({"ok": True, "report": report, "subtitles": subs})
                    return

                if route == "/api/ai/vocab-prompt":
                    body = self._read_json()
                    with state.lock:
                        cards = state.lexicon.extract_vocabulary(
                            state.project["subtitles"],
                            base_lang=state.project["base_lang"],
                            target_langs=state.project["target_langs"],
                            scope=str(body.get("scope") or "target"),
                            hide_stopwords=bool(body.get("hide_stopwords", True)),
                            min_freq=int(body.get("min_freq") or 1),
                            saved_overrides=state.project["vocab_overrides"],
                        )
                        prompt = build_vocab_ai_prompt(
                            cards,
                            translate_to=str(body.get("translate_to") or state.project["translate_to"]),
                            include_only_selected=True,
                        )
                    self._send_json({"ok": True, "prompt": prompt, "card_count": sum(1 for c in cards if c["selected"])})
                    return

                if route == "/api/ai/vocab-apply":
                    body = self._read_json()
                    with state.lock:
                        cards = state.lexicon.extract_vocabulary(
                            state.project["subtitles"],
                            base_lang=state.project["base_lang"],
                            target_langs=state.project["target_langs"],
                            scope=str(body.get("scope") or "target"),
                            hide_stopwords=bool(body.get("hide_stopwords", True)),
                            min_freq=int(body.get("min_freq") or 1),
                            saved_overrides=state.project["vocab_overrides"],
                        )
                        report = apply_vocab_ai_response(
                            cards,
                            str(body.get("response") or ""),
                            state.project["vocab_overrides"],
                        )
                        # Optional: also persist new meanings to Custom Dictionary!
                        saved_to_dict = 0
                        if body.get("save_to_dictionary"):
                            for c in cards:
                                ov = state.project["vocab_overrides"].get(c["id"]) or {}
                                if ov.get("meaning"):
                                    state.lexicon.upsert_custom(
                                        c["word"], c["lang"], c["romanization"],
                                        ov["meaning"], c.get("level") or "Lesson", ov.get("pos") or c.get("pos") or "",
                                    )
                                    saved_to_dict += 1
                        updated_cards = state.lexicon.extract_vocabulary(
                            state.project["subtitles"],
                            base_lang=state.project["base_lang"],
                            target_langs=state.project["target_langs"],
                            scope=str(body.get("scope") or "target"),
                            hide_stopwords=bool(body.get("hide_stopwords", True)),
                            min_freq=int(body.get("min_freq") or 1),
                            saved_overrides=state.project["vocab_overrides"],
                        )
                    self._send_json({
                        "ok": True,
                        "report": {**report, "saved_to_dict": saved_to_dict},
                        "cards": updated_cards,
                    })
                    return

                if route == "/api/vocab/update":
                    body = self._read_json()
                    cid = str(body.get("id") or "")
                    if not cid:
                        raise ValueError("Missing card id.")
                    with state.lock:
                        ov = state.project["vocab_overrides"].setdefault(cid, {})
                        for k in ("romanization", "meaning", "pos", "level", "note", "audio_file"):
                            if k in body:
                                ov[k] = str(body[k])
                        if "selected" in body:
                            ov["selected"] = bool(body["selected"])
                        if body.get("save_to_dictionary") and body.get("word"):
                            state.lexicon.upsert_custom(
                                str(body["word"]),
                                str(body.get("lang") or ""),
                                str(ov.get("romanization") or body.get("romanization") or ""),
                                str(ov.get("meaning") or body.get("meaning") or ""),
                                str(ov.get("level") or body.get("level") or "Custom"),
                                str(ov.get("pos") or body.get("pos") or ""),
                            )
                    self._send_json({"ok": True})
                    return

                if route == "/api/vocab/select-all":
                    body = self._read_json()
                    sel = bool(body.get("selected", True))
                    ids = body.get("ids") or []
                    with state.lock:
                        for cid in ids:
                            state.project["vocab_overrides"].setdefault(str(cid), {})["selected"] = sel
                    self._send_json({"ok": True})
                    return

                # --- Custom Dictionary CRUD & Bulk Import ---

                if route == "/api/dict/upsert":
                    body = self._read_json()
                    with state.lock:
                        ent = state.lexicon.upsert_custom(
                            surface=str(body.get("surface") or ""),
                            lang=str(body.get("lang") or ""),
                            romanization=str(body.get("romanization") or ""),
                            meaning=str(body.get("meaning") or ""),
                            level=str(body.get("level") or "Custom"),
                            pos=str(body.get("pos") or ""),
                        )
                        subs = state.enrich_subtitles()
                        st = state.lexicon.stats()
                    self._send_json({"ok": True, "entry": ent.to_dict(), "stats": st, "subtitles": subs})
                    return

                if route == "/api/dict/delete":
                    body = self._read_json()
                    with state.lock:
                        deleted = state.lexicon.delete_custom(
                            str(body.get("surface") or ""), str(body.get("lang") or "")
                        )
                        subs = state.enrich_subtitles()
                        st = state.lexicon.stats()
                    self._send_json({"ok": True, "deleted": deleted, "stats": st, "subtitles": subs})
                    return

                if route == "/api/dict/import":
                    from urllib.parse import unquote
                    fname = unquote(self.headers.get("X-Filename") or "words.tsv")
                    def_lang = self.headers.get("X-Default-Lang") or "zh"
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length)
                    with state.lock:
                        count = state.lexicon.import_custom_rows(raw, fname, default_lang=def_lang)
                        subs = state.enrich_subtitles()
                        st = state.lexicon.stats()
                    self._send_json({"ok": True, "imported": count, "stats": st, "subtitles": subs})
                    return

                # --- File Downloads & Media Exports ---

                if route == "/api/export/subtitles":
                    body = self._read_json()
                    fmt = str(body.get("format") or "srt").lower()
                    roma = str(body.get("romanization_mode") or state.project["style"]["romanization_mode"])
                    cmode = str(body.get("content_mode") or "bilingual")
                    bidi = bool(body.get("enforce_bidi", True))
                    with state.lock:
                        stem = _safe_stem(state.project["title"])
                        subs = state.project["subtitles"]
                        base = state.project["base_lang"]
                        targets = state.project["target_langs"]
                        style = dict(state.project["style"])
                        style["romanization_mode"] = roma
                        if fmt == "ass":
                            data = export_ass(subs, state.lexicon, style, base_lang=base, target_langs=targets, content_mode=cmode)
                            self._send_download(data, f"{stem}.ass", "text/plain; charset=utf-8")
                        elif fmt == "vtt":
                            data = export_vtt(subs, state.lexicon, base_lang=base, target_langs=targets, romanization_mode=roma, content_mode=cmode, enforce_bidi=bidi)
                            self._send_download(data, f"{stem}.vtt", "text/vtt; charset=utf-8")
                        elif fmt == "txt":
                            data = export_txt(subs, state.lexicon, base_lang=base, target_langs=targets, romanization_mode=roma, content_mode=cmode, paragraph=bool(body.get("paragraph", False)))
                            self._send_download(data, f"{stem}_transcript.txt", "text/plain; charset=utf-8")
                        elif fmt == "hosub":
                            proj_copy = {
                                k: state.project[k]
                                for k in ("title", "base_lang", "target_langs", "translate_to", "glossary", "style", "subtitles", "vocab_overrides")
                            }
                            data = json.dumps({"generator": f"{__app_name__} {__version__}", "project": proj_copy}, ensure_ascii=False, indent=2).encode("utf-8")
                            self._send_download(data, f"{stem}.hosub.json", "application/json; charset=utf-8")
                        else:
                            data = export_srt(subs, state.lexicon, base_lang=base, target_langs=targets, romanization_mode=roma, content_mode=cmode, enforce_bidi=bidi)
                            self._send_download(data, f"{stem}.srt", "application/x-subrip; charset=utf-8")
                    return

                if route == "/api/export/vocab":
                    body = self._read_json()
                    fmt = str(body.get("format") or "xlsx").lower()
                    with state.lock:
                        stem = _safe_stem(state.project["title"])
                        cards = state.lexicon.extract_vocabulary(
                            state.project["subtitles"],
                            base_lang=state.project["base_lang"],
                            target_langs=state.project["target_langs"],
                            scope=str(body.get("scope") or "target"),
                            hide_stopwords=bool(body.get("hide_stopwords", True)),
                            min_freq=int(body.get("min_freq") or 1),
                            saved_overrides=state.project["vocab_overrides"],
                        )
                        title = str(state.project["title"])
                    if fmt == "csv":
                        data = export_vocab_csv(cards, only_selected=True)
                        self._send_download(data, f"{stem}_flashcards.csv", "text/csv; charset=utf-8")
                    elif fmt == "json":
                        data = export_vocab_json(cards, project_title=title, only_selected=True)
                        self._send_download(data, f"{stem}_flashcards.json", "application/json; charset=utf-8")
                    else:
                        data = export_vocab_xlsx(cards, project_title=title, only_selected=True)
                        self._send_download(
                            data,
                            f"{stem}_flashcards.xlsx",
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        )
                    return

                if route == "/api/export/target-audio-zip":
                    body = self._read_json()
                    cut_mode = str(body.get("cut_mode") or "switch_span")
                    pre_pad = int(body.get("pre_pad_ms") if body.get("pre_pad_ms") is not None else 120)
                    post_pad = int(body.get("post_pad_ms") if body.get("post_pad_ms") is not None else 180)
                    dedup = bool(body.get("deduplicate", True))
                    naming = str(body.get("naming_mode") or "word_only")
                    fmt = str(body.get("format") or "mp3")
                    link_to_vocab = bool(body.get("link_to_vocab", True))

                    with state.lock:
                        stem = _safe_stem(state.project["title"])
                        dur = int((state.project.get("waveform") or {}).get("duration_ms") or 0)
                        mp = state.media_path
                        wc = state.wav_cache_path
                        ff = state.tools.resolve_ffmpeg()
                        clips = plan_target_audio_clips(
                            state.project["subtitles"],
                            state.lexicon,
                            base_lang=state.project["base_lang"],
                            target_langs=state.project["target_langs"],
                            cut_mode=cut_mode,
                            pre_pad_ms=pre_pad,
                            post_pad_ms=post_pad,
                            deduplicate=dedup,
                            naming_mode=naming,
                            ext=fmt,
                            media_duration_ms=dur,
                        )

                    zip_bytes, _, exported = build_target_audio_zip(
                        media_path=mp,
                        wav_cache_path=wc,
                        ffmpeg_exe=ff,
                        clips=clips,
                        audio_format=fmt,
                        fade_ms=20,
                    )

                    # Auto-link generated clip filenames into Vocabulary Flashcards for Excel & book-reader!
                    if link_to_vocab:
                        with state.lock:
                            cards = state.lexicon.extract_vocabulary(
                                state.project["subtitles"],
                                base_lang=state.project["base_lang"],
                                target_langs=state.project["target_langs"],
                                scope="target",
                                hide_stopwords=False,
                                saved_overrides=state.project["vocab_overrides"],
                            )
                            for c in cards:
                                w = c["word"]
                                # Exact match first, then phrase containment
                                hit = next((r["filename"] for r in exported if r["phrase"] == w), None)
                                if not hit:
                                    hit = next((r["filename"] for r in exported if w in r["phrase"]), None)
                                if hit:
                                    state.project["vocab_overrides"].setdefault(c["id"], {})["audio_file"] = hit

                    self._send_download(zip_bytes, f"{stem}_target_audio_clips.zip", "application/zip")
                    return

                if route == "/api/export/full-audio":
                    body = self._read_json()
                    fmt = str(body.get("format") or "mp3")
                    with state.lock:
                        stem = _safe_stem(state.project["title"])
                        mp = state.media_path
                        ff = state.tools.resolve_ffmpeg()
                    if not mp or not mp.is_file():
                        raise ValueError("No media file is loaded.")
                    audio_bytes, actual_fmt = extract_full_audio(mp, ff, audio_format=fmt)
                    mime_map = {"mp3": "audio/mpeg", "wav": "audio/wav", "flac": "audio/flac", "m4a": "audio/mp4"}
                    self._send_download(audio_bytes, f"{stem}_audio.{actual_fmt}", mime_map.get(actual_fmt, "application/octet-stream"))
                    return

                if route == "/api/export/video":
                    body = self._read_json()
                    mode = str(body.get("mode") or "softsub")  # "softsub" | "hardsub"
                    cmode = str(body.get("content_mode") or "bilingual")
                    container = str(body.get("container") or "mkv")
                    with state.lock:
                        mp = state.media_path
                        kind = state.project.get("media_kind")
                        ff = state.tools.resolve_ffmpeg()
                        stem = _safe_stem(state.project["title"])
                        subs = list(state.project["subtitles"])
                        style = dict(state.project["style"])
                        base = state.project["base_lang"]
                        targets = list(state.project["target_langs"])
                    if not mp or not mp.is_file() or kind != "video":
                        raise ValueError("Load a video file (MP4/MKV/MOV/WEBM) to export subtitled video.")
                    if not ff:
                        raise ValueError("FFmpeg is not configured. Open 'AI Engine' to set tools/ffmpeg.exe.")
                    state.exports_dir.mkdir(parents=True, exist_ok=True)
                    if mode == "hardsub":
                        out_p = render_hardsub_video(
                            mp, ff, subs, state.lexicon, style,
                            base_lang=base, target_langs=targets, content_mode=cmode,
                            output_path=state.exports_dir / f"{stem}_hardsub.mp4",
                        )
                    else:
                        out_p = render_softsub_video(
                            mp, ff, subs, state.lexicon,
                            base_lang=base, target_langs=targets, content_mode=cmode,
                            container=container,
                            output_path=state.exports_dir / f"{stem}_softsub.{container}",
                        )
                    # For videos under 150 MB also offer browser download; always save cleanly to exports/!
                    self._send_json({
                        "ok": True,
                        "saved_path": str(out_p),
                        "filename": out_p.name,
                        "size_mb": round(out_p.stat().st_size / (1024 * 1024), 2),
                    })
                    return

                self._send_error_json(f"Unknown endpoint: {route}", status=404)
            except Exception as exc:
                self._send_error_json(str(exc), status=400)

    return HoSubHandler


def create_server(app_root: Path, host: str = "127.0.0.1", port: int = 8765) -> tuple[ThreadingHTTPServer, AppState]:
    state = AppState(app_root)
    httpd = ThreadingHTTPServer((host, port), make_handler(state))
    httpd.daemon_threads = True
    return httpd, state
