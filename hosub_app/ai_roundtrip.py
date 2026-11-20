# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Shaghayegh Mastalipour

"""Zero-API-key Clipboard AI Round-Trip engine for Subtitles and Vocabulary Flashcards.

Lets editors copy timecode-locked subtitle cues or extracted vocabulary lists into ANY external
AI chat interface (ChatGPT, Claude, Gemini, DeepSeek, local Ollama/LM Studio — zero API billing
or geo-blocking issues), and paste the response back with strict line-ID verification, target-script
preservation checks, and reading-speed QA.
"""

from __future__ import annotations

import re

from .bidi import stabilize_mixed_line, strip_bidi_controls
from .romanizer import MultilingualLexicon, strip_inline_romanization
from .timecage import compute_cps

LANG_NAMES = {
    "en": "English",
    "fa": "Persian (فارسی)",
    "zh": "Mandarin Chinese (中文)",
    "ko": "Korean (한국어)",
    "ja": "Japanese (日本語)",
    "ro": "Romanian (Română)",
    "de": "German (Deutsch)",
    "fr": "French (Français)",
    "es": "Spanish (Español)",
    "it": "Italian (Italiano)",
    "tr": "Turkish (Türkçe)",
    "ar": "Arabic (العربية)",
    "ru": "Russian (Русский)",
}

_LINE_ID_RE = re.compile(r"^\s*(?:[-*]\s*)?(?:\[(\d{1,5})\]|(\d{1,5})[.):\]])\s*(.+?)\s*$")
_CODE_FENCE_RE = re.compile(r"^\s*```")


