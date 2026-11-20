"""HoSub Studio v2.0 — offline automated test suite (stdlib only).

Run:  python3 -m pytest tests/test_hosub.py -v
  or: python3 tests/test_hosub.py
"""
import io, json, sys, urllib.request, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hosub_app.romanizer import MultilingualLexicon
from hosub_app.bidi import stabilize_mixed_line, strip_bidi_controls
from hosub_app.timecage import analyze_wav, snap_subtitles_to_islands, synthesize_demo_lesson_wav
from hosub_app.audio_cutter import plan_target_audio_clips, build_target_audio_zip
from hosub_app.ai_roundtrip import build_subtitle_ai_prompt, apply_subtitle_ai_response, build_vocab_ai_prompt, apply_vocab_ai_response
from hosub_app.exporters import export_srt, export_vtt, export_ass, export_txt, export_vocab_xlsx, export_vocab_csv, parse_srt_or_vtt
from hosub_app.server import _build_demo_project, create_server
import threading

LEX = MultilingualLexicon(ROOT / "data")
CUES = [
    {"id":1,"start_ms":400,"end_ms":3600,"text":"Welcome! In Mandarin, say 早上好.","translation":"خوش آمدید! بگویید 早上好.","locked":False,"group_id":0,
     "words":[{"text":"Welcome!","start_ms":420,"end_ms":880},{"text":"早上好","start_ms":3000,"end_ms":3560}]},
    {"id":2,"start_ms":4100,"end_ms":7500,"text":"In Korean, thank you is 감사합니다.","translation":"در کره‌ای تشکر 감사합니다 است.","locked":False,"group_id":0,
     "words":[{"text":"감사합니다","start_ms":6200,"end_ms":7000}]},
]

def test_romanization():
    assert "zǎo" in LEX.romanize_word("早上好", "zh") or "zao" in LEX.romanize_word("早上好", "zh")
    assert LEX.romanize_word("谢谢", "zh")
    ko = LEX.romanize_word("감사합니다", "ko")
    assert "gamsa" in ko, ko
    assert "annyeong" in LEX.romanize_word("안녕하세요", "ko")
    ja = LEX.romanize_word("ありがとう", "ja")
    assert "arigat" in ja, ja
    inl = LEX.annotate_line(CUES[0]["text"], mode="inline", base_lang="en", target_langs=["zh","ko"])
    assert "早上好" in inl and "(" in inl, inl

def test_bidi():
    out = stabilize_mixed_line("سلام! بگویید 早上好.")
    assert strip_bidi_controls(out) == "سلام! بگویید 早上好."
    assert "早上好" in out

def test_wav_vad_snap(tmp_path=None):
    import tempfile
    d = Path(tempfile.mkdtemp())
    proj, wav = _build_demo_project(d)
    wf = analyze_wav(wav)
    assert wf["duration_ms"] > 20000 and wf["peaks"] and wf["islands"]
    subs = json.loads(json.dumps(proj["subtitles"]))
    changed = snap_subtitles_to_islands(subs, wf["islands"])
    assert isinstance(changed, int)
    for a, b in zip(subs, subs[1:]):
        assert a["end_ms"] <= b["start_ms"] + 400

def test_audio_zip_and_vocab_link():
    import tempfile
    d = Path(tempfile.mkdtemp())
    proj, wav = _build_demo_project(d)
    clips = plan_target_audio_clips(proj["subtitles"], LEX, base_lang="en", target_langs=["zh","ko"],
                                    cut_mode="switch_span", pre_pad_ms=50, post_pad_ms=50, deduplicate=True,
                                    naming_mode="index_word_roma", ext="wav", media_duration_ms=26200)
    assert clips, "no clips planned"
    assert any("早上好" in c["phrase"] for c in clips)
    zb, manifest, exported = build_target_audio_zip(media_path=wav, wav_cache_path=wav, ffmpeg_exe=None,
                                                     clips=clips, audio_format="wav")
    with zipfile.ZipFile(io.BytesIO(zb)) as zf:
        names = zf.namelist()
        assert any(n.endswith(".wav") for n in names), names
        assert "_manifest.csv" in names
    cards = LEX.extract_vocabulary(proj["subtitles"], base_lang="en", target_langs=["zh","ko"])
    assert any(c["word"] == "早上好" for c in cards)

def test_xlsx_and_subtitle_exports():
    data = export_vocab_xlsx(LEX.extract_vocabulary(CUES, base_lang="en", target_langs=["zh","ko"]), project_title="t")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert "xl/workbook.xml" in zf.namelist()
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data))
        assert "Flashcards" in wb.sheetnames
    except ImportError:
        pass
    srt = export_srt(CUES, LEX, base_lang="en", target_langs=["zh","ko"]).decode("utf-8")
    assert "00:00:00,400" in srt and "早上好" in srt
    assert "WEBVTT" in export_vtt(CUES, LEX, base_lang="en", target_langs=["zh","ko"]).decode("utf-8")
    assert "早上好" in export_ass(CUES, LEX, {"romanization_mode":"inline"}, base_lang="en", target_langs=["zh","ko"]).decode("utf-8")
    assert "早上好" in export_txt(CUES, LEX, base_lang="en", target_langs=["zh","ko"]).decode("utf-8")
    parsed = parse_srt_or_vtt(srt)
    assert len(parsed) == 2

