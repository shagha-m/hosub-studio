# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Shaghayegh Mastalipour

"""Automatic Code-Switching Target-Language Audio Cutter -> ZIP archive.

When an instructor teaches e.g. Mandarin or Korean in English, detects the exact moment the
language switches, trims out each target-language word/phrase/sentence with natural breath
padding and click-free micro-fades, names each clip after the actual phrase itself, links
the audio filenames back to the Vocabulary Flashcard sheet, and bundles everything into a `.zip`.
"""

from __future__ import annotations

import array
import csv
import io
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

from .bidi import strip_bidi_controls
from .romanizer import MultilingualLexicon, strip_inline_romanization
from .timecage import read_wav_mono_s16, write_wav_mono_s16

# Windows + macOS + Linux illegal filename characters
_UNSAFE_FILE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_MULTI_SPACE_RE = re.compile(r"\s+")
_ROMA_SLUG_RE = re.compile(r"[^A-Za-z0-9āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜü\-]+")
_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def make_safe_clip_stem(
    phrase: str,
    *,
    index: int,
    romanization: str = "",
    naming_mode: str = "word_only",  # "word_only" | "index_word" | "index_word_roma"
    max_chars: int = 56,
) -> str:
    """Create a human-searchable, 100% Windows/macOS/Linux-safe Unicode audio filename stem."""
    clean = strip_bidi_controls(phrase or "").strip()
    # Strip sentence punctuation edges while preserving internal spaces/hyphens
    clean = clean.strip(" .,!?;:،؟؛。！？、«»\"'()[]{}")
    clean = _UNSAFE_FILE_RE.sub("", clean)
    clean = _MULTI_SPACE_RE.sub(" ", clean).strip(" .")
    if not clean:
        clean = f"clip_{index:03d}"
    if len(clean) > max_chars:
        clean = clean[:max_chars].rstrip(" ._-")
    if clean.upper() in _WIN_RESERVED:
        clean = f"_{clean}"

    roma_slug = ""
    if romanization and naming_mode == "index_word_roma":
        roma_slug = _ROMA_SLUG_RE.sub("-", romanization.strip().lower()).strip("-")[:36]

    if naming_mode == "index_word":
        return f"{index:03d}_{clean}"
    if naming_mode == "index_word_roma":
        return f"{index:03d}_{clean}_{roma_slug}" if roma_slug else f"{index:03d}_{clean}"
    return clean


