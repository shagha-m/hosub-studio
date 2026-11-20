# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Shaghayegh Mastalipour

"""Acoustic waveform analyzer, VAD speech-island detector, and Anti-Domino Time-Cage solver.

Properly parses arbitrary RIFF/WAVE chunk layouts (including FFmpeg's LIST/INFO metadata chunks)
using zero external packages.
"""

from __future__ import annotations

import array
import math
from pathlib import Path
import struct
import sys


def read_wav_mono_s16(wav_path: str | Path) -> tuple[array.array, int]:
    """Parse a 16-bit PCM RIFF/WAVE file (any header chunk layout) into mono int16 samples + sample_rate."""
    p = Path(wav_path)
    raw = p.read_bytes()
    if len(raw) < 44 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError("Not a valid RIFF/WAVE file.")

    pos = 12
    n = len(raw)
    audio_fmt = 0
    channels = 0
    sample_rate = 0
    bits_per_sample = 0
    data_offset = -1
    data_size = 0

    while pos + 8 <= n:
        chunk_id = raw[pos : pos + 4]
        chunk_sz = struct.unpack_from("<I", raw, pos + 4)[0]
        body_start = pos + 8
        body_end = min(n, body_start + chunk_sz)
        if chunk_id == b"fmt " and chunk_sz >= 16:
            audio_fmt, channels, sample_rate, _, _, bits_per_sample = struct.unpack_from(
                "<HHIIHH", raw, body_start
            )
        elif chunk_id == b"data":
            data_offset = body_start
            data_size = body_end - body_start
            break
        # RIFF chunks are word-aligned (pad odd sizes by 1 byte)
        pos = body_start + chunk_sz + (chunk_sz & 1)

    if audio_fmt != 1 or channels < 1 or sample_rate <= 0 or bits_per_sample != 16 or data_offset < 0:
        raise ValueError(
            f"Unsupported WAV format (fmt={audio_fmt}, ch={channels}, sr={sample_rate}, bits={bits_per_sample})."
        )

    usable = data_size - (data_size % (channels * 2))
    samples = array.array("h")
    samples.frombytes(raw[data_offset : data_offset + usable])
    if sys.byteorder != "little":
        samples.byteswap()

    if channels == 1:
        return samples, sample_rate

    # Downmix multi-channel by taking channel 0 (fast & clean for speech)
    mono = samples[0::channels]
    return mono, sample_rate


def write_wav_mono_s16(wav_path: str | Path, samples: array.array, sample_rate: int) -> Path:
    """Write a clean standard 16-bit mono PCM WAV file."""
    out = Path(wav_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    pcm = samples
    if sys.byteorder != "little":
        pcm = array.array("h", samples)
        pcm.byteswap()
    raw = pcm.tobytes()
    data_sz = len(raw)
    byte_rate = sample_rate * 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_sz,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        byte_rate,
        2,
        16,
        b"data",
        data_sz,
    )
    out.write_bytes(header + raw)
    return out