def test_ai_roundtrips():
    subs = json.loads(json.dumps(CUES))
    p = build_subtitle_ai_prompt(subs, task="proofread_and_translate", translate_to="fa", batch_size=50)
    assert "[0001]" in p["prompt"] and p["total_rows"] == 2
    rep = apply_subtitle_ai_response(subs, "[0001] Welcome! In Mandarin, say 早上好. || خوش آمدید! بگویید 早上好.\n[0002] In Korean, thank you is 감사합니다. || تشکر 감사합니다 است.",
                                     LEX, task="proofread_and_translate", base_lang="en", target_langs=["zh","ko"])
    assert rep["updated_count"] == 2 and subs[0]["translation"]
    cards = LEX.extract_vocabulary(CUES, base_lang="en", target_langs=["zh","ko"])
    vp = build_vocab_ai_prompt(cards, translate_to="fa")
    assert "[001]" in vp
    ov = {}
    rep2 = apply_vocab_ai_response(cards, "[001] 早上好 | صبح بخیر | phrase | greeting", ov)
    assert rep2["updated_count"] >= 1

def test_dict_crud():
    import tempfile, shutil
    d = Path(tempfile.mkdtemp()) / "data"
    shutil.copytree(ROOT / "data", d)
    lex = MultilingualLexicon(d)
    lex.upsert_custom("电脑测试词", "zh", "diànnǎo", "computer", "Custom", "noun")
    hits = lex.search("电脑测试词", lang="zh", source="custom")
    assert hits and hits[0]["meaning"] == "computer"
    assert lex.delete_custom("电脑测试词", "zh")

def test_http_range_and_state():
    httpd, state = create_server(ROOT, host="127.0.0.1", port=8899)
    th = threading.Thread(target=httpd.serve_forever, daemon=True); th.start()
    try:
        with urllib.request.urlopen("http://127.0.0.1:8899/api/state", timeout=10) as r:
            j = json.loads(r.read())
            assert j["ok"] and j["project"]["subtitles"]
        req = urllib.request.Request("http://127.0.0.1:8899/api/media", headers={"Range": "bytes=0-99"})
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.status == 206 and len(r.read()) == 100
        req = urllib.request.Request("http://127.0.0.1:8899/api/export/subtitles", data=json.dumps({"format":"srt"}).encode(), headers={"Content-Type":"application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            assert "早上好" in r.read().decode("utf-8")
    finally:
        httpd.shutdown()

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print("PASS", fn.__name__)
    print(f"All {len(fns)} tests passed.")

def test_asr_runner_no_deadlock_and_cancel():
    import os, stat, tempfile, time as _t
    from hosub_app.media_tools import ToolManager, AsrJobRunner
    d = Path(tempfile.mkdtemp())
    proj, wav = _build_demo_project(d)
    tools_dir = d / "tools"; tools_dir.mkdir()
    fake_ff = tools_dir / "ffmpeg"; fake_wh = tools_dir / "whisper-cli"; model = d / "ggml-fake.bin"; model.write_bytes(b"x" * 1024)
    fake_ff.write_text("#!/usr/bin/env python3\nimport sys, shutil\nshutil.copy2(sys.argv[sys.argv.index('-i')+1], sys.argv[-1])\n")
    fake_wh.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, os, time, json\n"
        "prefix = sys.argv[sys.argv.index('-of')+1]\n"
        "# Flood stdout like real whisper-cli (used to deadlock an unread PIPE)\n"
        "for i in range(3000): print('transcript chunk', i, 'x'*40)\n"
        "sys.stdout.flush()\n"
        "sleep_s = float(os.environ.get('HOSUB_FAKE_SLEEP','0'))\n"
        "for pct in (25, 50, 75, 100):\n"
        "    sys.stderr.write(f'whisper: progress = {pct}%\\r'); sys.stderr.flush()\n"
        "    if sleep_s: time.sleep(sleep_s)\n"
        "json.dump({'transcription':[{'offsets':{'from':0,'to':1200},'text':'hello 早上好','tokens':[{'text':' hello','offsets':{'from':0,'to':400}},{'text':'早上好','offsets':{'from':500,'to':1100}}]}]}, open(prefix+'.json','w'))\n"
        "open(prefix+'.srt','w').write('1\\n00:00:00,000 --> 00:00:01,200\\nhello\\n')\n")
    for f in (fake_ff, fake_wh): f.chmod(f.stat().st_mode | stat.S_IEXEC)
    tm = ToolManager(d, d / "engine_config.json")
    tm.config.update({"ffmpeg_path": str(fake_ff), "whisper_path": str(fake_wh), "model_path": str(model)})
    runner = AsrJobRunner(tm)
    done = {}
    runner.start(media_path=wav, wav_cache_path=d / "active_16k.wav", base_lang="en", target_langs=["zh"],
                 on_complete=lambda cues, wf: done.update(cues=cues))
    for _ in range(100):
        if runner.get_state()["status"] == "done": break
        _t.sleep(0.1)
    assert runner.get_state()["status"] == "done", runner.get_state()
    assert done["cues"] and done["cues"][0]["words"], done
    # Cancel a slow job, then immediately start a new one — stale worker must not interfere
    os.environ["HOSUB_FAKE_SLEEP"] = "1.2"
    runner.start(media_path=wav, wav_cache_path=d / "active_16k.wav", base_lang="en", target_langs=["zh"],
                 on_complete=lambda cues, wf: done.update(cues2=cues))
    _t.sleep(0.4); runner.cancel()
    assert runner.get_state()["status"] == "cancelled"
    os.environ["HOSUB_FAKE_SLEEP"] = "0"
    runner.start(media_path=wav, wav_cache_path=d / "active_16k.wav", base_lang="en", target_langs=["zh"],
                 on_complete=lambda cues, wf: done.update(cues3=cues))
    for _ in range(100):
        if runner.get_state()["status"] == "done": break
        _t.sleep(0.1)
    assert runner.get_state()["status"] == "done" and done.get("cues3"), runner.get_state()