def plan_target_audio_clips(
    subtitles: list[dict],
    lexicon: MultilingualLexicon,
    *,
    base_lang: str = "en",
    target_langs: list[str] | None = None,
    cut_mode: str = "switch_span",     # "switch_span" (exact target phrase) | "full_cue" (whole subtitle row)
    pre_pad_ms: int = 120,
    post_pad_ms: int = 180,
    deduplicate: bool = True,
    naming_mode: str = "word_only",
    ext: str = "mp3",
    media_duration_ms: int = 0,
) -> list[dict]:
    """Build the exact cut list of target-language audio clips across all subtitle cues."""
    targets = [t.lower() for t in (target_langs or ["zh", "ko"])]
    target_set = set(targets)
    ext_clean = ext.lower().lstrip(".") or "mp3"

    raw_cuts: list[dict] = []
    seen_phrases: set[tuple[str, str]] = set()

    for row_idx, sub in enumerate(subtitles):
        cue_text = strip_inline_romanization(strip_bidi_controls(str(sub.get("text", "")))).strip()
        if not cue_text:
            continue
        cue_start = max(0, int(sub.get("start_ms", 0)))
        cue_end = max(cue_start + 80, int(sub.get("end_ms", cue_start + 500)))
        cue_dur = cue_end - cue_start

        # Strategy 1: If Whisper word-level timestamps exist on this cue, get exact ms boundaries!
        word_ts: list[dict] = sub.get("words") or []
        matched_from_words = False

        if cut_mode == "switch_span" and word_ts:
            i = 0
            nw = len(word_ts)
            while i < nw:
                wt = str(word_ts[i].get("text", "")).strip()
                if not wt:
                    i += 1
                    continue
                w_lang = lexicon.guess_word_lang(wt, fallback=base_lang)
                # Also check custom dict for Latin target words
                c_ent = lexicon.lookup(wt.strip(".,!?;:\"'"))
                if c_ent and c_ent.lang in target_set:
                    w_lang = c_ent.lang
                if w_lang in target_set and w_lang != base_lang:
                    s_ms = int(word_ts[i].get("start_ms", cue_start))
                    e_ms = int(word_ts[i].get("end_ms", s_ms + 250))
                    parts = [wt]
                    j = i + 1
                    while j < nw:
                        nwt = str(word_ts[j].get("text", "")).strip()
                        if not nwt:
                            j += 1
                            continue
                        n_lang = lexicon.guess_word_lang(nwt, fallback=base_lang)
                        nc = lexicon.lookup(nwt.strip(".,!?;:\"'"))
                        if nc and nc.lang in target_set:
                            n_lang = nc.lang
                        if n_lang == w_lang and int(word_ts[j].get("start_ms", e_ms)) - e_ms <= 650:
                            joiner = " " if w_lang not in ("zh", "ja") else ""
                            parts.append(joiner + nwt)
                            e_ms = max(e_ms, int(word_ts[j].get("end_ms", e_ms)))
                            j += 1
                        else:
                            break
                    phrase = "".join(parts).strip(" .,!?;:،؟؛。！？\"'")
                    if phrase:
                        matched_from_words = True
                        ent = lexicon.lookup(phrase, w_lang)
                        raw_cuts.append(
                            {
                                "phrase": phrase,
                                "lang": w_lang,
                                "romanization": ent.romanization if ent else lexicon.romanize_word(phrase, w_lang),
                                "meaning": ent.meaning if ent else "",
                                "row": row_idx + 1,
                                "raw_start_ms": s_ms,
                                "raw_end_ms": max(s_ms + 120, e_ms),
                                "precision": "word_timestamp",
                                "context": cue_text,
                            }
                        )
                    i = j
                else:
                    i += 1

        # Strategy 2: Token-weighted span boundaries (or full cue if requested)
        if not matched_from_words:
            spans = lexicon.find_target_spans(cue_text, base_lang=base_lang, target_langs=targets)
            if not spans:
                continue
            if cut_mode == "full_cue":
                # Name after the joined target phrase(s) or full line if line itself is purely target language
                label = spans[0]["text"] if len(spans) == 1 else " · ".join(str(s["text"]) for s in spans[:3])
                if spans[0].get("is_full_line"):
                    label = cue_text
                raw_cuts.append(
                    {
                        "phrase": str(label),
                        "lang": str(spans[0]["lang"]),
                        "romanization": " · ".join(str(s["romanization"]) for s in spans if s.get("romanization")),
                        "meaning": " / ".join(str(s["meaning"]) for s in spans if s.get("meaning")),
                        "row": row_idx + 1,
                        "raw_start_ms": cue_start,
                        "raw_end_ms": cue_end,
                        "precision": "full_cue",
                        "context": cue_text,
                    }
                )
            else:
                for sp in spans:
                    sr0 = float(sp["start_ratio"])
                    sr1 = float(sp["end_ratio"])
                    s_ms = cue_start + int(round(cue_dur * sr0))
                    e_ms = cue_start + int(round(cue_dur * sr1))
                    if e_ms - s_ms < 220:
                        e_ms = min(cue_end, s_ms + 220)
                    raw_cuts.append(
                        {
                            "phrase": str(sp["text"]),
                            "lang": str(sp["lang"]),
                            "romanization": str(sp.get("romanization", "")),
                            "meaning": str(sp.get("meaning", "")),
                            "row": row_idx + 1,
                            "raw_start_ms": s_ms,
                            "raw_end_ms": e_ms,
                            "precision": "full_cue" if sp.get("is_full_line") else "estimated_span",
                            "context": cue_text,
                        }
                    )

    # Apply deduplication, breath padding, and collision-free filename generation
    final_clips: list[dict] = []
    used_names: dict[str, int] = {}
    pre = max(0, min(1500, int(pre_pad_ms)))
    post = max(0, min(1500, int(post_pad_ms)))

    for cut in raw_cuts:
        key = (cut["lang"], cut["phrase"].lower())
        if deduplicate and key in seen_phrases:
            continue
        seen_phrases.add(key)

        idx = len(final_clips) + 1
        clip_s = max(0, int(cut["raw_start_ms"]) - pre)
        clip_e = int(cut["raw_end_ms"]) + post
        if media_duration_ms > 0:
            clip_e = min(media_duration_ms, clip_e)
        if clip_e <= clip_s + 60:
            clip_e = clip_s + 200

        stem = make_safe_clip_stem(
            cut["phrase"],
            index=idx,
            romanization=cut["romanization"],
            naming_mode=naming_mode,
        )
        low_stem = stem.lower()
        if low_stem in used_names:
            used_names[low_stem] += 1
            filename = f"{stem}_{used_names[low_stem]}.{ext_clean}"
        else:
            used_names[low_stem] = 1
            filename = f"{stem}.{ext_clean}"

        final_clips.append(
            {
                "index": idx,
                "filename": filename,
                "phrase": cut["phrase"],
                "lang": cut["lang"],
                "romanization": cut["romanization"],
                "meaning": cut["meaning"],
                "row": cut["row"],
                "start_ms": clip_s,
                "end_ms": clip_e,
                "duration_ms": clip_e - clip_s,
                "precision": cut["precision"],
                "context": cut["context"],
            }
        )

    return final_clips


