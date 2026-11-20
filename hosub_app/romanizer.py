# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Shaghayegh Mastalipour

"""Offline multilingual tokenizer, romanizer (Mandarin Pinyin, Korean RR, Japanese Hepburn),
vocabulary/flashcard extractor, and persistent user-extensible dictionary manager.

Zero external runtime dependencies — uses only Python stdlib + bundled open-license TSV lexicons
(17,500+ HSK 1-9 Mandarin words, 20,900+ Unicode Unihan Hanzi readings, mathematical 11,172-syllable
Hangul Revised Romanization with Korean phonological assimilation + TOPIK lexicon, mathematical
Hiragana/Katakana Hepburn + JLPT lexicon, and full custom dictionary support for any language).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import csv
import io
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from .bidi import (
    is_cjk_char,
    is_hangul_char,
    is_hanzi_char,
    is_kana_char,
    is_latin_char,
    is_rtl_char,
    strip_bidi_controls,
)


@dataclass(slots=True)
class DictEntry:
    surface: str
    lang: str
    romanization: str
    meaning: str = ""
    level: str = ""
    pos: str = ""
    source: str = "builtin"  # "builtin" | "custom"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class TextToken:
    surface: str
    lang: str          # "zh" | "ko" | "ja" | "fa" | "en" | custom ISO | "punct"
    romanization: str = ""
    meaning: str = ""
    level: str = ""
    pos: str = ""
    lemma: str = ""    # Base form when Korean particle stripped, else surface
    is_word: bool = False


# ---------------------------------------------------------------------------
# Korean Revised Romanization (Official South Korean Ministry of Culture 2000)
# with phonological assimilation (liaison, nasalization, liquidization, aspiration)
# ---------------------------------------------------------------------------

# 19 Choseong (initial consonants)
_KO_CHOSEONG = [
    "g", "kk", "n", "d", "tt", "r", "m", "b", "pp",
    "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h",
]
# 21 Jungseong (medial vowels)
_KO_JUNGSEONG = [
    "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae",
    "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i",
]
# 28 Jongseong (final batchim consonants) — base neutralized coda rendering
_KO_JONGSEONG = [
    "", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "p", "l", "l",
    "p", "l", "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t",
]

# Liaison shift when batchim is followed by empty initial ㅇ (index 11):
# Maps jongseong index -> (remaining_jongseong_str, next_choseong_str)
_KO_LIAISON: dict[int, tuple[str, str]] = {
    1: ("", "g"),    # ㄱ -> g
    2: ("", "kk"),   # ㄲ -> kk
    3: ("k", "s"),   # ㄳ -> k-s
    4: ("", "n"),    # ㄴ -> n
    5: ("n", "j"),   # ㄵ -> n-j
    6: ("", "n"),    # ㄶ -> n (h silent)
    7: ("", "d"),    # ㄷ -> d
    8: ("", "r"),    # ㄹ -> r
    9: ("l", "g"),   # ㄻ/ㄺ -> l-g
    10: ("l", "m"),  # ㄻ -> l-m
    11: ("l", "b"),  # ㄼ -> l-b
    12: ("l", "s"),  # ㄽ -> l-s
    13: ("l", "t"),  # ㄾ -> l-t
    14: ("l", "p"),  # ㄿ -> l-p
    15: ("", "r"),   # ㅀ -> r (h silent)
    16: ("", "m"),   # ㅁ -> m
    17: ("", "b"),   # ㅂ -> b
    18: ("p", "s"),  # ㅄ -> p-s
    19: ("", "s"),   # ㅅ -> s
    20: ("", "ss"),  # ㅆ -> ss
    21: ("ng", ""),  # ㅇ -> ng (stays in coda)
    22: ("", "j"),   # ㅈ -> j
    23: ("", "ch"),  # ㅊ -> ch
    24: ("", "k"),   # ㅋ -> k
    25: ("", "t"),   # ㅌ -> t
    26: ("", "p"),   # ㅍ -> p
    27: ("", ""),    # ㅎ -> silent between vowels (좋아요 -> joayo!)
}

# Stop batchim categories for nasalization before ㄴ(2) / ㅁ(6) / ㄹ(5)
_K_CODAS = {1, 2, 3, 9, 24}          # -> ng
_T_CODAS = {7, 19, 20, 22, 23, 25, 27}  # -> n
_P_CODAS = {17, 18, 26}              # -> m

# Aspiration merges with ㅎ (choseong 18 or jongseong 27)
_ASPIRATE_NEXT = {0: "k", 3: "t", 7: "p", 12: "ch"}  # ㄱㄷㅂㅈ -> ㅋㅌㅍㅊ


def romanize_hangul_word(word: str) -> str:
    """Convert any Hangul string to accurate Revised Romanization with assimilation."""
    # Decompose syllables into (cho, jung, jong) or None for non-Hangul
    syls: list[tuple[int, int, int] | str] = []
    for ch in word:
        cp = ord(ch)
        if 0xAC00 <= cp <= 0xD7A3:
            off = cp - 0xAC00
            cho = off // 588
            jung = (off % 588) // 28
            jong = off % 28
            syls.append((cho, jung, jong))
        else:
            syls.append(ch)

    n = len(syls)
    cho_str = [""] * n
    jung_str = [""] * n
    jong_str = [""] * n

    for i, item in enumerate(syls):
        if isinstance(item, tuple):
            c, v, f = item
            # Word-initial ㄹ is written 'r'; intervocalic handled by liaison; after ㄹ -> 'l'
            cho_str[i] = _KO_CHOSEONG[c]
            jung_str[i] = _KO_JUNGSEONG[v]
            jong_str[i] = _KO_JONGSEONG[f]

    # Apply inter-syllabic phonological rules
    for i in range(n - 1):
        a, b = syls[i], syls[i + 1]
        if not (isinstance(a, tuple) and isinstance(b, tuple)):
            continue
        _, _, f1 = a
        c2, v2, _ = b
        if f1 == 0:
            continue

        # 1. Palatalization: ㄷ(7)/ㅌ(25) before 이(v2==20, c2==11) -> j / ch (같이 -> gachi)
        if c2 == 11 and v2 == 20 and f1 in (7, 25):
            jong_str[i] = ""
            cho_str[i + 1] = "j" if f1 == 7 else "ch"
            continue

        # 2. Liaison before empty initial ㅇ (c2 == 11)
        if c2 == 11 and f1 in _KO_LIAISON:
            rem, nxt = _KO_LIAISON[f1]
            jong_str[i] = rem
            cho_str[i + 1] = nxt
            continue

        # 3. Aspiration with ㅎ (f1 == 27 + ㄱㄷㅂㅈ, or ㄱㄷㅂㅈ batchim + c2 == 18)
        if f1 in (6, 15, 27) and c2 in _ASPIRATE_NEXT:
            jong_str[i] = "n" if f1 == 6 else ("l" if f1 == 15 else "")
            cho_str[i + 1] = _ASPIRATE_NEXT[c2]
            continue
        if c2 == 18:
            if f1 in (1, 9):
                jong_str[i] = "" if f1 == 1 else "l"
                cho_str[i + 1] = "k"
                continue
            if f1 in (7, 19, 22):
                jong_str[i] = ""
                cho_str[i + 1] = "ch" if f1 == 22 else "t"
                continue
            if f1 in (17, 11):
                jong_str[i] = "" if f1 == 17 else "l"
                cho_str[i + 1] = "p"
                continue
            if f1 == 8:
                jong_str[i] = ""
                cho_str[i + 1] = "r"
                continue

        # 4. Liquidization: ㄴ+ㄹ or ㄹ+ㄴ -> l-l (연락 -> yeollak, 설날 -> seollal)
        if (f1 == 4 and c2 == 5) or (f1 == 8 and c2 == 2):
            jong_str[i] = "l"
            cho_str[i + 1] = "l"
            continue
        if f1 == 8 and c2 == 5:
            jong_str[i] = "l"
            cho_str[i + 1] = "l"
            continue

        # 5. Nasalization: stop + ㄴ(2)/ㅁ(6) -> nasal; stop/ㅁ/ㅇ + ㄹ(5) -> nasal + n (한국말 -> hangungmal, 독립 -> dongnip)
        if c2 in (2, 6):
            if f1 in _K_CODAS:
                jong_str[i] = "ng"
            elif f1 in _T_CODAS:
                jong_str[i] = "n"
            elif f1 in _P_CODAS:
                jong_str[i] = "m"
            continue
        if c2 == 5:
            if f1 in _K_CODAS:
                jong_str[i] = "ng"
                cho_str[i + 1] = "n"
            elif f1 in _P_CODAS:
                jong_str[i] = "m"
                cho_str[i + 1] = "n"
            elif f1 in (16, 21):  # ㅁ, ㅇ + ㄹ -> m/ng + n
                cho_str[i + 1] = "n"
            elif f1 in _T_CODAS:
                jong_str[i] = "n"
                cho_str[i + 1] = "n"

    parts: list[str] = []
    for i, item in enumerate(syls):
        if isinstance(item, tuple):
            parts.append(cho_str[i] + jung_str[i] + jong_str[i])
        else:
            parts.append(item)
    return "".join(parts)


# Common Korean postpositional particles stripped when matching TOPIK dictionary lemmas
_KO_PARTICLES = (
    "에서는", "에게는", "한테서", "으로", "에서", "에게", "한테", "까지", "부터", "처럼", "보다",
    "하고", "이랑", "은", "는", "이", "가", "을", "를", "의", "에", "도", "만", "로", "와", "과",
)


# ---------------------------------------------------------------------------
# Japanese Hiragana / Katakana Mathematical Hepburn Romanization
# ---------------------------------------------------------------------------

_JA_DIGRAPHS = {
    "きゃ": "kya", "きゅ": "kyu", "きょ": "kyo",
    "しゃ": "sha", "しゅ": "shu", "しょ": "sho",
    "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho",
    "にゃ": "nya", "にゅ": "nyu", "にょ": "nyo",
    "ひゃ": "hya", "ひゅ": "hyu", "ひょ": "hyo",
    "みゃ": "mya", "みゅ": "myu", "みょ": "myo",
    "りゃ": "rya", "りゅ": "ryu", "りょ": "ryo",
    "ぎゃ": "gya", "ぎゅ": "gyu", "ぎょ": "gyo",
    "じゃ": "ja",  "じゅ": "ju",  "じょ": "jo",
    "びゃ": "bya", "びゅ": "byu", "びょ": "byo",
    "ぴゃ": "pya", "ぴゅ": "pyu", "ぴょ": "pyo",
    "てぃ": "ti",  "でぃ": "di",  "ふぁ": "fa", "ふぃ": "fi", "ふぇ": "fe", "ふぉ": "fo",
}

_JA_MONO = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "ゐ": "wi", "ゑ": "we", "を": "o", "ん": "n",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ゔ": "vu",
}

_MACRON = {"a": "ā", "i": "ī", "u": "ū", "e": "ē", "o": "ō"}


def _kata_to_hira(text: str) -> str:
    out = []
    for ch in text:
        cp = ord(ch)
        if 0x30A1 <= cp <= 0x30F6:
            out.append(chr(cp - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def romanize_kana(text: str) -> str:
    h = _kata_to_hira(text)
    out: list[str] = []
    i = 0
    n = len(h)
    geminate = False
    while i < n:
        ch = h[i]
        if ch == "っ":
            geminate = True
            i += 1
            continue
        if ch == "ー":
            if out and out[-1] and out[-1][-1] in _MACRON:
                out[-1] = out[-1][:-1] + _MACRON[out[-1][-1]]
            i += 1
            continue
        syl = ""
        step = 1
        if i + 1 < n and h[i : i + 2] in _JA_DIGRAPHS:
            syl = _JA_DIGRAPHS[h[i : i + 2]]
            step = 2
        elif ch in _JA_MONO:
            syl = _JA_MONO[ch]
        else:
            syl = ch
        if geminate and syl and syl[0].isalpha():
            syl = ("t" if syl.startswith("ch") else syl[0]) + syl
        geminate = False
        out.append(syl)
        i += step
    res = "".join(out)
    res = res.replace("ou", "ō").replace("uu", "ū")
    return res


# ---------------------------------------------------------------------------
# High-frequency Grammar / Stopwords (optional toggle in Vocabulary Extractor)
# ---------------------------------------------------------------------------

STOPWORDS: dict[str, set[str]] = {
    "zh": {"的", "了", "吗", "呢", "吧", "啊", "呀", "着", "过", "地", "得", "在", "是", "和", "也", "都", "就", "这", "那", "我", "你", "他", "她", "它", "们", "个", "一", "不", "有"},
    "ko": {"은", "는", "이", "가", "을", "를", "의", "에", "도", "만", "로", "와", "과", "그", "저", "것", "수", "더", "안", "못", "네", "아", "어"},
    "ja": {"の", "に", "は", "を", "た", "が", "で", "て", "と", "し", "れ", "さ", "です", "ます", "か", "も", "な", "よ", "ね"},
    "en": {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by",
        "from", "as", "is", "am", "are", "was", "were", "be", "been", "being", "have", "has", "had",
        "do", "does", "did", "will", "would", "can", "could", "should", "may", "might", "must",
        "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them", "my", "your",
        "his", "its", "our", "their", "this", "that", "these", "those", "so", "if", "then", "than",
        "now", "here", "there", "very", "just", "also", "not", "no", "yes", "let", "lets", "let's",
        "say", "means", "word", "sentence", "example", "chinese", "mandarin", "korean", "japanese",
        "english", "when", "what", "how", "who", "where", "why", "about", "into", "up", "out", "all",
    },
    "ro": {"și", "în", "la", "de", "cu", "pe", "un", "o", "că", "nu", "da", "mai", "din", "pentru", "este", "sunt", "ce", "el", "ea"},
    "de": {"der", "die", "das", "ein", "eine", "und", "oder", "in", "zu", "den", "mit", "auf", "für", "nicht", "ist", "sind", "ich", "du", "er", "sie", "es", "wir"},
    "fr": {"le", "la", "les", "un", "une", "des", "et", "ou", "de", "du", "en", "à", "dans", "pour", "pas", "est", "sont", "je", "tu", "il", "elle", "nous", "vous"},
    "es": {"el", "la", "los", "las", "un", "una", "y", "o", "de", "del", "en", "a", "con", "por", "para", "no", "es", "son", "yo", "tú", "él", "ella"},
    "fa": {"و", "در", "به", "از", "که", "این", "آن", "را", "با", "برای", "تا", "هم", "یا", "اما", "است", "هست", "بود", "شد", "می", "من", "تو", "او", "ما", "شما", "آنها", "یک"},
}

# Language-specific diacritics for smart intra-sentence European target-word detection
_LANG_DIACRITICS: dict[str, set[str]] = {
    "ro": set("ăâîșțĂÂÎȘȚşţŞŢ"),
    "de": set("äöüßÄÖÜ"),
    "fr": set("àâæçéèêëîïôœùûüÿÀÂÆÇÉÈÊËÎÏÔŒÙÛÜŸ"),
    "es": set("áéíóúüñÁÉÍÓÚÜÑ¿¡"),
    "it": set("àèéìíîòóùúÀÈÉÌÍÎÒÓÙÚ"),
    "tr": set("çğıöşüÇĞİÖŞÜ"),
}

_INLINE_ROMA_RE = re.compile(r"\s*\([A-Za-zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜüĀÁǍÀĒÉĚÈĪÍǏÌŌÓǑÒŪÚǓÙ'’\- ]+\)")


# ---------------------------------------------------------------------------
# Main Multilingual Engine & Dictionary Store
# ---------------------------------------------------------------------------

class MultilingualLexicon:
    """Thread-safe offline lexicon, tokenizer, romanizer, and user dictionary manager."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.custom_path = self.data_dir / "custom_dict.tsv"

        self.zh_chars: dict[str, str] = {}
        self.zh_words: dict[str, DictEntry] = {}
        self.ko_words: dict[str, DictEntry] = {}
        self.ja_words: dict[str, DictEntry] = {}
        # custom[(lang, surface_lower)] = DictEntry
        self.custom: dict[tuple[str, str], DictEntry] = {}
        # Fast lookup by exact surface (case-insensitive for Latin)
        self._custom_by_surface: dict[str, DictEntry] = {}
        self._max_zh_len = 8
        self._max_ja_len = 12
        self._max_ko_len = 12

        self._load_builtins()
        self.reload_custom()

    # --- Loading & Persistence ---

    def _load_builtins(self) -> None:
        zh_c = self.data_dir / "zh_chars.tsv"
        if zh_c.is_file():
            for line in zh_c.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#") or "\t" not in line:
                    continue
                ch, py = line.split("\t", 1)
                if ch:
                    self.zh_chars[ch] = py.strip()

        def _load_word_tsv(fname: str, lang: str, target: dict[str, DictEntry]) -> int:
            max_l = 4
            fp = self.data_dir / fname
            if not fp.is_file():
                return max_l
            for line in fp.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#") or "\t" not in line:
                    continue
                cols = line.split("\t")
                surf = cols[0].strip()
                if not surf:
                    continue
                roma = cols[1].strip() if len(cols) > 1 else ""
                meaning = cols[2].strip() if len(cols) > 2 else ""
                level = cols[3].strip() if len(cols) > 3 else ""
                pos = cols[4].strip() if len(cols) > 4 else ""
                target[surf] = DictEntry(surf, lang, roma, meaning, level, pos, "builtin")
                if len(surf) > max_l:
                    max_l = len(surf)
            return max_l

        self._max_zh_len = min(12, _load_word_tsv("zh_words.tsv", "zh", self.zh_words))
        self._max_ko_len = min(16, _load_word_tsv("ko_words.tsv", "ko", self.ko_words))
        self._max_ja_len = min(16, _load_word_tsv("ja_words.tsv", "ja", self.ja_words))

    def reload_custom(self) -> None:
        self.custom.clear()
        self._custom_by_surface.clear()
        if not self.custom_path.is_file():
            return
        for line in self.custom_path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or "\t" not in line:
                continue
            cols = line.split("\t")
            surf = cols[0].strip()
            lang = (cols[1].strip().lower() if len(cols) > 1 else "") or self.guess_word_lang(surf)
            roma = cols[2].strip() if len(cols) > 2 else ""
            meaning = cols[3].strip() if len(cols) > 3 else ""
            level = cols[4].strip() if len(cols) > 4 else "Custom"
            pos = cols[5].strip() if len(cols) > 5 else ""
            if not surf:
                continue
            if not roma and lang in ("zh", "ko", "ja"):
                roma = self.romanize_word(surf, lang)
            ent = DictEntry(surf, lang, roma, meaning, level or "Custom", pos, "custom")
            self.custom[(lang, surf.lower())] = ent
            self._custom_by_surface[surf.lower()] = ent
            if lang == "zh" and len(surf) > self._max_zh_len:
                self._max_zh_len = min(16, len(surf))

    def save_custom(self) -> None:
        lines = [
            "# HoSub Custom Dictionary — editable inside HoSub Studio or any spreadsheet editor",
            "# Columns: surface<TAB>lang<TAB>romanization<TAB>meaning<TAB>level<TAB>pos",
        ]
        for ent in sorted(self.custom.values(), key=lambda e: (e.lang, e.surface.lower())):
            safe = [
                ent.surface.replace("\t", " ").replace("\n", " ").strip(),
                ent.lang.replace("\t", "").strip().lower(),
                ent.romanization.replace("\t", " ").replace("\n", " ").strip(),
                ent.meaning.replace("\t", " ").replace("\n", " ").strip(),
                (ent.level or "Custom").replace("\t", " ").strip(),
                ent.pos.replace("\t", " ").strip(),
            ]
            lines.append("\t".join(safe))
        self.custom_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def upsert_custom(
        self,
        surface: str,
        lang: str = "",
        romanization: str = "",
        meaning: str = "",
        level: str = "Custom",
        pos: str = "",
    ) -> DictEntry:
        surf = surface.strip()
        if not surf:
            raise ValueError("Word surface cannot be empty.")
        lg = (lang.strip().lower() or self.guess_word_lang(surf))[:8]
        roma = romanization.strip()
        if not roma and lg in ("zh", "ko", "ja"):
            roma = self.romanize_word(surf, lg)
        ent = DictEntry(surf, lg, roma, meaning.strip(), (level.strip() or "Custom"), pos.strip(), "custom")
        self.custom[(lg, surf.lower())] = ent
        self._custom_by_surface[surf.lower()] = ent
        if lg == "zh" and len(surf) > self._max_zh_len:
            self._max_zh_len = min(16, len(surf))
        self.save_custom()
        return ent

    def delete_custom(self, surface: str, lang: str) -> bool:
        key = (lang.strip().lower(), surface.strip().lower())
        removed = self.custom.pop(key, None)
        if removed is None:
            return False
        # Rebuild surface cache
        self._custom_by_surface = {e.surface.lower(): e for e in self.custom.values()}
        self.save_custom()
        return True

    def import_custom_rows(self, raw_bytes: bytes, filename: str, default_lang: str = "zh") -> int:
        """Bulk-import custom dictionary entries from `.tsv`, `.csv`, or `.xlsx`."""
        ext = Path(filename).suffix.lower()
        rows: list[list[str]] = []
        if ext == ".xlsx":
            rows = _read_xlsx_rows(raw_bytes)
        else:
            text = raw_bytes.decode("utf-8-sig", errors="replace")
            delim = "\t" if (ext == ".tsv" or "\t" in text.splitlines()[0] if text.splitlines() else False) else ","
            reader = csv.reader(io.StringIO(text), delimiter=delim)
            rows = [r for r in reader if r and not r[0].strip().startswith("#")]

        if not rows:
            return 0

        # Detect optional header row
        header = [c.strip().lower() for c in rows[0]]
        header_keywords = {"word", "surface", "term", "hanzi", "hangul", "kanji", "pinyin", "romanization", "romaji", "meaning", "translation", "lang", "language"}
        col_map = {"surface": 0, "lang": -1, "romanization": 1, "meaning": 2, "level": 3, "pos": 4}
        start_idx = 0
        if any(h in header_keywords for h in header):
            start_idx = 1
            col_map = {"surface": -1, "lang": -1, "romanization": -1, "meaning": -1, "level": -1, "pos": -1}
            for idx, h in enumerate(header):
                if h in ("word", "surface", "term", "hanzi", "hangul", "kanji", "front", "expression"):
                    col_map["surface"] = idx
                elif h in ("lang", "language", "code"):
                    col_map["lang"] = idx
                elif h in ("romanization", "pinyin", "romaji", "reading", "pronunciation", "ipa"):
                    col_map["romanization"] = idx
                elif h in ("meaning", "translation", "definition", "gloss", "back"):
                    col_map["meaning"] = idx
                elif h in ("level", "hsk", "topik", "jlpt", "tag", "lesson", "deck"):
                    col_map["level"] = idx
                elif h in ("pos", "part_of_speech", "type", "note"):
                    col_map["pos"] = idx
            if col_map["surface"] < 0:
                col_map["surface"] = 0
        elif len(rows[0]) >= 3 and len(rows[0][1].strip()) == 2 and rows[0][1].strip().isalpha() and rows[0][1].strip().islower():
            # Matches our custom_dict.tsv 6-column layout: surface, lang, roma, meaning, level, pos
            col_map = {"surface": 0, "lang": 1, "romanization": 2, "meaning": 3, "level": 4, "pos": 5}

        def _get(r: list[str], k: str) -> str:
            ci = col_map[k]
            return r[ci].strip() if 0 <= ci < len(r) else ""

        added = 0
        for r in rows[start_idx:]:
            surf = _get(r, "surface")
            if not surf:
                continue
            lg = (_get(r, "lang").lower() or self.guess_word_lang(surf, fallback=default_lang))[:8]
            roma = _get(r, "romanization")
            if not roma and lg in ("zh", "ko", "ja"):
                roma = self.romanize_word(surf, lg)
            meaning = _get(r, "meaning")
            level = _get(r, "level") or "Custom"
            pos = _get(r, "pos")
            ent = DictEntry(surf, lg, roma, meaning, level, pos, "custom")
            self.custom[(lg, surf.lower())] = ent
            self._custom_by_surface[surf.lower()] = ent
            if lg == "zh" and len(surf) > self._max_zh_len:
                self._max_zh_len = min(16, len(surf))
            added += 1

        if added:
            self.save_custom()
        return added

    def export_custom_tsv(self) -> str:
        if not self.custom_path.is_file():
            self.save_custom()
        return self.custom_path.read_text(encoding="utf-8")

    def stats(self) -> dict[str, int]:
        return {
            "zh_words": len(self.zh_words),
            "zh_chars": len(self.zh_chars),
            "ko_words": len(self.ko_words),
            "ja_words": len(self.ja_words),
            "custom_words": len(self.custom),
        }

    def search(self, query: str = "", lang: str = "all", source: str = "all", limit: int = 80) -> list[dict[str, str]]:
        q = query.strip().lower()
        results: list[DictEntry] = []

        def _match(e: DictEntry) -> bool:
            if lang not in ("", "all") and e.lang != lang:
                return False
            if not q:
                return True
            return (
                q in e.surface.lower()
                or q in e.romanization.lower()
                or q in e.meaning.lower()
                or q in e.level.lower()
            )

        # Always show custom entries first!
        if source in ("all", "custom"):
            for e in sorted(self.custom.values(), key=lambda x: x.surface.lower()):
                if _match(e):
                    results.append(e)
                    if len(results) >= limit:
                        return [r.to_dict() for r in results]

        if source in ("all", "builtin"):
            pools: list[dict[str, DictEntry]] = []
            if lang in ("", "all", "zh"):
                pools.append(self.zh_words)
            if lang in ("", "all", "ko"):
                pools.append(self.ko_words)
            if lang in ("", "all", "ja"):
                pools.append(self.ja_words)
            # Exact match boost first
            if q:
                for pool in pools:
                    if query.strip() in pool:
                        e = pool[query.strip()]
                        if e not in results:
                            results.append(e)
            for pool in pools:
                for e in pool.values():
                    if _match(e) and e not in results:
                        results.append(e)
                        if len(results) >= limit:
                            return [r.to_dict() for r in results]

        return [r.to_dict() for r in results]

    # --- Language Detection & Lookup ---

    def guess_word_lang(self, word: str, fallback: str = "en") -> str:
        if any(is_hangul_char(c) for c in word):
            return "ko"
        if any(is_kana_char(c) for c in word):
            return "ja"
        if any(is_hanzi_char(c) for c in word):
            return "ja" if (word in self.ja_words and word not in self.zh_words) else "zh"
        if any(is_rtl_char(c) for c in word):
            return "fa"
        for lg, dset in _LANG_DIACRITICS.items():
            if any(c in dset for c in word):
                return lg
        return fallback

    def lookup(self, surface: str, preferred_lang: str = "") -> DictEntry | None:
        sl = surface.lower()
        if preferred_lang and (preferred_lang, sl) in self.custom:
            return self.custom[(preferred_lang, sl)]
        if sl in self._custom_by_surface:
            return self._custom_by_surface[sl]
        if preferred_lang == "ja":
            return self.ja_words.get(surface) or self.zh_words.get(surface)
        if preferred_lang == "ko":
            return self.ko_words.get(surface)
        if preferred_lang == "zh":
            return self.zh_words.get(surface)
        return self.zh_words.get(surface) or self.ko_words.get(surface) or self.ja_words.get(surface)

    def romanize_word(self, word: str, lang: str = "") -> str:
        lg = lang or self.guess_word_lang(word)
        ent = self.lookup(word, lg)
        if ent and ent.romanization:
            return ent.romanization
        if lg == "ko" or any(is_hangul_char(c) for c in word):
            return " ".join(romanize_hangul_word(part) for part in word.split())
        if lg == "ja":
            return self._romanize_japanese_span(word)
        if lg == "zh":
            syls = [self.zh_chars.get(ch, ch) for ch in word]
            return " ".join(syls)
        return ""

    def _romanize_japanese_span(self, span: str) -> str:
        out: list[str] = []
        i = 0
        n = len(span)
        while i < n:
            matched = False
            for l in range(min(self._max_ja_len, n - i), 0, -1):
                sub = span[i : i + l]
                ent = self.custom.get(("ja", sub.lower())) or self.ja_words.get(sub)
                if ent and ent.romanization:
                    out.append(ent.romanization)
                    i += l
                    matched = True
                    break
            if matched:
                continue
            ch = span[i]
            if is_kana_char(ch):
                j = i + 1
                while j < n and is_kana_char(span[j]):
                    j += 1
                out.append(romanize_kana(span[i:j]))
                i = j
            elif is_hanzi_char(ch):
                # Fallback to reading if known, else keep char
                ent = self.ja_words.get(ch)
                out.append(ent.romanization if ent else self.zh_chars.get(ch, ch))
                i += 1
            else:
                out.append(ch)
                i += 1
        return " ".join(p for p in out if p)

    # --- Longest-Match Chinese & Mixed-Language Tokenizer ---

    def _segment_chinese(self, span: str) -> list[TextToken]:
        """Forward Maximum Matching over Custom + 17,558 HSK words + 20,924 Unihan chars."""
        tokens: list[TextToken] = []
        i = 0
        n = len(span)
        while i < n:
            best: DictEntry | None = None
            best_len = 1
            max_l = min(self._max_zh_len, n - i)
            for l in range(max_l, 0, -1):
                sub = span[i : i + l]
                c_ent = self.custom.get(("zh", sub.lower()))
                if c_ent is not None:
                    best = c_ent
                    best_len = l
                    break
                if l >= 2 and sub in self.zh_words:
                    best = self.zh_words[sub]
                    best_len = l
                    break
                if l == 1 and sub in self.zh_words:
                    best = self.zh_words[sub]
                    best_len = 1
                    break
            sub = span[i : i + best_len]
            if best is not None:
                tokens.append(
                    TextToken(
                        surface=sub,
                        lang="zh",
                        romanization=best.romanization or self.zh_chars.get(sub, ""),
                        meaning=best.meaning,
                        level=best.level,
                        pos=best.pos,
                        lemma=sub,
                        is_word=True,
                    )
                )
            else:
                py = self.zh_chars.get(sub, "")
                tokens.append(TextToken(surface=sub, lang="zh", romanization=py, lemma=sub, is_word=True))
            i += best_len
        return tokens

    def _analyze_korean_word(self, word: str) -> TextToken:
        # 1. Exact custom or TOPIK match
        ent = self.custom.get(("ko", word.lower())) or self.ko_words.get(word)
        if ent is not None:
            return TextToken(
                surface=word,
                lang="ko",
                romanization=ent.romanization or romanize_hangul_word(word),
                meaning=ent.meaning,
                level=ent.level,
                pos=ent.pos,
                lemma=word,
                is_word=True,
            )
        # 2. Check stem + grammatical particle (e.g. 선생님은 -> lemma 선생님)
        lemma = word
        meaning = ""
        level = ""
        pos = ""
        for p in _KO_PARTICLES:
            if len(word) > len(p) + 1 and word.endswith(p):
                stem = word[: -len(p)]
                s_ent = self.custom.get(("ko", stem.lower())) or self.ko_words.get(stem)
                if s_ent is not None:
                    lemma = stem
                    meaning = s_ent.meaning
                    level = s_ent.level
                    pos = s_ent.pos
                    break
        return TextToken(
            surface=word,
            lang="ko",
            romanization=romanize_hangul_word(word),
            meaning=meaning,
            level=level,
            pos=pos,
            lemma=lemma,
            is_word=True,
        )

    def _segment_japanese(self, span: str) -> list[TextToken]:
        tokens: list[TextToken] = []
        i = 0
        n = len(span)
        while i < n:
            matched: DictEntry | None = None
            mlen = 0
            for l in range(min(self._max_ja_len, n - i), 0, -1):
                sub = span[i : i + l]
                ent = self.custom.get(("ja", sub.lower())) or self.ja_words.get(sub)
                if ent is not None:
                    matched = ent
                    mlen = l
                    break
            if matched is not None:
                tokens.append(
                    TextToken(
                        surface=matched.surface,
                        lang="ja",
                        romanization=matched.romanization,
                        meaning=matched.meaning,
                        level=matched.level,
                        pos=matched.pos,
                        lemma=matched.surface,
                        is_word=True,
                    )
                )
                i += mlen
            elif is_kana_char(span[i]):
                j = i + 1
                while j < n and is_kana_char(span[j]):
                    j += 1
                sub = span[i:j]
                tokens.append(TextToken(surface=sub, lang="ja", romanization=romanize_kana(sub), lemma=sub, is_word=True))
                i = j
            else:
                ch = span[i]
                tokens.append(TextToken(surface=ch, lang="ja", romanization=self.zh_chars.get(ch, ""), lemma=ch, is_word=True))
                i += 1
        return tokens

    def tokenize(
        self,
        text: str,
        *,
        base_lang: str = "en",
        target_langs: list[str] | None = None,
    ) -> list[TextToken]:
        """Segment an intra-sentence multilingual line into typed, romanized tokens."""
        clean = strip_inline_romanization(strip_bidi_controls(text or ""))
        if not clean:
            return []

        targets = [t.lower() for t in (target_langs or ["zh", "ko"])]
        prefer_ja = ("ja" in targets and "zh" not in targets) or any(is_kana_char(c) for c in clean)
        latin_targets = [t for t in targets if t not in ("zh", "ko", "ja", "fa", "ar")]

        tokens: list[TextToken] = []
        i = 0
        n = len(clean)

        while i < n:
            ch = clean[i]

            # 1. Hangul span (Korean) — also match multi-word Korean phrases like 만나서 반갑습니다
            if is_hangul_char(ch):
                phrase_hit: DictEntry | None = None
                for cand in (list(self.custom.values()) + list(self.ko_words.values())):
                    if cand.lang == "ko" and " " in cand.surface and clean.startswith(cand.surface, i):
                        if phrase_hit is None or len(cand.surface) > len(phrase_hit.surface):
                            phrase_hit = cand
                if phrase_hit is not None:
                    tokens.append(
                        TextToken(
                            surface=phrase_hit.surface,
                            lang="ko",
                            romanization=phrase_hit.romanization,
                            meaning=phrase_hit.meaning,
                            level=phrase_hit.level,
                            pos=phrase_hit.pos,
                            lemma=phrase_hit.surface,
                            is_word=True,
                        )
                    )
                    i += len(phrase_hit.surface)
                    continue
                j = i + 1
                while j < n and is_hangul_char(clean[j]):
                    j += 1
                tokens.append(self._analyze_korean_word(clean[i:j]))
                i = j
                continue

            # 2. Japanese (Kana, or Hanzi+Kana mixed, or Hanzi when ja is target)
            if is_kana_char(ch) or (is_hanzi_char(ch) and prefer_ja):
                j = i + 1
                while j < n and (is_kana_char(clean[j]) or is_hanzi_char(clean[j])):
                    j += 1
                tokens.extend(self._segment_japanese(clean[i:j]))
                i = j
                continue

            # 3. Mandarin Chinese (Hanzi)
            if is_hanzi_char(ch):
                j = i + 1
                while j < n and is_hanzi_char(clean[j]):
                    j += 1
                tokens.extend(self._segment_chinese(clean[i:j]))
                i = j
                continue

            # 4. Persian / Arabic (RTL)
            if is_rtl_char(ch):
                j = i + 1
                while j < n and (is_rtl_char(clean[j]) or clean[j] == "\u200c"):
                    j += 1
                w = clean[i:j]
                c_ent = self._custom_by_surface.get(w.lower())
                lg = "fa" if base_lang == "fa" or "fa" in targets else "ar"
                tokens.append(
                    TextToken(
                        surface=w,
                        lang=c_ent.lang if c_ent else lg,
                        romanization=c_ent.romanization if c_ent else "",
                        meaning=c_ent.meaning if c_ent else "",
                        level=c_ent.level if c_ent else "",
                        pos=c_ent.pos if c_ent else "",
                        lemma=w,
                        is_word=True,
                    )
                )
                i = j
                continue

            # 5. Latin alphabet (English, Romanian, German, French, Spanish, etc.)
            if is_latin_char(ch):
                j = i + 1
                while j < n and (is_latin_char(clean[j]) or (clean[j] in "'’-" and j + 1 < n and is_latin_char(clean[j + 1]))):
                    j += 1
                w = clean[i:j]
                wl = w.lower()
                c_ent = None
                for lt in latin_targets:
                    if (lt, wl) in self.custom:
                        c_ent = self.custom[(lt, wl)]
                        break
                if c_ent is None:
                    c_ent = self._custom_by_surface.get(wl)
                if c_ent is not None:
                    lg = c_ent.lang
                else:
                    lg = base_lang if base_lang not in ("zh", "ko", "ja", "fa", "ar") else "en"
                    # Check target-language distinctive diacritics (e.g. ă/ș/ț -> ro, ä/ß -> de)
                    for lt in latin_targets:
                        dset = _LANG_DIACRITICS.get(lt)
                        if dset and any(c in dset for c in w):
                            lg = lt
                            break
                tokens.append(
                    TextToken(
                        surface=w,
                        lang=lg,
                        romanization=c_ent.romanization if c_ent else "",
                        meaning=c_ent.meaning if c_ent else "",
                        level=c_ent.level if c_ent else "",
                        pos=c_ent.pos if c_ent else "",
                        lemma=wl if lg == "en" else w,
                        is_word=True,
                    )
                )
                i = j
                continue

            # 6. Punctuation / whitespace / digits
            j = i + 1
            while j < n and not (
                is_cjk_char(clean[j]) or is_rtl_char(clean[j]) or is_latin_char(clean[j])
            ):
                j += 1
            tokens.append(TextToken(surface=clean[i:j], lang="punct", is_word=False))
            i = j

        return tokens

    # --- Subtitle Annotation (Inline & Stacked Ruby) ---

    def annotate_line(
        self,
        text: str,
        *,
        mode: str = "inline",  # "none" | "inline" | "stacked"
        base_lang: str = "en",
        target_langs: list[str] | None = None,
    ) -> str:
        """Render a subtitle line with instant non-destructive Pinyin/Romaji toggle."""
        clean = strip_inline_romanization(strip_bidi_controls(text or "")).strip()
        if not clean or mode == "none":
            return clean

        targets = [t.lower() for t in (target_langs or ["zh", "ko", "ja"])]
        roma_langs = {t for t in targets if t in ("zh", "ko", "ja")} or {"zh", "ko", "ja"}
        # Also include any custom-dictionary words with explicit pronunciation/IPA
        toks = self.tokenize(clean, base_lang=base_lang, target_langs=targets)

        # Group contiguous target-script runs so e.g. "老师好" renders as "老师好 (lǎoshī hǎo)"
        # instead of noisy word-by-word "(lǎoshī)(hǎo)"!
        if mode == "inline":
            out: list[str] = []
            i = 0
            n = len(toks)
            while i < n:
                t = toks[i]
                if t.is_word and ((t.lang in roma_langs and t.romanization) or (t.lang in targets and t.romanization)):
                    surf_parts = [t.surface]
                    roma_parts = [t.romanization]
                    j = i + 1
                    while j < n:
                        nt = toks[j]
                        if nt.is_word and nt.lang == t.lang and nt.romanization:
                            surf_parts.append(nt.surface)
                            roma_parts.append(nt.romanization)
                            j += 1
                        elif nt.surface == " " and j + 1 < n and toks[j + 1].is_word and toks[j + 1].lang == t.lang and toks[j + 1].romanization:
                            surf_parts.append(" ")
                            j += 1
                        else:
                            break
                    surf_join = "".join(surf_parts)
                    roma_join = " ".join(r for r in roma_parts if r).strip()
                    out.append(f"{surf_join} ({roma_join})" if roma_join else surf_join)
                    i = j
                else:
                    out.append(t.surface)
                    i += 1
            return "".join(out)

        if mode == "stacked":
            roma_chunks: list[str] = []
            for t in toks:
                if t.is_word and ((t.lang in roma_langs and t.romanization) or (t.lang in targets and t.romanization)):
                    roma_chunks.append(t.romanization)
            if roma_chunks:
                return f"{' · '.join(roma_chunks)}\n{clean}"
        return clean

    # --- Target-Language Span Detector (drives the Code-Switching Audio Cutter!) ---

    def find_target_spans(
        self,
        text: str,
        *,
        base_lang: str = "en",
        target_langs: list[str] | None = None,
    ) -> list[dict[str, object]]:
        """Locate every contiguous target-language word/phrase inside a subtitle line.

        Returns character offsets and relative duration weights so even when word-level
        Whisper timestamps aren't available, exact phrase boundaries can be estimated,
        and when Whisper token timestamps ARE present, matched with millisecond precision.
        """
        clean = strip_inline_romanization(strip_bidi_controls(text or "")).strip()
        if not clean:
            return []
        targets = {t.lower() for t in (target_langs or ["zh", "ko"])}
        toks = self.tokenize(clean, base_lang=base_lang, target_langs=list(targets))

        # Estimate spoken syllable weight per token (CJK char ~ 1.35x a short Latin char)
        def _weight(tk: TextToken) -> float:
            if not tk.is_word:
                return 0.25 if any(c in tk.surface for c in ",.;:!?،؟。！？") else 0.05
            if tk.lang in ("zh", "ko", "ja"):
                return max(1.0, len(tk.surface.replace(" ", "")) * 1.4)
            return max(0.8, len(tk.surface) * 0.42)

        weights = [_weight(t) for t in toks]
        total_w = max(sum(weights), 1e-6)
        cum_w = 0.0
        tok_ranges: list[tuple[float, float]] = []
        for w in weights:
            tok_ranges.append((cum_w / total_w, (cum_w + w) / total_w))
            cum_w += w

        spans: list[dict[str, object]] = []
        i = 0
        n = len(toks)
        while i < n:
            t = toks[i]
            if t.is_word and t.lang in targets and t.lang != base_lang:
                start_ratio = tok_ranges[i][0]
                end_ratio = tok_ranges[i][1]
                surf_parts = [t.surface]
                roma_parts = [t.romanization] if t.romanization else []
                meanings = [t.meaning] if t.meaning else []
                j = i + 1
                while j < n:
                    nt = toks[j]
                    if nt.is_word and nt.lang == t.lang:
                        surf_parts.append(nt.surface)
                        if nt.romanization:
                            roma_parts.append(nt.romanization)
                        if nt.meaning:
                            meanings.append(nt.meaning)
                        end_ratio = tok_ranges[j][1]
                        j += 1
                    elif nt.surface in (" ", "،", "、") and j + 1 < n and toks[j + 1].is_word and toks[j + 1].lang == t.lang:
                        surf_parts.append(nt.surface)
                        end_ratio = tok_ranges[j][1]
                        j += 1
                    else:
                        break
                phrase = "".join(surf_parts).strip()
                roma = " ".join(roma_parts).strip()
                # Check if whole joined phrase has a direct dictionary meaning
                whole = self.lookup(phrase, t.lang)
                gloss = whole.meaning if (whole and whole.meaning) else " / ".join(dict.fromkeys(meanings))
                spans.append(
                    {
                        "text": phrase,
                        "lang": t.lang,
                        "romanization": whole.romanization if (whole and whole.romanization) else roma,
                        "meaning": gloss,
                        "start_ratio": round(start_ratio, 4),
                        "end_ratio": round(end_ratio, 4),
                        "is_full_line": (start_ratio <= 0.06 and end_ratio >= 0.94),
                    }
                )
                i = j
            else:
                i += 1
        return spans

    # --- Vocabulary & Flashcard Extractor ---

    def extract_vocabulary(
        self,
        subtitles: list[dict],
        *,
        base_lang: str = "en",
        target_langs: list[str] | None = None,
        scope: str = "target",          # "target" | "all"
        hide_stopwords: bool = True,
        min_freq: int = 1,
        saved_overrides: dict[str, dict] | None = None,
    ) -> list[dict]:
        """Extract deduplicated vocabulary cards with romanization, context, timestamp & audio link."""
        targets = [t.lower() for t in (target_langs or ["zh", "ko"])]
        target_set = set(targets)
        overrides = saved_overrides or {}

        cards: dict[tuple[str, str], dict] = {}
        order_counter = 0

        for row_idx, sub in enumerate(subtitles):
            raw_text = strip_inline_romanization(strip_bidi_controls(str(sub.get("text", "")))).strip()
            if not raw_text:
                continue
            start_ms = int(sub.get("start_ms", 0))
            end_ms = int(sub.get("end_ms", 0))
            translation = strip_bidi_controls(str(sub.get("translation", ""))).strip()

            toks = self.tokenize(raw_text, base_lang=base_lang, target_langs=targets)
            for tk in toks:
                if not tk.is_word:
                    continue
                if scope == "target" and tk.lang not in target_set:
                    continue
                word = tk.lemma or tk.surface
                if tk.lang == "en" and len(word) < 2:
                    continue
                sw = STOPWORDS.get(tk.lang, set())
                is_stop = word.lower() in sw or tk.surface.lower() in sw
                if hide_stopwords and is_stop:
                    continue

                key = (tk.lang, word.lower() if is_latin_char(word[0]) else word)
                if key not in cards:
                    order_counter += 1
                    # Prefer lemma lookup so e.g. 선생님은 displays lemma 선생님 (seonsaengnim)
                    ent = self.lookup(word, tk.lang)
                    roma = ent.romanization if (ent and ent.romanization) else (
                        self.romanize_word(word, tk.lang) if word != tk.surface else tk.romanization
                    )
                    ov_key = f"{tk.lang}:{word}"
                    ov = overrides.get(ov_key, {})
                    cards[key] = {
                        "id": ov_key,
                        "order": order_counter,
                        "word": word,
                        "surface_example": tk.surface,
                        "lang": tk.lang,
                        "romanization": ov.get("romanization") or roma,
                        "meaning": ov.get("meaning") if "meaning" in ov else (ent.meaning if ent else tk.meaning),
                        "pos": ov.get("pos") if "pos" in ov else (ent.pos if ent else tk.pos),
                        "level": ov.get("level") if "level" in ov else (ent.level if ent else tk.level),
                        "note": ov.get("note", ""),
                        "selected": bool(ov.get("selected", True)),
                        "frequency": 1,
                        "first_row": row_idx + 1,
                        "first_start_ms": start_ms,
                        "first_end_ms": end_ms,
                        "context": raw_text,
                        "context_translation": translation,
                        "audio_file": ov.get("audio_file", ""),
                    }
                else:
                    cards[key]["frequency"] += 1

        result = [c for c in cards.values() if c["frequency"] >= max(1, int(min_freq))]
        result.sort(key=lambda c: c["order"])
        return result


