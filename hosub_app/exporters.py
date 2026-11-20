# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Shaghayegh Mastalipour

"""Subtitle, Styled Excel (.xlsx) Flashcard, CSV, JSON, Audio & FFmpeg Video Exporters.

Zero external Python packages required — includes a standards-compliant OOXML `.xlsx`
workbook writer (with styled header, freeze panes, zebra rows, auto column widths, and full
UTF-8 CJK + Persian RTL fidelity) built on Python's standard `zipfile` library.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
from xml.sax.saxutils import escape as xml_escape
import zipfile

from .bidi import format_for_ass, is_cjk_char, stabilize_mixed_line, strip_bidi_controls
from .romanizer import MultilingualLexicon, strip_inline_romanization


# ---------------------------------------------------------------------------
# Timecode Formatting & SRT / VTT Parser
# ---------------------------------------------------------------------------

_SRT_TIME_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _to_ms(h: str, m: str, s: str, ms: str) -> int:
    ms_val = int(ms.ljust(3, "0")[:3])
    return ((int(h) * 3600) + (int(m) * 60) + int(s)) * 1000 + ms_val


def format_srt_time(ms: int, sep: str = ",") -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, mil = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{mil:03d}"


def format_ass_time(ms: int) -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, mil = divmod(rem, 1000)
    cs = mil // 10
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def format_human_time(ms: int) -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, mil = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{mil // 100}" if h else f"{m:02d}:{s:02d}.{mil // 100}"


def parse_srt_or_vtt(content: str) -> list[dict]:
    """Parse `.srt` or `.vtt` subtitle text into normalized HoSub cue dicts."""
    text = (content or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text.strip())
    cues: list[dict] = []
    next_id = 1
    for blk in blocks:
        lines = [ln.strip() for ln in blk.split("\n") if ln.strip()]
        if not lines or lines[0].upper().startswith("WEBVTT") or lines[0].startswith("NOTE"):
            continue
        time_line_idx = -1
        match = None
        for idx, ln in enumerate(lines[:3]):
            m = _SRT_TIME_RE.search(ln)
            if m:
                time_line_idx = idx
                match = m
                break
        if match is None:
            continue
        s_ms = _to_ms(*match.groups()[:4])
        e_ms = max(s_ms + 120, _to_ms(*match.groups()[4:]))
        body_lines = [re.sub(r"<[^>]+>", "", ln) for ln in lines[time_line_idx + 1 :]]
        body = strip_bidi_controls("\n".join(body_lines).strip())
        if not body:
            continue
        cues.append(
            {
                "id": next_id,
                "start_ms": s_ms,
                "end_ms": e_ms,
                "text": strip_inline_romanization(body),
                "translation": "",
                "locked": False,
                "group_id": 0,
                "words": [],
            }
        )
        next_id += 1
    return cues


# ---------------------------------------------------------------------------
# Line Composition & Subtitle Exports (SRT / VTT / ASS / TXT)
# ---------------------------------------------------------------------------

def compose_cue_text(
    sub: dict,
    lexicon: MultilingualLexicon,
    *,
    base_lang: str = "en",
    target_langs: list[str] | None = None,
    romanization_mode: str = "inline",   # "none" | "inline" | "stacked"
    content_mode: str = "bilingual",     # "original" | "translation" | "bilingual"
    enforce_bidi: bool = True,
) -> str:
    raw_orig = str(sub.get("text", "")).strip()
    raw_tr = str(sub.get("translation", "")).strip()

    orig_fmt = (
        lexicon.annotate_line(
            raw_orig, mode=romanization_mode, base_lang=base_lang, target_langs=target_langs
        )
        if raw_orig
        else ""
    )
    # If translation includes target script and user asks for inline romanization on translation-only mode:
    tr_fmt = (
        lexicon.annotate_line(
            raw_tr,
            mode="inline" if (romanization_mode != "none" and content_mode == "translation") else "none",
            base_lang=base_lang,
            target_langs=target_langs,
        )
        if raw_tr
        else ""
    )

    lines: list[str] = []
    if content_mode == "original":
        if orig_fmt:
            lines.append(orig_fmt)
    elif content_mode == "translation":
        chosen = tr_fmt or orig_fmt
        if chosen:
            lines.append(chosen)
    else:  # bilingual
        if orig_fmt:
            lines.append(orig_fmt)
        if tr_fmt:
            lines.append(tr_fmt)

    joined = "\n".join(lines).strip()
    if enforce_bidi and joined:
        return stabilize_mixed_line(joined)
    return joined


def export_srt(
    subtitles: list[dict],
    lexicon: MultilingualLexicon,
    *,
    base_lang: str = "en",
    target_langs: list[str] | None = None,
    romanization_mode: str = "inline",
    content_mode: str = "bilingual",
    enforce_bidi: bool = True,
) -> bytes:
    blocks: list[str] = []
    idx = 0
    for sub in subtitles:
        txt = compose_cue_text(
            sub,
            lexicon,
            base_lang=base_lang,
            target_langs=target_langs,
            romanization_mode=romanization_mode,
            content_mode=content_mode,
            enforce_bidi=enforce_bidi,
        )
        if not txt:
            continue
        idx += 1
        blocks.append(f"{idx}\n{format_srt_time(sub['start_ms'])} --> {format_srt_time(sub['end_ms'])}\n{txt}")
    # UTF-8 BOM guarantees Premiere Pro, DaVinci Resolve & Windows players read CJK + Persian cleanly
    return ("\ufeff" + "\n\n".join(blocks) + "\n").encode("utf-8")


def export_vtt(
    subtitles: list[dict],
    lexicon: MultilingualLexicon,
    *,
    base_lang: str = "en",
    target_langs: list[str] | None = None,
    romanization_mode: str = "inline",
    content_mode: str = "bilingual",
    enforce_bidi: bool = True,
) -> bytes:
    blocks = ["WEBVTT"]
    for idx, sub in enumerate(subtitles, start=1):
        txt = compose_cue_text(
            sub,
            lexicon,
            base_lang=base_lang,
            target_langs=target_langs,
            romanization_mode=romanization_mode,
            content_mode=content_mode,
            enforce_bidi=enforce_bidi,
        )
        if not txt:
            continue
        blocks.append(
            f"{idx}\n{format_srt_time(sub['start_ms'], '.')} --> {format_srt_time(sub['end_ms'], '.')}\n{txt}"
        )
    return ("\n\n".join(blocks) + "\n").encode("utf-8")


def export_txt(
    subtitles: list[dict],
    lexicon: MultilingualLexicon,
    *,
    base_lang: str = "en",
    target_langs: list[str] | None = None,
    romanization_mode: str = "inline",
    content_mode: str = "bilingual",
    paragraph: bool = False,
) -> bytes:
    chunks: list[str] = []
    for sub in subtitles:
        txt = compose_cue_text(
            sub,
            lexicon,
            base_lang=base_lang,
            target_langs=target_langs,
            romanization_mode=romanization_mode,
            content_mode=content_mode,
            enforce_bidi=False,
        )
        if txt:
            chunks.append(txt.replace("\n", " ") if paragraph else txt)
    sep = " " if paragraph else "\n\n"
    return ("\ufeff" + sep.join(chunks) + "\n").encode("utf-8")


def _hex_to_ass_color(hex_rgb: str, opacity_pct: int = 100) -> str:
    """Convert `#RRGGBB` + opacity (0..100, 100=opaque) into ASS `&HAABBGGRR`."""
    h = (hex_rgb or "#FFFFFF").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    alpha = int(round((100 - max(0, min(100, int(opacity_pct)))) * 255 / 100.0))
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def _colorize_ass_line(text: str, pri_c: str, target_c: str, roma_c: str) -> str:
    """Highlight CJK spans and parenthesized Pinyin/Romaji in their dedicated ASS colors."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "(":
            end = text.find(")", i + 1)
            if end != -1:
                out.append(f"{{\\c{roma_c}}}{text[i : end + 1]}{{\\c{pri_c}}}")
                i = end + 1
                continue
        if is_cjk_char(ch):
            j = i + 1
            while j < n and is_cjk_char(text[j]):
                j += 1
            out.append(f"{{\\c{target_c}}}{text[i:j]}{{\\c{pri_c}}}")
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def export_ass(
    subtitles: list[dict],
    lexicon: MultilingualLexicon,
    style: dict,
    *,
    base_lang: str = "en",
    target_langs: list[str] | None = None,
    content_mode: str = "bilingual",
) -> bytes:
    """Export styled Advanced SubStation Alpha (`.ass`) with per-script colors and box opacity."""
    font = str(style.get("font_family") or "Segoe UI").split(",")[0].strip().strip("'\"")
    font_size = max(14, min(96, int(style.get("font_size", 42))))
    pri_c = _hex_to_ass_color(style.get("primary_color", "#F8FAFC"), 100)
    target_c = _hex_to_ass_color(style.get("target_color", "#FBBF24"), 100)
    roma_c = _hex_to_ass_color(style.get("roma_color", "#38BDF8"), 100)
    show_box = bool(style.get("show_box", True))
    box_opacity = int(style.get("box_opacity", 72))
    box_c = _hex_to_ass_color(style.get("box_color", "#0F172A"), box_opacity)
    outline_c = box_c if show_box else _hex_to_ass_color("#000000", 100)
    border_style = 3 if show_box else 1
    outline_w = 10 if show_box else 3
    shadow_w = 0 if show_box else 1
    margin_v = max(12, min(240, int(style.get("margin_v", 44))))
    roma_mode = str(style.get("romanization_mode", "inline"))
    color_langs = bool(style.get("highlight_target", True))

    header = [
        "[Script Info]",
        "; Generated by HoSub Studio 2.0",
        "ScriptType: v4.00+",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font},{font_size},{pri_c},{roma_c},{outline_c},{box_c},"
        f"0,0,0,0,100,100,0,0,{border_style},{outline_w},{shadow_w},2,64,64,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    for sub in subtitles:
        txt = compose_cue_text(
            sub,
            lexicon,
            base_lang=base_lang,
            target_langs=target_langs,
            romanization_mode=roma_mode,
            content_mode=content_mode,
            enforce_bidi=True,
        )
        if not txt:
            continue
        ass_txt = format_for_ass(txt)
        if color_langs:
            ass_txt = _colorize_ass_line(ass_txt, pri_c, target_c, roma_c)
        header.append(
            f"Dialogue: 0,{format_ass_time(sub['start_ms'])},{format_ass_time(sub['end_ms'])},"
            f"Default,,0,0,0,,{ass_txt}"
        )

    return ("\ufeff" + "\n".join(header) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# Zero-Dependency Styled OOXML Excel (.xlsx) Flashcard Workbook Writer
# ---------------------------------------------------------------------------

def _excel_col_name(idx0: int) -> str:
    n = idx0 + 1
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


_ILLEGAL_XML_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean_xml_text(val: object) -> str:
    s = _ILLEGAL_XML_RE.sub("", str(val if val is not None else ""))
    return xml_escape(s)


def _build_sheet_xml(
    headers: list[str],
    rows: list[list[object]],
    col_widths: list[float],
) -> str:
    cols_xml = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{w:.1f}" customWidth="1"/>'
        for i, w in enumerate(col_widths)
    )
    last_col = _excel_col_name(len(headers) - 1)
    last_row = len(rows) + 1

    row_parts: list[str] = []
    # Header row (style s="1": dark slate fill, bold white text, centered vertical)
    h_cells = "".join(
        f'<c r="{_excel_col_name(c)}1" t="inlineStr" s="1"><is><t xml:space="preserve">'
        f"{_clean_xml_text(h)}</t></is></c>"
        for c, h in enumerate(headers)
    )
    row_parts.append(f'<row r="1" ht="26" customHeight="1">{h_cells}</row>')

    # Data rows (style 2/3 text zebra, style 4/5 integer zebra)
    for r_idx, row in enumerate(rows, start=2):
        even = (r_idx % 2) == 0
        txt_s = "2" if even else "3"
        num_s = "4" if even else "5"
        cells: list[str] = []
        for c_idx, val in enumerate(row):
            ref = f"{_excel_col_name(c_idx)}{r_idx}"
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                cells.append(f'<c r="{ref}" s="{num_s}"><v>{val}</v></c>')
            else:
                cells.append(
                    f'<c r="{ref}" t="inlineStr" s="{txt_s}"><is><t xml:space="preserve">'
                    f"{_clean_xml_text(val)}</t></is></c>"
                )
        row_parts.append(f'<row r="{r_idx}" ht="22" customHeight="1">{"".join(cells)}</row>')

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{last_col}{last_row}"/>'
        '<sheetViews><sheetView tabSelected="1" workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>"
        '<sheetFormatPr defaultRowHeight="20"/>'
        f"<cols>{cols_xml}</cols>"
        f"<sheetData>{''.join(row_parts)}</sheetData>"
        f'<autoFilter ref="A1:{last_col}{last_row}"/>'
        "</worksheet>"
    )


