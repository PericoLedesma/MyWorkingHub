/* SmartRecorder – frontend JavaScript */
'use strict';

// ── Global state ──────────────────────────────────────────────────────────────
const S = {
  ws:              null,
  wsChat:          null,
  wsRetryDelay:    1000,
  recordings:      [],       // full list from API
  filtered:        [],       // after search/filter
  recording:       false,
  recordingStart:  null,     // epoch seconds
  timerInterval:   null,
  source:          'both',
  deviceIndex:     null,
  micLevel:        0,
  appLevel:        0,
  dispMic:         0,        // smoothed display level
  dispApp:         0,
  chatBubble:      null,     // current streaming assistant bubble
  pendingJobs:     {},       // {id: Set<'transcribe'|'summarize'>}
};

// ── Utilities ─────────────────────────────────────────────────────────────────
function esc(s) {
  return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function fmtDuration(s) {
  if (!s) return '0:00';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
  return `${m}:${String(sec).padStart(2,'0')}`;
}

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
  });
}

function srcLabel(s) {
  return { mic: '🎙 Mic', app: '🔊 App', both: '🎙+🔊 Both', import: '📥 Import' }[s] || s;
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  return r.json();
}

// ── WebSocket: /ws/app ────────────────────────────────────────────────────────
function connectWS() {
  const ws = new WebSocket(`ws://${location.host}/ws/app`);
  S.ws = ws;

  ws.onopen = () => {
    S.wsRetryDelay = 1000;
  };

  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    onWsMessage(msg);
  };

  ws.onclose = () => {
    S.ws = null;
    setTimeout(() => {
      S.wsRetryDelay = Math.min(S.wsRetryDelay * 1.5, 10000);
      connectWS();
    }, S.wsRetryDelay);
  };
}

function onWsMessage(msg) {
  switch (msg.type) {
    case 'init':
      S.recording = msg.recording;
      S.recordingStart = msg.recording_start;
      (msg.transcribing || []).forEach(id => addJob(id, 'transcribe'));
      (msg.summarizing  || []).forEach(id => addJob(id, 'summarize'));
      updateRecordButton();
      if (S.recording) startTimer();
      break;

    case 'level':
      S.micLevel = msg.mic;
      S.appLevel = msg.app;
      break;

    case 'recording_started':
      S.recording = true;
      S.recordingStart = msg.start_time;
      updateRecordButton();
      startTimer();
      break;

    case 'recording_stopped':
      S.recording = false;
      S.recordingStart = null;
      updateRecordButton();
      stopTimer();
      if (msg.meta) {
        // Prepend the new recording card
        S.recordings.unshift(msg.meta);
        filterRecordings();
        updateProjectFilter();
      }
      break;

    case 'job_started':
      addJob(msg.id, msg.job);
      refreshCard(msg.id);
      break;

    case 'transcribed':
      removeJob(msg.id, 'transcribe');
      refreshCardFromServer(msg.id);
      break;

    case 'summarized':
      removeJob(msg.id, 'summarize');
      refreshCardFromServer(msg.id);
      break;

    case 'imported':
      if (msg.meta) {
        S.recordings.unshift(msg.meta);
        filterRecordings();
        updateProjectFilter();
      }
      break;
  }
}

// ── WebSocket: /ws/chat ───────────────────────────────────────────────────────
function connectChatWS() {
  const ws = new WebSocket(`ws://${location.host}/ws/chat`);
  S.wsChat = ws;

  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    onChatMessage(msg);
  };

  ws.onclose = () => {
    S.wsChat = null;
    setTimeout(connectChatWS, 2000);
  };
}

function onChatMessage(msg) {
  if (msg.type === 'token') {
    if (S.chatBubble) {
      S.chatBubble.textContent += msg.text;
      scrollChatToBottom();
    }
  } else if (msg.type === 'done') {
    if (S.chatBubble) {
      if (!S.chatBubble.textContent.trim() && msg.text) {
        S.chatBubble.textContent = msg.text;
      }
      S.chatBubble.classList.remove('streaming');
      S.chatBubble = null;
    }
    document.getElementById('chat-send').disabled = false;
    document.getElementById('chat-input').disabled = false;
  }
}

// ── Chat UI ───────────────────────────────────────────────────────────────────
function appendChatBubble(role, text) {
  const el = document.createElement('div');
  el.className = `chat-bubble ${role}`;
  el.textContent = text;
  document.getElementById('chat-messages').appendChild(el);
  scrollChatToBottom();
  return el;
}

