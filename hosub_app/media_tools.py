# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Shaghayegh Mastalipour

"""Offline FFmpeg + whisper.cpp tool auto-discovery and word-timestamp Code-Switching ASR runner.

Automatically discovers existing `tools/ffmpeg.exe`, `tools/whisper-cli.exe`, and
`models/ggml-large-v3-turbo.bin` installations (including `E:\\hosub`, parent folders,
Subtitle Edit caches, and system PATH) with zero manual setup and zero internet dependency.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time

from .exporters import parse_srt_or_vtt
from .timecage import analyze_wav, read_wav_mono_s16, snap_subtitles_to_islands, write_wav_mono_s16

# Minimum plausible file sizes (bytes) to catch truncated downloads cleanly
_MODEL_MIN_BYTES = [
    ("large-v3-turbo", 700_000_000),  # ~1.5 GB (f16) or ~800 MB (q8_0)
    ("large", 1_200_000_000),
    ("medium", 650_000_000),
    ("small", 200_000_000),
    ("base", 70_000_000),
    ("tiny", 35_000_000),
]

# Preferred model ranking when multiple `.bin` files sit inside `models/`
_MODEL_PRIORITY = [
    "ggml-large-v3-turbo.bin",
    "ggml-large-v3-turbo-q8_0.bin",
    "ggml-large-v3.bin",
    "ggml-large-v2.bin",
    "ggml-medium.bin",
    "ggml-small.bin",
    "ggml-base.bin",
    "ggml-tiny.bin",
]

_CODE_SWITCH_HINTS = {
    "zh": "中文普通话: 你好, 老师, 谢谢, 不客气, 早上好, 请问, 什么意思.",
    "ko": "한국어: 안녕하세요, 선생님, 감사합니다, 괜찮아요, 같이 연습해요.",
    "ja": "日本語: こんにちは, おはようございます, 先生, ありがとうございます.",
    "fa": "فارسی: سلام، خوش آمدید، بیایید این جمله را تمرین کنیم.",
    "ro": "Română: Bună ziua, mulțumesc, cu plăcere, să exersăm.",
    "de": "Deutsch: Guten Morgen, Dankeschön, Entschuldigung, Übung.",
    "fr": "Français: Bonjour, merci beaucoup, s'il vous plaît, répétez.",
    "es": "Español: Buenos días, muchas gracias, por favor, repitan.",
}

_PROGRESS_RE = re.compile(r"progress\s*=\s*(\d+)%")


def build_code_switching_prompt(
    base_lang: str,
    target_langs: list[str],
    lesson_vocab_hint: str = "",
) -> str:
    """Construct a concise native-script initial prompt so Whisper preserves both scripts in one sentence."""
    parts = ["Multilingual language lesson transcript; preserve each language in its native script."]
    seen = set()
    for lg in [base_lang, *target_langs]:
        code = lg.strip().lower()
        if code in _CODE_SWITCH_HINTS and code not in seen:
            parts.append(_CODE_SWITCH_HINTS[code])
            seen.add(code)
    if lesson_vocab_hint.strip():
        parts.append(f"Lesson terms: {lesson_vocab_hint.strip()[:260]}")
    return " ".join(parts)


class ToolManager:
    """Persistent configuration + automatic path discovery for offline FFmpeg & whisper.cpp."""

    def __init__(self, app_root: Path, config_path: Path) -> None:
        self.app_root = app_root.resolve()
        self.config_path = config_path
        self.config: dict[str, object] = {
            "ffmpeg_path": "",
            "whisper_path": "",
            "model_path": "",
            "threads": 0,          # 0 = auto (all logical cores)
            "use_gpu": True,
            "strategy": "balanced",  # "fast" | "balanced" | "accurate"
        }
        self._load()

    def _load(self) -> None:
        if self.config_path.is_file():
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.config.update(data)
            except Exception:
                pass

    def save(self, updates: dict[str, object]) -> dict[str, object]:
        for k in ("ffmpeg_path", "whisper_path", "model_path", "strategy"):
            if k in updates and updates[k] is not None:
                self.config[k] = str(updates[k]).strip().strip('"')
        if "threads" in updates:
            self.config["threads"] = max(0, min(128, int(updates["threads"])))
        if "use_gpu" in updates:
            self.config["use_gpu"] = bool(updates["use_gpu"])
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(self.config, indent=2, ensure_ascii=False), encoding="utf-8")
        return self.status()

    def _search_roots(self) -> list[Path]:
        roots: list[Path] = []
        cur = self.app_root
        for _ in range(5):
            roots.append(cur)
            if cur.parent == cur:
                break
            cur = cur.parent

        # Common Windows offline locations (including user's existing E:\hosub setup!)
        if os.name == "nt":
            for drv in ("E:", "D:", "C:", "F:", "G:"):
                roots.append(Path(f"{drv}/hosub"))
            appdata = os.environ.get("APPDATA")
            localapp = os.environ.get("LOCALAPPDATA")
            if appdata:
                roots.append(Path(appdata) / "hosub")
                roots.append(Path(appdata) / "Subtitle Edit" / "Whisper")
            if localapp:
                roots.append(Path(localapp) / "hosub")
        else:
            roots.append(Path.home() / "hosub")
            roots.append(Path.home() / ".local" / "share" / "hosub")

        uniq: list[Path] = []
        seen = set()
        for r in roots:
            s = str(r)
            if s not in seen and r.is_dir():
                seen.add(s)
                uniq.append(r)
        return uniq

    def resolve_ffmpeg(self) -> str | None:
        cfg = str(self.config.get("ffmpeg_path") or "").strip()
        if cfg and Path(cfg).is_file():
            return str(Path(cfg).resolve())
        names = ("ffmpeg.exe", "ffmpeg") if os.name == "nt" else ("ffmpeg", "ffmpeg.exe")
        for root in self._search_roots():
            for rel in ("tools", "tools/ffmpeg/bin", "ffmpeg/bin", "bin", "."):
                for nm in names:
                    cand = root / rel / nm
                    if cand.is_file():
                        return str(cand.resolve())
        return shutil.which("ffmpeg")

    def resolve_whisper(self) -> str | None:
        cfg = str(self.config.get("whisper_path") or "").strip()
        if cfg and Path(cfg).is_file():
            return str(Path(cfg).resolve())
        names = (
            ("whisper-cli.exe", "main.exe", "whisper-cli")
            if os.name == "nt"
            else ("whisper-cli", "whisper-cpp", "main")
        )
        for root in self._search_roots():
            for rel in ("tools", "tools/whisper", "CPP", "bin", "."):
                for nm in names:
                    cand = root / rel / nm
                    if cand.is_file():
                        return str(cand.resolve())
        return shutil.which("whisper-cli") or shutil.which("whisper-cpp")

    def list_available_models(self) -> list[dict[str, object]]:
        found: list[Path] = []
        seen: set[str] = set()
        cfg = str(self.config.get("model_path") or "").strip()
        if cfg and Path(cfg).is_file():
            rp = Path(cfg).resolve()
            found.append(rp)
            seen.add(str(rp))

        for root in self._search_roots():
            for sub in ("models", "Models", "tools/models", "."):
                mdir = root / sub
                if not mdir.is_dir():
                    continue
                for p in mdir.glob("ggml-*.bin"):
                    if p.is_file():
                        rp = p.resolve()
                        if str(rp) not in seen:
                            seen.add(str(rp))
                            found.append(rp)

        def _rank(p: Path) -> tuple[int, int]:
            nm = p.name.lower()
            try:
                idx = _MODEL_PRIORITY.index(nm)
            except ValueError:
                idx = 50
            return (idx, -p.stat().st_size)

        found.sort(key=_rank)
        out: list[dict[str, object]] = []
        for p in found:
            sz = p.stat().st_size
            min_b = 20_000_000
            low = p.name.lower()
            for tag, mb in _MODEL_MIN_BYTES:
                if tag in low and "-q5" not in low and "-q4" not in low:
                    min_b = mb
                    break
            out.append({
                "name": p.name,
                "path": str(p),
                "size_mb": round(sz / (1024 * 1024), 1),
                "complete": sz >= min_b,
            })
        return out

    def resolve_model(self) -> str | None:
        cfg = str(self.config.get("model_path") or "").strip()
        if cfg and Path(cfg).is_file():
            return str(Path(cfg).resolve())
        models = self.list_available_models()
        for m in models:
            if m["complete"]:
                return str(m["path"])
        return str(models[0]["path"]) if models else None

    def status(self) -> dict[str, object]:
        ff = self.resolve_ffmpeg()
        wh = self.resolve_whisper()
        md = self.resolve_model()
        models = self.list_available_models()
        cpu_cores = os.cpu_count() or 4
        return {
            "ffmpeg_found": bool(ff),
            "ffmpeg_path": ff or "",
            "whisper_found": bool(wh),
            "whisper_path": wh or "",
            "model_found": bool(md),
            "model_path": md or "",
            "available_models": models,
            "ready_for_asr": bool(ff and wh and md),
            "cpu_cores": cpu_cores,
            "config": dict(self.config),
            "expected_folders": {
                "tools": str(self.app_root / "tools"),
                "models": str(self.app_root / "models"),
            },
        }


# ---------------------------------------------------------------------------
# Whisper.cpp Full JSON (-ojf) Word-Timestamp Parser
# ---------------------------------------------------------------------------

def _parse_whisper_full_json(json_path: Path) -> list[dict]:
    """Parse whisper.cpp `-ojf` output into HoSub cues WITH exact word-level ms timestamps."""
    data = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
    segments = data.get("transcription") or []
    cues: list[dict] = []
    next_id = 1

    for seg in segments:
        offs = seg.get("offsets") or {}
        s_ms = int(offs.get("from", 0))
        e_ms = max(s_ms + 120, int(offs.get("to", s_ms + 500)))
        text = str(seg.get("text") or "").strip()
        if not text:
            continue

        # Merge sub-word BPE tokens into clean word/script timestamps
        words: list[dict] = []
        raw_tokens = seg.get("tokens") or []
        for tk in raw_tokens:
            t_txt = str(tk.get("text") or "")
            # Skip Whisper special control tokens like [_BEG_], [_TT_120], <|zh|>
            if not t_txt or (t_txt.startswith("[_") and t_txt.endswith("_]")) or (
                t_txt.startswith("<|") and t_txt.endswith("|>")
            ):
                continue
            t_off = tk.get("offsets") or {}
            ts = int(t_off.get("from", s_ms))
            te = max(ts + 20, int(t_off.get("to", ts + 80)))
            starts_new = t_txt.startswith(" ") or not words
            clean_tok = t_txt.strip()
            if not clean_tok:
                continue
            # Merge multi-byte UTF-8 continuation tokens or unspaced Latin subwords
            if (
                not starts_new
                and words
                and clean_tok.isascii()
                and words[-1]["text"].isascii()
                and clean_tok[0].isalnum()
            ):
                words[-1]["text"] += clean_tok
                words[-1]["end_ms"] = max(words[-1]["end_ms"], te)
            elif "\ufffd" in clean_tok:
                continue
            else:
                words.append({"text": clean_tok, "start_ms": ts, "end_ms": te})

        cues.append(
            {
                "id": next_id,
                "start_ms": s_ms,
                "end_ms": e_ms,
                "text": text,
                "translation": "",
                "locked": False,
                "group_id": 0,
                "words": words,
            }
        )
        next_id += 1

    return cues


# ---------------------------------------------------------------------------
# Background ASR Job Runner (Extract 16kHz WAV -> Time-Cages -> whisper.cpp)
# ---------------------------------------------------------------------------

class AsrJobRunner:
    """Runs offline FFmpeg + whisper.cpp transcription in a background thread with live progress."""

    def __init__(self, tools: ToolManager) -> None:
        self.tools = tools
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._cancelled = False
        self._thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._job_seq = 0
        self.state: dict[str, object] = {
            "status": "idle",   # "idle" | "running" | "done" | "error" | "cancelled"
            "percent": 0,
            "message": "Ready",
            "elapsed_sec": 0.0,
            "cue_count": 0,
            "log": "",
        }

    def get_state(self) -> dict[str, object]:
        with self._lock:
            return dict(self.state)

    def _update(self, _job_id: int | None = None, **kw: object) -> None:
        with self._lock:
            # Updates from a stale (cancelled/replaced) job must never
            # overwrite the state of a newer job.
            if _job_id is not None and _job_id != self._job_seq:
                return
            self.state.update(kw)

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            self._job_seq += 1  # invalidate the running worker immediately
            self._cancel_event.set()
            proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        with self._lock:
            self.state.update(status="cancelled", percent=0,
                              message="Transcription cancelled. You can start a new one right away.")
            th = self._thread
        # Wait briefly so the killed worker is really gone — a new job
        # started in the same click must never collide with the old one.
        if th and th is not threading.current_thread() and th.is_alive():
            th.join(timeout=3.0)

    def cancel_and_wait(self, timeout: float = 4.0) -> None:
        """Cancel any running job and give its worker a moment to exit
        (used before new media replaces the shared WAV cache)."""
        with self._lock:
            running = self.state.get("status") == "running" or bool(self._thread and self._thread.is_alive())
            th = self._thread
        if not running:
            return
        self.cancel()
        if th and th is not threading.current_thread():
            th.join(timeout=timeout)

    def start(
        self,
        *,
        media_path: Path,
        wav_cache_path: Path,
        base_lang: str,
        target_langs: list[str],
        asr_lang_mode: str = "auto",   # "auto" (multilingual code-switch) | explicit code
        lesson_vocab_hint: str = "",
        snap_timecages: bool = True,
        on_complete,
    ) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("A transcription is already running — press Cancel, wait a second, then start again.")
            self._cancelled = False
            self._cancel_event = threading.Event()
            self._job_seq += 1
            job_id = self._job_seq
            cancel_event = self._cancel_event
            self.state = {
                "status": "running",
                "percent": 3,
                "message": "Extracting 16 kHz mono waveform & VAD Time-Cages...",
                "elapsed_sec": 0.0,
                "cue_count": 0,
                "log": "",
            }

        th = threading.Thread(
            target=self._worker,
            kwargs={
                "_job_id": job_id,
                "_cancel_event": cancel_event,
                "media_path": media_path,
                "wav_cache_path": wav_cache_path,
                "base_lang": base_lang,
                "target_langs": target_langs,
                "asr_lang_mode": asr_lang_mode,
                "lesson_vocab_hint": lesson_vocab_hint,
                "snap_timecages": snap_timecages,
                "on_complete": on_complete,
            },
            daemon=True,
        )
        with self._lock:
            self._thread = th
        th.start()

    def _worker(
        self,
        *,
        _job_id: int,
        _cancel_event: threading.Event,
        media_path: Path,
        wav_cache_path: Path,
        base_lang: str,
        target_langs: list[str],
        asr_lang_mode: str,
        lesson_vocab_hint: str,
        snap_timecages: bool,
        on_complete,
    ) -> None:
        t0 = time.perf_counter()
        ff = self.tools.resolve_ffmpeg()
        wh = self.tools.resolve_whisper()
        md = self.tools.resolve_model()

        if not wh or not md:
            missing = []
            if not ff:
                missing.append("ffmpeg.exe (in tools/)")
            if not wh:
                missing.append("whisper-cli.exe (in tools/)")
            if not md:
                missing.append("ggml-*.bin model (in models/)")
            self._update(
                _job_id,
                status="error",
                percent=0,
                message="Offline engine files not found: " + ", ".join(missing)
                + ". Open 'AI Engine' in the top bar to point HoSub to your existing tools/ and models/ folders.",
            )
            return

        try:
            # Step 1: Extract clean 16 kHz mono PCM WAV
            wav_cache_path.parent.mkdir(parents=True, exist_ok=True)
            if ff:
                cmd_ff = [
                    ff, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(media_path),
                    "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                    str(wav_cache_path),
                ]
                proc = subprocess.Popen(cmd_ff, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                with self._lock:
                    self._proc = proc
                _, err_b = proc.communicate()
                if _cancel_event.is_set():
                    return
                if proc.returncode != 0 or not wav_cache_path.is_file():
                    raise RuntimeError(f"FFmpeg WAV extraction failed: {err_b.decode('utf-8', 'replace')[:300]}")
            elif media_path.suffix.lower() == ".wav":
                shutil.copy2(media_path, wav_cache_path)
            else:
                raise RuntimeError("FFmpeg is needed to decode non-WAV video/audio files.")

            # Step 2: Analyze acoustic waveform & VAD speech islands
            self._update(_job_id, percent=12, message="Scanning waveform & locking Anti-Domino Time-Cages...")
            wf = analyze_wav(wav_cache_path)

            # Step 2b: Rewrite as a canonical 44-byte-header PCM16 WAV.
            # Some whisper.cpp builds decode input with miniaudio/drwav, which can
            # stall on FFmpeg WAV metadata chunks; a plain WAV always loads instantly.
            whisper_wav = wav_cache_path
            try:
                samples, sr0 = read_wav_mono_s16(wav_cache_path)
                clean_wav = wav_cache_path.with_name("active_16k_clean.wav")
                write_wav_mono_s16(clean_wav, samples, sr0)
                whisper_wav = clean_wav
                self._update(_job_id, percent=14, message="Audio normalized to canonical WAV for Whisper...")
            except Exception:
                whisper_wav = wav_cache_path

            # Step 3: Run whisper.cpp with word-level JSON (-ojf) + SRT (-osrt) + anti-domino (-mc 0)
            cfg = self.tools.config
            threads = int(cfg.get("threads") or 0) or (os.cpu_count() or 4)
            strategy = str(cfg.get("strategy") or "balanced")
            prompt = build_code_switching_prompt(base_lang, target_langs, lesson_vocab_hint)

            with tempfile.TemporaryDirectory(prefix="hosub_asr_") as td:
                out_prefix = Path(td) / "result"
                args = [
                    wh,
                    "-m", md,
                    "-f", str(whisper_wav),
                    "-l", asr_lang_mode or "auto",
                    "-ojf",              # Full JSON with per-token millisecond timestamps!
                    "-osrt",             # Fallback SRT
                    "-of", str(out_prefix),
                    "-t", str(threads),
                    "-pp",               # Real-time progress %
                    "--prompt", prompt,
                ]
                if not cfg.get("use_gpu", True):
                    args.append("-ng")
                if strategy in ("fast", "balanced"):
                    args.extend(["-bs", "1", "-bo", "1"])
                elif strategy == "accurate":
                    args.extend(["-bs", "5", "-bo", "5"])

                self._update(
                    _job_id,
                    percent=15,
                    message=f"Running offline Whisper ({Path(md).name}, {threads} threads)...",
                    elapsed_sec=round(time.perf_counter() - t0, 1),
                )

                # Ensure DLLs next to whisper-cli.exe are found on Windows!
                wh_dir = str(Path(wh).parent)
                env = os.environ.copy()
                env["PATH"] = wh_dir + os.pathsep + env.get("PATH", "")

                wproc = subprocess.Popen(
                    args,
                    cwd=wh_dir,
                    env=env,
                    # IMPORTANT: stdout must never be an unread PIPE — whisper-cli
                    # prints the transcript there, the 64 KB pipe buffer fills up
                    # and the process deadlocks (the classic "stuck at 15%" hang).
                    # Results are read from the -ojf/-osrt files instead.
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                with self._lock:
                    self._proc = wproc

                err_tail: list[str] = []
                prog: dict[str, float] = {"pct": 0, "last_line_at": time.perf_counter()}

                def _read_stderr() -> None:
                    assert wproc.stderr is not None
                    buf = b""
                    while True:
                        chunk = wproc.stderr.read1(4096)
                        if not chunk:
                            break
                        buf += chunk
                        # whisper progress lines end with \r or \n
                        parts = buf.replace(b"\r", b"\n").split(b"\n")
                        buf = parts.pop()
                        for raw in parts:
                            line = raw.decode("utf-8", "replace")
                            if not line.strip():
                                continue
                            err_tail.append(line)
                            prog["last_line_at"] = time.perf_counter()
                            if len(err_tail) > 40:
                                err_tail.pop(0)
                            m = _PROGRESS_RE.search(line)
                            if m:
                                prog["pct"] = int(m.group(1))

                reader = threading.Thread(target=_read_stderr, daemon=True)
                reader.start()

                while wproc.poll() is None:
                    if _cancel_event.is_set():
                        try:
                            wproc.kill()
                        except Exception:
                            pass
                        break
                    wp = int(prog["pct"])
                    idle_s = time.perf_counter() - float(prog["last_line_at"])
                    mapped = 15 + int(round(wp * 0.80)) if wp else 15
                    elapsed = round(time.perf_counter() - t0, 1)
                    self._update(
                        _job_id,
                        log="\n".join(err_tail[-10:]),
                        percent=min(96, mapped),
                        message=(f"Transcribing multilingual speech… {wp}% · {elapsed:.0f}s elapsed"
                                 if wp else
                                 (f"Running offline Whisper ({Path(md).name})… {elapsed:.0f}s elapsed — no new output for {idle_s:.0f}s (see log below)"
                                  if idle_s > 90 else
                                  f"Running offline Whisper ({Path(md).name})… {elapsed:.0f}s elapsed — first progress line pending")),
                        elapsed_sec=elapsed,
                    )
                    time.sleep(0.5)

                reader.join(timeout=3)
                if _cancel_event.is_set():
                    return

                json_out = Path(f"{out_prefix}.json")
                srt_out = Path(f"{out_prefix}.srt")
                cues: list[dict] = []
                if wproc.returncode == 0 and json_out.is_file():
                    try:
                        cues = _parse_whisper_full_json(json_out)
                    except Exception:
                        cues = []
                if not cues and wproc.returncode == 0 and srt_out.is_file():
                    cues = parse_srt_or_vtt(srt_out.read_text(encoding="utf-8", errors="replace"))

                if wproc.returncode != 0:
                    detail = "".join(err_tail).strip()[-450:]
                    raise RuntimeError(f"whisper-cli exited with code {wproc.returncode}: {detail}")

            if _cancel_event.is_set():
                return
            if snap_timecages and cues and wf.get("islands"):
                snap_subtitles_to_islands(cues, wf["islands"])  # type: ignore[arg-type]

            on_complete(cues, wf)
            elapsed = round(time.perf_counter() - t0, 1)
            self._update(
                _job_id,
                log="\n".join(err_tail[-10:]) if 'err_tail' in dir() else "",
                status="done",
                percent=100,
                cue_count=len(cues),
                elapsed_sec=elapsed,
                message=f"Transcribed {len(cues)} multilingual cues with word timestamps in {elapsed}s.",
            )
        except Exception as exc:
            self._update(
                _job_id,
                status="error",
                percent=0,
                elapsed_sec=round(time.perf_counter() - t0, 1),
                message=str(exc),
            )
        finally:
            with self._lock:
                if _job_id == self._job_seq:
                    self._proc = None
