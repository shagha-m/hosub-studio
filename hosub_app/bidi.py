# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Shaghayegh Mastalipour

"""Unicode Bidirectional (BiDi) & punctuation stabilizer for mixed RTL + CJK + Latin subtitles.

Prevents flipped parentheses, jumping terminal punctuation (., ?, !, ...), and scrambled word
order when Persian/Arabic explanations appear alongside Mandarin, Korean, Japanese, Pinyin/Romaji,
or European expressions in Premiere Pro, DaVinci Resolve, VLC, MPV, and FFmpeg libass.
"""

from __future__ import annotations

import re

LRI = "\u2066"  # Left-to-Right Isolate
RLI = "\u2067"  # Right-to-Left Isolate
FSI = "\u2068"  # First-Strong Isolate
PDI = "\u2069"  # Pop Directional Isolate
LRM = "\u200e"  # Left-to-Right Mark
RLM = "\u200f"  # Right-to-Left Mark
LRE = "\u202a"  # Left-to-Right Embedding
RLE = "\u202b"  # Right-to-Left Embedding
PDF = "\u202c"  # Pop Directional Formatting

_BIDI_CTRL_RE = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_TRAILING_PUNCT_RE = re.compile(r"([.!?؟،؛:…]+)$")


def is_rtl_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x0590 <= cp <= 0x08FF
        or 0xFB1D <= cp <= 0xFDFF
        or 0xFE70 <= cp <= 0xFEFF
    )


def is_hanzi_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0xF900 <= cp <= 0xFAFF
    )


def is_hangul_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0xAC00 <= cp <= 0xD7A3
        or 0x1100 <= cp <= 0x11FF
        or 0x3130 <= cp <= 0x318F
    )


def is_kana_char(ch: str) -> bool:
    cp = ord(ch)
    return 0x3040 <= cp <= 0x30FF or 0x31F0 <= cp <= 0x31FF


def is_cjk_char(ch: str) -> bool:
    return is_hanzi_char(ch) or is_hangul_char(ch) or is_kana_char(ch)


def is_latin_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x0041 <= cp <= 0x005A
        or 0x0061 <= cp <= 0x007A
        or 0x00C0 <= cp <= 0x024F  # Accented Latin incl. Pinyin tones & RO/DE/FR/ES diacritics
        or 0x1E00 <= cp <= 0x1EFF
    )


def contains_rtl(text: str) -> bool:
    return any(is_rtl_char(c) for c in text)


def strip_bidi_controls(text: str) -> str:
    return _BIDI_CTRL_RE.sub("", text or "")


def _is_ltr_island_char(ch: str) -> bool:
    return is_cjk_char(ch) or is_latin_char(ch) or ch.isdigit()


def stabilize_mixed_line(text: str, *, force_rtl: bool | None = None) -> str:
    """Wrap embedded LTR/CJK+(romanization) spans in LRI..PDI inside RTL lines and pin end punctuation."""
    clean = strip_bidi_controls(text)
    if not clean.strip():
        return clean

    out_lines: list[str] = []
    for line in clean.split("\n"):
        s = line.strip()
        if not s:
            out_lines.append("")
            continue

        rtl = contains_rtl(s) if force_rtl is None else (force_rtl or contains_rtl(s))
        if not rtl:
            out_lines.append(s)
            continue

        # Detach trailing sentence punctuation so we can anchor it with RLM at the visual left end.
        trailing = ""
        m = _TRAILING_PUNCT_RE.search(s)
        if m:
            trailing = m.group(1)
            s = s[: m.start()].rstrip()

        # Isolate contiguous LTR / CJK spans (including immediately attached "(Pinyin/Romaji)")
        buf: list[str] = []
        i = 0
        n = len(s)
        while i < n:
            ch = s[i]
            if _is_ltr_island_char(ch) or (
                ch in "([«\"'" and i + 1 < n and _is_ltr_island_char(s[i + 1])
            ):
                start = i
                depth_paren = 0
                depth_brack = 0
                j = i
                last_good = i
                while j < n:
                    c = s[j]
                    if is_rtl_char(c):
                        break
                    if c == "(":
                        depth_paren += 1
                        j += 1
                        last_good = j
                        continue
                    if c == ")" and depth_paren > 0:
                        depth_paren -= 1
                        j += 1
                        last_good = j
                        continue
                    if c == "[":
                        depth_brack += 1
                        j += 1
                        last_good = j
                        continue
                    if c == "]" and depth_brack > 0:
                        depth_brack -= 1
                        j += 1
                        last_good = j
                        continue
                    if _is_ltr_island_char(c) or depth_paren > 0 or depth_brack > 0:
                        j += 1
                        last_good = j
                        continue
                    if c in " -'’·/,:+" and j + 1 < n and (
                        _is_ltr_island_char(s[j + 1]) or s[j + 1] in "(["
                    ):
                        j += 1
                        continue
                    break
                span = s[start:last_good]
                if span:
                    buf.append(f"{LRI}{span}{PDI}")
                    i = last_good
                    continue
            buf.append(ch)
            i += 1

        rebuilt = "".join(buf)
        if trailing:
            rebuilt = f"{rebuilt}{RLM}{trailing}{RLM}"
        out_lines.append(f"{RLE}{rebuilt}{PDF}")

    return "\n".join(out_lines)


def format_for_ass(text: str) -> str:
    """Format mixed RTL/LTR/CJK text safely for libass / FFmpeg burn-in."""
    stabilized = stabilize_mixed_line(text)
    return stabilized.replace("\n", "\\N")