function scrollChatToBottom() {
  const el = document.getElementById('chat-messages');
  el.scrollTop = el.scrollHeight;
}

function sendChatMessage() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text || !S.wsChat || S.wsChat.readyState !== WebSocket.OPEN) return;

  input.value = '';
  appendChatBubble('user', text);
  S.chatBubble = appendChatBubble('assistant', '');
  S.chatBubble.classList.add('streaming');
  document.getElementById('chat-send').disabled = true;
  document.getElementById('chat-input').disabled = true;

  S.wsChat.send(JSON.stringify({ text }));
}

function onChatKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendChatMessage();
  }
}

// ── Record controls ───────────────────────────────────────────────────────────
function setSource(src) {
  S.source = src;
  ['mic','app','both'].forEach(s => {
    document.getElementById(`src-${s}`).classList.toggle('active', s === src);
  });
}

function updateRecordButton() {
  const btn = document.getElementById('record-btn');
  const label = document.getElementById('record-label');
  if (S.recording) {
    btn.className = 'recording';
    label.textContent = 'Stop';
  } else {
    btn.className = 'idle';
    label.textContent = 'Record';
    document.getElementById('timer').textContent = '00:00';
  }
}

async function toggleRecord() {
  if (S.recording) {
    await api('POST', '/api/record/stop');
  } else {
    const sel = document.getElementById('device-select');
    const deviceIndex = sel.value !== '' ? parseInt(sel.value) : null;
    await api('POST', '/api/record/start', {
      source: S.source,
      device_index: deviceIndex,
    });
  }
}

