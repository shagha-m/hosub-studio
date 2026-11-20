/* HoSub Studio 2.0 — Offline frontend (vanilla JS, zero external requests) */
'use strict';
const $ = (id) => document.getElementById(id);
const els = {};
['projectTitleInput','cueCountBadge','vocabCountBadge','videoEl','audioEl','audioSlate','audioSlateName','subtitleOverlayWrap','subtitleOverlayBox','btnStepBack','btnPlayPause','iconPlay','iconPause','btnStepFwd','timecodeReadout','playbackRateSelect','btnToggleCC','romaModeSelect','btnToggleTrans','btnToggleStyle','btnQuickAudio','styleDrawer','styleFontSize','styleFontSizeVal','styleBoxOpacity','styleBoxOpacityVal','stylePrimaryColor','styleTargetColor','styleRomaColor','styleBoxColor','styleShowBox','styleHighlightTarget','styleEnforceBidi','baseLangSelect','extraTargetLangSelect','extraTargetBadges','cueTableWrap','cueTableBody','inspectorRowLabel','cueStartInput','cueEndInput','inspectorCps','btnAddCue','btnSplitCue','btnMergeCues','btnLockCue','btnDeleteCue','cueTextInput','cueTransInput','liveRomaPreview','toolSelect','toolForward','chkStopAtLock','btnSelectToEnd','btnGroupCues','btnUngroupCues','selectionSummary','btnNudgeBack','nudgeMsInput','btnShiftMinus','btnShiftPlus','btnNudgeFwd','btnSnapVoice','btnUndo','btnRedo','timelineZoom','btnZoomFit','timelineWrap','timelineCanvas','aiBatchInfo','aiTaskSelect','aiTargetLangSelect','aiBatchSizeSelect','aiPreserveScript','aiGlossaryInput','btnAiPrevBatch','btnAiCopyPrompt','btnAiNextBatch','aiPromptPreview','aiResponseInput','btnAiApply','btnAiClear','aiQaReport','btnOpenVocabAi','btnToggleAudioPanel','btnExportXlsx','btnExportVocabCsv','btnExportVocabJson','audioCutterPanel','btnDownloadAudioZip','cutModeSelect','cutNamingSelect','cutFormatSelect','cutPrePad','cutPostPad','cutDedup','cutLinkVocab','clipPreviewList','vocabScopeSelect','vocabHideStopwords','vocabMinFreq','vocabSearchInput','btnVocabSelectAll','btnVocabSelectNone','vocabSummaryPill','vocabTableBody','dictStatsLine','btnImportDict','dictForm','dictSurface','dictLang','dictLevel','dictRoma','dictMeaning','dictPos','dictSearchInput','dictFilterLang','dictFilterSource','dictTableBody','hiddenFileInput','hiddenDictFileInput','clipPreviewAudio','modalOpen','modalTranscribe','modalExport','modalVocabAi','modalEngine','btnOpenFile','btnOpenTranscribe','btnOpenExport','btnOpenEngine','engineStatusDot','btnToggleTheme','btnPickUpload','localPathInput','btnOpenLocalPath','chkClearSubsOnMedia','btnReloadDemo','asrEngineBanner','asrLangMode','asrSnapSelect','asrVocabHint','asrProgressArea','asrProgressFill','asrProgressText','asrLogBox','btnStartAsr','btnCancelAsr','btnJumpEngineFromAsr','expContentMode','expRomaMode','expBidi','expFullAudioFmt','btnExportFullAudio','btnJumpAudioZip','btnExportXlsxModal','videoExportResult','vocabAiLang','btnCopyVocabPrompt','vocabAiPromptBox','chkSaveVocabAiToDict','btnApplyVocabAi','vocabAiPasteBox','engineStatusCards','engineForm','engFfmpeg','engWhisper','engModel','engStrategy','engThreads','engGpu','toast','stageDropZone'].forEach(id=>els[id]=$(id));

const VIDEO_EXTS=new Set(['mp4','mkv','mov','webm','avi','m4v']);
const AUDIO_EXTS=new Set(['mp3','wav','m4a','ogg','flac','aac','opus']);
let project=null, subtitles=[], vocabCards=[], dictItems=[], clips=[];
let selected=new Set([1]), activeId=1, tool='select';
let undoStack=[], redoStack=[], syncTimer=null, vocabLoaded=false, dictLoaded=false;
let aiBatchStart=0, asrTimer=null, mediaKind='audio', asrRunning=false;
let pxPerSec=95;