def export_vocab_xlsx(
    cards: list[dict],
    *,
    project_title: str = "HoSub Lesson",
    only_selected: bool = True,
) -> bytes:
    """Write a clean, styled `.xlsx` Flashcard workbook ready for Excel, Anki & book-reader apps."""
    chosen = [c for c in cards if (c.get("selected", True) or not only_selected)]

    headers = [
        "#",
        "Word",
        "Romanization",
        "Meaning / Translation",
        "Language",
        "Level",
        "POS",
        "Frequency",
        "First Seen",
        "Context Sentence",
        "Context Translation",
        "Audio File",
        "Notes",
    ]
    widths = [6, 18, 22, 28, 10, 11, 11, 11, 12, 44, 42, 28, 24]

    data_rows: list[list[object]] = []
    for idx, c in enumerate(chosen, start=1):
        data_rows.append([
            idx,
            c.get("word", ""),
            c.get("romanization", ""),
            c.get("meaning", ""),
            str(c.get("lang", "")).upper(),
            c.get("level", ""),
            c.get("pos", ""),
            int(c.get("frequency", 1)),
            format_human_time(int(c.get("first_start_ms", 0))),
            c.get("context", ""),
            c.get("context_translation", ""),
            c.get("audio_file", ""),
            c.get("note", ""),
        ])

    # Summary sheet (Sheet 2)
    by_lang: dict[str, int] = {}
    for c in chosen:
        lg = str(c.get("lang", "")).upper()
        by_lang[lg] = by_lang.get(lg, 0) + 1
    sum_rows: list[list[object]] = [
        ["Lesson / Project", project_title],
        ["Generator", "HoSub Studio 2.0 (Offline-First Multilingual Workflow)"],
        ["Exported Flashcards", len(chosen)],
        ["Cards With Translation", sum(1 for c in chosen if str(c.get("meaning", "")).strip())],
        ["Cards Linked to Audio Clip", sum(1 for c in chosen if str(c.get("audio_file", "")).strip())],
        *[[f"Language ({lg})", cnt] for lg, cnt in sorted(by_lang.items())],
    ]

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2">'
        '<font><sz val="11"/><color rgb="FF0F172A"/><name val="Segoe UI"/></font>'
        '<font><b/><sz val="11"/><color rgb="FFF8FAFC"/><name val="Segoe UI"/></font>'
        "</fonts>"
        '<fills count="4">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF1E293B"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFF8FAFC"/></patternFill></fill>'
        "</fills>"
        '<borders count="2">'
        "<border><left/><right/><top/><bottom/><diagonal/></border>"
        '<border>'
        '<left style="thin"><color rgb="FFE2E8F0"/></left>'
        '<right style="thin"><color rgb="FFE2E8F0"/></right>'
        '<top style="thin"><color rgb="FFE2E8F0"/></top>'
        '<bottom style="thin"><color rgb="FFE2E8F0"/></bottom>'
        "<diagonal/></border>"
        "</borders>"
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="6">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">'
        '<alignment vertical="center" horizontal="left"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1">'
        '<alignment vertical="center" wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1">'
        '<alignment vertical="center" wrapText="1"/></xf>'
        '<xf numFmtId="1" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1" applyAlignment="1">'
        '<alignment vertical="center" horizontal="center"/></xf>'
        '<xf numFmtId="1" fontId="0" fillId="3" borderId="1" xfId="0" applyNumberFormat="1" applyFill="1" applyBorder="1" applyAlignment="1">'
        '<alignment vertical="center" horizontal="center"/></xf>'
        "</cellXfs>"
        "</styleSheet>"
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        '<sheet name="Flashcards" sheetId="1" r:id="rId1"/>'
        '<sheet name="Summary" sheetId="2" r:id="rId2"/>'
        "</sheets></workbook>"
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/styles.xml", styles_xml)
        zf.writestr("xl/worksheets/sheet1.xml", _build_sheet_xml(headers, data_rows, widths))
        zf.writestr("xl/worksheets/sheet2.xml", _build_sheet_xml(["Metric", "Value"], sum_rows, [28, 48]))
    return buf.getvalue()