// ── Timer ─────────────────────────────────────────────────────────────────────
function startTimer() {
  stopTimer();
  S.timerInterval = setInterval(() => {
    if (!S.recordingStart) return;
    const elapsed = Math.floor(Date.now() / 1000 - S.recordingStart);
    const m = Math.floor(elapsed / 60), sec = elapsed % 60;
    document.getElementById('timer').textContent =
      `${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
  }, 500);
}

function stopTimer() {
  clearInterval(S.timerInterval);
  S.timerInterval = null;
}

// ── VU meter ──────────────────────────────────────────────────────────────────
function initVuMeter() {
  const micCanvas = document.getElementById('vu-mic');
  const appCanvas = document.getElementById('vu-app');
  const micCtx = micCanvas.getContext('2d');
  const appCtx = appCanvas.getContext('2d');

  function drawBar(ctx, canvas, level) {
    const W = canvas.width, H = canvas.height;
    ctx.fillStyle = '#27272a';
    ctx.fillRect(0, 0, W, H);
    const w = Math.floor(W * Math.min(level, 1));
    if (w > 0) {
      ctx.fillStyle = level < 0.5 ? '#22c55e' : level < 0.75 ? '#f59e0b' : '#ef4444';
      ctx.fillRect(0, 0, w, H);
    }
  }

  function frame() {
    S.dispMic = S.dispMic * 0.72 + S.micLevel * 0.28;
    S.dispApp = S.dispApp * 0.72 + S.appLevel * 0.28;
    drawBar(micCtx, micCanvas, S.dispMic);
    drawBar(appCtx, appCanvas, S.dispApp);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

// ── Devices ───────────────────────────────────────────────────────────────────
async function loadDevices() {
  const devices = await fetch('/api/devices').then(r => r.json()).catch(() => []);
  const sel = document.getElementById('device-select');
  sel.innerHTML = '<option value="">Default mic</option>';
  devices.forEach(d => {
    const opt = document.createElement('option');
    opt.value = d.index;
    opt.textContent = d.name.length > 40 ? d.name.slice(0,38)+'…' : d.name;
    sel.appendChild(opt);
  });
}

// ── Recordings list ───────────────────────────────────────────────────────────
async function loadRecordings() {
  const list = await fetch('/api/recordings').then(r => r.json()).catch(() => []);
  S.recordings = list;
  updateProjectFilter();
  filterRecordings();
}

function updateProjectFilter() {
  const projects = [...new Set(S.recordings.map(r => r.project).filter(Boolean))].sort();
  const sel = document.getElementById('project-filter');
  const cur = sel.value;
  sel.innerHTML = '<option value="">All projects</option>';
  projects.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p; opt.textContent = p;
    if (p === cur) opt.selected = true;
    sel.appendChild(opt);
  });
}

function filterRecordings() {
  const proj = document.getElementById('project-filter').value.toLowerCase();
  const q    = document.getElementById('search-input').value.toLowerCase();
  S.filtered = S.recordings.filter(r => {
    if (proj && (r.project || '').toLowerCase() !== proj) return false;
    if (q) {
      const hay = [r.project, r.meeting, r.source, r.start_time].join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  renderList();
}

function renderList() {
  const el = document.getElementById('rec-list');
  if (S.filtered.length === 0) {
    el.innerHTML = '<div class="empty-state"><p>No recordings found.</p></div>';
    return;
  }
  el.innerHTML = S.filtered.map(r => renderCard(r)).join('');
  // Attach tag blur listeners
  el.querySelectorAll('.tag-value').forEach(span => {
    span.addEventListener('blur', () => {
      const rid = parseInt(span.closest('.rec-card').dataset.id);
      const field = span.dataset.field;
      saveTag(rid, field, span.textContent.trim());
    });
    span.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); span.blur(); }
    });
  });
}

function renderCard(r) {
  const jobs = S.pendingJobs[r.id] || new Set();
  const isTranscribing = jobs.has('transcribe');
  const isSummarizing  = jobs.has('summarize');

  const badges = [
    r.has_transcript ? '<span class="badge badge-ok">Transcript</span>'
                     : '<span class="badge badge-pending">No transcript</span>',
    r.has_summary      ? '<span class="badge badge-ok">Summary</span>'  : '',
  ].filter(Boolean).join('');

  const statusMsg = isTranscribing ? 'Transcribing...'
                  : isSummarizing  ? 'Summarizing...' : '';

  const cardClass = ['rec-card', r.file_missing ? 'missing' : '', r.has_transcript ? 'has-transcript' : ''].filter(Boolean).join(' ');

  return `
<div class="${cardClass}" data-id="${r.id}">
  <div class="rec-header">
    <span class="rec-date">${esc(fmtDate(r.start_time))}</span>
    <span class="rec-sep">·</span>
    <span class="rec-duration">${esc(fmtDuration(r.duration))}</span>
    <span class="rec-sep">·</span>
    <span class="rec-source">${esc(srcLabel(r.source))}</span>
    ${r.language ? `<span class="rec-lang">${esc(r.language)}</span>` : ''}
    <div class="rec-badges">${badges}</div>
  </div>
  <div class="rec-tags">
    <div class="tag-pair">
      <span class="tag-label">Project</span>
      <span class="tag-value" contenteditable="true" data-field="project" data-placeholder="--">${esc(r.project || '')}</span>
    </div>
    <div class="tag-pair">
      <span class="tag-label">Meeting</span>
      <span class="tag-value" contenteditable="true" data-field="meeting" data-placeholder="--">${esc(r.meeting || '')}</span>
    </div>
  </div>
  ${!r.file_missing ? `
  <div class="rec-audio-row">
    <audio class="rec-audio" src="/audio/${r.id}" controls preload="none"></audio>
  </div>` : `<div class="rec-status" style="color:#ef4444;margin-bottom:8px;">Audio file missing</div>`}
  <div class="rec-actions">
    ${r.has_transcript ? `
      <button class="act-btn primary" onclick="viewTranscript(${r.id})">View transcript</button>
      ${!r.has_summary ? `<button class="act-btn${isSummarizing?' spinning':''}" ${isSummarizing?'disabled':''} onclick="summarize(${r.id})">Summarize</button>` : ''}
    ` : ''}
    <button class="act-btn${isTranscribing?' spinning':''}" ${isTranscribing?'disabled':''} onclick="transcribe(${r.id})">
      ${r.has_transcript ? 'Re-transcribe' : 'Transcribe'}
    </button>
    <button class="act-btn" onclick="openFolder(${r.id})">Open folder</button>
    <button class="act-btn danger" onclick="deleteRecording(${r.id})">Delete</button>
  </div>
  ${statusMsg ? `<div class="rec-status">${esc(statusMsg)}</div>` : ''}
</div>`;
}

function addJob(id, job) {
  if (!S.pendingJobs[id]) S.pendingJobs[id] = new Set();
  S.pendingJobs[id].add(job);
}

function removeJob(id, job) {
  if (S.pendingJobs[id]) S.pendingJobs[id].delete(job);
}

function refreshCard(id) {
  const card = document.querySelector(`.rec-card[data-id="${id}"]`);
  if (!card) return;
  const rec = S.recordings.find(r => r.id === id);
  if (!rec) return;
  card.outerHTML = renderCard(rec);
  // Re-attach listeners for the replaced card
  const newCard = document.querySelector(`.rec-card[data-id="${id}"]`);
  if (newCard) attachTagListeners(newCard);
}

async function refreshCardFromServer(id) {
  const recs = await fetch('/api/recordings').then(r => r.json()).catch(() => null);
  if (!recs) return;
  S.recordings = recs;
  filterRecordings();
  updateProjectFilter();
}

function attachTagListeners(card) {
  card.querySelectorAll('.tag-value').forEach(span => {
    span.addEventListener('blur', () => {
      const rid = parseInt(span.closest('.rec-card').dataset.id);
      saveTag(rid, span.dataset.field, span.textContent.trim());
    });
    span.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); span.blur(); }
    });
  });
}

// ── Recording actions ─────────────────────────────────────────────────────────
async function transcribe(id) {
  addJob(id, 'transcribe');
  refreshCard(id);
  await api('POST', `/api/recordings/${id}/transcribe`);
}

async function summarize(id) {
  addJob(id, 'summarize');
  refreshCard(id);
  await api('POST', `/api/recordings/${id}/summarize`);
}

async function deleteRecording(id) {
  if (!confirm('Delete this recording? This cannot be undone.')) return;
  await api('DELETE', `/api/recordings/${id}`);
  S.recordings = S.recordings.filter(r => r.id !== id);
  delete S.pendingJobs[id];
  filterRecordings();
  updateProjectFilter();
}

async function openFolder(id) {
  await api('GET', `/api/open_folder/${id}`);
}

// ── Tag editing ───────────────────────────────────────────────────────────────
async function saveTag(id, field, value) {
  await api('PATCH', `/api/recordings/${id}/tags`, { field, value });
  const rec = S.recordings.find(r => r.id === id);
  if (rec) rec[field] = value;
  updateProjectFilter();
}

// ── Transcript modal ──────────────────────────────────────────────────────────
async function viewTranscript(id) {
  const rec = S.recordings.find(r => r.id === id);
  const data = await fetch(`/api/recordings/${id}/transcript`).then(r => r.json());

  document.getElementById('modal-title').textContent =
    rec ? `Transcript — ${fmtDate(rec.start_time)}` : 'Transcript';

  // Summary
  const sumSection = document.getElementById('modal-summary-section');
  if (data.summary) {
    document.getElementById('modal-summary-text').textContent = data.summary;
    sumSection.style.display = '';
  } else {
    sumSection.style.display = 'none';
  }

  // Transcript panes
  document.getElementById('modal-corrected').textContent = data.corrected || '(not available)';
  document.getElementById('modal-raw').textContent       = data.raw       || '(not available)';

  // Reset to corrected tab
  showTab('corrected', document.querySelector('.tab-btn'));

  document.getElementById('transcript-modal').showModal();
}

function showTab(name, btn) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');

  const panes = { corrected: '#pane-corrected', raw: '#pane-raw' };
  Object.entries(panes).forEach(([key, sel]) => {
    const el = document.querySelector(sel);
    if (el) el.style.display = key === name ? '' : 'none';
  });

  document.getElementById('modal-panes').style.gridTemplateColumns = '1fr';
}

// ── Import ────────────────────────────────────────────────────────────────────
async function importFile(input) {
  const file = input.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  input.value = '';
  await fetch('/api/import', { method: 'POST', body: fd });
}

// ── Resize handle ─────────────────────────────────────────────────────────────
function initResizeHandle() {
  const handle  = document.getElementById('resize-handle');
  const recPanel = document.getElementById('recordings-panel');
  const main    = document.getElementById('main');
  let dragging  = false, startX = 0, startW = 0;

  handle.addEventListener('mousedown', e => {
    dragging = true;
    startX   = e.clientX;
    startW   = recPanel.offsetWidth;
    handle.classList.add('dragging');
    document.body.style.cursor    = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });

  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const totalW  = main.offsetWidth;
    const newW    = Math.min(Math.max(startW + (e.clientX - startX), 280), totalW - 240 - 5);
    recPanel.style.width = newW + 'px';
  });

  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove('dragging');
    document.body.style.cursor     = '';
    document.body.style.userSelect = '';
  });
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadDevices();
  loadRecordings();
  connectWS();
  connectChatWS();
  initVuMeter();
  initResizeHandle();
});