def build_subtitle_ai_prompt(
    subtitles: list[dict],
    *,
    task: str = "proofread_and_translate",  # "proofread_and_translate" | "translate_only" | "proofread_only"
    base_lang: str = "en",
    target_langs: list[str] | None = None,
    translate_to: str = "fa",
    preserve_target_script: bool = True,
    glossary_text: str = "",
    batch_start: int = 0,
    batch_size: int = 80,
) -> dict[str, object]:
    """Generate a deterministic, line-locked prompt chunk ready to paste into any LLM."""
    total = len(subtitles)
    s_idx = max(0, min(total, int(batch_start)))
    e_idx = max(s_idx, min(total, s_idx + max(5, int(batch_size))))
    chunk = subtitles[s_idx:e_idx]

    t_langs = [LANG_NAMES.get(x, x.upper()) for x in (target_langs or ["zh", "ko"])]
    t_str = ", ".join(t_langs) if t_langs else "the taught target language"
    base_str = LANG_NAMES.get(base_lang, base_lang.upper())
    dest_str = LANG_NAMES.get(translate_to, translate_to.upper())

    preserve_rule = (
        f"- Keep all {t_str} words/phrases in their ORIGINAL native script inside the translation — "
        f"do NOT delete or phoneticize them."
        if preserve_target_script
        else f"- Translate everything naturally into {dest_str}."
    )

    if task == "proofread_only":
        instructions = [
            f"You are proofreading subtitles for a multilingual language course (spoken in {base_str}, teaching {t_str}).",
            "Rules:",
            "1. Keep EVERY line number `[0001]` EXACTLY as given. Never merge, split, skip, or reorder lines.",
            f"2. Fix ASR spelling, homophones, punctuation, and misrecognized {t_str} characters/words using lesson context.",
            "3. Do NOT add Pinyin/Romaji in parentheses (HoSub generates romanization automatically).",
            "4. Output ONLY the corrected lines in this exact format — no intro or explanation:",
            "   [0001] <corrected original line>",
        ]
    elif task == "translate_only":
        instructions = [
            f"You are translating subtitles for a language lesson (spoken in {base_str}, teaching {t_str}) into natural {dest_str}.",
            "Rules:",
            "1. Keep EVERY line number `[0001]` EXACTLY as given. Never merge, split, skip, or reorder lines.",
            f"2. {preserve_rule}",
            "3. Keep subtitle length concise and natural for spoken instructional video.",
            "4. Output ONLY the translated lines in this exact format — no intro or explanation:",
            f"   [0001] <{dest_str} translation>",
        ]
    else:  # proofread_and_translate
        instructions = [
            f"You are proofreading AND translating subtitles for a multilingual language lesson "
            f"(instructor language: {base_str}; taught language(s): {t_str}; translation language: {dest_str}).",
            "Rules:",
            "1. Keep EVERY line number `[0001]` EXACTLY as given. Never merge, split, skip, or reorder lines.",
            f"2. Before ` || `, write the proofread original line (fix ASR typos and {t_str} script mistakes; do NOT add Pinyin/Romaji).",
            f"3. After ` || `, write a fluent, concise {dest_str} subtitle translation.",
            f"4. {preserve_rule}",
            "5. Output ONLY lines in this exact format — no extra commentary:",
            f"   [0001] <corrected original> || <{dest_str} translation>",
        ]

    if glossary_text.strip():
        instructions.append("\nCourse Glossary / Mandatory Terms:")
        instructions.append(glossary_text.strip())

    instructions.append("\nSubtitles:")
    for offset, sub in enumerate(chunk):
        row_num = s_idx + offset + 1
        clean = strip_inline_romanization(strip_bidi_controls(str(sub.get("text", "")))).replace("\n", " ").strip()
        instructions.append(f"[{row_num:04d}] {clean}")

    total_batches = max(1, (total + batch_size - 1) // batch_size)
    cur_batch = (s_idx // max(1, batch_size)) + 1
    return {
        "prompt": "\n".join(instructions),
        "range_start": s_idx + 1 if chunk else 0,
        "range_end": e_idx,
        "total_rows": total,
        "current_batch": cur_batch,
        "total_batches": total_batches,
    }


def apply_subtitle_ai_response(
    subtitles: list[dict],
    pasted_text: str,
    lexicon: MultilingualLexicon,
    *,
    task: str = "proofread_and_translate",
    base_lang: str = "en",
    target_langs: list[str] | None = None,
    enforce_bidi: bool = True,
) -> dict[str, object]:
    """Parse pasted AI response, apply cleanly to matching subtitle IDs, and return a QA report."""
    parsed: dict[int, str] = {}
    for raw_ln in (pasted_text or "").splitlines():
        if _CODE_FENCE_RE.match(raw_ln):
            continue
        m = _LINE_ID_RE.match(raw_ln)
        if not m:
            continue
        idx = int(m.group(1) or m.group(2))
        body = m.group(3).strip()
        if idx >= 1 and body:
            parsed[idx] = body

    if not parsed:
        raise ValueError(
            "No numbered lines like `[0001] ...` were found. Paste the numbered lines returned by your AI."
        )

    min_id = min(parsed)
    max_id = min(len(subtitles), max(parsed))
    updated = 0
    missing_ids: list[int] = []
    warnings: list[str] = []

    for row_num in range(min_id, max_id + 1):
        if row_num not in parsed:
            missing_ids.append(row_num)
            continue
        sub = subtitles[row_num - 1]
        body = parsed[row_num]

        orig_spans = [
            str(sp["text"])
            for sp in lexicon.find_target_spans(
                str(sub.get("text", "")), base_lang=base_lang, target_langs=target_langs
            )
        ]

        if task == "proofread_only":
            new_orig = strip_inline_romanization(body.split("||", 1)[0].strip())
            sub["text"] = stabilize_mixed_line(new_orig) if enforce_bidi else new_orig
        elif task == "translate_only":
            new_tr = body.split("||", -1)[-1].strip()
            sub["translation"] = stabilize_mixed_line(new_tr) if enforce_bidi else new_tr
            for term in orig_spans:
                if term not in new_tr and len(warnings) < 25:
                    warnings.append(f"Row #{row_num:03d}: target term '{term}' not present in translation.")
        else:
            if "||" in body:
                left, right = body.split("||", 1)
                new_orig = strip_inline_romanization(left.strip())
                new_tr = right.strip()
                if new_orig:
                    sub["text"] = stabilize_mixed_line(new_orig) if enforce_bidi else new_orig
                if new_tr:
                    sub["translation"] = stabilize_mixed_line(new_tr) if enforce_bidi else new_tr
                    # Verify target terms in the corrected line appear in translation if desired
                    chk_spans = [
                        str(sp["text"])
                        for sp in lexicon.find_target_spans(
                            new_orig, base_lang=base_lang, target_langs=target_langs
                        )
                    ]
                    for term in chk_spans:
                        if term not in new_tr and len(warnings) < 25:
                            warnings.append(f"Row #{row_num:03d}: target term '{term}' omitted in translation.")
            else:
                # Graceful fallback if AI forgot '||' separator -> treat as translation
                sub["translation"] = stabilize_mixed_line(body) if enforce_bidi else body

        # Reading-speed check (flag > 24 CPS)
        check_txt = sub.get("translation") or sub.get("text") or ""
        cps = compute_cps(strip_bidi_controls(check_txt), int(sub["start_ms"]), int(sub["end_ms"]))
        if cps > 25.0 and len(warnings) < 25:
            warnings.append(f"Row #{row_num:03d}: fast reading speed ({cps} chars/sec).")

        updated += 1

    out_of_range = [k for k in parsed if k > len(subtitles)]
    return {
        "updated_count": updated,
        "range": [min_id, max_id],
        "missing_ids": missing_ids[:30],
        "out_of_range_ids": out_of_range[:20],
        "warnings": warnings,
    }


def build_vocab_ai_prompt(
    cards: list[dict],
    *,
    translate_to: str = "fa",
    include_only_selected: bool = True,
) -> str:
    """Create a numbered vocabulary translation prompt with real lesson context sentences."""
    dest_str = LANG_NAMES.get(translate_to, translate_to.upper())
    chosen = [c for c in cards if (c.get("selected", True) or not include_only_selected)]
    lines = [
        f"Translate the following extracted lesson vocabulary into accurate, concise {dest_str} for learner flashcards.",
        "Use each word's Context sentence to choose the exact meaning used in this lesson.",
        "Rules:",
        "1. Keep every `[001]` ID and the original word unchanged.",
        "2. Output ONLY lines in this exact pipe-separated format — no extra text:",
        f"   [001] <word> | <{dest_str} meaning> | <part of speech> | <short usage note>",
        "",
        "Vocabulary:",
    ]
    for idx, c in enumerate(chosen, start=1):
        ctx = str(c.get("context", "")).replace("\n", " ").strip()
        roma = f" ({c['romanization']})" if c.get("romanization") else ""
        lines.append(f"[{idx:03d}] {c['word']}{roma} — Context: \"{ctx}\"")
    return "\n".join(lines)


def apply_vocab_ai_response(
    cards: list[dict],
    pasted_text: str,
    overrides: dict[str, dict],
    *,
    include_only_selected: bool = True,
) -> dict[str, object]:
    """Parse AI flashcard translations and save into project vocabulary overrides."""
    chosen = [c for c in cards if (c.get("selected", True) or not include_only_selected)]
    by_idx = {i + 1: c for i, c in enumerate(chosen)}
    by_word = {str(c["word"]).strip().lower(): c for c in cards}

    updated = 0
    for raw_ln in (pasted_text or "").splitlines():
        if _CODE_FENCE_RE.match(raw_ln) or "|" not in raw_ln:
            continue
        m = _LINE_ID_RE.match(raw_ln)
        card = None
        rest = raw_ln.strip()
        if m:
            idx = int(m.group(1) or m.group(2))
            rest = m.group(3).strip()
            card = by_idx.get(idx)
        parts = [p.strip() for p in rest.split("|")]
        if card is None and parts:
            w_key = strip_inline_romanization(parts[0]).strip().lower()
            card = by_word.get(w_key)
        if card is None or len(parts) < 2:
            continue

        ov = overrides.setdefault(card["id"], {})
        if parts[1]:
            ov["meaning"] = parts[1]
        if len(parts) >= 3 and parts[2]:
            ov["pos"] = parts[2]
        if len(parts) >= 4 and parts[3]:
            ov["note"] = parts[3]
        updated += 1

    if updated == 0:
        raise ValueError(
            "Could not match any `[001] word | meaning | pos | note` lines. Check the pasted format."
        )
    return {"updated_count": updated, "total_cards": len(chosen)}
