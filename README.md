# HoSub Studio 2.0 — Offline Multilingual Subtitle & Flashcard Studio

**100% offline. Zero `pip` installs. Zero API keys.** Pure Python 3.9+ standard library + vanilla HTML/CSS/JS.

HoSub Studio turns multilingual lesson videos — where a teacher constantly code-switches
(e.g. English explanation → 早上好 → back to English → 감사합니다) — into styled subtitles,
a Premiere-style waveform timeline, Excel flashcards, and a `.zip` of perfectly named
target-language audio clips.

## Quick start

```bash
# Windows: double-click Run_HoSub.bat
# Linux/macOS:
./run_hosub.sh
# or directly:
python hosub.py            # opens the studio in your browser / app window
python hosub.py --port 9000 --no-browser
```

Then (optionally) drop your existing tools next to the app — auto-detected, incl. `E:\hosub`:

```
hosub/
  tools/ffmpeg.exe, tools/whisper-cli.exe
  models/ggml-large-v3-turbo.bin
```

## What it does

1. **Subtitles tab** — video/audio player with live styled overlay (target-script color,
   Pinyin/Romaji inline or stacked, RTL BiDi punctuation lock), cue table + inspector,
   and a **Premiere-style waveform timeline**: `V` select/marquee, `A` Track Select Forward
   (with *Stop at 🔒*), drag/trim with word-timestamps in lockstep, `Ctrl+G` group,
   ±ms nudge, Snap Voice (VAD islands, anti-domino), Split `S`, Merge `M`, Lock `K`, Undo/Redo.
2. **AI Round-Trip tab** — copy a line-locked `[0001]` prompt into any AI chat, paste the
   answer back; HoSub verifies IDs, target terms and reading speed. No API key needed.
3. **Vocab & Audio ZIP tab** — deduplicated flashcards (word, Pinyin/Romanization, meaning,
   POS, level, frequency, context, timestamp) exportable to **.xlsx / .csv / .json**, plus a
   code-switching audio cutter that slices each taught phrase into `001_早上好_zaoshang-hao.mp3`-style
   clips in a `.zip` (with in-browser preview and auto-linking of filenames into Excel cards).
4. **Dictionary tab** — bundled offline lexicons (HSK 1–9 + 20,924 Unihan Hanzi, TOPIK,
   JLPT, full Hangul Revised Romanization with phonological assimilation) plus your own
   persistent custom dictionary (`data/custom_dict.tsv`) with bulk `.xlsx/.csv/.tsv` import.

Exports: `.srt`, `.vtt`, styled `.ass`, `.txt`, `.hosub.json` projects, full audio
(mp3/wav/m4a/flac), dual-track soft-sub MKV/MP4 and hard-sub MP4 (via FFmpeg).

## Tests

```bash
python tests/test_hosub.py     # or: python -m pytest tests/ -v
```

## License

MIT © 2026 Shaghayegh Mastalipour. Lexicon data credits: Unicode Unihan, HSK lists (MIT), TOPIK/JLPT study lists.