function toast(msg, err=false){ els.toast.textContent=msg; els.toast.className='toast'+(err?' error':''); clearTimeout(toast._t); toast._t=setTimeout(()=>els.toast.classList.add('hidden'),3600); }
async function api(path, opts={}){ const r=await fetch('api/'+path, opts); if(!r.ok){ let m=r.statusText; try{ const j=await r.json(); m=j.error||j.message||m; }catch(e){ try{m=await r.text();}catch(_){}} throw new Error(m||('HTTP '+r.status)); } return r; }
async function apiJson(path, opts={}){ const r=await api(path, opts); return r.json(); }
async function postJson(path, body){ return apiJson(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})}); }
function esc(s){ return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function fmtMs(ms, srt=false){ ms=Math.max(0,Math.round(ms||0)); const h=Math.floor(ms/3600000),m=Math.floor(ms%3600000/60000),s=Math.floor(ms%60000/1000),mmm=ms%1000; const p=(n,l=2)=>String(n).padStart(l,'0'); return srt?`${p(h)}:${p(m)}:${p(s)},${p(mmm,3)}`:`${p(m)}:${p(s)}.${Math.floor(mmm/100)}`; }
function fmtSrt(ms){ return fmtMs(ms,true); }
function parseTime(v){ v=String(v).trim().replace('.',':'); if(/^\d+(\.\d+)?$/.test(v)) return Math.round(parseFloat(v)*1000); const parts=v.split(':').map(x=>parseFloat(x)||0); let ms=0; if(parts.length===4) ms=((parts[0]*60+parts[1])*60+parts[2])*1000+parts[3]; else if(parts.length===3) ms=((parts[0]*60+parts[1])*60+parts[2])*1000; else if(parts.length===2) ms=(parts[0]*60+parts[1])*1000; return Math.round(ms); }
function cps(cue){ const txt=(cue.translation||cue.text||'').replace(/\s+/g,''); const dur=Math.max(1,(cue.end_ms-cue.start_ms)/1000); return txt.length/dur; }
function cleanCues(list){ return (list||[]).map((c,i)=>({id:i+1,start_ms:+c.start_ms||0,end_ms:+c.end_ms||0,text:c.text||'',translation:c.translation||'',locked:!!c.locked,group_id:+c.group_id||0,words:Array.isArray(c.words)?c.words.map(w=>({text:w.text,start_ms:+w.start_ms||0,end_ms:+w.end_ms||0})):[]})); }
function mediaEl(){ return mediaKind==='video'?els.videoEl:els.audioEl; }
function currentMs(){ const m=mediaEl(); return Math.round((m.currentTime||0)*1000); }
function durationMs(){ const wf=project&&project.waveform; const m=mediaEl(); if(m.duration&&isFinite(m.duration)) return Math.round(m.duration*1000); return (wf&&wf.duration_ms)|| (subtitles.length?subtitles[subtitles.length-1].end_ms:0); }
function cueById(id){ return subtitles.find(c=>c.id===id); }

/* ---------- History ---------- */
function snapshotCues(){ return JSON.stringify(cleanCues(subtitles)); }
function pushHistory(){ undoStack.push(snapshotCues()); if(undoStack.length>50) undoStack.shift(); redoStack=[]; updateUndoBtns(); }
function restoreHistory(json){ subtitles=JSON.parse(json); subtitles.forEach((c,i)=>{/* keep enriched later */}); selected=new Set(subtitles.length?[Math.min(activeId,subtitles.length)]:[]); activeId=subtitles.length?[...selected][0]:0; renderAll(false); syncProject(); }
function updateUndoBtns(){ els.btnUndo.disabled=!undoStack.length; els.btnRedo.disabled=!redoStack.length; }
function undo(){ if(!undoStack.length) return; redoStack.push(snapshotCues()); restoreHistory(undoStack.pop()); updateUndoBtns(); }
function redo(){ if(!redoStack.length) return; undoStack.push(snapshotCues()); restoreHistory(redoStack.pop()); updateUndoBtns(); }

/* ---------- Project sync / load ---------- */
async function syncProject(immediate=false){
  const doSync=async()=>{ if(!project) return; try{ const body={title:els.projectTitleInput.value, base_lang:project.base_lang, target_langs:project.target_langs, translate_to:project.translate_to, glossary:els.aiGlossaryInput.value, style:project.style, subtitles:cleanCues(subtitles)}; const j=await postJson('project/sync',body); subtitles=j.subtitles||subtitles; renderCueTable(); renderOverlay(); drawTimeline(); }catch(e){ toast(e.message,true); } };
  if(immediate){ clearTimeout(syncTimer); return doSync(); }
  clearTimeout(syncTimer); syncTimer=setTimeout(doSync,550);
}
function applyProject(p){
  project=p; subtitles=p.subtitles||[]; mediaKind=p.media_kind||'audio';
  els.projectTitleInput.value=p.title||'Untitled Lesson';
  els.baseLangSelect.value=p.base_lang||'en';
  els.aiGlossaryInput.value=p.glossary||'';
  if(p.translate_to) els.aiTargetLangSelect.value=p.translate_to;
  renderLangChips(); applyStyleToControls(); updateMediaSource(); renderAll(true);
  els.cueCountBadge.textContent=subtitles.length;
  if(p.dict_stats) renderDictStats(p.dict_stats);
  if(p.engine) renderEngine(p.engine);
}
async function loadState(){ const j=await apiJson('state'); applyProject(j.project); }
function updateMediaSource(){
  const src='api/media?v='+(project.media_version||1);
  const isVideo=mediaKind==='video';
  els.videoEl.style.display=isVideo?'block':'none';
  els.audioSlate.style.display=isVideo?'none':'flex';
  els.audioSlateName.textContent=project.media_name||'Audio lesson';
  const m=mediaEl(), other=isVideo?els.audioEl:els.videoEl; other.pause&&other.pause();
  if(m.getAttribute('data-src')!==src){ m.setAttribute('data-src',src); m.src=src; m.load(); }
  m.playbackRate=parseFloat(els.playbackRateSelect.value||'1');
}
function renderAll(resetSel=false){
  if(resetSel){ selected=new Set(subtitles.length?[1]:[]); activeId=subtitles.length?1:0; }
  if(activeId&&!cueById(activeId)&&subtitles.length){ activeId=subtitles[0].id; selected=new Set([activeId]); }
  els.cueCountBadge.textContent=subtitles.length;
  renderCueTable(); renderInspector(); renderOverlay(); drawTimeline(); updateSelectionUI(); updateUndoBtns();
}

/* ---------- Language chips ---------- */
function renderLangChips(){
  const targets=project.target_langs||[];
  document.querySelectorAll('.lang-chip').forEach(ch=>{ const lg=ch.dataset.lang; ch.classList.toggle('active', targets.includes(lg)); });
  const known=new Set(['zh','ko','ja']);
  els.extraTargetBadges.innerHTML=targets.filter(t=>!known.has(t)).map(t=>`<button class="lang-chip active" data-extra-lang="${esc(t)}" title="Remove">${esc(t.toUpperCase())} ✕</button>`).join('');
  els.extraTargetBadges.querySelectorAll('[data-extra-lang]').forEach(b=>b.onclick=()=>{ project.target_langs=project.target_langs.filter(x=>x!==b.dataset.extraLang); renderLangChips(); syncProject(true); refreshVocabAndClips(); });
}
document.querySelectorAll('.lang-chip[data-lang]').forEach(ch=>ch.onclick=()=>{ const lg=ch.dataset.lang; const arr=project.target_langs; const i=arr.indexOf(lg); if(i>=0) arr.splice(i,1); else arr.push(lg); renderLangChips(); syncProject(true); refreshVocabAndClips(); });
els.baseLangSelect.onchange=()=>{ project.base_lang=els.baseLangSelect.value; syncProject(true); refreshVocabAndClips(); };
els.extraTargetLangSelect.onchange=()=>{ const v=els.extraTargetLangSelect.value; if(v&&!project.target_langs.includes(v)) project.target_langs.push(v); els.extraTargetLangSelect.value=''; renderLangChips(); syncProject(true); refreshVocabAndClips(); };

/* ---------- Style / overlay ---------- */
function hexToRgba(hex,op){ const h=hex.replace('#',''); const n=parseInt(h.length===3?h.split('').map(c=>c+c).join(''):h,16); return `rgba(${(n>>16)&255},${(n>>8)&255},${n&255},${op/100})`; }
function applyStyleToControls(){ const s=project.style||{}; els.styleFontSize.value=s.font_size||22; els.styleFontSizeVal.textContent=(s.font_size||22)+'px'; els.styleBoxOpacity.value=s.box_opacity??76; els.styleBoxOpacityVal.textContent=(s.box_opacity??76)+'%'; els.stylePrimaryColor.value=s.primary_color||'#f8fafc'; els.styleTargetColor.value=s.target_color||'#f08ba6'; els.styleRomaColor.value=s.roma_color||'#2dd4bf'; els.styleBoxColor.value=s.box_color||'#090d16'; els.styleShowBox.checked=s.show_box!==false; els.styleHighlightTarget.checked=s.highlight_target!==false; els.styleEnforceBidi.checked=s.enforce_bidi!==false; els.romaModeSelect.value=s.romanization_mode||'inline'; els.btnToggleTrans.classList.toggle('active', s.show_translation!==false); els.btnToggleCC.classList.toggle('active', s.cc_enabled!==false); }
function readStyleControls(){ const s=project.style; s.font_size=+els.styleFontSize.value; s.box_opacity=+els.styleBoxOpacity.value; s.primary_color=els.stylePrimaryColor.value; s.target_color=els.styleTargetColor.value; s.roma_color=els.styleRomaColor.value; s.box_color=els.styleBoxColor.value; s.show_box=els.styleShowBox.checked; s.highlight_target=els.styleHighlightTarget.checked; s.enforce_bidi=els.styleEnforceBidi.checked; s.romanization_mode=els.romaModeSelect.value; els.styleFontSizeVal.textContent=s.font_size+'px'; els.styleBoxOpacityVal.textContent=s.box_opacity+'%'; renderOverlay(); syncProject(); }
['styleFontSize','styleBoxOpacity','stylePrimaryColor','styleTargetColor','styleRomaColor','styleBoxColor','styleShowBox','styleHighlightTarget','styleEnforceBidi'].forEach(id=>{ els[id].addEventListener('input',readStyleControls); els[id].addEventListener('change',readStyleControls); });
els.romaModeSelect.onchange=readStyleControls;
els.btnToggleStyle.onclick=()=>els.styleDrawer.classList.toggle('hidden');
els.btnToggleCC.onclick=()=>{ project.style.cc_enabled=!(project.style.cc_enabled!==false); els.btnToggleCC.classList.toggle('active',project.style.cc_enabled); renderOverlay(); syncProject(); };
els.btnToggleTrans.onclick=()=>{ project.style.show_translation=!(project.style.show_translation!==false); els.btnToggleTrans.classList.toggle('active',project.style.show_translation); renderOverlay(); syncProject(); };

function styledTextHtml(text){
  const s=project.style||{}; const target=s.highlight_target?s.target_color:'#inherit';
  // Split parenthesised romanization
  return esc(text).replace(/\(([^()]*)\)/g, (m,g)=>`(<span style="color:${esc(s.roma_color||'#2dd4bf')}">${g}</span>)`)
    .replace(/([\u3400-\u9fff\u3040-\u30ff\uac00-\uac7f]+)/g, `<span style="color:${esc(target)}">$1</span>`);
}
function renderOverlay(){
  if(!project) return; const s=project.style||{};
  if(s.cc_enabled===false){ els.subtitleOverlayBox.innerHTML=''; return; }
  const t=currentMs(); const cue=subtitles.find(c=>t>=c.start_ms&&t<=c.end_ms);
  if(!cue){ els.subtitleOverlayBox.innerHTML=''; return; }
  const mode=s.romanization_mode||'inline'; let main='';
  if(mode==='stacked'&&cue.text_stacked){ const lines=cue.text_stacked.split('\n'); if(lines.length>1){ main=`<div class="sub-overlay-ruby" style="color:${esc(s.roma_color)}">${esc(lines[0])}</div><div>${styledTextHtml(lines.slice(1).join(' '))}</div>`; } else main=styledTextHtml(cue.text_stacked); }
  else if(mode==='none'){ main=styledTextHtml((cue.text||'').replace(/\s*\([^()]*\)\s*/g,' ')); }
  else main=styledTextHtml(cue.text_inline||cue.text||'');
  let html=`<div>${main}</div>`;
  if(s.show_translation!==false&&cue.translation) html+=`<div class="sub-overlay-trans" dir="auto" style="color:${esc(s.translation_color||'#e2e8f0')}">${esc(cue.translation)}</div>`;
  const box=els.subtitleOverlayBox; box.innerHTML=html; box.style.fontSize=(s.font_size||22)+'px'; box.style.color=s.primary_color||'#f8fafc'; box.style.fontFamily=s.font_family||''; box.style.background=s.show_box?hexToRgba(s.box_color||'#090d16', s.box_opacity??76):'transparent'; box.style.textShadow=s.show_box?'none':'0 1px 4px rgba(0,0,0,.9)';
}

/* ---------- Cue table & inspector ---------- */
function flagBadges(c){ let h=''; if(c.locked) h+='<span class="mini-badge lock">🔒 LOCK</span>'; if(c.group_id) h+=`<span class="mini-badge">G${c.group_id}</span>`; const v=cps(c); if(v>20) h+=`<span class="mini-badge warn">${v.toFixed(0)} CPS</span>`; if((c.words||[]).length) h+='<span class="mini-badge">WORDS</span>'; return h||'<span class="mini-badge">—</span>'; }
function renderCueTable(){
  const t=currentMs();
  els.cueTableBody.innerHTML=subtitles.map((c,i)=>{
    const disp=(project.style.romanization_mode==='stacked'?c.text_stacked:(project.style.romanization_mode==='none'?c.text:c.text_inline))||c.text||'';
    return `<tr class="cue-row ${selected.has(c.id)?'selected':''} ${(t>=c.start_ms&&t<=c.end_ms)?'playing':''}" data-id="${c.id}">
      <td class="cue-idx">${i+1}</td><td class="cue-time">${fmtMs(c.start_ms)}<br>→ ${fmtMs(c.end_ms)}</td>
      <td><div class="cue-line-orig" dir="auto">${esc(disp).replace(/\n/g,'<br>')}</div>${c.translation?`<div class="cue-line-trans" dir="auto">${esc(c.translation)}</div>`:''}</td>
      <td><div class="flag-stack">${flagBadges(c)}</div></td></tr>`;
  }).join('') || '<tr><td colspan="4" style="padding:18px;color:var(--text-muted)">No subtitles yet — transcribe or import an SRT file.</td></tr>';
  els.cueTableBody.querySelectorAll('.cue-row').forEach(row=>{ row.onclick=(e)=>{ const id=+row.dataset.id; if(e.shiftKey&&[...selected].length){ const last=activeId; const a=subtitles.findIndex(c=>c.id===last), b=subtitles.findIndex(c=>c.id===id); subtitles.slice(Math.min(a,b),Math.max(a,b)+1).forEach(c=>selected.add(c.id)); } else if(e.ctrlKey||e.metaKey){ selected.has(id)?selected.delete(id):selected.add(id); } else selected=new Set([id]); activeId=id; renderCueTable(); renderInspector(); updateSelectionUI(); drawTimeline(); }; row.ondblclick=()=>{ const c=cueById(id); if(c) seekTo(c.start_ms+10,true); }; });
}
function renderInspector(){
  const c=cueById(activeId)||subtitles[0]; if(!c){ els.inspectorRowLabel.textContent='#—'; return; }
  activeId=c.id; els.inspectorRowLabel.textContent='#'+c.id+(c.locked?' 🔒':'');
  if(document.activeElement!==els.cueStartInput) els.cueStartInput.value=fmtSrt(c.start_ms);
  if(document.activeElement!==els.cueEndInput) els.cueEndInput.value=fmtSrt(c.end_ms);
  if(document.activeElement!==els.cueTextInput) els.cueTextInput.value=c.text||'';
  if(document.activeElement!==els.cueTransInput) els.cueTransInput.value=c.translation||'';
  els.liveRomaPreview.textContent=c.text_inline&&c.text_inline!==c.text?c.text_inline:'';
  const v=cps(c); els.inspectorCps.textContent=v.toFixed(1)+' CPS'; els.inspectorCps.classList.toggle('warn',v>20);
  els.btnLockCue.textContent=c.locked?'Unlock':'Lock';
}
function updateSelectionUI(){ els.selectionSummary.textContent=`${selected.size} selected`; const c=cueById(activeId); if(c&&c.group_id) els.selectionSummary.textContent+=` · Group ${c.group_id}`; }
function commitTextEdits(){ const c=cueById(activeId); if(!c) return; c.text=els.cueTextInput.value; c.translation=els.cueTransInput.value; renderCueTable(); renderOverlay(); syncProject(); }
let textPushDone=false;
els.cueTextInput.addEventListener('input',()=>{ if(!textPushDone){pushHistory(); textPushDone=true;} commitTextEdits(); });
els.cueTransInput.addEventListener('input',()=>{ if(!textPushDone){pushHistory(); textPushDone=true;} commitTextEdits(); });
els.cueTextInput.addEventListener('change',()=>{textPushDone=false; syncProject(true);});
els.cueTransInput.addEventListener('change',()=>{textPushDone=false; syncProject(true);});
function applyInspectorTimes(){ const c=cueById(activeId); if(!c) return; pushHistory(); c.start_ms=parseTime(els.cueStartInput.value); c.end_ms=Math.max(c.start_ms+80,parseTime(els.cueEndInput.value)); clampWords(c); sortAndRenumber(); renderAll(); syncProject(true); }
els.cueStartInput.onchange=applyInspectorTimes; els.cueEndInput.onchange=applyInspectorTimes;
function clampWords(c){ (c.words||[]).forEach(w=>{ w.start_ms=Math.max(c.start_ms,Math.min(w.start_ms,c.end_ms-20)); w.end_ms=Math.max(w.start_ms+20,Math.min(w.end_ms,c.end_ms)); }); }
function sortAndRenumber(){ subtitles.sort((a,b)=>a.start_ms-b.start_ms); subtitles.forEach((c,i)=>c.id=i+1); }
function shiftCue(c,delta){ c.start_ms=Math.max(0,c.start_ms+delta); c.end_ms=Math.max(c.start_ms+80,c.end_ms+delta); (c.words||[]).forEach(w=>{ w.start_ms=Math.max(0,w.start_ms+delta); w.end_ms=Math.max(w.start_ms+20,w.end_ms+delta); }); }
function expansionIds(ids){ const out=new Set(ids); let changed=true; while(changed){ changed=false; subtitles.forEach(c=>{ if(out.has(c.id)&&c.group_id){ subtitles.filter(x=>x.group_id===c.group_id).forEach(x=>{ if(!out.has(x.id)){out.add(x.id); changed=true;} }); } }); } return out; }
function moveSelected(delta, push=true){ if(!selected.size) return; if(push) pushHistory(); const ids=expansionIds(selected); subtitles.filter(c=>ids.has(c.id)).forEach(c=>shiftCue(c,delta)); sortAndRenumberKeep(ids); renderAll(); syncProject(); }
function sortAndRenumberKeep(oldIds){ // ids change after sort; keep selection by object identity
  const objs=subtitles.filter(c=>oldIds.has(c.id)); subtitles.sort((a,b)=>a.start_ms-b.start_ms); subtitles.forEach((c,i)=>c.id=i+1); selected=new Set(objs.map(o=>o.id)); if(objs.length) activeId=objs[0].id; }
els.btnAddCue.onclick=()=>{ pushHistory(); const c=cueById(activeId); const start=c?c.end_ms:currentMs(); const nc={id:subtitles.length+1,start_ms:start,end_ms:start+2000,text:'',translation:'',locked:false,group_id:0,words:[]}; subtitles.push(nc); sortAndRenumber(); activeId=subtitles.indexOf(nc)+1; selected=new Set([activeId]); renderAll(); syncProject(true); els.cueTextInput.focus(); };
els.btnSplitCue.onclick=splitCue;
function splitCue(){ const c=cueById(activeId); if(!c) return; let at=currentMs(); if(at<=c.start_ms+80||at>=c.end_ms-80) at=Math.round((c.start_ms+c.end_ms)/2); pushHistory(); const words=c.words||[]; const w1=words.filter(w=>w.end_ms<=at), w2=words.filter(w=>w.start_ms>=at); const nc={id:subtitles.length+1,start_ms:at,end_ms:c.end_ms,text:c.text,translation:'',locked:false,group_id:c.group_id,words:w2}; c.end_ms=at; c.words=w1; subtitles.push(nc); sortAndRenumber(); renderAll(); syncProject(true); toast('Cue split at playhead. Edit the second half text as needed.'); }
els.btnMergeCues.onclick=()=>{ const ids=expansionIds(selected); const list=subtitles.filter(c=>ids.has(c.id)).sort((a,b)=>a.start_ms-b.start_ms); if(list.length<2){ toast('Select at least two cues to merge.',true); return; } pushHistory(); const first=list[0]; first.end_ms=Math.max(...list.map(c=>c.end_ms)); first.text=list.map(c=>c.text).filter(Boolean).join(' '); first.translation=list.map(c=>c.translation).filter(Boolean).join(' '); first.words=list.flatMap(c=>c.words||[]); const remove=new Set(list.slice(1)); subtitles=subtitles.filter(c=>!remove.has(c)); sortAndRenumber(); selected=new Set([first.id]); activeId=first.id; renderAll(); syncProject(true); };
els.btnLockCue.onclick=()=>{ if(!selected.size) return; pushHistory(); const anyUnlocked=subtitles.some(c=>selected.has(c.id)&&!c.locked); subtitles.forEach(c=>{ if(selected.has(c.id)) c.locked=anyUnlocked; }); renderAll(); syncProject(true); };
els.btnDeleteCue.onclick=deleteSelected;
function deleteSelected(){ if(!selected.size) return; pushHistory(); subtitles=subtitles.filter(c=>!selected.has(c.id)); sortAndRenumber(); selected=new Set(subtitles.length?[Math.min(activeId,subtitles.length)]:[]); activeId=subtitles.length?[...selected][0]:0; renderAll(); syncProject(true); }
els.btnGroupCues.onclick=groupCues; els.btnUngroupCues.onclick=ungroupCues;
function groupCues(){ if(selected.size<2){ toast('Select at least two cues to group.',true); return; } pushHistory(); const gid=Math.max(0,...subtitles.map(c=>c.group_id||0))+1; subtitles.forEach(c=>{ if(selected.has(c.id)) c.group_id=gid; }); renderAll(); syncProject(true); toast('Grouped as G'+gid); }
function ungroupCues(){ pushHistory(); subtitles.forEach(c=>{ if(selected.has(c.id)) c.group_id=0; }); renderAll(); syncProject(true); }
els.btnSelectToEnd.onclick=()=>{ const idx=subtitles.findIndex(c=>c.id===activeId); if(idx<0) return; subtitles.slice(idx).forEach(c=>selected.add(c.id)); renderCueTable(); updateSelectionUI(); drawTimeline(); };
els.btnNudgeBack.onclick=()=>moveSelected(-100); els.btnNudgeFwd.onclick=()=>moveSelected(100);
els.btnShiftMinus.onclick=()=>moveSelected(-Math.abs(+els.nudgeMsInput.value||250)); els.btnShiftPlus.onclick=()=>moveSelected(Math.abs(+els.nudgeMsInput.value||250));
els.btnSnapVoice.onclick=async()=>{ pushHistory(); try{ const j=await postJson('project/snap-timecages',{}); subtitles=j.subtitles; renderAll(); toast(`Snap Voice repaired ${j.changed||0} cue edge(s).`); }catch(e){ toast(e.message,true); } };
els.btnUndo.onclick=undo; els.btnRedo.onclick=redo;

/* ---------- Transport ---------- */
function seekTo(ms, play=false){ const m=mediaEl(); m.currentTime=Math.max(0,ms/1000); if(play) m.play(); updatePlaybackUI(); }
els.btnPlayPause.onclick=()=>{ const m=mediaEl(); if(m.paused) m.play(); else m.pause(); };
els.btnStepBack.onclick=()=>seekTo(currentMs()-2000); els.btnStepFwd.onclick=()=>seekTo(currentMs()+2000);
els.playbackRateSelect.onchange=()=>{ els.videoEl.playbackRate=els.audioEl.playbackRate=parseFloat(els.playbackRateSelect.value); };
function updatePlaybackUI(){ const m=mediaEl(); els.iconPlay.style.display=m.paused?'':'none'; els.iconPause.style.display=m.paused?'none':''; els.timecodeReadout.textContent=`${fmtMs(currentMs())} / ${fmtMs(durationMs())}`; renderOverlay(); }
['videoEl','audioEl'].forEach(k=>{ const m=els[k]; m.addEventListener('timeupdate',()=>{ if(m===mediaEl()){ updatePlaybackUI(); drawTimeline(); highlightPlayingRow(); } }); m.addEventListener('play',updatePlaybackUI); m.addEventListener('pause',updatePlaybackUI); m.addEventListener('loadedmetadata',()=>{ updatePlaybackUI(); drawTimeline(); }); m.addEventListener('ended',updatePlaybackUI); });
let lastPlayingId=0;
function highlightPlayingRow(){ const t=currentMs(); const c=subtitles.find(x=>t>=x.start_ms&&t<=x.end_ms); const id=c?c.id:0; if(id!==lastPlayingId){ lastPlayingId=id; renderCueTable(); } }
els.btnQuickAudio.onclick=()=>downloadExport('full-audio',{format:'mp3'});

/* ---------- Timeline ---------- */
const ctx=els.timelineCanvas.getContext('2d');
function timelineWidth(){ return Math.max(els.timelineWrap.clientWidth, Math.ceil(durationMs()/1000*pxPerSec)+80); }
function setupCanvas(){ const w=timelineWidth(), h=els.timelineWrap.clientHeight||160, dpr=window.devicePixelRatio||1; els.timelineCanvas.style.width=w+'px'; els.timelineCanvas.style.height=h+'px'; els.timelineCanvas.width=Math.round(w*dpr); els.timelineCanvas.height=Math.round(h*dpr); ctx.setTransform(dpr,0,0,dpr,0,0); return {w,h}; }
function msToX(ms){ return 12+ms/1000*pxPerSec; }
function xToMs(x){ return Math.max(0,Math.round((x-12)/pxPerSec*1000)); }
function cssVar(n){ return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
function drawTimeline(){
  if(!project) return; const {w,h}=setupCanvas(); ctx.clearRect(0,0,w,h);
  const wf=project.waveform||{peaks:[],buckets_per_sec:40,islands:[]}; const dur=Math.max(durationMs(),1000);
  // ruler
  ctx.fillStyle=cssVar('--tl-ruler'); ctx.fillRect(0,0,w,24);
  ctx.strokeStyle=cssVar('--border'); ctx.beginPath(); ctx.moveTo(0,24.5); ctx.lineTo(w,24.5); ctx.stroke();
  ctx.fillStyle=cssVar('--text-dim'); ctx.font='10px Segoe UI'; const step=pxPerSec>=200?1:(pxPerSec>=80?2:5);
  for(let s=0;s<=dur/1000+step;s+=step){ const x=msToX(s*1000); ctx.fillRect(x,18,1,6); ctx.fillText(fmtMs(s*1000),x+3,15); }
  // islands
  ctx.fillStyle=cssVar('--tl-island'); (wf.islands||[]).forEach(i=>{ ctx.fillRect(msToX(i.start_ms),25,Math.max(2,msToX(i.end_ms)-msToX(i.start_ms)),h-25); });
  // waveform
  const peaks=wf.peaks||[], bps=wf.buckets_per_sec||40; const midY=25+(h-70)/2, amp=(h-78)/2;
  ctx.strokeStyle=cssVar('--tl-wave'); ctx.beginPath();
  peaks.forEach((p,i)=>{ const ms=i/bps*1000; const x=msToX(ms); const y=Math.max(1,p*amp); ctx.moveTo(x,midY-y); ctx.lineTo(x,midY+y); });
  ctx.stroke();
  // clips
  const clipY=h-38, clipH=30;
  subtitles.forEach(c=>{ const x=msToX(c.start_ms), cw=Math.max(10,msToX(c.end_ms)-x); const sel=selected.has(c.id);
    ctx.fillStyle=sel?cssVar('--tl-clip-sel'):cssVar('--tl-clip'); ctx.strokeStyle=sel?cssVar('--accent'):cssVar('--tl-clip-border');
    ctx.beginPath(); ctx.roundRect(x,clipY,cw,clipH,4); ctx.fill(); ctx.stroke();
    ctx.save(); ctx.beginPath(); ctx.rect(x+3,clipY,cw-6,clipH); ctx.clip(); ctx.fillStyle=cssVar('--text'); ctx.font='10px Segoe UI'; ctx.fillText(`${c.id}. ${c.text||''}`,x+5,clipY+13); ctx.fillStyle=cssVar('--text-dim'); ctx.fillText(`${fmtMs(c.start_ms)}${c.locked?' 🔒':''}${c.group_id?' G'+c.group_id:''}`,x+5,clipY+25); ctx.restore();
  });
  // playhead
  const px=msToX(currentMs()); ctx.strokeStyle=cssVar('--cyan'); ctx.beginPath(); ctx.moveTo(px,0); ctx.lineTo(px,h); ctx.stroke(); ctx.fillStyle=cssVar('--cyan'); ctx.beginPath(); ctx.moveTo(px-5,0); ctx.lineTo(px+5,0); ctx.lineTo(px,8); ctx.closePath(); ctx.fill();
  if(dragState&&dragState.marquee){ const {x1,x2}=dragState.marquee; ctx.fillStyle='rgba(194,67,107,.16)'; ctx.strokeStyle=cssVar('--accent'); const a=Math.min(x1,x2), bw=Math.abs(x2-x1); ctx.fillRect(a,25,bw,h-25); ctx.strokeRect(a,25,bw,h-25); }
}
let dragState=null;
function canvasPos(e){ const r=els.timelineCanvas.getBoundingClientRect(); return {x:e.clientX-r.left, y:e.clientY-r.top}; }
function hitCue(x,y){ const h=els.timelineWrap.clientHeight||160; if(y<h-42) return null; for(let i=subtitles.length-1;i>=0;i--){ const c=subtitles[i]; const a=msToX(c.start_ms), b=msToX(c.end_ms); if(x>=a&&x<=b){ let edge=null; if(x-a<8) edge='start'; else if(b-x<8) edge='end'; return {cue:c,edge}; } } return null; }
els.timelineCanvas.addEventListener('pointerdown',e=>{
  const {x,y}=canvasPos(e); const ms=xToMs(x);
  if(y<25){ seekTo(ms); return; }
  const hit=hitCue(x,y);
  dragState={startX:x,startY:y,ms, moved:false, marquee:null};
  if(hit){
    const c=hit.cue;
    if(tool==='forward'){ selected=new Set(); const idx=subtitles.indexOf(c); for(let i=idx;i<subtitles.length;i++){ if(els.chkStopAtLock.checked&&i>idx&&subtitles[i-1].locked) break; selected.add(subtitles[i].id); if(els.chkStopAtLock.checked&&subtitles[i].locked&&i>idx) break; } activeId=c.id; }
    else if(e.shiftKey){ const a=subtitles.findIndex(z=>z.id===activeId), b=subtitles.indexOf(c); subtitles.slice(Math.min(a,b),Math.max(a,b)+1).forEach(z=>selected.add(z.id)); activeId=c.id; }
    else if(e.ctrlKey||e.metaKey){ selected.has(c.id)?selected.delete(c.id):selected.add(c.id); activeId=c.id; }
    else if(!selected.has(c.id)){ selected=new Set([c.id]); activeId=c.id; }
    else activeId=c.id;
    const ids=expansionIds(selected); dragState.ids=ids; dragState.edge=hit.edge; dragState.orig=new Map(subtitles.filter(z=>ids.has(z.id)).map(z=>[z.id, JSON.parse(JSON.stringify(z))])); dragState.activeOrig=JSON.parse(JSON.stringify(c)); pushHistory(); dragState.pushed=true;
    renderCueTable(); renderInspector(); updateSelectionUI();
  } else {
    if(!(e.ctrlKey||e.metaKey||e.shiftKey)) selected.clear();
    dragState.marquee={x1:x,x2:x};
  }
  els.timelineCanvas.setPointerCapture(e.pointerId); drawTimeline();
});
els.timelineCanvas.addEventListener('pointermove',e=>{
  if(!dragState) { const {x,y}=canvasPos(e); const hit=hitCue(x,y); els.timelineCanvas.style.cursor=hit?(hit.edge?'ew-resize':'move'):(y<25?'pointer':'crosshair'); return; }
  const {x,y}=canvasPos(e); const delta=xToMs(x)-dragState.ms; if(Math.abs(x-dragState.startX)>2) dragState.moved=true;
  if(dragState.marquee){ dragState.marquee.x2=x; const a=xToMs(Math.min(dragState.marquee.x1,dragState.marquee.x2)), b=xToMs(Math.max(dragState.marquee.x1,dragState.marquee.x2)); subtitles.forEach(c=>{ if(c.end_ms>=a&&c.start_ms<=b) selected.add(c.id); }); updateSelectionUI(); drawTimeline(); return; }
  if(!dragState.ids) return;
  const ids=dragState.ids;
  subtitles.forEach(c=>{ const o=dragState.orig.get(c.id); if(!o) return; c.start_ms=o.start_ms; c.end_ms=o.end_ms; c.words=JSON.parse(JSON.stringify(o.words||[])); });
  if(dragState.edge){ const c=cueById(activeId); const o=dragState.activeOrig; if(c&&o){ if(dragState.edge==='start'){ c.start_ms=Math.max(0,Math.min(o.start_ms+delta,c.end_ms-80)); const d=c.start_ms-o.start_ms; (c.words||[]).forEach(w=>{ w.start_ms+=d; w.end_ms+=d; }); clampWords(c); } else { c.end_ms=Math.max(c.start_ms+80,o.end_ms+delta); clampWords(c); } } }
  else if(delta!==0){ subtitles.forEach(c=>{ if(ids.has(c.id)) shiftCue(c,delta); }); }
  drawTimeline();
});
els.timelineCanvas.addEventListener('pointerup',e=>{
  if(!dragState) return;
  if(dragState.marquee&&!dragState.moved){ seekTo(dragState.ms); }
  if(dragState.ids||dragState.edge){ /* already pushed */ } else undoStack.pop(), updateUndoBtns();
  if(dragState.marquee&&dragState.moved){ renderCueTable(); updateSelectionUI(); }
  dragState=null;
  if(subtitles.length){ const keep=new Set([...selected].map(id=>cueById(id)).filter(Boolean)); sortAndRenumber(); selected=new Set([...keep].map(c=>c.id)); if(keep.size) activeId=[...keep][0].id; }
  renderAll(); syncProject(true);
});
els.timelineZoom.oninput=()=>{ pxPerSec=+els.timelineZoom.value; drawTimeline(); };
els.btnZoomFit.onclick=()=>{ const dur=Math.max(1,durationMs()/1000); pxPerSec=Math.max(10,Math.min(420,(els.timelineWrap.clientWidth-24)/dur)); els.timelineZoom.value=pxPerSec; drawTimeline(); };
els.timelineWrap.addEventListener('wheel',e=>{ if(e.ctrlKey){ e.preventDefault(); pxPerSec=Math.max(25,Math.min(420,pxPerSec+(e.deltaY<0?18:-18))); els.timelineZoom.value=pxPerSec; drawTimeline(); } },{passive:false});
function setTool(t){ tool=t; els.toolSelect.classList.toggle('active',t==='select'); els.toolForward.classList.toggle('active',t==='forward'); }
els.toolSelect.onclick=()=>setTool('select'); els.toolForward.onclick=()=>setTool('forward');
new ResizeObserver(()=>drawTimeline()).observe(els.timelineWrap);

/* ---------- Tabs / modals ---------- */
document.querySelectorAll('.main-tab').forEach(b=>b.onclick=()=>switchTab(b.dataset.tab));
function switchTab(name){ document.querySelectorAll('.main-tab').forEach(b=>{ const on=b.dataset.tab===name; b.classList.toggle('active',on); b.setAttribute('aria-selected',on); }); document.querySelectorAll('.workspace').forEach(w=>w.classList.toggle('active',w.id==='view-'+name)); if(name==='vocab') refreshVocabAndClips(); if(name==='dict'&&!dictLoaded) loadDict(); if(name==='editor') setTimeout(drawTimeline,30); }
function openModal(id){ els[id].classList.remove('hidden'); }
function closeModal(id){ els[id].classList.add('hidden'); }
document.querySelectorAll('.modal-close').forEach(b=>b.onclick=()=>{ if(b.dataset.close==='modalTranscribe'&&asrRunning) toast('Transcription keeps running in the background — reopen Transcribe to watch it, or press Cancel there to stop it.'); closeModal(b.dataset.close); });
document.querySelectorAll('.modal-backdrop').forEach(m=>m.addEventListener('mousedown',e=>{ if(e.target===m) m.classList.add('hidden'); }));
els.btnOpenFile.onclick=()=>openModal('modalOpen'); els.btnOpenExport.onclick=()=>openModal('modalExport');
els.btnOpenTranscribe.onclick=()=>{ openModal('modalTranscribe'); refreshEngine(); if(asrRunning){ els.asrProgressArea.classList.remove('hidden'); els.btnCancelAsr.classList.remove('hidden'); pollAsr(); } };
els.btnOpenEngine.onclick=()=>{ openModal('modalEngine'); refreshEngine(); };
els.btnJumpEngineFromAsr.onclick=()=>{ closeModal('modalTranscribe'); openModal('modalEngine'); refreshEngine(); };
els.btnToggleTheme.onclick=()=>{ const cur=document.documentElement.dataset.theme==='light'?'dark':'light'; document.documentElement.dataset.theme=cur; localStorage.setItem('hosub-theme',cur); drawTimeline(); };
document.documentElement.dataset.theme=localStorage.getItem('hosub-theme')||'dark';

/* ---------- Open / upload / import ---------- */
els.btnPickUpload.onclick=()=>els.hiddenFileInput.click();
els.hiddenFileInput.onchange=()=>{ const f=els.hiddenFileInput.files[0]; if(f) handleFile(f); els.hiddenFileInput.value=''; };
async function handleFile(file){
  if(asrRunning){ toast('Running transcription cancelled — loading new media.'); try{ await postJson('asr/cancel',{}); }catch(e){} asrRunning=false; els.btnStartAsr.disabled=false; clearInterval(asrTimer); await new Promise(r=>setTimeout(r,700)); }
  const ext=file.name.split('.').pop().toLowerCase();
  try{
    if(VIDEO_EXTS.has(ext)||AUDIO_EXTS.has(ext)){
      toast('Uploading media…'); const r=await fetch('api/media/upload',{method:'POST',headers:{'X-Filename':encodeURIComponent(file.name),'X-Clear-Subtitles':els.chkClearSubsOnMedia.checked?'1':'0'},body:file}); const j=await r.json(); if(!r.ok||!j.ok) throw new Error(j.error||'Upload failed'); applyProject(j.project); toast('Media loaded: '+file.name);
    } else if(ext==='srt'||ext==='vtt'){ const text=await file.text(); const j=await postJson('subtitles/import',{content:text,filename:file.name}); subtitles=j.subtitles; selected=new Set(subtitles.length?[1]:[]); activeId=subtitles.length?1:0; renderAll(); toast(`Imported ${j.count} subtitles.`); }
    else if(ext==='json'){ const obj=JSON.parse(await file.text()); const j=await postJson('project/import-json',{project:obj.project||obj}); applyProject(j.project); toast('Project imported.'); }
    else throw new Error('Unsupported file type: .'+ext);
    closeModal('modalOpen');
  }catch(e){ toast(e.message,true); }
}
els.btnOpenLocalPath.onclick=async()=>{ try{ const j=await postJson('media/open-local',{path:els.localPathInput.value,clear_subtitles:els.chkClearSubsOnMedia.checked}); applyProject(j.project); closeModal('modalOpen'); toast('Local media loaded instantly.'); }catch(e){ toast(e.message,true); } };
els.btnReloadDemo.onclick=async()=>{ try{ const j=await postJson('project/load-demo',{}); applyProject(j.project); closeModal('modalOpen'); toast('Sample lesson loaded.'); }catch(e){ toast(e.message,true); } };
['dragenter','dragover'].forEach(ev=>els.stageDropZone.addEventListener(ev,e=>{e.preventDefault(); els.stageDropZone.classList.add('drag-over');}));
['dragleave','drop'].forEach(ev=>els.stageDropZone.addEventListener(ev,e=>{e.preventDefault(); els.stageDropZone.classList.remove('drag-over');}));
els.stageDropZone.addEventListener('drop',e=>{ const f=e.dataTransfer.files[0]; if(f) handleFile(f); });

/* ---------- Engine & ASR ---------- */
function renderEngine(eng){ if(!eng) return; els.engineStatusDot.classList.toggle('ready',!!eng.ready_for_asr);
  els.engineStatusCards.innerHTML=[['FFmpeg',eng.ffmpeg_found,eng.ffmpeg_path],['Whisper CLI',eng.whisper_found,eng.whisper_path],['GGML Model',eng.model_found,eng.model_path]].map(([n,ok,p])=>`<div class="status-card ${ok?'ok':''}"><strong>${n}: ${ok?'Found':'Missing'}</strong><span>${esc(p||'Not detected')}</span></div>`).join('')+((eng.available_models||[]).length?`<div class="status-card"><strong>Models</strong><span>${eng.available_models.map(m=>esc(m.name)+' ('+m.size_mb+' MB'+(m.complete?'':' ⚠ incomplete')+')').join('<br>')}</span></div>`:'');
  const cfg=eng.config||{}; if(document.activeElement!==els.engFfmpeg) els.engFfmpeg.value=cfg.ffmpeg_path||''; if(document.activeElement!==els.engWhisper) els.engWhisper.value=cfg.whisper_path||''; if(document.activeElement!==els.engModel) els.engModel.value=cfg.model_path||''; els.engStrategy.value=cfg.strategy||'balanced'; els.engThreads.value=cfg.threads||0; els.engGpu.checked=cfg.use_gpu!==false;
  els.asrEngineBanner.className='engine-banner '+(eng.ready_for_asr?'ok':'warn'); els.asrEngineBanner.textContent=eng.ready_for_asr?`Ready — ${eng.model_path}`:'FFmpeg / Whisper.cpp / model not fully detected. Configure paths in Engine Settings (demo playback still works).';
}
async function refreshEngine(){ try{ const j=await apiJson('engine'); renderEngine(j.engine); }catch(e){ toast(e.message,true); } }
els.engineForm.onsubmit=async(e)=>{ e.preventDefault(); try{ const j=await postJson('engine/save',{ffmpeg_path:els.engFfmpeg.value,whisper_path:els.engWhisper.value,model_path:els.engModel.value,strategy:els.engStrategy.value,threads:+els.engThreads.value,use_gpu:els.engGpu.checked}); renderEngine(j.engine); toast('Engine settings saved & re-scanned.'); }catch(err){ toast(err.message,true); } };
els.btnStartAsr.onclick=async()=>{ if(asrRunning){ toast('A transcription is already running — Cancel it first.',true); return; } try{ asrRunning=true; els.btnStartAsr.disabled=true; els.asrProgressArea.classList.remove('hidden'); els.btnCancelAsr.classList.remove('hidden'); els.asrProgressFill.style.width='3%'; const mode=els.asrLangMode.value; await postJson('asr/start',{base_lang:project.base_lang,target_langs:project.target_langs,asr_lang_mode:mode,lesson_vocab_hint:els.asrVocabHint.value,snap_timecages:els.asrSnapSelect.value==='1'}); pollAsr(); }catch(e){ asrRunning=false; els.btnStartAsr.disabled=false; toast(e.message,true); } };
els.btnCancelAsr.onclick=async()=>{ els.asrProgressText.textContent='Cancelling…'; try{ await postJson('asr/cancel',{}); }catch(e){} setTimeout(()=>{ if(asrRunning) pollAsr(); },600); };
function pollAsr(){ clearInterval(asrTimer); asrTimer=setInterval(async()=>{ try{ const j=await apiJson('asr/status'); const a=j.asr; els.asrProgressFill.style.width=(a.percent||0)+'%'; els.asrProgressText.textContent=`${a.message||''} (${a.percent||0}%)`; if(a.log!==undefined&&document.activeElement!==els.asrLogBox){ els.asrLogBox.textContent=a.log||''; els.asrLogBox.scrollTop=els.asrLogBox.scrollHeight; } if(a.status==='done'){ clearInterval(asrTimer); asrRunning=false; els.btnStartAsr.disabled=false; els.btnCancelAsr.classList.add('hidden'); toast(`Transcription complete: ${a.cue_count} cues.`); closeModal('modalTranscribe'); loadState(); } else if(a.status==='error'||a.status==='cancelled'){ clearInterval(asrTimer); asrRunning=false; els.btnStartAsr.disabled=false; els.btnCancelAsr.classList.add('hidden'); els.asrProgressFill.style.width='0%'; if(a.status==='error') toast(a.message,true); else toast('Transcription cancelled — you can start a new one right away.'); } }catch(e){ clearInterval(asrTimer); } },700); }

/* ---------- AI subtitles ---------- */
async function generateAiPrompt(){ try{ const j=await postJson('ai/subtitles-prompt',{task:els.aiTaskSelect.value,translate_to:els.aiTargetLangSelect.value,preserve_target_script:els.aiPreserveScript.checked,glossary:els.aiGlossaryInput.value,batch_start:aiBatchStart,batch_size:+els.aiBatchSizeSelect.value}); els.aiPromptPreview.value=j.prompt||''; els.aiBatchInfo.textContent=`Batch ${j.current_batch} / ${j.total_batches} · lines ${j.range_start}–${j.range_end} of ${j.total_rows}`; project.translate_to=els.aiTargetLangSelect.value; project.glossary=els.aiGlossaryInput.value; return j; }catch(e){ toast(e.message,true); } }
els.btnAiCopyPrompt.onclick=async()=>{ const j=await generateAiPrompt(); if(!j) return; try{ await navigator.clipboard.writeText(els.aiPromptPreview.value); toast('Prompt copied — paste it into any AI chat.'); }catch(e){ els.aiPromptPreview.select(); document.execCommand('copy'); toast('Prompt copied.'); } };
els.btnAiPrevBatch.onclick=()=>{ aiBatchStart=Math.max(0,aiBatchStart-(+els.aiBatchSizeSelect.value)); generateAiPrompt(); };
els.btnAiNextBatch.onclick=()=>{ aiBatchStart+=(+els.aiBatchSizeSelect.value); generateAiPrompt(); };
['aiTaskSelect','aiTargetLangSelect','aiBatchSizeSelect','aiPreserveScript'].forEach(id=>els[id].onchange=()=>{ aiBatchStart=0; generateAiPrompt(); });
els.btnAiClear.onclick=()=>{ els.aiResponseInput.value=''; els.aiQaReport.classList.add('hidden'); };
els.btnAiApply.onclick=async()=>{ try{ pushHistory(); const j=await postJson('ai/subtitles-apply',{response:els.aiResponseInput.value,task:els.aiTaskSelect.value}); subtitles=j.subtitles; renderAll(); const r=j.report||{}; els.aiQaReport.classList.remove('hidden'); els.aiQaReport.innerHTML=`<strong>QA: updated ${r.updated_count||0} line(s)</strong> (range ${(r.range||[]).join('–')}) ${(r.missing_ids||[]).length?`<div>Missing: ${r.missing_ids.join(', ')}</div>`:''} ${(r.out_of_range_ids||[]).length?`<div>Out of range: ${r.out_of_range_ids.join(', ')}</div>`:''} ${(r.warnings||[]).length?`<ul>${r.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul>`:'<div>No warnings — clean round-trip.</div>'}`; toast('AI subtitles applied.'); refreshVocabAndClips(); }catch(e){ toast(e.message,true); } };

/* ---------- Vocab & cutter ---------- */
function vocabParams(){ return {scope:els.vocabScopeSelect.value,hide_stopwords:els.vocabHideStopwords.checked,min_freq:+els.vocabMinFreq.value||1}; }
async function loadVocab(){ const p=vocabParams(); const j=await apiJson(`vocab?scope=${encodeURIComponent(p.scope)}&hide_stopwords=${p.hide_stopwords?1:0}&min_freq=${p.min_freq}`); vocabCards=j.cards||[]; vocabLoaded=true; renderVocab(); }
function renderVocab(){
  const q=els.vocabSearchInput.value.trim().toLowerCase(); const list=vocabCards.filter(c=>!q||[c.word,c.romanization,c.meaning,c.pos,c.level].join(' ').toLowerCase().includes(q));
  els.vocabTableBody.innerHTML=list.map(c=>`<tr data-id="${esc(c.id)}">
    <td><input type="checkbox" class="vocab-check" ${c.selected?'checked':''}></td>
    <td><span class="vocab-word" dir="auto">${esc(c.word)}</span>${c.surface_example&&c.surface_example!==c.word?`<div class="muted">${esc(c.surface_example)}</div>`:''}</td>
    <td><input class="inline-edit vocab-roma" value="${esc(c.romanization||'')}" ></td>
    <td><input class="inline-edit vocab-meaning" dir="auto" value="${esc(c.meaning||'')}" placeholder="Add meaning…"></td>
    <td>${esc(c.lang.toUpperCase())}<br><span class="muted">${esc(c.level||'')}</span></td>
    <td><input class="inline-edit vocab-pos" value="${esc(c.pos||'')}" placeholder="—"></td>
    <td>${c.frequency}</td><td><button class="btn btn-ghost btn-xs vocab-play" title="Play context">▶ ${fmtMs(c.first_start_ms)}</button></td>
    <td class="context-cell" dir="auto" title="${esc(c.context_translation||'')}">${esc(c.context||'')}</td>
    <td>${c.audio_file?`<div class="muted" title="${esc(c.audio_file)}">🔊 ${esc(c.audio_file)}</div>`:''}<button class="btn btn-ghost btn-xs vocab-dict" title="Save to Custom Dictionary">💾 Dict</button></td></tr>`).join('')||'<tr><td colspan="10" style="padding:16px;color:var(--text-muted)">No vocabulary matched.</td></tr>';
  els.vocabTableBody.querySelectorAll('tr[data-id]').forEach(tr=>{ const id=tr.dataset.id; const card=vocabCards.find(c=>c.id===id);
    tr.querySelector('.vocab-check').onchange=e=>updateVocab(id,{selected:e.target.checked});
    tr.querySelector('.vocab-roma').onchange=e=>updateVocab(id,{romanization:e.target.value});
    tr.querySelector('.vocab-meaning').onchange=e=>updateVocab(id,{meaning:e.target.value});
    tr.querySelector('.vocab-pos').onchange=e=>updateVocab(id,{pos:e.target.value});
    tr.querySelector('.vocab-play').onclick=()=>{ seekTo(card.first_start_ms,true); switchTab('editor'); };
    tr.querySelector('.vocab-dict').onclick=e=>updateVocab(id,{save_to_dictionary:true,word:card.word,lang:card.lang,romanization:tr.querySelector('.vocab-roma').value,meaning:tr.querySelector('.vocab-meaning').value,pos:tr.querySelector('.vocab-pos').value,level:card.level},'Saved to Custom Dictionary.');
  });
  const sel=vocabCards.filter(c=>c.selected).length; els.vocabSummaryPill.textContent=`${sel} / ${vocabCards.length} selected`; els.vocabCountBadge.textContent=vocabCards.length;
}
async function updateVocab(id, patch, msg){ try{ await postJson('vocab/update',{id,...patch}); const c=vocabCards.find(x=>x.id===id); if(c) Object.assign(c,patch); renderVocab(); if(msg) toast(msg); }catch(e){ toast(e.message,true); } }
['vocabScopeSelect','vocabHideStopwords','vocabMinFreq'].forEach(id=>els[id].onchange=()=>refreshVocabAndClips());
els.vocabSearchInput.oninput=renderVocab;
els.btnVocabSelectAll.onclick=()=>setVocabSelection(true); els.btnVocabSelectNone.onclick=()=>setVocabSelection(false);
async function setVocabSelection(sel){ try{ await postJson('vocab/select-all',{selected:sel,ids:vocabCards.map(c=>c.id)}); vocabCards.forEach(c=>c.selected=sel); renderVocab(); }catch(e){ toast(e.message,true); } }
function cutterParams(){ return {cut_mode:els.cutModeSelect.value,naming_mode:els.cutNamingSelect.value,format:els.cutFormatSelect.value,pre_pad_ms:+els.cutPrePad.value||0,post_pad_ms:+els.cutPostPad.value||0,deduplicate:els.cutDedup.checked,link_to_vocab:els.cutLinkVocab.checked}; }
async function loadClips(){ const p=cutterParams(); const q=new URLSearchParams({cut_mode:p.cut_mode,pre_pad_ms:p.pre_pad_ms,post_pad_ms:p.post_pad_ms,deduplicate:p.deduplicate?1:0,naming_mode:p.naming_mode,format:p.format}); const j=await apiJson('audio-clips/preview?'+q.toString()); clips=j.clips||[]; if(j.actual_format&&j.actual_format!==p.format) toast(`FFmpeg not found — clips will export as WAV.`); renderClips(); }
function renderClips(){ els.clipPreviewList.innerHTML=clips.map((c,i)=>`<div class="clip-chip"><div class="clip-chip-main"><div class="clip-chip-name" dir="auto">${esc(c.filename)}</div><div class="clip-chip-meta" dir="auto">${esc(c.phrase||'')} ${c.romanization?'· '+esc(c.romanization):''} · ${((c.duration_ms||0)/1000).toFixed(1)}s · cue ${c.row}</div></div><button class="btn btn-ghost btn-xs clip-play" data-i="${i}">▶ Play</button></div>`).join('')||'<div class="muted">No target-language clips detected yet.</div>';
  els.clipPreviewList.querySelectorAll('.clip-play').forEach(b=>b.onclick=()=>{ const c=clips[+b.dataset.i]; els.clipPreviewAudio.src=`api/audio-clips/sample?start_ms=${c.start_ms}&end_ms=${c.end_ms}&v=${Date.now()}`; els.clipPreviewAudio.play(); }); }
['cutModeSelect','cutNamingSelect','cutFormatSelect','cutPrePad','cutPostPad','cutDedup'].forEach(id=>els[id].onchange=loadClips);
els.btnToggleAudioPanel.onclick=()=>{ els.audioCutterPanel.classList.toggle('hidden'); if(!els.audioCutterPanel.classList.contains('hidden')) loadClips(); };
async function refreshVocabAndClips(){ if(!project) return; try{ await loadVocab(); if(!els.audioCutterPanel.classList.contains('hidden')) await loadClips(); }catch(e){ /* silent on tab hidden */ } }
els.btnDownloadAudioZip.onclick=()=>downloadExport('target-audio-zip',cutterParams()).then(()=>refreshVocabAndClips());
els.btnJumpAudioZip.onclick=()=>{ closeModal('modalExport'); switchTab('vocab'); els.audioCutterPanel.classList.remove('hidden'); loadClips(); };

/* Vocab AI modal */
els.btnOpenVocabAi.onclick=async()=>{ openModal('modalVocabAi'); try{ const p=vocabParams(); const j=await postJson('ai/vocab-prompt',{translate_to:els.vocabAiLang.value,...p}); els.vocabAiPromptBox.value=j.prompt||''; }catch(e){ toast(e.message,true); } };
els.btnCopyVocabPrompt.onclick=async()=>{ try{ const p=vocabParams(); const j=await postJson('ai/vocab-prompt',{translate_to:els.vocabAiLang.value,...p}); els.vocabAiPromptBox.value=j.prompt||''; await navigator.clipboard.writeText(els.vocabAiPromptBox.value); toast(`Wordlist prompt copied (${j.card_count} selected cards).`); }catch(e){ toast(e.message,true); } };
els.vocabAiLang.onchange=()=>els.btnOpenVocabAi.onclick();
els.btnApplyVocabAi.onclick=async()=>{ try{ const p=vocabParams(); const j=await postJson('ai/vocab-apply',{response:els.vocabAiPasteBox.value,save_to_dictionary:els.chkSaveVocabAiToDict.checked,...p}); vocabCards=j.cards||vocabCards; renderVocab(); toast(`Vocabulary AI applied: ${j.report.updated_count||0} updated${j.report.saved_to_dict?', '+j.report.saved_to_dict+' saved to dictionary':''}.`); closeModal('modalVocabAi'); }catch(e){ toast(e.message,true); } };

/* ---------- Dictionary ---------- */
function renderDictStats(st){ els.dictStatsLine.textContent=`Built-in: ${st.zh_words||0} Chinese words · ${st.zh_chars||0} Hanzi · ${st.ko_words||0} Korean · ${st.ja_words||0} Japanese · ${st.custom_words||0} custom words (persistent in data/custom_dict.tsv).`; }
async function loadDict(){ const q=els.dictSearchInput.value.trim(), lang=els.dictFilterLang.value, src=els.dictFilterSource.value; const j=await apiJson(`dict/search?q=${encodeURIComponent(q)}&lang=${lang}&source=${src}`); dictItems=j.items||[]; if(j.stats) renderDictStats(j.stats); renderDict(); dictLoaded=true; }
function renderDict(){ els.dictTableBody.innerHTML=dictItems.map((it,i)=>`<tr><td dir="auto"><strong>${esc(it.surface)}</strong></td><td>${esc(it.romanization||'')}</td><td dir="auto">${esc(it.meaning||'')}</td><td>${esc((it.lang||'').toUpperCase())}</td><td>${esc(it.level||'')}</td><td>${it.source==='custom'?`<button class="btn btn-ghost btn-xs dict-edit" data-i="${i}">Edit</button> <button class="btn btn-ghost btn-xs dict-del" data-i="${i}">✕</button>`:'<span class="muted">built-in</span>'}</td></tr>`).join('')||'<tr><td colspan="6" style="padding:14px;color:var(--text-muted)">No entries found.</td></tr>';
  els.dictTableBody.querySelectorAll('.dict-edit').forEach(b=>b.onclick=()=>{ const it=dictItems[+b.dataset.i]; els.dictSurface.value=it.surface; els.dictLang.value=it.lang||'zh'; els.dictLevel.value=it.level||'Custom'; els.dictRoma.value=it.romanization||''; els.dictMeaning.value=it.meaning||''; els.dictPos.value=it.pos||''; els.dictSurface.focus(); });
  els.dictTableBody.querySelectorAll('.dict-del').forEach(b=>b.onclick=async()=>{ const it=dictItems[+b.dataset.i]; if(!confirm('Delete custom word "'+it.surface+'"?')) return; try{ const j=await postJson('dict/delete',{surface:it.surface,lang:it.lang}); subtitles=j.subtitles||subtitles; renderCueTable(); renderDictStats(j.stats); loadDict(); toast('Deleted.'); }catch(e){ toast(e.message,true); } });
}
let dictTimer=null; els.dictSearchInput.oninput=()=>{ clearTimeout(dictTimer); dictTimer=setTimeout(loadDict,250); }; els.dictFilterLang.onchange=loadDict; els.dictFilterSource.onchange=loadDict;
els.dictForm.onsubmit=async(e)=>{ e.preventDefault(); try{ const j=await postJson('dict/upsert',{surface:els.dictSurface.value,lang:els.dictLang.value,level:els.dictLevel.value,romanization:els.dictRoma.value,meaning:els.dictMeaning.value,pos:els.dictPos.value}); subtitles=j.subtitles||subtitles; renderCueTable(); renderOverlay(); if(j.stats) renderDictStats(j.stats); loadDict(); refreshVocabAndClips(); toast('Saved to Custom Dictionary: '+j.entry.surface); }catch(err){ toast(err.message,true); } };
els.btnImportDict.onclick=()=>els.hiddenDictFileInput.click();
els.hiddenDictFileInput.onchange=async()=>{ const f=els.hiddenDictFileInput.files[0]; els.hiddenDictFileInput.value=''; if(!f) return; try{ const r=await fetch('api/dict/import',{method:'POST',headers:{'X-Filename':encodeURIComponent(f.name),'X-Default-Lang':els.dictLang.value},body:f}); const j=await r.json(); if(!r.ok) throw new Error(j.error||'Import failed'); subtitles=j.subtitles||subtitles; renderCueTable(); if(j.stats) renderDictStats(j.stats); loadDict(); toast(`Imported ${j.imported} dictionary rows.`); }catch(e){ toast(e.message,true); } };

/* ---------- Downloads / exports ---------- */
async function downloadExport(kind, body){
  const map={'subtitles':'export/subtitles','vocab':'export/vocab','target-audio-zip':'export/target-audio-zip','full-audio':'export/full-audio','video':'export/video'};
  try{
    if(kind==='video'){ const j=await postJson(map[kind],body); els.videoExportResult.textContent=`Saved: ${j.saved_path} (${j.size_mb} MB)`; toast('Video exported to exports/ folder.'); return j; }
    toast('Preparing download…');
    const r=await fetch('api/'+map[kind],{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
    if(!r.ok){ let m='Export failed'; try{ m=(await r.json()).error||m; }catch(e){} throw new Error(m); }
    const blob=await r.blob(); const cd=r.headers.get('Content-Disposition')||''; const m=/filename\*?=(?:UTF-8''|")?([^";]+)/i.exec(cd); const name=m?decodeURIComponent(m[1].replace(/"/g,'')):'hosub_export';
    const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=name; document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(a.href),4000); toast('Downloaded: '+name);
  }catch(e){ toast(e.message,true); }
}
document.querySelectorAll('[data-sub-export]').forEach(b=>b.onclick=()=>downloadExport('subtitles',{format:b.dataset.subExport,content_mode:els.expContentMode.value,romanization_mode:els.expRomaMode.value,enforce_bidi:els.expBidi.checked}));
document.querySelectorAll('[data-vid-export]').forEach(b=>{ b.onclick=()=>{ const v=b.dataset.vidExport; downloadExport('video',{mode:v.startsWith('hard')?'hardsub':'softsub',container:v.endsWith('mp4')?'mp4':'mkv',content_mode:els.expContentMode.value}); }; });
els.btnExportFullAudio.onclick=()=>downloadExport('full-audio',{format:els.expFullAudioFmt.value});
els.btnExportXlsx.onclick=()=>downloadExport('vocab',{format:'xlsx',...vocabParams()});
els.btnExportXlsxModal.onclick=()=>downloadExport('vocab',{format:'xlsx',...vocabParams()});
els.btnExportVocabCsv.onclick=()=>downloadExport('vocab',{format:'csv',...vocabParams()});
els.btnExportVocabJson.onclick=()=>downloadExport('vocab',{format:'json',...vocabParams()});
els.projectTitleInput.addEventListener('change',()=>{ project.title=els.projectTitleInput.value; syncProject(true); });

/* ---------- Keyboard ---------- */
document.addEventListener('keydown',e=>{
  const tag=(e.target.tagName||'').toLowerCase(); const editing=tag==='input'||tag==='textarea'||tag==='select'||e.target.isContentEditable;
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'&&!e.shiftKey){ e.preventDefault(); undo(); return; }
  if((e.ctrlKey||e.metaKey)&&(e.key.toLowerCase()==='y'||(e.shiftKey&&e.key.toLowerCase()==='z'))){ e.preventDefault(); redo(); return; }
  if(editing) return;
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='g'){ e.preventDefault(); e.shiftKey?ungroupCues():groupCues(); return; }
  switch(e.key.toLowerCase()){
    case ' ': e.preventDefault(); els.btnPlayPause.onclick(); break;
    case 'j': seekTo(currentMs()-2000); break; case 'l': seekTo(currentMs()+2000); break;
    case 'v': setTool('select'); break; case 'a': setTool('forward'); break;
    case 's': splitCue(); break; case 'm': els.btnMergeCues.onclick(); break; case 'l2': break;
    case 'delete': case 'backspace': e.preventDefault(); deleteSelected(); break;
    case 'arrowdown': case 'arrowup': { e.preventDefault(); const idx=subtitles.findIndex(c=>c.id===activeId); const n=subtitles[Math.max(0,Math.min(subtitles.length-1,idx+(e.key==='ArrowDown'?1:-1)))]; if(n){ activeId=n.id; if(!e.shiftKey) selected=new Set([n.id]); else selected.add(n.id); renderCueTable(); renderInspector(); updateSelectionUI(); drawTimeline(); document.querySelector(`.cue-row[data-id="${n.id}"]`)?.scrollIntoView({block:'nearest'}); } break; }
  }
  if(e.key.toLowerCase()==='l'&&e.shiftKey){} // reserved
});
document.addEventListener('keydown',e=>{ if(e.key.toLowerCase()==='l'&&!e.ctrlKey&&!e.metaKey){ const tag=(e.target.tagName||'').toLowerCase(); if(tag!=='input'&&tag!=='textarea'&&tag!=='select'&&e.shiftKey){} } });
// Lock shortcut separate (avoid conflict with L seek: use plain L when not editing? keep button + key 'k')
document.addEventListener('keydown',e=>{ const tag=(e.target.tagName||'').toLowerCase(); if(tag==='input'||tag==='textarea'||tag==='select') return; if(e.key.toLowerCase()==='k'){ els.btnLockCue.onclick(); } });

/* ---------- Init ---------- */
(async()=>{ try{ await loadState(); await generateAiPrompt(); refreshEngine(); }catch(e){ toast('Failed to load HoSub: '+e.message,true); } })();
