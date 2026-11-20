# Notices — Code License & Bundled Data

## Application code (MIT)
All original HoSub Studio source code (`hosub.py`, `hosub_app/`, `tests/`,
launchers, and documentation other than bundled data files) is licensed under
the **MIT License** — see [`LICENSE`](LICENSE). Copyright (c) 2026
Shaghayegh Mastalipour.

## Bundled lexicon data (NOT covered by the MIT license above)
The wordlists under `data/` are redistributed under their own terms and are
**not** re-licensed as MIT:

| File | Content | Terms |
|---|---|---|
| `data/zh_chars.tsv` | Hanzi readings derived from the Unicode® Unihan Database (kMandarin) | **Unicode License v3** (https://www.unicode.org/terms_of_use.html). Unicode® and Unihan are properties of Unicode, Inc. |
| `data/zh_words.tsv` | Mandarin HSK 1–9 vocabulary, derived from the `complete-hsk-vocabulary` dataset | **MIT License** (upstream project). |
| `data/ko_words.tsv` | Korean TOPIK I–II core study vocabulary compiled for this project | Study wordlist; compiled from public TOPIK curriculum vocabulary. Rights holders may contact the maintainer for attribution/removal. |
| `data/ja_words.tsv` | Japanese JLPT N5–N4 core study vocabulary compiled for this project | Study wordlist; compiled from public JLPT curriculum vocabulary. Rights holders may contact the maintainer for attribution/removal. |
| `data/custom_dict.tsv` | Empty user dictionary template shipped with the app | Your own entries are your own data; the app never transmits them anywhere. |

## External tools (not bundled)
FFmpeg and whisper.cpp / GGML models are **not** included in this repository.
HoSub auto-detects installations provided by the user; those tools and models
remain under their own licenses (FFmpeg: LGPL/GPL depending on build;
whisper.cpp: MIT; Whisper models: MIT, OpenAI).