def _slice_wav_pure_python(
    samples: array.array,
    sample_rate: int,
    start_ms: int,
    end_ms: int,
    fade_ms: int = 20,
) -> bytes:
    """Slice a 16-bit mono PCM WAV segment in pure Python with smooth anti-click edge fades."""
    n = len(samples)
    s_i = max(0, min(n, int(round(start_ms * sample_rate / 1000.0))))
    e_i = max(s_i + 1, min(n, int(round(end_ms * sample_rate / 1000.0))))
    seg = array.array("h", samples[s_i:e_i])
    seg_len = len(seg)
    fade_n = min(seg_len // 3, max(1, int(round(fade_ms * sample_rate / 1000.0))))
    for k in range(fade_n):
        gain = k / float(fade_n)
        seg[k] = int(seg[k] * gain)
        seg[seg_len - 1 - k] = int(seg[seg_len - 1 - k] * gain)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        tmp = Path(tf.name)
    try:
        write_wav_mono_s16(tmp, seg, sample_rate)
        return tmp.read_bytes()
    finally:
        tmp.unlink(missing_ok=True)


def build_target_audio_zip(
    *,
    media_path: Path | None,
    wav_cache_path: Path | None,
    ffmpeg_exe: str | None,
    clips: list[dict],
    audio_format: str = "mp3",   # "mp3" | "wav" | "m4a" | "ogg"
    fade_ms: int = 20,
) -> tuple[bytes, str, list[dict]]:
    """Cut all planned target-language clips and pack them + `manifest.csv` into a ZIP buffer.

    Uses FFmpeg for MP3/M4A/OGG/high-bitrate WAV when available; falls back seamlessly to the
    built-in pure-Python WAV slicer when FFmpeg is absent and a WAV source is loaded.
    Returns `(zip_bytes, actual_format, exported_clips)`.
    """
    if not clips:
        raise ValueError("No target-language words or phrases found to cut.")

    fmt = audio_format.lower().lstrip(".")
    if fmt not in ("mp3", "wav", "m4a", "ogg"):
        fmt = "mp3"

    can_ffmpeg = bool(ffmpeg_exe and media_path and media_path.is_file())
    wav_source = wav_cache_path if (wav_cache_path and wav_cache_path.is_file()) else (
        media_path if (media_path and media_path.is_file() and media_path.suffix.lower() == ".wav") else None
    )

    if not can_ffmpeg and wav_source is None:
        raise RuntimeError(
            "Audio cutting requires either an active media file with FFmpeg configured, or a WAV audio file."
        )

    use_pure_wav = (not can_ffmpeg) or (fmt == "wav" and wav_source is not None and media_path == wav_source)
    actual_fmt = "wav" if use_pure_wav else fmt

    pcm_samples: array.array | None = None
    pcm_sr = 16000
    if use_pure_wav and wav_source is not None:
        pcm_samples, pcm_sr = read_wav_mono_s16(wav_source)

    zip_buf = io.BytesIO()
    exported: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="hosub_clips_") as td:
        td_p = Path(td)
        with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for c in clips:
                fname = c["filename"]
                if actual_fmt != Path(fname).suffix.lstrip(".").lower():
                    fname = str(Path(fname).with_suffix(f".{actual_fmt}"))

                s_ms = int(c["start_ms"])
                e_ms = int(c["end_ms"])
                dur_s = max(0.08, (e_ms - s_ms) / 1000.0)
                fade_s = min(dur_s * 0.25, max(0.005, fade_ms / 1000.0))

                if use_pure_wav and pcm_samples is not None:
                    clip_bytes = _slice_wav_pure_python(pcm_samples, pcm_sr, s_ms, e_ms, fade_ms=fade_ms)
                else:
                    assert ffmpeg_exe and media_path
                    out_file = td_p / f"c_{c['index']:04d}.{actual_fmt}"
                    af = (
                        f"afade=t=in:st=0:d={fade_s:.3f},"
                        f"afade=t=out:st={max(0.0, dur_s - fade_s):.3f}:d={fade_s:.3f}"
                    )
                    codec_args = {
                        "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
                        "wav": ["-c:a", "pcm_s16le", "-ar", "44100", "-ac", "1"],
                        "m4a": ["-c:a", "aac", "-b:a", "160k"],
                        "ogg": ["-c:a", "libvorbis", "-q:a", "5"],
                    }[actual_fmt]
                    cmd = [
                        ffmpeg_exe, "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", f"{s_ms / 1000.0:.3f}",
                        "-t", f"{dur_s:.3f}",
                        "-i", str(media_path),
                        "-vn", "-af", af,
                        *codec_args,
                        str(out_file),
                    ]
                    proc = subprocess.run(cmd, capture_output=True, timeout=60)
                    if proc.returncode != 0 or not out_file.is_file():
                        # Fallback to pure-Python WAV if wav_source exists
                        if wav_source is not None:
                            if pcm_samples is None:
                                pcm_samples, pcm_sr = read_wav_mono_s16(wav_source)
                            fname = str(Path(fname).with_suffix(".wav"))
                            actual_fmt = "wav"
                            clip_bytes = _slice_wav_pure_python(pcm_samples, pcm_sr, s_ms, e_ms, fade_ms=fade_ms)
                        else:
                            err = proc.stderr.decode("utf-8", errors="replace")[:300]
                            raise RuntimeError(f"FFmpeg failed cutting '{c['phrase']}': {err}")
                    else:
                        clip_bytes = out_file.read_bytes()

                zf.writestr(fname, clip_bytes)
                rec = dict(c)
                rec["filename"] = fname
                exported.append(rec)

            # Include UTF-8-BOM manifest.csv so the ZIP is instantly browsable in Excel / book-reader apps
            csv_io = io.StringIO()
            csv_io.write("\ufeff")
            writer = csv.writer(csv_io)
            writer.writerow([
                "Index", "Audio File", "Phrase", "Romanization", "Meaning",
                "Language", "Cue #", "Start (ms)", "End (ms)", "Duration (ms)", "Precision", "Source Sentence",
            ])
            for rec in exported:
                writer.writerow([
                    rec["index"], rec["filename"], rec["phrase"], rec["romanization"], rec["meaning"],
                    rec["lang"], rec["row"], rec["start_ms"], rec["end_ms"], rec["duration_ms"],
                    rec["precision"], rec["context"],
                ])
            zf.writestr("_manifest.csv", csv_io.getvalue().encode("utf-8"))

    return zip_buf.getvalue(), actual_fmt, exported