def analyze_wav(
    wav_path: str | Path,
    *,
    buckets_per_sec: int = 40,
    min_island_ms: int = 180,
    hangover_ms: int = 240,
) -> dict[str, object]:
    """Extract normalized waveform peaks, duration_ms, and adaptive VAD speech islands."""
    samples, sr = read_wav_mono_s16(wav_path)
    total_frames = len(samples)
    if total_frames == 0:
        return {"duration_ms": 0, "buckets_per_sec": buckets_per_sec, "peaks": [], "islands": []}

    duration_ms = int(round(total_frames * 1000.0 / sr))
    bps = max(10, min(100, int(buckets_per_sec)))
    step = max(1, sr // bps)
    # Cap total peak buckets at 24,000 (~10 min at 40 Hz, scales smoothly for 2-hour lectures)
    if total_frames // step > 24000:
        step = max(1, total_frames // 24000)
        bps = max(1, int(round(sr / step)))

    peaks: list[float] = []
    # Fast chunked peak scan (array min/max in C is ~30x faster than Python per-sample loops)
    for start in range(0, total_frames, step):
        chunk = samples[start : start + step]
        hi = max(chunk)
        lo = -min(chunk)
        pk = (hi if hi >= lo else lo) / 32768.0
        peaks.append(round(min(1.0, pk), 4))

    # Adaptive noise floor -> speech threshold
    if peaks:
        sorted_p = sorted(peaks)
        p20 = sorted_p[int(len(sorted_p) * 0.20)]
        p85 = sorted_p[int(len(sorted_p) * 0.85)]
        threshold = max(0.035, min(0.18, p20 * 2.2 + (p85 - p20) * 0.14))
    else:
        threshold = 0.05

    hangover_buckets = max(2, int(round((hangover_ms / 1000.0) * bps)))
    ms_per_bucket = (step * 1000.0) / sr

    islands: list[dict[str, object]] = []
    in_speech = False
    i_start = 0
    silence = 0
    max_e = 0.0

    for idx, amp in enumerate(peaks):
        if amp >= threshold:
            if not in_speech:
                in_speech = True
                i_start = idx
                max_e = amp
            elif amp > max_e:
                max_e = amp
            silence = 0
        elif in_speech:
            silence += 1
            if silence >= hangover_buckets:
                end_b = idx - silence + 1
                s_ms = int(round(i_start * ms_per_bucket))
                e_ms = int(round(end_b * ms_per_bucket))
                if e_ms - s_ms >= min_island_ms:
                    islands.append({"start_ms": s_ms, "end_ms": e_ms, "peak": round(max_e, 3)})
                in_speech = False
                silence = 0

    if in_speech:
        s_ms = int(round(i_start * ms_per_bucket))
        e_ms = duration_ms
        if e_ms - s_ms >= min_island_ms:
            islands.append({"start_ms": s_ms, "end_ms": e_ms, "peak": round(max_e, 3)})

    return {
        "duration_ms": duration_ms,
        "sample_rate": sr,
        "buckets_per_sec": round(1000.0 / ms_per_bucket, 3),
        "peaks": peaks,
        "islands": islands,
    }


def snap_subtitles_to_islands(
    subtitles: list[dict],
    islands: list[dict],
    *,
    max_snap_ms: int = 550,
    min_gap_ms: int = 35,
) -> int:
    """Snap unlocked subtitle edges to nearby VAD acoustic islands without cascade drift."""
    if not subtitles or not islands:
        return 0
    changed = 0
    for sub in subtitles:
        if sub.get("locked"):
            continue
        s0 = int(sub["start_ms"])
        e0 = int(sub["end_ms"])
        # Find nearest start & end island boundary within tolerance
        best_s = s0
        best_sd = max_snap_ms + 1
        best_e = e0
        best_ed = max_snap_ms + 1
        for isl in islands:
            is_s = int(isl["start_ms"])
            is_e = int(isl["end_ms"])
            ds = abs(s0 - is_s)
            if ds < best_sd:
                best_sd = ds
                best_s = is_s
            de = abs(e0 - is_e)
            if de < best_ed:
                best_ed = de
                best_e = is_e
        if best_e - best_s >= 250 and (best_s != s0 or best_e != e0):
            sub["start_ms"] = max(0, best_s)
            sub["end_ms"] = best_e
            sub["cage_start_ms"] = sub["start_ms"]
            sub["cage_end_ms"] = sub["end_ms"]
            changed += 1

    changed += repair_overlaps_antidomino(subtitles, min_gap_ms=min_gap_ms)
    return changed


def repair_overlaps_antidomino(subtitles: list[dict], *, min_gap_ms: int = 35) -> int:
    """Resolve adjacent overlaps locally without pushing later cues down the timeline."""
    if len(subtitles) < 2:
        return 0
    subtitles.sort(key=lambda r: (int(r["start_ms"]), int(r["end_ms"])))
    fixed = 0
    for i in range(len(subtitles) - 1):
        a = subtitles[i]
        b = subtitles[i + 1]
        a_end = int(a["end_ms"])
        b_start = int(b["start_ms"])
        if a_end + min_gap_ms <= b_start:
            continue
        a_lock = bool(a.get("locked"))
        b_lock = bool(b.get("locked"))
        if a_lock and b_lock:
            continue
        if b_lock and not a_lock:
            new_end = max(int(a["start_ms"]) + 150, b_start - min_gap_ms)
            if new_end != a_end:
                a["end_ms"] = new_end
                fixed += 1
        elif a_lock and not b_lock:
            new_start = min(int(b["end_ms"]) - 150, a_end + min_gap_ms)
            if new_start != b_start:
                b["start_ms"] = new_start
                fixed += 1
        else:
            mid = (a_end + b_start) // 2
            na = max(int(a["start_ms"]) + 150, mid - (min_gap_ms // 2))
            nb = min(int(b["end_ms"]) - 150, na + min_gap_ms)
            a["end_ms"] = na
            b["start_ms"] = nb
            fixed += 1
    return fixed


def compute_cps(text: str, start_ms: int, end_ms: int) -> float:
    """Characters-per-second reading speed metric for broadcast/course subtitle QC."""
    dur_s = max(0.2, (int(end_ms) - int(start_ms)) / 1000.0)
    visible = "".join(c for c in (text or "") if not c.isspace())
    return round(len(visible) / dur_s, 1)


def synthesize_demo_lesson_wav(wav_path: str | Path, cues: list[dict], duration_ms: int = 26000) -> Path:
    """Create a real, playable 16 kHz mono WAV with warm voice-like cadence envelopes per cue.

    Lets users test the real waveform timeline, scrubbing, audio extraction, and automatic
    target-language ZIP audio cutter immediately on first launch before loading a video.
    """
    sr = 16000
    total = int(sr * (duration_ms / 1000.0))
    buf = array.array("h", [0] * total)

    for idx, cue in enumerate(cues):
        s_ms = max(0, int(cue["start_ms"]))
        e_ms = min(duration_ms, int(cue["end_ms"]))
        if e_ms <= s_ms + 40:
            continue
        s_i = int(sr * s_ms / 1000.0)
        e_i = min(total, int(sr * e_ms / 1000.0))
        length = e_i - s_i
        # Gentle musical/syllabic contour (distinct warmer harmonic on target-language spans!)
        base_f = 196.0 + (idx % 4) * 22.0
        spans = cue.get("_demo_target_ranges") or []
        for k in range(length):
            t_s = k / sr
            cur_ms = s_ms + int(k * 1000.0 / sr)
            in_target = any(a <= cur_ms <= b for a, b in spans)
            freq = base_f * (1.333 if in_target else 1.0)
            # Syllable-rate amplitude modulation (~4.2 Hz) so the waveform looks & feels natural
            syl_env = 0.52 + 0.48 * abs(math.sin(math.pi * 4.1 * t_s + idx * 0.7))
            # Soft attack/release fade
            edge = min(1.0, k / (sr * 0.035), (length - 1 - k) / (sr * 0.045))
            gain = (0.34 if in_target else 0.21) * syl_env * edge
            sig = (
                0.68 * math.sin(2.0 * math.pi * freq * t_s)
                + 0.22 * math.sin(2.0 * math.pi * freq * 2.0 * t_s)
                + 0.10 * math.sin(2.0 * math.pi * freq * 3.0 * t_s)
            )
            buf[s_i + k] = int(max(-32000, min(32000, sig * gain * 32767.0)))

    return write_wav_mono_s16(wav_path, buf, sr)