def export_vocab_csv(cards: list[dict], *, only_selected: bool = True) -> bytes:
    chosen = [c for c in cards if (c.get("selected", True) or not only_selected)]
    out = io.StringIO()
    out.write("\ufeff")
    w = csv.writer(out)
    w.writerow([
        "word", "romanization", "meaning", "lang", "level", "pos",
        "frequency", "first_seen", "context", "context_translation", "audio_file", "note",
    ])
    for c in chosen:
        w.writerow([
            c.get("word", ""),
            c.get("romanization", ""),
            c.get("meaning", ""),
            c.get("lang", ""),
            c.get("level", ""),
            c.get("pos", ""),
            c.get("frequency", 1),
            format_human_time(int(c.get("first_start_ms", 0))),
            c.get("context", ""),
            c.get("context_translation", ""),
            c.get("audio_file", ""),
            c.get("note", ""),
        ])
    return out.getvalue().encode("utf-8")


def export_vocab_json(
    cards: list[dict], *, project_title: str = "HoSub Lesson", only_selected: bool = True
) -> bytes:
    chosen = [c for c in cards if (c.get("selected", True) or not only_selected)]
    payload = {
        "generator": "HoSub Studio 2.0",
        "deck": project_title,
        "count": len(chosen),
        "cards": [
            {
                "front": c.get("word", ""),
                "reading": c.get("romanization", ""),
                "back": c.get("meaning", ""),
                "lang": c.get("lang", ""),
                "level": c.get("level", ""),
                "pos": c.get("pos", ""),
                "frequency": c.get("frequency", 1),
                "first_start_ms": c.get("first_start_ms", 0),
                "context": c.get("context", ""),
                "context_translation": c.get("context_translation", ""),
                "audio_file": c.get("audio_file", ""),
                "note": c.get("note", ""),
            }
            for c in chosen
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


# ---------------------------------------------------------------------------
# FFmpeg Full Audio & Soft/Hard Video Exporters
# ---------------------------------------------------------------------------

def extract_full_audio(
    media_path: Path,
    ffmpeg_exe: str | None,
    *,
    audio_format: str = "mp3",  # "mp3" | "wav" | "flac" | "m4a"
) -> tuple[bytes, str]:
    """Extract clean audio-only track from any loaded video or audio file."""
    fmt = audio_format.lower().lstrip(".")
    if fmt not in ("mp3", "wav", "flac", "m4a"):
        fmt = "mp3"

    # Zero-FFmpeg direct pass-through when already a WAV file and WAV is requested!
    if not ffmpeg_exe:
        if media_path.suffix.lower() == ".wav":
            return media_path.read_bytes(), "wav"
        raise RuntimeError(
            "FFmpeg is required to convert video/audio formats. Configure tools/ffmpeg.exe in Engine Settings."
        )

    codec_map = {
        "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
        "wav": ["-c:a", "pcm_s16le", "-ar", "44100"],
        "flac": ["-c:a", "flac"],
        "m4a": ["-c:a", "aac", "-b:a", "192k"],
    }
    with tempfile.TemporaryDirectory(prefix="hosub_audio_") as td:
        out_p = Path(td) / f"audio.{fmt}"
        cmd = [
            ffmpeg_exe, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(media_path),
            "-vn",
            *codec_map[fmt],
            str(out_p),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=600)
        if proc.returncode != 0 or not out_p.is_file():
            raise RuntimeError(f"FFmpeg audio extraction failed: {proc.stderr.decode('utf-8', 'replace')[:400]}")
        return out_p.read_bytes(), fmt


def render_softsub_video(
    media_path: Path,
    ffmpeg_exe: str,
    subtitles: list[dict],
    lexicon: MultilingualLexicon,
    *,
    base_lang: str = "en",
    target_langs: list[str] | None = None,
    content_mode: str = "bilingual",
    container: str = "mkv",  # "mkv" | "mp4"
    output_path: Path,
) -> Path:
    """Mux TWO toggleable subtitle tracks (Track 1: With Pinyin/Romaji, Track 2: Without) losslessly."""
    ext = "mp4" if container.lower() == "mp4" else "mkv"
    sub_codec = "mov_text" if ext == "mp4" else "srt"
    out_p = output_path.with_suffix(f".{ext}")

    with tempfile.TemporaryDirectory(prefix="hosub_soft_") as td:
        t1 = Path(td) / "with_reading.srt"
        t2 = Path(td) / "clean.srt"
        t1.write_bytes(
            export_srt(
                subtitles, lexicon,
                base_lang=base_lang, target_langs=target_langs,
                romanization_mode="inline", content_mode=content_mode, enforce_bidi=True,
            )
        )
        t2.write_bytes(
            export_srt(
                subtitles, lexicon,
                base_lang=base_lang, target_langs=target_langs,
                romanization_mode="none", content_mode=content_mode, enforce_bidi=True,
            )
        )
        cmd = [
            ffmpeg_exe, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(media_path),
            "-i", str(t1),
            "-i", str(t2),
            "-map", "0:v", "-map", "0:a?", "-map", "1:0", "-map", "2:0",
            "-c:v", "copy", "-c:a", "copy", "-c:s", sub_codec,
            "-metadata:s:s:0", "title=With Pinyin / Romanization",
            "-metadata:s:s:1", "title=Without Romanization",
            str(out_p),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=900)
        if proc.returncode != 0 or not out_p.is_file():
            raise RuntimeError(f"Soft-sub mux failed: {proc.stderr.decode('utf-8', 'replace')[:400]}")
    return out_p


def render_hardsub_video(
    media_path: Path,
    ffmpeg_exe: str,
    subtitles: list[dict],
    lexicon: MultilingualLexicon,
    style: dict,
    *,
    base_lang: str = "en",
    target_langs: list[str] | None = None,
    content_mode: str = "bilingual",
    output_path: Path,
) -> Path:
    """Burn styled `.ass` subtitles permanently into an H.264 MP4 video via FFmpeg libass."""
    out_p = output_path.with_suffix(".mp4")
    with tempfile.TemporaryDirectory(prefix="hosub_hard_") as td:
        ass_p = Path(td) / "burn.ass"
        ass_p.write_bytes(
            export_ass(
                subtitles, lexicon, style,
                base_lang=base_lang, target_langs=target_langs, content_mode=content_mode,
            )
        )
        # Escape Windows drive colon and quotes for FFmpeg's libass filter graph
        esc = ass_p.as_posix().replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        cmd = [
            ffmpeg_exe, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(media_path),
            "-vf", f"ass='{esc}'",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "copy",
            str(out_p),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=3600)
        if proc.returncode != 0 or not out_p.is_file():
            raise RuntimeError(f"Hard-sub render failed: {proc.stderr.decode('utf-8', 'replace')[:400]}")
    return out_p