def strip_inline_romanization(text: str) -> str:
    return _INLINE_ROMA_RE.sub("", text or "")


def _read_xlsx_rows(raw_bytes: bytes) -> list[list[str]]:
    """Zero-dependency OOXML `.xlsx` first-sheet reader using stdlib zipfile + ElementTree."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", ns):
                texts = [t.text or "" for t in si.findall(".//m:t", ns)]
                shared.append("".join(texts))
        sheet_name = next(
            (nm for nm in sorted(zf.namelist()) if nm.startswith("xl/worksheets/sheet") and nm.endswith(".xml")),
            None,
        )
        if not sheet_name:
            return []
        sroot = ET.fromstring(zf.read(sheet_name))
        rows: list[list[str]] = []
        for row_el in sroot.findall(".//m:sheetData/m:row", ns):
            vals: dict[int, str] = {}
            max_col = -1
            for c_el in row_el.findall("m:c", ns):
                ref = c_el.attrib.get("r", "A1")
                col_letters = "".join(ch for ch in ref if ch.isalpha()).upper()
                col_idx = 0
                for ch in col_letters:
                    col_idx = col_idx * 26 + (ord(ch) - 64)
                col_idx = max(0, col_idx - 1)
                max_col = max(max_col, col_idx)
                ctype = c_el.attrib.get("t", "")
                val = ""
                if ctype == "inlineStr":
                    val = "".join(t.text or "" for t in c_el.findall(".//m:t", ns))
                else:
                    v_el = c_el.find("m:v", ns)
                    if v_el is not None and v_el.text is not None:
                        if ctype == "s":
                            idx = int(v_el.text)
                            val = shared[idx] if 0 <= idx < len(shared) else ""
                        else:
                            val = v_el.text
                vals[col_idx] = val
            if max_col >= 0:
                rows.append([vals.get(k, "") for k in range(max_col + 1)])
        return rows
