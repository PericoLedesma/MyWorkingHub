/* My Working Hub – frontend JavaScript */
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
  pendingJobs:     {},       // {id: Set<'transcribe'|'notes'>}
  expandedCardId:  null,     // id of currently expanded card, or null
  nextProject:          '',       // project to apply when recording stops
  nextMeeting:          '',       // meeting to apply when recording stops
  calEvents:            [],       // today's calendar events (cached)
  projects:             [],       // project entities from /api/projects (for color lookup)
  liveTranscriptEnabled: false,   // whether to show live transcript panel
};

// ── Page navigation ───────────────────────────────────────────────────────────
function showPage(name) {
  window.electronAPI?.pageChange(name);
  document.querySelectorAll('.page').forEach(p => { p.style.display = 'none'; });
  document.getElementById('page-' + name).style.display = '';
  document.querySelectorAll('.nav-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.page === name);
  });
  if (name === 'recordings') initVuMeter();
  if (name === 'history') renderHistoryBar();
  if (name === 'focustime') { ftShowTab('today'); ftLoadSessions(); }
  else {
    document.getElementById('nav-ft-today-btn')?.classList.remove('active');
    document.getElementById('nav-ft-stats-btn')?.classList.remove('active');
  }
  if (name === 'calendar') calInit();
  if (name === 'home') homeInit();
  if (name === 'tools') toolsInit();
}


function renderHistoryBar() {
  const bar = document.getElementById('history-tl-bar');
  if (!bar) return;

  const recs = [...S.recordings].sort((a, b) => (a.start_time || '').localeCompare(b.start_time || ''));
  if (!recs.length) { bar.innerHTML = ''; return; }

  const byMonth = {};
  recs.forEach(r => {
    const d = new Date(r.start_time || '');
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    if (!byMonth[key]) byMonth[key] = [];
    byMonth[key].push(r);
  });

  const inner = document.createElement('div');
  inner.className = 'htl-inner';

  const track = document.createElement('div');
  track.className = 'htl-track';
  inner.appendChild(track);

  Object.entries(byMonth).forEach(([monthKey, monthRecs], mi) => {
    const [year, month] = monthKey.split('-');
    const monthLabel = new Date(+year, +month - 1, 1).toLocaleDateString(undefined, { month: 'short', year: 'numeric' });

    if (mi > 0) {
      const sep = document.createElement('div');
      sep.className = 'htl-month-sep';
      inner.appendChild(sep);
    }

    const group = document.createElement('div');
    group.className = 'htl-day-group';

    const dotsRow = document.createElement('div');
    dotsRow.className = 'htl-dots';

    monthRecs.forEach(r => {
      const color = projectColor(r.project) || '#3a3a44';
      const dot = document.createElement('div');
      dot.className = 'htl-dot';
      dot.style.background = color;
      dot.title = [fmtDateOnly(r.start_time), fmtTimeOnly(r.start_time), r.meeting || r.project || 'Untitled'].filter(Boolean).join(' · ');
      dot.onclick = () => scrollToCard(r.id);
      dotsRow.appendChild(dot);
    });

    const lbl = document.createElement('div');
    lbl.className = 'htl-day-label';
    lbl.textContent = monthLabel;

    group.appendChild(dotsRow);
    group.appendChild(lbl);
    inner.appendChild(group);
  });

  bar.innerHTML = '';
  bar.appendChild(inner);
}

function scrollToCard(id) {
  if (document.getElementById('page-history').style.display === 'none') showPage('history');
  const card = document.querySelector(`.rec-card[data-id="${id}"]`);
  if (!card) return;
  card.scrollIntoView({ behavior: 'smooth', block: 'center' });
  card.classList.add('highlight');
  setTimeout(() => card.classList.remove('highlight'), 1500);
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function showToast(message, type = 'error') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = message;
  document.getElementById('toast-container').appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

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

function fmtDateOnly(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function fmtTimeOnly(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

function fmtTimeSec(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

const PROJECT_PALETTE = [
  '#f472b6','#fb923c','#fbbf24','#a3e635',
  '#2dd4bf','#38bdf8','#a78bfa','#e879f9',
  '#34d399','#f87171'
];
function projectColor(name) {
  if (!name) return null;
  const proj = Array.isArray(S.projects) && S.projects.find(p => p.name === name);
  if (proj && proj.color) return proj.color;
  const names = [...new Set(S.recordings.map(r => r.project).filter(Boolean))].sort();
  const idx = names.indexOf(name);
  return PROJECT_PALETTE[(idx >= 0 ? idx : 0) % PROJECT_PALETTE.length];
}
function hexAlpha(hex, a) {
  const r = parseInt(hex.slice(1,3),16), g = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
  return `rgba(${r},${g},${b},${a})`;
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
      clearLiveTranscript();
      document.getElementById('live-transcript-panel').style.display = S.liveTranscriptEnabled ? '' : 'none';
      { const badge = document.getElementById('rec-linked-meeting');
        if (badge) { badge.textContent = S.nextMeeting ? `📅 ${S.nextMeeting}` : ''; badge.style.display = S.nextMeeting ? '' : 'none'; } }
      break;

    case 'live_transcript':
      appendLiveTranscript(msg.timestamp, msg.text, msg.speaker || '');
      break;

    case 'recording_stopped':
      S.recording = false;
      S.recordingStart = null;
      updateRecordButton();
      stopTimer();
      { const badge = document.getElementById('rec-linked-meeting'); if (badge) badge.style.display = 'none'; }
      // Stay on Recordings page — live transcript remains visible for any in-flight chunks.
      // The recording is saved in the background; user can navigate to History when ready.
      if (msg.meta) {
        const newRec = msg.meta;
        S.recordings.unshift(newRec);
        (async () => {
          if (S.nextProject) { await saveTag(newRec.id, 'project', S.nextProject); S.nextProject = ''; }
          if (S.nextMeeting) { await saveTag(newRec.id, 'meeting', S.nextMeeting); S.nextMeeting = ''; }
          const notesTa = document.getElementById('new-user-notes');
          const notesText = notesTa ? notesTa.value.trim() : '';
          if (notesText) {
            await saveUserNotes(newRec.id, notesText);
            newRec.user_notes = notesText;
            if (notesTa) notesTa.value = '';
          }
          filterRecordings();
          updateProjectFilter();
          renderHistoryBar();
          recWfShow(newRec.id);
        })();
      }
      break;

    case 'job_started':
      addJob(msg.id, msg.job);
      refreshCard(msg.id);
      break;

    case 'transcribed':
      removeJob(msg.id, 'transcribe');
      refreshCardFromServer(msg.id);
      if (!msg.ok) {
        const errMsgs = {
          quota_exceeded: 'Transcription failed: OpenAI quota exceeded — check your billing.',
          rate_limited:   'Transcription failed: OpenAI rate limit hit — try again in a moment.',
          error:          'Transcription failed — see server logs for details.',
        };
        showToast(errMsgs[msg.error] || errMsgs.error);
      } else {
        if (msg.warning === 'quota_exceeded_used_local') {
          showToast('OpenAI quota exceeded — transcript generated with local model instead.', 'warn');
        }
        window.electronAPI?.showNotification('Transcription complete', recLabel(msg.id));
      }
      break;

    case 'notes_generated':
      removeJob(msg.id, 'notes');
      refreshCardFromServer(msg.id);
      if (msg.ok) {
        window.electronAPI?.showNotification('Notes ready', recLabel(msg.id));
      }
      break;

    case 'imported':
      if (msg.meta) {
        S.recordings.unshift(msg.meta);
        filterRecordings();
        updateProjectFilter();
        renderHistoryBar();
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
  const btn = document.getElementById('record-btn');
  if (btn) btn.disabled = true;

  try {
    if (S.recording) {
      await api('POST', '/api/record/stop');
    } else {
      recWfHide();
      const sel = document.getElementById('device-select');
      const deviceIndex = sel.value !== '' ? parseInt(sel.value) : null;
      S.nextProject = document.getElementById('new-project').value.trim();
      S.nextMeeting = document.getElementById('new-meeting').value.trim();
      // Auto-link to a currently-active Outlook meeting if the user left the field blank
      if (!S.nextMeeting && S.calEvents.length) {
        const now = new Date();
        const active = S.calEvents.find(e => new Date(e.start) <= now && now < new Date(e.end));
        if (active) S.nextMeeting = active.subject;
      }
      document.getElementById('new-project').value = '';
      document.getElementById('new-meeting').value = '';
      const notesTa = document.getElementById('new-user-notes');
      if (notesTa) notesTa.value = '';
      const result = await api('POST', '/api/record/start', {
        source: S.source,
        device_index: deviceIndex,
      });
      if (result?.ok) {
        S.recording = true;
        S.recordingStart = Date.now() / 1000;
        updateRecordButton();
        startTimer();
        clearLiveTranscript();
        document.getElementById('live-transcript-panel').style.display = S.liveTranscriptEnabled ? '' : 'none';
      }
    }
  } catch (e) {
    console.error('toggleRecord error:', e);
  } finally {
    if (btn) btn.disabled = false;
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

// ── Live transcript ───────────────────────────────────────────────────────────
let _lastShownTsSeconds = -1;

function clearLiveTranscript() {
  document.getElementById('live-tx-body').innerHTML = '';
  _lastShownTsSeconds = -1;
}

function toggleLiveTranscript() {
  S.liveTranscriptEnabled = !S.liveTranscriptEnabled;
  const btn = document.getElementById('live-tx-toggle');
  if (btn) btn.classList.toggle('active', S.liveTranscriptEnabled);
  const panel = document.getElementById('live-transcript-panel');
  if (S.recording) panel.style.display = S.liveTranscriptEnabled ? '' : 'none';
}

function _tsToSeconds(ts) {
  // Accepts "[HH:MM:SS]" or "HH:MM:SS"
  const m = ts.replace(/[\[\]]/g, '').match(/(\d+):(\d+):(\d+)/);
  if (!m) return 0;
  return parseInt(m[1]) * 3600 + parseInt(m[2]) * 60 + parseInt(m[3]);
}

function appendLiveTranscript(timestamp, text, speaker) {
  const body = document.getElementById('live-tx-body');
  const line = document.createElement('div');
  line.className = 'live-tx-line';
  if (speaker) {
    const sp = document.createElement('span');
    sp.className = 'live-tx-speaker live-tx-speaker--' + speaker.toLowerCase();
    sp.textContent = speaker;
    line.appendChild(sp);
  }
  const currentSeconds = _tsToSeconds(timestamp);
  if (_lastShownTsSeconds < 0 || currentSeconds - _lastShownTsSeconds >= 300) {
    const ts = document.createElement('span');
    ts.className = 'live-tx-ts';
    ts.textContent = timestamp;
    line.appendChild(ts);
    _lastShownTsSeconds = currentSeconds;
  }
  const tx = document.createElement('span');
  tx.className = 'live-tx-text';
  tx.textContent = ' ' + text;
  line.appendChild(tx);
  body.appendChild(line);
  body.scrollTop = body.scrollHeight;
}

// ── VU meter ──────────────────────────────────────────────────────────────────
let _vuStarted = false;
function initVuMeter() {
  if (_vuStarted) return;
  _vuStarted = true;
  const micCanvas = document.getElementById('vu-mic');
  const appCanvas = document.getElementById('vu-app');
  micCanvas.width = micCanvas.offsetWidth || 420;
  appCanvas.width = appCanvas.offsetWidth || 420;
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
  renderHistoryBar();
}

function updateProjectFilter() {
  const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
  const recentProjects = new Set(
    S.recordings
      .filter(r => r.project && new Date(r.start_time).getTime() >= cutoff)
      .map(r => r.project)
  );

  const allProjects = [...new Set(S.recordings.map(r => r.project).filter(Boolean))];
  // Active (recent) first, then inactive — each group sorted alphabetically
  const sorted = [
    ...allProjects.filter(p =>  recentProjects.has(p)).sort(),
    ...allProjects.filter(p => !recentProjects.has(p)).sort(),
  ];

  const container = document.getElementById('project-bubbles');
  const selected = new Set(
    [...container.querySelectorAll('.proj-bubble.active')].map(b => b.dataset.project)
  );
  container.innerHTML = '';

  sorted.forEach(p => {
    const isRecent = recentProjects.has(p);
    const wrap = document.createElement('span');
    wrap.className = 'proj-bubble-wrap';

    const b = document.createElement('button');
    const isSelected = selected.has(p);
    b.className = 'proj-bubble' + (isSelected ? ' active' : '') + (isRecent ? '' : ' stale');
    b.dataset.project = p;
    b.title = isRecent ? '' : 'No recordings in the last 7 days';
    b.textContent = p;
    const color = projectColor(p);
    if (color) {
      b.style.setProperty('--proj-color', color);
      if (isSelected) b.style.background = hexAlpha(color, 0.15);
    }
    b.onclick = () => {
      b.classList.toggle('active');
      if (color) b.style.background = b.classList.contains('active') ? hexAlpha(color, 0.15) : '';
      filterRecordings();
    };

    const renameBtn = document.createElement('button');
    renameBtn.className = 'proj-bubble-rename';
    renameBtn.title = 'Rename project';
    renameBtn.textContent = '✎';
    renameBtn.onclick = e => { e.stopPropagation(); openProjectRename(p, renameBtn); };

    wrap.appendChild(b);
    wrap.appendChild(renameBtn);
    container.appendChild(wrap);
  });

  const row = document.getElementById('filter-row-projects');
  if (row) row.style.display = sorted.length ? '' : 'none';
}

function filterRecordings() {
  const activeProjects = new Set(
    [...document.querySelectorAll('#project-bubbles .proj-bubble.active')]
      .map(b => b.dataset.project.toLowerCase())
  );
  const q = document.getElementById('search-input').value.toLowerCase();
  S.filtered = S.recordings.filter(r => {
    if (activeProjects.size && !activeProjects.has((r.project || '').toLowerCase())) return false;
    if (q) {
      const hay = [r.project, r.meeting, r.source, r.start_time].join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  const btn = document.getElementById('clear-filter-btn');
  if (btn) btn.style.display = (activeProjects.size || q) ? '' : 'none';
  const countEl = document.getElementById('rec-count');
  if (countEl) {
    const total = S.recordings.length;
    const shown = S.filtered.length;
    countEl.textContent = shown < total ? `${shown} of ${total}` : `${total}`;
  }
  renderList();
}

function clearFilter() {
  document.getElementById('search-input').value = '';
  document.querySelectorAll('#project-bubbles .proj-bubble.active').forEach(b => {
    b.classList.remove('active');
    b.style.background = '';
  });
  filterRecordings();
}

function monthKey(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return `${d.getFullYear()}-${d.getMonth()}`;
}

function monthLabel(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
}

function renderList() {
  const el = document.getElementById('rec-list');
  if (S.filtered.length === 0) {
    el.innerHTML = '<div class="empty-state"><p>No recordings found.</p></div>';
    return;
  }
  let html = '';
  let lastMonth = null;
  for (const r of S.filtered) {
    const mk = monthKey(r.start_time);
    if (mk !== lastMonth) {
      if (lastMonth !== null) html += '</div>';
      html += `<div class="month-sep" onclick="this.classList.toggle('collapsed')" data-month="${mk}"><span class="month-chevron">▾</span><span>${monthLabel(r.start_time)}</span></div><div class="month-group">`;
      lastMonth = mk;
    }
    html += renderCard(r);
  }
  if (lastMonth !== null) html += '</div>';
  el.innerHTML = html;
  initAudioPlayers(el);
  // Attach tag blur listeners
  el.querySelectorAll('[contenteditable][data-field="meeting"]').forEach(span => {
    span.addEventListener('blur', () => {
      const rid = parseInt(span.closest('.rec-card').dataset.id);
      saveTag(rid, 'meeting', span.textContent.trim());
    });
    span.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); span.blur(); }
    });
  });
  if (S.expandedCardId !== null) {
    const idToExpand = S.expandedCardId;
    S.expandedCardId = null;
    toggleCardExpand(idToExpand);
  }
}

function renderCard(r) {
  const jobs = S.pendingJobs[r.id] || new Set();
  const isTranscribing = jobs.has('transcribe');

  const cardClass = ['rec-card', r.file_missing ? 'missing' : '', r.has_transcript ? 'has-transcript' : ''].filter(Boolean).join(' ');
  const projColor  = projectColor(r.project);
  const asideBg    = projColor ? `background:${hexAlpha(projColor, 0.28)}` : '';
  const asideColor = projColor ? `color:${projColor}` : '';
  const projStyle  = projColor ? `style="color:${projColor}"` : '';

  const metaParts = [srcLabel(r.source), fmtDuration(r.duration)];
  if (r.language) metaParts.push(r.language.toUpperCase());
  const txBadge = r.has_transcript
    ? `<span class="rec-tx-badge tx-done">✓ Transcript</span>`
    : `<span class="rec-tx-badge tx-none">No transcript</span>`;

  const micSvg    = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 19v4M8 23h8"/></svg>`;
  const folderSvg = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`;
  const trashSvg  = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg>`;
  const chevronSvg = `<svg class="rec-expand-chevron" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>`;

  const txBtnClass = `act-btn${!r.has_transcript ? ' primary' : ''}${isTranscribing ? ' spinning' : ''}`;

  return `
<div class="${cardClass}" data-id="${r.id}">
  <div class="rec-card-aside" style="${asideBg}">
    <span class="rec-aside-label" style="${asideColor}">${esc(r.project || '·')}</span>
  </div>
  <div class="rec-card-content">
    <div class="rec-card-header">
      <div class="rec-title-col">
        <div class="rec-meeting-name" contenteditable="true" data-field="meeting"
             data-placeholder="Untitled recording" onclick="event.stopPropagation()">${esc(r.meeting || '')}</div>
        <div class="rec-tags-row">
          <button class="rec-project-tag${r.project ? '' : ' no-project'}" onclick="event.stopPropagation(); openProjectPicker(${r.id}, this)" ${projStyle}>${r.project ? esc(r.project) : 'No project'}</button>
          <span class="rec-meta-text">${esc(metaParts.join(' · '))}</span>
          ${txBadge}
        </div>
      </div>
      <div class="rec-date-col">
        <span class="rec-date-only">${esc(fmtDateOnly(r.start_time))}</span>
        <span class="rec-time-only">${esc(fmtTimeOnly(r.start_time))}</span>
      </div>
    </div>
    ${!r.file_missing ? `
    <div class="rec-audio-row">
      <audio class="rec-audio" src="/audio/${r.id}" preload="none"></audio>
      <div class="audio-player">
        <button class="audio-play-btn" aria-label="Play">▶</button>
        <span class="audio-time">0:00</span>
        <div class="wf-seek-wrap">
          <canvas class="wf-seek-canvas"></canvas>
          <span class="wf-seek-hint">Click ▶ to load waveform</span>
        </div>
        <span class="audio-dur">--:--</span>
      </div>
      <div class="wf-card-controls" style="display:none">
        <span class="wf-crop-info">Drag to select the region to keep</span>
        <span class="wf-clear-btn" style="display:none" onclick="wfClearSelection(${r.id})">Clear</span>
        <button class="act-btn wf-card-crop-btn" disabled onclick="wfCrop(${r.id})">Crop to selection</button>
      </div>
      <div class="wf-restore-row" style="display:none">
        <button class="act-btn wf-restore-btn" onclick="wfRestore(${r.id})">↩ Restore original</button>
      </div>
    </div>` : `<div class="rec-status" style="color:#ef4444;margin-bottom:8px;">Audio file missing</div>`}
    <div class="rec-card-footer">
      <button class="${txBtnClass}" ${isTranscribing?'disabled':''} onclick="transcribe(${r.id})">
        ${micSvg} ${r.has_transcript ? 'Re-transcribe' : 'Transcribe'}
      </button>
      ${isTranscribing ? `<span class="rec-status-inline">Transcribing…</span>` : ''}
      <div class="rec-footer-right">
        <button class="act-btn act-icon" title="Open folder" onclick="event.stopPropagation(); openFolder(${r.id})">${folderSvg}</button>
        <button class="act-btn act-icon danger" title="Delete recording" onclick="event.stopPropagation(); deleteRecording(${r.id})">${trashSvg}</button>
        <div class="rec-footer-sep"></div>
        <button class="rec-expand-btn" onclick="toggleCardExpand(${r.id})" aria-label="Expand details">
          <span class="rec-expand-label">Details</span>
          ${chevronSvg}
        </button>
      </div>
    </div>
    <div class="rec-detail" id="rec-detail-${r.id}"></div>
  </div>
</div>`;
}

function fmtPlayerTime(s) {
  if (!isFinite(s)) return '--:--';
  const m = Math.floor(s / 60);
  const ss = String(Math.floor(s % 60)).padStart(2, '0');
  return `${m}:${ss}`;
}

function initAudioPlayers(container) {
  container.querySelectorAll('.rec-audio-row').forEach(row => {
    const audio = row.querySelector('.rec-audio');
    const btn   = row.querySelector('.audio-play-btn');
    const time  = row.querySelector('.audio-time');
    const dur   = row.querySelector('.audio-dur');
    const rid   = parseInt(row.closest('.rec-card').dataset.id);

    btn.addEventListener('click', () => {
      if (!_wfState.has(rid)) loadCardWf(rid);
      if (audio.paused) { audio.play(); btn.textContent = '⏸'; }
      else              { audio.pause(); btn.textContent = '▶'; }
    });
    audio.addEventListener('loadedmetadata', () => { if (dur) dur.textContent = fmtPlayerTime(audio.duration); });
    audio.addEventListener('timeupdate',     () => { if (time) time.textContent = fmtPlayerTime(audio.currentTime); });
    audio.addEventListener('ended', () => { btn.textContent = '▶'; if (time) time.textContent = '0:00'; });
  });
}

function addJob(id, job) {
  if (!S.pendingJobs[id]) S.pendingJobs[id] = new Set();
  S.pendingJobs[id].add(job);
}

function removeJob(id, job) {
  if (S.pendingJobs[id]) S.pendingJobs[id].delete(job);
}

function recLabel(id) {
  const r = S.recordings.find(r => r.id === id);
  return r?.meeting || r?.project || 'Recording';
}

function refreshCard(id) {
  const card = document.querySelector(`.rec-card[data-id="${id}"]`);
  if (!card) return;
  const rec = S.recordings.find(r => r.id === id);
  if (!rec) return;
  const wasExpanded = S.expandedCardId === id;
  card.outerHTML = renderCard(rec);
  const newCard = document.querySelector(`.rec-card[data-id="${id}"]`);
  if (newCard) {
    attachTagListeners(newCard);
    if (wasExpanded) {
      S.expandedCardId = null;
      toggleCardExpand(id);
    }
  }
}

async function refreshCardFromServer(id) {
  const recs = await fetch('/api/recordings').then(r => r.json()).catch(() => null);
  if (!recs) return;
  S.recordings = recs;
  filterRecordings();
  updateProjectFilter();
  renderHistoryBar();
}

function attachTagListeners(card) {
  card.querySelectorAll('[contenteditable][data-field="meeting"]').forEach(span => {
    span.addEventListener('blur', () => {
      const rid = parseInt(span.closest('.rec-card').dataset.id);
      saveTag(rid, 'meeting', span.textContent.trim());
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
  if (field === 'project') refreshCard(id);
}

// ── Project bulk rename ───────────────────────────────────────────────────────
function openProjectRename(oldName, btn) {
  document.querySelectorAll('.proj-rename-popup').forEach(p => p.remove());

  const popup = document.createElement('div');
  popup.className = 'proj-picker proj-rename-popup';

  const label = document.createElement('div');
  label.className = 'proj-rename-label';
  label.textContent = `Rename "${oldName}"`;
  popup.appendChild(label);

  const inp = document.createElement('input');
  inp.type = 'text';
  inp.value = oldName;
  inp.className = 'proj-picker-input';
  inp.onclick = e => e.stopPropagation();
  popup.appendChild(inp);

  const row = document.createElement('div');
  row.className = 'proj-rename-actions';

  const saveBtn = document.createElement('button');
  saveBtn.className = 'proj-rename-save';
  saveBtn.textContent = 'Rename';
  saveBtn.onclick = async () => {
    const newName = inp.value.trim();
    if (!newName || newName === oldName) { popup.remove(); return; }
    popup.remove();
    await api('POST', '/api/projects/rename', { old_name: oldName, new_name: newName });
    S.recordings.forEach(r => { if (r.project === oldName) r.project = newName; });
    filterRecordings();
    updateProjectFilter();
    renderHistoryBar();
  };

  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'proj-rename-cancel';
  cancelBtn.textContent = 'Cancel';
  cancelBtn.onclick = () => popup.remove();

  row.appendChild(saveBtn);
  row.appendChild(cancelBtn);
  popup.appendChild(row);

  document.body.appendChild(popup);
  const rect = btn.getBoundingClientRect();
  popup.style.top  = (rect.bottom + window.scrollY + 4) + 'px';
  popup.style.left = (rect.left + window.scrollX) + 'px';

  inp.select();
  inp.onkeydown = e => {
    if (e.key === 'Enter') saveBtn.click();
    if (e.key === 'Escape') popup.remove();
  };

  const close = e => { if (!popup.contains(e.target) && e.target !== btn) { popup.remove(); document.removeEventListener('mousedown', close, true); } };
  document.addEventListener('mousedown', close, true);
}

// ── Project picker dropdown ───────────────────────────────────────────────────
function openProjectPicker(rid, btn) {
  // Remove any existing picker
  document.querySelectorAll('.proj-picker').forEach(p => p.remove());

  const existing = [...new Set(S.recordings.map(r => r.project).filter(Boolean))].sort();
  const current  = S.recordings.find(r => r.id === rid)?.project || '';

  const picker = document.createElement('div');
  picker.className = 'proj-picker';

  // Existing project options
  existing.forEach(name => {
    const item = document.createElement('button');
    item.className = 'proj-picker-item' + (name === current ? ' active' : '');
    const color = projectColor(name);
    if (color) {
      const dot = document.createElement('span');
      dot.className = 'proj-picker-dot';
      dot.style.background = color;
      item.appendChild(dot);
    }
    item.appendChild(document.createTextNode(name));
    item.onclick = async () => {
      picker.remove();
      await saveTag(rid, 'project', name === current ? '' : name);
    };
    picker.appendChild(item);
  });

  // Divider + clear option if a project is set
  if (current) {
    const clr = document.createElement('button');
    clr.className = 'proj-picker-item proj-picker-clear';
    clr.textContent = '✕ Clear project';
    clr.onclick = async () => { picker.remove(); await saveTag(rid, 'project', ''); };
    picker.appendChild(clr);
  }

  // New project input
  const newRow = document.createElement('div');
  newRow.className = 'proj-picker-new';
  const inp = document.createElement('input');
  inp.type = 'text';
  inp.placeholder = 'New project name…';
  inp.className = 'proj-picker-input';
  inp.onclick = e => e.stopPropagation();
  inp.onkeydown = async e => {
    if (e.key === 'Enter') {
      const val = inp.value.trim();
      picker.remove();
      if (val) await saveTag(rid, 'project', val);
    } else if (e.key === 'Escape') {
      picker.remove();
    }
  };
  newRow.appendChild(inp);
  picker.appendChild(newRow);

  // Position below the button
  document.body.appendChild(picker);
  const rect = btn.getBoundingClientRect();
  picker.style.top  = (rect.bottom + window.scrollY + 4) + 'px';
  picker.style.left = (rect.left + window.scrollX) + 'px';

  // Focus the input and close on outside click
  inp.focus();
  const close = e => { if (!picker.contains(e.target) && e.target !== btn) { picker.remove(); document.removeEventListener('mousedown', close, true); } };
  document.addEventListener('mousedown', close, true);
}

// ── Card expand (3-tab detail panel) ─────────────────────────────────────────
async function toggleCardExpand(id) {
  const card = document.querySelector(`.rec-card[data-id="${id}"]`);
  if (!card) return;

  // Collapse a previously expanded different card
  if (S.expandedCardId !== null && S.expandedCardId !== id) {
    const prev = document.querySelector(`.rec-card[data-id="${S.expandedCardId}"]`);
    if (prev) {
      prev.classList.remove('expanded');
      const d = prev.querySelector('.rec-detail');
      if (d) d.innerHTML = '';
    }
    _wfState.delete(S.expandedCardId);
    S.expandedCardId = null;
  }

  // Toggle current card
  if (card.classList.contains('expanded')) {
    card.classList.remove('expanded');
    card.querySelector('.rec-detail').innerHTML = '';
    _wfState.delete(id);
    S.expandedCardId = null;
    return;
  }

  const data = await fetch(`/api/recordings/${id}/transcript`).then(r => r.json()).catch(() => ({}));
  card.classList.add('expanded');
  S.expandedCardId = id;

  const detail = card.querySelector('.rec-detail');
  detail.innerHTML = buildDetailHTML(id, data);
  loadCardWf(id);
}

function buildDetailHTML(id, data) {
  const hasTranscript = !!data.raw;
  const hasSpeaker = !!data.speaker;
  const rec = S.recordings.find(r => r.id === id);

  const transcriptPanel = hasTranscript
    ? `<div class="detail-transcript-text">${esc(data.raw)}</div>`
    : `<p class="detail-empty">No transcript yet — click Transcribe to generate one.</p>`;

  const speakersPanel = hasSpeaker
    ? `<div class="detail-transcript-text">${esc(data.speaker)}</div>`
    : `<p class="detail-empty">No speaker transcript yet.</p>`;

  const notesPanel = `
    <div class="detail-notes-panel">
      <textarea class="rec-user-notes detail-user-notes" onblur="saveUserNotes(${id}, this.value)"
                placeholder="Your personal notes…" onclick="event.stopPropagation()">${esc(rec?.user_notes || data.user_notes || '')}</textarea>
    </div>
  `;

  return `
    <div class="detail-tabs">
      <button class="detail-tab active" data-tab="notes" onclick="switchDetailTab(this,'notes',${id})">Notes</button>
      <button class="detail-tab" data-tab="transcript" onclick="switchDetailTab(this,'transcript',${id})">Transcript</button>
      <button class="detail-tab${!hasSpeaker ? ' tab-empty' : ''}" data-tab="speakers" onclick="switchDetailTab(this,'speakers',${id})">Speakers</button>
    </div>
    <div class="detail-panel" data-tab="notes">${notesPanel}</div>
    <div class="detail-panel" data-tab="transcript" style="display:none">${transcriptPanel}</div>
    <div class="detail-panel" data-tab="speakers" style="display:none">${speakersPanel}</div>
  `;
}

function switchDetailTab(btn, tab, id) {
  const card = document.querySelector(`.rec-card[data-id="${id}"]`);
  if (!card) return;
  card.querySelectorAll('.detail-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  card.querySelectorAll('.detail-panel').forEach(p => {
    p.style.display = p.dataset.tab === tab ? '' : 'none';
  });
}

async function refreshExpandedDetail(id) {
  const card = document.querySelector(`.rec-card[data-id="${id}"]`);
  if (!card || !card.classList.contains('expanded')) return;
  const data = await fetch(`/api/recordings/${id}/transcript`).then(r => r.json()).catch(() => ({}));
  const detail = card.querySelector('.rec-detail');
  if (detail) {
    detail.innerHTML = buildDetailHTML(id, data);
  }
}

async function saveUserNotes(id, text) {
  await api('PATCH', `/api/recordings/${id}/user-notes`, { text });
}

// ── Waveform & Crop ───────────────────────────────────────────────────────────
const _wfState = new Map(); // rid → { peaks, duration, cropStart, cropEnd, dragging, canvas, audio }

async function loadCardWf(rid) {
  if (_wfState.has(rid)) return;
  const card = document.querySelector(`.rec-card[data-id="${rid}"]`);
  if (!card) return;
  const canvas       = card.querySelector('.wf-seek-canvas');
  const audio        = card.querySelector('.rec-audio');
  const hint         = card.querySelector('.wf-seek-hint');
  const controlsRow  = card.querySelector('.wf-card-controls');
  const restoreRow   = card.querySelector('.wf-restore-row');
  const infoEl       = controlsRow?.querySelector('.wf-crop-info');
  const clearEl      = controlsRow?.querySelector('.wf-clear-btn');
  const cropBtn      = controlsRow?.querySelector('.wf-card-crop-btn');
  if (!canvas) return;

  // Placeholder entry so concurrent calls bail out
  _wfState.set(rid, { peaks: null, duration: 0, cropStart: null, cropEnd: null, dragging: null, canvas, audio, infoEl, clearEl, cropBtn, controlsRow, restoreRow });
  if (hint) hint.textContent = 'Loading…';

  let result;
  try {
    result = await fetch(`/api/recordings/${rid}/peaks?n=600`).then(r => r.json());
  } catch {
    _wfState.delete(rid);
    if (hint) hint.textContent = '!';
    return;
  }

  const st = _wfState.get(rid);
  if (!st) return; // card was collapsed while loading
  st.peaks    = result.peaks;
  st.duration = result.duration;

  canvas.width  = canvas.offsetWidth  || 400;
  canvas.height = canvas.offsetHeight || 44;
  if (hint) hint.style.display = 'none';
  if (restoreRow) restoreRow.style.display = result.has_backup ? '' : 'none';

  _wfDraw(rid);

  canvas.addEventListener('mousedown',  e => _wfDown(rid, e));
  canvas.addEventListener('mousemove',  e => _wfMove(rid, e));
  canvas.addEventListener('mouseup',    e => _wfUp(rid, e));
  canvas.addEventListener('mouseleave', e => { const s = _wfState.get(rid); if (s?.dragging) _wfUp(rid, e); });
  if (audio) {
    audio.addEventListener('timeupdate', () => _wfDraw(rid));
    audio.addEventListener('ended',      () => _wfDraw(rid));
  }
}

function _wfXtoT(rid, x) {
  const st = _wfState.get(rid);
  if (!st) return 0;
  return Math.max(0, Math.min(st.duration, (x / st.canvas.width) * st.duration));
}

function _wfTtoX(rid, t) {
  const st = _wfState.get(rid);
  if (!st || !st.duration) return 0;
  return (t / st.duration) * st.canvas.width;
}

function _wfDraw(rid) {
  const st = _wfState.get(rid);
  if (!st) return;
  const { peaks, duration, cropStart, cropEnd, canvas, audio } = st;
  const W = canvas.width, H = canvas.height;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, W, H);

  const midY = H / 2;
  const csA = cropStart !== null && cropEnd !== null ? Math.min(cropStart, cropEnd) : null;
  const csB = cropStart !== null && cropEnd !== null ? Math.max(cropStart, cropEnd) : null;

  // Crop region highlight
  if (csA !== null) {
    const x1 = _wfTtoX(rid, csA), x2 = _wfTtoX(rid, csB);
    ctx.fillStyle = 'rgba(99,102,241,0.13)';
    ctx.fillRect(x1, 0, x2 - x1, H);
  }

  // Waveform bars
  const n = peaks.length;
  const barW = Math.max(1, (W / n) - 0.5);
  for (let i = 0; i < n; i++) {
    const x = (i / n) * W;
    const t = (i / n) * duration;
    const inSel = csA !== null && t >= csA && t <= csB;
    const barH = peaks[i] * H * 0.88;
    ctx.fillStyle = inSel ? '#818cf8' : '#3a3a4e';
    ctx.fillRect(x, midY - barH / 2, barW, barH);
  }

  // Crop handles
  if (cropStart !== null) {
    const x = _wfTtoX(rid, cropStart);
    ctx.strokeStyle = '#6366f1'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
    ctx.fillStyle = '#6366f1';
    ctx.beginPath(); ctx.moveTo(x - 5, 0); ctx.lineTo(x + 5, 0); ctx.lineTo(x, 10); ctx.fill();
  }
  if (cropEnd !== null) {
    const x = _wfTtoX(rid, cropEnd);
    ctx.strokeStyle = '#6366f1'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
    ctx.fillStyle = '#6366f1';
    ctx.beginPath(); ctx.moveTo(x - 5, H); ctx.lineTo(x + 5, H); ctx.lineTo(x, H - 10); ctx.fill();
  }

  // Playback cursor
  if (audio && isFinite(audio.duration) && audio.duration > 0) {
    const cx = (audio.currentTime / audio.duration) * W;
    ctx.strokeStyle = 'rgba(240,240,245,0.7)'; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(cx, 0); ctx.lineTo(cx, H); ctx.stroke();
  }
}

function _wfDown(rid, e) {
  const st = _wfState.get(rid);
  if (!st) return;
  e.preventDefault();
  const rect = st.canvas.getBoundingClientRect();
  const t = _wfXtoT(rid, e.clientX - rect.left);
  // Snap to existing handle if within 10px
  const snap = (10 / st.canvas.width) * st.duration;
  if (st.cropStart !== null && Math.abs(t - st.cropStart) < snap) { st.dragging = 'start'; return; }
  if (st.cropEnd   !== null && Math.abs(t - st.cropEnd)   < snap) { st.dragging = 'end';   return; }
  // Start new selection — also seek audio on plain click (handled in mouseup if no drag)
  st.cropStart = t;
  st.cropEnd   = null;
  st.dragging  = 'end';
  _wfUpdateInfo(rid);
  _wfDraw(rid);
}

function _wfMove(rid, e) {
  const st = _wfState.get(rid);
  if (!st?.dragging) return;
  const rect = st.canvas.getBoundingClientRect();
  const t = _wfXtoT(rid, e.clientX - rect.left);
  if (st.dragging === 'start') st.cropStart = t; else st.cropEnd = t;
  _wfUpdateInfo(rid);
  _wfDraw(rid);
}

function _wfUp(rid, e) {
  const st = _wfState.get(rid);
  if (!st) return;
  // If the user just clicked without dragging (cropEnd still null), seek the audio
  if (st.dragging === 'end' && st.cropEnd === null && st.audio && isFinite(st.audio.duration)) {
    const rect = st.canvas.getBoundingClientRect();
    st.audio.currentTime = _wfXtoT(rid, e.clientX - rect.left);
    st.cropStart = null;
  }
  st.dragging = null;
  _wfUpdateInfo(rid);
  _wfDraw(rid);
}

function _wfFmtT(s) {
  if (s === null || !isFinite(s)) return '--';
  const m = Math.floor(s / 60), sec = (s % 60).toFixed(1).padStart(4, '0');
  return `${m}:${sec}`;
}

function _wfUpdateInfo(rid) {
  const st = _wfState.get(rid);
  const infoEl  = st?.infoEl  ?? document.getElementById('wf-info-'     + rid);
  const clearEl = st?.clearEl ?? document.getElementById('wf-clear-'    + rid);
  const cropBtn = st?.cropBtn ?? document.getElementById('wf-crop-btn-' + rid);
  if (!infoEl) return;
  const ready = st?.cropStart !== null && st?.cropEnd !== null;
  if (ready) {
    const a = Math.min(st.cropStart, st.cropEnd);
    const b = Math.max(st.cropStart, st.cropEnd);
    infoEl.textContent = `${_wfFmtT(a)} → ${_wfFmtT(b)}  (${_wfFmtT(b - a)} kept)`;
    if (clearEl) clearEl.style.display = '';
    if (cropBtn) cropBtn.disabled = false;
    if (st?.controlsRow) st.controlsRow.style.display = '';
  } else {
    infoEl.textContent = 'Drag to select the region to keep';
    if (clearEl) clearEl.style.display = 'none';
    if (cropBtn) cropBtn.disabled = true;
    if (st?.controlsRow) st.controlsRow.style.display = 'none';
  }
}

function wfClearSelection(rid) {
  const st = _wfState.get(rid);
  if (st) { st.cropStart = null; st.cropEnd = null; }
  _wfUpdateInfo(rid);
  _wfDraw(rid);
}

async function wfCrop(rid) {
  const st = _wfState.get(rid);
  if (!st || st.cropStart === null || st.cropEnd === null) return;
  const a = Math.min(st.cropStart, st.cropEnd);
  const b = Math.max(st.cropStart, st.cropEnd);
  if (!confirm(`Keep only ${_wfFmtT(a)} → ${_wfFmtT(b)}?\n\nThe original will be kept as a backup so you can restore it.`)) return;
  const btn = st.cropBtn ?? document.getElementById('wf-crop-btn-' + rid);
  if (btn) { btn.disabled = true; btn.textContent = 'Cropping…'; }
  const resp = await fetch(`/api/recordings/${rid}/crop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ start: a, end: b }),
  });
  if (!resp.ok) {
    alert('Crop failed: ' + (await resp.text()));
    if (btn) { btn.disabled = false; btn.textContent = 'Crop to selection'; }
    return;
  }
  const st2 = _wfState.get(rid);
  if (st2?.restoreRow) st2.restoreRow.style.display = '';
  _wfState.delete(rid);
  await refreshCardFromServer(rid);
}

async function wfRestore(rid) {
  if (!confirm('Restore the original (pre-crop) audio? The current version will be overwritten.')) return;
  const card = document.querySelector(`.rec-card[data-id="${rid}"]`);
  const btn = card?.querySelector('.wf-restore-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Restoring…'; }
  const resp = await fetch(`/api/recordings/${rid}/restore`, { method: 'POST' });
  if (!resp.ok) {
    alert('Restore failed: ' + (await resp.text()));
    if (btn) { btn.disabled = false; btn.textContent = '↩ Restore original'; }
    return;
  }
  _wfState.delete(rid);
  await refreshCardFromServer(rid);
}


// ── Recording-page waveform (shown after a recording stops) ──────────────────
let _recWf = null; // { rid, peaks, duration, cropStart, cropEnd, dragging }

async function recWfShow(rid) {
  const panel = document.getElementById('rec-waveform-panel');
  if (!panel) return;

  _recWf = { rid, peaks: null, duration: 0, cropStart: null, cropEnd: null, dragging: null };
  panel.style.display = '';
  recWfUpdateInfo();

  // Wire audio player
  const audio = document.getElementById('rec-wf-audio');
  const playBtn = document.getElementById('rec-wf-play-btn');
  const timeEl = document.getElementById('rec-wf-time');
  const durEl = document.getElementById('rec-wf-dur');
  if (audio) {
    audio.src = `/audio/${rid}`;
    audio.load();
    if (playBtn) {
      playBtn.onclick = () => {
        if (audio.paused) { audio.play(); playBtn.textContent = '⏸'; }
        else              { audio.pause(); playBtn.textContent = '▶'; }
      };
    }
    audio.onloadedmetadata = () => { if (durEl) durEl.textContent = fmtPlayerTime(audio.duration); };
    audio.ontimeupdate = () => {
      if (timeEl) timeEl.textContent = fmtPlayerTime(audio.currentTime);
      recWfDraw();
    };
    audio.onended = () => { if (playBtn) playBtn.textContent = '▶'; recWfDraw(); };
  }

  // Wire canvas events
  const canvas = document.getElementById('rec-wf-canvas');
  if (canvas) {
    canvas.onmousedown = e => recWfMouseDown(e);
    canvas.onmousemove = e => recWfMouseMove(e);
    canvas.onmouseup   = e => recWfMouseUp(e);
    canvas.onmouseleave = e => { if (_recWf?.dragging) recWfMouseUp(e); };
  }

  const statusEl = document.getElementById('rec-wf-status');
  if (statusEl) statusEl.textContent = 'Loading waveform…';
  const ctx2d = canvas?.getContext('2d');
  if (ctx2d && canvas) ctx2d.clearRect(0, 0, canvas.width, canvas.height);

  let result;
  try {
    result = await fetch(`/api/recordings/${rid}/peaks?n=800`).then(r => r.json());
  } catch {
    if (statusEl) statusEl.textContent = 'Failed to load waveform.';
    return;
  }

  _recWf.peaks = result.peaks;
  _recWf.duration = result.duration;
  if (statusEl) statusEl.textContent = '';

  const restoreRow = document.getElementById('rec-wf-restore-row');
  if (restoreRow) restoreRow.style.display = result.has_backup ? '' : 'none';

  if (canvas) {
    canvas.width  = canvas.offsetWidth  || 600;
    canvas.height = canvas.offsetHeight || 80;
  }
  recWfDraw();
}

function recWfHide() {
  const panel = document.getElementById('rec-waveform-panel');
  if (panel) panel.style.display = 'none';
  const audio = document.getElementById('rec-wf-audio');
  if (audio) { audio.pause(); audio.src = ''; }
  _recWf = null;
}

function recWfDraw() {
  if (!_recWf?.peaks) return;
  const canvas = document.getElementById('rec-wf-canvas');
  if (!canvas) return;
  const { peaks, duration, cropStart, cropEnd } = _recWf;
  const audio = document.getElementById('rec-wf-audio');
  const W = canvas.width, H = canvas.height;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, W, H);
  const midY = H / 2;
  const csA = cropStart !== null && cropEnd !== null ? Math.min(cropStart, cropEnd) : null;
  const csB = cropStart !== null && cropEnd !== null ? Math.max(cropStart, cropEnd) : null;

  if (csA !== null) {
    ctx.fillStyle = 'rgba(99,102,241,0.13)';
    ctx.fillRect((csA / duration) * W, 0, ((csB - csA) / duration) * W, H);
  }

  const n = peaks.length;
  const barW = Math.max(1, (W / n) - 0.5);
  for (let i = 0; i < n; i++) {
    const x = (i / n) * W;
    const t = (i / n) * duration;
    const inSel = csA !== null && t >= csA && t <= csB;
    const barH = peaks[i] * H * 0.88;
    ctx.fillStyle = inSel ? '#818cf8' : '#3a3a4e';
    ctx.fillRect(x, midY - barH / 2, barW, barH);
  }

  if (cropStart !== null) {
    const x = (cropStart / duration) * W;
    ctx.strokeStyle = '#6366f1'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
    ctx.fillStyle = '#6366f1';
    ctx.beginPath(); ctx.moveTo(x - 5, 0); ctx.lineTo(x + 5, 0); ctx.lineTo(x, 10); ctx.fill();
  }
  if (cropEnd !== null) {
    const x = (cropEnd / duration) * W;
    ctx.strokeStyle = '#6366f1'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
    ctx.fillStyle = '#6366f1';
    ctx.beginPath(); ctx.moveTo(x - 5, H); ctx.lineTo(x + 5, H); ctx.lineTo(x, H - 10); ctx.fill();
  }

  if (audio && isFinite(audio.duration) && audio.duration > 0) {
    const cx = (audio.currentTime / audio.duration) * W;
    ctx.strokeStyle = 'rgba(240,240,245,0.7)'; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(cx, 0); ctx.lineTo(cx, H); ctx.stroke();
  }
}

function recWfMouseDown(e) {
  if (!_recWf) return;
  e.preventDefault();
  const canvas = document.getElementById('rec-wf-canvas');
  const rect = canvas.getBoundingClientRect();
  const t = Math.max(0, Math.min(_recWf.duration, ((e.clientX - rect.left) / canvas.width) * _recWf.duration));
  const snap = (10 / canvas.width) * _recWf.duration;
  if (_recWf.cropStart !== null && Math.abs(t - _recWf.cropStart) < snap) { _recWf.dragging = 'start'; return; }
  if (_recWf.cropEnd   !== null && Math.abs(t - _recWf.cropEnd)   < snap) { _recWf.dragging = 'end';   return; }
  _recWf.cropStart = t; _recWf.cropEnd = null; _recWf.dragging = 'end';
  recWfUpdateInfo(); recWfDraw();
}

function recWfMouseMove(e) {
  if (!_recWf?.dragging) return;
  const canvas = document.getElementById('rec-wf-canvas');
  const rect = canvas.getBoundingClientRect();
  const t = Math.max(0, Math.min(_recWf.duration, ((e.clientX - rect.left) / canvas.width) * _recWf.duration));
  if (_recWf.dragging === 'start') _recWf.cropStart = t; else _recWf.cropEnd = t;
  recWfUpdateInfo(); recWfDraw();
}

function recWfMouseUp(e) {
  if (!_recWf) return;
  // Single click without drag → seek audio instead of creating selection
  if (_recWf.dragging === 'end' && _recWf.cropEnd === null) {
    const canvas = document.getElementById('rec-wf-canvas');
    const rect = canvas.getBoundingClientRect();
    const audio = document.getElementById('rec-wf-audio');
    if (audio && isFinite(audio.duration)) {
      audio.currentTime = Math.max(0, Math.min(_recWf.duration, ((e.clientX - rect.left) / canvas.width) * _recWf.duration));
    }
    _recWf.cropStart = null;
  }
  _recWf.dragging = null;
  recWfUpdateInfo(); recWfDraw();
}

function recWfUpdateInfo() {
  const infoEl  = document.getElementById('rec-wf-info');
  const clearEl = document.getElementById('rec-wf-clear');
  const btn     = document.getElementById('rec-wf-crop-btn');
  if (!infoEl) return;
  const ready = _recWf?.cropStart !== null && _recWf?.cropEnd !== null;
  if (ready) {
    const a = Math.min(_recWf.cropStart, _recWf.cropEnd);
    const b = Math.max(_recWf.cropStart, _recWf.cropEnd);
    infoEl.textContent = `${_wfFmtT(a)} → ${_wfFmtT(b)}  (${_wfFmtT(b - a)} kept)`;
    if (clearEl) clearEl.style.display = '';
    if (btn)     btn.disabled = false;
  } else {
    infoEl.textContent = 'Drag to select the region to keep';
    if (clearEl) clearEl.style.display = 'none';
    if (btn)     btn.disabled = true;
  }
}

function recWfClear() {
  if (_recWf) { _recWf.cropStart = null; _recWf.cropEnd = null; }
  recWfUpdateInfo(); recWfDraw();
}

async function recWfCrop() {
  if (!_recWf || _recWf.cropStart === null || _recWf.cropEnd === null) return;
  const rid = _recWf.rid;
  const a = Math.min(_recWf.cropStart, _recWf.cropEnd);
  const b = Math.max(_recWf.cropStart, _recWf.cropEnd);
  if (!confirm(`Keep only ${_wfFmtT(a)} → ${_wfFmtT(b)}?\n\nThe original will be kept as a backup so you can restore it.`)) return;
  const btn = document.getElementById('rec-wf-crop-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Cropping…'; }
  const resp = await fetch(`/api/recordings/${rid}/crop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ start: a, end: b }),
  });
  if (!resp.ok) {
    alert('Crop failed: ' + (await resp.text()));
    if (btn) { btn.disabled = false; btn.textContent = 'Crop to selection'; }
    return;
  }
  const audio = document.getElementById('rec-wf-audio');
  if (audio) { audio.pause(); audio.src = ''; }
  _recWf = null;
  await refreshCardFromServer(rid);
  recWfShow(rid);
}

async function recWfRestore() {
  if (!_recWf) return;
  const rid = _recWf.rid;
  if (!confirm('Restore the original (pre-crop) audio? The current version will be overwritten.')) return;
  const btn = document.querySelector('#rec-wf-restore-row .act-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Restoring…'; }
  const resp = await fetch(`/api/recordings/${rid}/restore`, { method: 'POST' });
  if (!resp.ok) {
    alert('Restore failed: ' + (await resp.text()));
    if (btn) { btn.disabled = false; btn.textContent = '↩ Restore original'; }
    return;
  }
  const audio = document.getElementById('rec-wf-audio');
  if (audio) { audio.pause(); audio.src = ''; }
  _recWf = null;
  await refreshCardFromServer(rid);
  recWfShow(rid);
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

// ── Calendar ──────────────────────────────────────────────────────────────────

function calFmtTime(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

async function calLoad() {
  const list   = document.getElementById('cal-list');
  const status = document.getElementById('cal-status-bar');
  list.innerHTML = '<div class="cal-empty">Loading…</div>';
  status.textContent = '';

  let data;
  try {
    data = await (await fetch('/api/calendar/events?days=7')).json();
  } catch {
    list.innerHTML = '<div class="cal-error">Could not reach the server.</div>';
    return;
  }

  if (!data.ok) {
    list.innerHTML = `<div class="cal-error">${esc(data.error || 'Could not connect to Outlook.')}<br><button class="act-btn" style="margin-top:8px" onclick="calLoad()">Retry</button></div>`;
    return;
  }

  const events = data.events || [];
  const now    = new Date();

  const today    = now.toDateString();
  const tomorrow = new Date(now.getTime() + 86400000).toDateString();

  status.textContent = `${events.length} event${events.length !== 1 ? 's' : ''} this week · updated ${now.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}`;

  if (!events.length) {
    list.innerHTML = '<div class="cal-empty">No events this week.</div>';
    return;
  }

  list.innerHTML = '';

  let lastDay = null;
  events.forEach(ev => {
    const start = new Date(ev.start);
    const end   = new Date(ev.end);
    const day   = start.toDateString();

    if (day !== lastDay) {
      lastDay = day;
      const sep = document.createElement('div');
      sep.className = 'cal-day-sep';
      sep.textContent = day === today ? 'Today' : day === tomorrow ? 'Tomorrow' : start.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
      list.appendChild(sep);
    }

    const isNow  = now >= start && now < end;
    const isPast = now >= end;
    const isSoon = !isNow && !isPast && (start - now) < 30 * 60 * 1000;

    const card = document.createElement('div');
    card.className = 'cal-event' + (isNow ? ' is-now' : isPast ? ' is-past' : '');

    const badges = [];
    if (isNow)        badges.push(`<span class="cal-event-badge cal-badge-now">Now</span>`);
    else if (isSoon)  badges.push(`<span class="cal-event-badge cal-badge-soon">Soon</span>`);
    if (ev.is_online) badges.push(`<span class="cal-event-badge cal-badge-online">Online</span>`);

    const meta = [];
    if (ev.location) meta.push(esc(ev.location));
    if (ev.organizer) meta.push(`by ${esc(ev.organizer)}`);

    card.innerHTML = `
      <div class="cal-event-time">
        <span class="cal-time-start">${calFmtTime(ev.start)}</span>
        <span class="cal-time-end">${calFmtTime(ev.end)}</span>
      </div>
      <div class="cal-event-body">
        <div class="cal-event-subject">${esc(ev.subject)}</div>
        <div class="cal-event-meta">
          ${badges.join('')}
          ${meta.map(m => `<span>${m}</span>`).join('')}
        </div>
      </div>`;
    list.appendChild(card);
  });
}

function calInit() {
  const dateEl = document.getElementById('cal-date');
  if (dateEl) {
    const now  = new Date();
    const end  = new Date(now.getTime() + 6 * 86400000);
    const fmt  = { month: 'short', day: 'numeric' };
    dateEl.textContent = `${now.toLocaleDateString(undefined, fmt)} – ${end.toLocaleDateString(undefined, { ...fmt, year: 'numeric' })}`;
  }
  calLoad();
}

// ── OneNote ───────────────────────────────────────────────────────────────────

async function onTestNote() {
  const btn = document.getElementById('on-test-btn');
  const result = document.getElementById('on-result');
  btn.disabled = true;
  btn.textContent = 'Creating…';
  result.style.display = 'none';
  result.className = 'on-result';
  try {
    const res = await fetch('/api/onenote/test', { method: 'POST' });
    const data = await res.json();
    result.style.display = '';
    if (res.ok && data.ok) {
      result.classList.add('on-result--ok');
      result.textContent = 'Page created successfully in OneNote.';
    } else {
      result.classList.add('on-result--err');
      result.textContent = 'Error: ' + (data.detail || data.error || 'Unknown error');
    }
  } catch (e) {
    result.style.display = '';
    result.classList.add('on-result--err');
    result.textContent = 'Request failed: ' + e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Create Hello World note';
  }
}

async function loadOneNoteHierarchy() {
  const btn = document.getElementById('on-browse-btn');
  const container = document.getElementById('on-hierarchy');
  btn.disabled = true;
  btn.textContent = 'Loading…';
  container.innerHTML = '';
  try {
    const res = await fetch('/api/onenote/hierarchy');
    const data = await res.json();
    if (!res.ok || !data.ok) {
      container.innerHTML = `<div class="on-result on-result--err">Error: ${data.detail || data.error || 'Unknown error'}</div>`;
      return;
    }
    container.innerHTML = renderOneNoteTree(data.notebooks || []);
  } catch (e) {
    container.innerHTML = `<div class="on-result on-result--err">Request failed: ${e.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Refresh';
  }
}

function renderOneNoteTree(notebooks) {
  notebooks = [].concat(notebooks || []);
  if (!notebooks.length) return '<p class="on-empty">No notebooks found. Make sure OneNote is open.</p>';
  return notebooks.map(nb => {
    const sections = [].concat(nb.sections || []).map(sec => {
      const secPages = [].concat(sec.pages || []);
      const pages = secPages.map(p => {
        const ts = p.modified ? new Date(p.modified).toLocaleDateString() : '';
        return `<li class="on-page"><span class="on-page-name">${escHtml(p.name)}</span>${ts ? `<span class="on-page-date">${ts}</span>` : ''}</li>`;
      }).join('');
      return `
        <details class="on-section">
          <summary class="on-section-summary">
            ${sec.color ? `<span class="on-section-dot" style="background:${escHtml(sec.color)}"></span>` : ''}
            ${escHtml(sec.name)} <span class="on-section-count">${secPages.length}</span>
          </summary>
          ${pages ? `<ul class="on-pages">${pages}</ul>` : '<p class="on-empty">No pages</p>'}
        </details>`;
    }).join('');
    return `
      <details class="on-notebook" open>
        <summary class="on-notebook-summary">${escHtml(nb.name)}</summary>
        <div class="on-sections">${sections || '<p class="on-empty">No sections</p>'}</div>
      </details>`;
  }).join('');
}

function escHtml(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Focus Time ────────────────────────────────────────────────────────────────

const FT = {
  active: false,
  sessionId: null,
  sessionStartMs: null,
  tickInterval: null,
  tickCount: 0,
  sessions: [],
  lastActivityMs: Date.now(),
  appEvents: [],
  idleThresholdMs: 5 * 60 * 1000,
};

function initFocusTime() {
  const bump = () => { FT.lastActivityMs = Date.now(); };
  document.addEventListener('mousemove', bump);
  document.addEventListener('keydown', bump);
  document.addEventListener('mousedown', bump);
  FT.tickInterval = setInterval(ftTick, 1000);
  ftFetchCurrentSession();
}

async function ftFetchCurrentSession() {
  try {
    const sess = await api('GET', '/api/work-sessions/current');
    if (sess && sess.id) {
      FT.sessionId = sess.id;
      FT.sessionStartMs = new Date(sess.start_time).getTime();
      FT.active = true;
      window.electronAPI?.setFocusState(true);
    }
  } catch (e) {}
}

async function ftCheckIdle() {
  const isIdle = (Date.now() - FT.lastActivityMs) > FT.idleThresholdMs;
  if (isIdle && FT.active) {
    FT.active = false;
    if (FT.sessionId) {
      try { await api('PATCH', `/api/work-sessions/${FT.sessionId}/end`); } catch (e) {}
      FT.sessionId = null;
      FT.sessionStartMs = null;
    }
    window.electronAPI?.setFocusState(false);
    await ftLoadSessions();
  } else if (!isIdle && (!FT.active || !FT.sessionStartMs)) {
    FT.active = true;
    try {
      const sess = await api('POST', '/api/work-sessions');
      FT.sessionId = sess.id;
      FT.sessionStartMs = new Date(sess.start_time).getTime();
    } catch (e) { FT.active = false; }
    window.electronAPI?.setFocusState(true);
  }
}

async function ftTick() {
  FT.tickCount++;
  await ftCheckIdle();
  ftUpdateUI();
  ftUpdateTodaySummary();
  if (FT.tickCount % 10 === 0) ftRenderSessions();
  if (FT.tickCount % 60 === 0) await ftLoadSessions();
}

const CIRCUMFERENCE    = 326.73;
const SESSION_MAX_SECS = 5400; // 90 minutes per round
const ROUND_COLORS = ['#22c55e', '#6366f1', '#f59e0b', '#2dd4bf', '#f472b6', '#fb923c'];

function ftUpdateUI() {
  const dot    = document.getElementById('ft-dot');
  const navDot = document.getElementById('nav-ft-dot');
  const label  = document.getElementById('ft-status-label');
  const hint   = document.getElementById('ft-idle-hint');
  const timer  = document.getElementById('ft-timer');
  const ring   = document.getElementById('ft-ring-fill');
  if (!dot) return;

  const startEl   = document.getElementById('ft-session-start');
  const lapDotsEl = document.getElementById('ft-lap-dots');

  if (FT.active && FT.sessionStartMs) {
    dot.classList.add('active');
    if (navDot) navDot.classList.add('active');
    if (label)  { label.textContent = 'Working'; label.classList.add('active'); }
    if (timer)  timer.classList.add('active');
    if (hint)   hint.textContent = '';

    const secs      = Math.floor((Date.now() - FT.sessionStartMs) / 1000);
    const round     = Math.floor(secs / SESSION_MAX_SECS);
    const roundSecs = secs % SESSION_MAX_SECS;
    const color     = ROUND_COLORS[round % ROUND_COLORS.length];

    if (timer) timer.textContent = fmtDuration(secs);
    if (ring) {
      ring.style.stroke        = color;
      ring.style.filter        = `drop-shadow(0 0 8px ${color}70)`;
      ring.style.strokeDashoffset = CIRCUMFERENCE * (1 - roundSecs / SESSION_MAX_SECS);
      ring.classList.add('active');
    }
    if (startEl) {
      const d = new Date(FT.sessionStartMs);
      const hh = String(d.getHours()).padStart(2, '0');
      const mm = String(d.getMinutes()).padStart(2, '0');
      startEl.textContent = `Started at ${hh}:${mm}`;
    }
    if (lapDotsEl) {
      lapDotsEl.innerHTML = round === 0 ? '' : Array.from({ length: round }, (_, i) =>
        `<span class="ft-lap-dot" style="background:${ROUND_COLORS[i % ROUND_COLORS.length]}"></span>`
      ).join('');
    }
  } else {
    dot.classList.remove('active');
    if (navDot) navDot.classList.remove('active');
    if (label)  { label.textContent = 'Idle'; label.classList.remove('active'); }
    if (timer)  { timer.textContent = '–'; timer.classList.remove('active'); }
    if (ring) {
      ring.style.strokeDashoffset = CIRCUMFERENCE;
      ring.style.stroke           = '';
      ring.style.filter           = '';
      ring.classList.remove('active');
    }
    if (startEl)   startEl.textContent = '';
    if (lapDotsEl) lapDotsEl.innerHTML = '';
    if (hint) {
      const idleSecs = Math.floor((Date.now() - FT.lastActivityMs) / 1000);
      hint.textContent = idleSecs > FT.idleThresholdMs / 1000
        ? `Away for ${fmtDuration(idleSecs)}`
        : 'Move mouse or press a key to start';
    }
  }
}

async function ftClearToday() {
  const today = new Date().toISOString().slice(0, 10);
  await api('DELETE', `/api/work-sessions?date=${today}`);
  // Wipe cleared the active session too — start a fresh one
  const sess = await api('POST', '/api/work-sessions');
  FT.sessionId = sess.id;
  FT.sessionStartMs = new Date(sess.start_time).getTime();
  FT.active = true;
  FT.lastActivityMs = Date.now();
  FT.sessions = [];
  ftRenderTimeline();
  ftRenderSessions();
  ftUpdateTodaySummary();
}

async function ftLoadSessions() {
  try {
    const today = new Date().toISOString().slice(0, 10);
    [FT.sessions, FT.appEvents] = await Promise.all([
      api('GET', `/api/work-sessions?date=${today}`),
      api('GET', `/api/app-events?date=${today}`),
    ]);
    ftRenderTimeline();
    ftRenderSessions();
    ftUpdateTodaySummary();
  } catch (e) {
    // silently ignore
  }
}

function ftRenderTimeline() {
  const el = document.getElementById('ft-timeline');
  if (!el) return;
  el.style.display = '';

  const now = new Date();
  const d   = now;
  const windowStart = new Date(d.getFullYear(), d.getMonth(), d.getDate(), 7, 0, 0);
  const windowEnd   = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1, 0, 0, 0);
  const totalMs     = windowEnd - windowStart;

  const clamp = t => Math.max(windowStart.getTime(), Math.min(windowEnd.getTime(), t));
  const pct   = t => ((clamp(t) - windowStart.getTime()) / totalMs * 100).toFixed(3) + '%';

  // Session blocks
  let segHtml = '';
  for (const s of FT.sessions) {
    if (!s.start_time) continue;
    const sStart = new Date(s.start_time);
    const sEnd   = s.end_time ? new Date(s.end_time) : now;
    if (sEnd < windowStart || sStart > windowEnd) continue;
    const left  = pct(sStart.getTime());
    const width = ((clamp(sEnd.getTime()) - clamp(sStart.getTime())) / totalMs * 100).toFixed(3) + '%';
    const isActive = !s.end_time;
    const dur = s.duration_seconds ? fmtDuration(s.duration_seconds) : (isActive ? fmtDuration(Math.floor((now - sStart) / 1000)) : '');
    segHtml += `<div class="ft-tl-seg${isActive ? ' ft-tl-seg--active' : ''}"
      style="left:${left};width:${width}"
      title="${fmtTimeOnly(sStart.toISOString())} → ${isActive ? 'now' : fmtTimeOnly(sEnd.toISOString())} · ${dur}"></div>`;
  }

  // "Now" needle
  const nowHtml = (now >= windowStart && now <= windowEnd)
    ? `<div class="ft-tl-now" style="left:${pct(now.getTime())}"></div>`
    : '';

  // Hour ticks + labels
  let tickHtml = '', labelHtml = '';
  for (let h = 7; h < 24; h += 2) {
    const t = new Date(d.getFullYear(), d.getMonth(), d.getDate(), h, 0, 0);
    const p = pct(t.getTime());
    const lbl = h === 0 ? '12am' : h === 12 ? '12pm' : h > 12 ? `${h - 12}pm` : `${h}am`;
    tickHtml  += `<div class="ft-tl-tick" style="left:${p}"></div>`;
    labelHtml += `<div class="ft-tl-tick-label" style="left:${p}">${lbl}</div>`;
  }

  el.innerHTML = `
    <div class="ft-tl-bar">${tickHtml}${segHtml}${nowHtml}</div>
    <div class="ft-tl-ticks-row">${labelHtml}</div>`;
}

function ftUpdateTodaySummary() {
  const totalEl   = document.getElementById('ft-today-total');
  const countEl   = document.getElementById('ft-today-count');
  const longestEl = document.getElementById('ft-today-longest');
  if (!totalEl) return;
  const completed   = FT.sessions.filter(s => s.duration_seconds != null);
  const activeSecs  = FT.sessionStartMs ? Math.floor((Date.now() - FT.sessionStartMs) / 1000) : 0;
  const longestSecs = completed.length ? Math.max(...completed.map(s => s.duration_seconds)) : 0;
  const totalSecs   = completed.reduce((sum, s) => sum + s.duration_seconds, 0) + activeSecs;
  totalEl.textContent   = totalSecs > 0 ? fmtDuration(totalSecs) : '–';
  if (countEl)   countEl.textContent   = completed.length + (FT.sessionId ? 1 : 0);
  if (longestEl) longestEl.textContent = longestSecs ? fmtDuration(longestSecs) : '–';
}

function ftRenderSessions() {
  const el = document.getElementById('ft-sessions-list');
  if (!el) return;
  if (!FT.sessions.length) { el.innerHTML = '<p class="ft-empty">No sessions today.</p>'; return; }

  const sorted = [...FT.sessions].filter(s => s.start_time)
    .sort((a, b) => new Date(b.start_time) - new Date(a.start_time));
  const completed = sorted.filter(s => s.duration_seconds != null);
  const maxDur = completed.length ? Math.max(...completed.map(s => s.duration_seconds)) : 1;

  let html = '<div class="ft-sessions">';
  for (let i = 0; i < sorted.length; i++) {
    const s = sorted[i];
    const isActive = !s.end_time;
    const start = fmtTimeOnly(s.start_time);
    const end   = s.end_time ? fmtTimeOnly(s.end_time) : 'now';
    const activeSecs = isActive && FT.sessionStartMs ? Math.floor((Date.now() - FT.sessionStartMs) / 1000) : null;
    const dur   = s.duration_seconds != null ? fmtDuration(s.duration_seconds) : (activeSecs !== null ? fmtDuration(activeSecs) : '–');
    const durForPct = s.duration_seconds ?? activeSecs ?? 0;
    const pct   = Math.round((durForPct / maxDur) * 100);
    html += `<div class="ft-session-row${isActive ? ' ft-session-row--active' : ''}">
      <div class="ft-session-meta">
        <span class="ft-session-range">${esc(start)} → ${esc(end)}</span>
        <span class="ft-session-dur">${esc(dur)}</span>
      </div>
      <div class="ft-session-bar-wrap">
        <div class="ft-session-bar" style="width:${pct}%"></div>
      </div>
    </div>`;

    // Gap to previous session (list is newest-first)
    if (i < sorted.length - 1 && s.start_time) {
      const prev       = sorted[i + 1];
      if (!prev.start_time) continue;
      const prevEndMs  = prev.end_time ? new Date(prev.end_time).getTime() : null;
      const gapEndMs   = new Date(s.start_time).getTime();
      const gapStartMs = prevEndMs ?? new Date(prev.start_time).getTime();
      const gapSecs    = Math.round((gapEndMs - gapStartMs) / 1000);

      if (gapSecs >= 5) {
        // App was closed if any recorded start event falls inside this gap window
        const appWasClosed = FT.appEvents.some(e => {
          const ts = new Date(e.timestamp).getTime();
          return ts > gapStartMs && ts <= gapEndMs;
        });
        const fmt      = gapSecs < 60 ? fmtTimeSec : fmtTimeOnly;
        const gapStart = fmt(prev.end_time || prev.start_time);
        const gapEnd   = fmt(s.start_time);
        const icon         = appWasClosed
          ? `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18.36 6.64A9 9 0 1 1 5.64 5.64"/><line x1="12" y1="2" x2="12" y2="12"/></svg>`
          : `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg>`;
        const label        = appWasClosed ? 'App not running' : 'Away from desk';
        html += `<div class="ft-gap-row${appWasClosed ? ' ft-gap-row--closed' : ''}">
          <span class="ft-gap-icon">${icon}</span>
          <span class="ft-gap-times">${esc(gapStart)} – ${esc(gapEnd)}</span>
          <span class="ft-gap-label">${esc(label)}</span>
          <span class="ft-gap-dur">${fmtDuration(gapSecs)}</span>
        </div>`;
      }
    }
  }
  html += '</div>';
  el.innerHTML = html;
}

function fmtHours(secs) {
  if (!secs) return '0m';
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

function ftShowTab(tab) {
  const todayPanel = document.getElementById('ft-panel-today');
  const statsPanel = document.getElementById('ft-panel-stats');
  const todayBtn   = document.getElementById('ft-tab-today');
  const statsBtn   = document.getElementById('ft-tab-stats');
  if (!todayPanel) return;
  todayPanel.style.display = tab === 'today' ? '' : 'none';
  statsPanel.style.display = tab === 'stats' ? '' : 'none';
  todayBtn?.classList.toggle('active', tab === 'today');
  statsBtn?.classList.toggle('active', tab === 'stats');
  // Sync sidebar sub-buttons
  document.getElementById('nav-ft-today-btn')?.classList.toggle('active', tab === 'today');
  document.getElementById('nav-ft-stats-btn')?.classList.toggle('active', tab === 'stats');
  if (tab === 'stats') ftLoadStats();
}

function showFocusStats() {
  showPage('focustime');
  ftShowTab('stats');
}

async function ftLoadStats() {
  try {
    const all = await api('GET', '/api/work-sessions');

    // Group completed sessions by date
    const byDate = {};
    for (const s of all) {
      if (!s.date || !s.duration_seconds) continue;
      byDate[s.date] = (byDate[s.date] || 0) + s.duration_seconds;
    }

    // Add current active session to today
    const now = new Date();
    const today = now.toISOString().slice(0, 10);
    if (FT.active && FT.sessionStartMs) {
      byDate[today] = (byDate[today] || 0) + Math.floor((Date.now() - FT.sessionStartMs) / 1000);
    }

    // Build last 30 days (oldest → newest)
    const days = [];
    for (let i = 29; i >= 0; i--) {
      const d = new Date(now);
      d.setDate(d.getDate() - i);
      const key = d.toISOString().slice(0, 10);
      days.push({ date: key, secs: byDate[key] || 0, dow: d.getDay(), d });
    }

    // Week boundaries (Monday-based)
    const dow = now.getDay();
    const weekStart = new Date(now);
    weekStart.setDate(now.getDate() - ((dow + 6) % 7));
    weekStart.setHours(0, 0, 0, 0);
    const weekStartStr = weekStart.toISOString().slice(0, 10);

    const lastWeekStart = new Date(weekStart);
    lastWeekStart.setDate(weekStart.getDate() - 7);
    const lastWeekEnd = new Date(weekStart);
    lastWeekEnd.setDate(weekStart.getDate() - 1);
    const lastWeekStartStr = lastWeekStart.toISOString().slice(0, 10);
    const lastWeekEndStr   = lastWeekEnd.toISOString().slice(0, 10);

    let weekTotal = 0, lastWeekTotal = 0, bestSecs = 0;
    for (const [date, secs] of Object.entries(byDate)) {
      if (date >= weekStartStr && date <= today) weekTotal += secs;
      if (date >= lastWeekStartStr && date <= lastWeekEndStr) lastWeekTotal += secs;
      if (secs > bestSecs) bestSecs = secs;
    }

    const activeDays = days.filter(d => d.secs > 0);
    const avg30 = activeDays.length
      ? days.reduce((s, d) => s + d.secs, 0) / activeDays.length
      : 0;

    // KPIs
    document.getElementById('fts-week-total').textContent     = fmtHours(weekTotal);
    document.getElementById('fts-lastweek-total').textContent = fmtHours(lastWeekTotal);
    document.getElementById('fts-avg-day').textContent        = avg30 ? fmtHours(avg30) : '–';
    document.getElementById('fts-best-day').textContent       = bestSecs ? fmtHours(bestSecs) : '–';

    // Bar chart
    const maxSecs = Math.max(...days.map(d => d.secs), 1);
    const barsEl  = document.getElementById('fts-bars');
    const labelsEl = document.getElementById('fts-bars-labels');
    if (!barsEl) return;

    barsEl.innerHTML = days.map(({ date, secs, dow, d }) => {
      const pct = secs > 0 ? Math.max(Math.round((secs / maxSecs) * 100), 4) : 0;
      const isWeekend = dow === 0 || dow === 6;
      const isToday   = date === today;
      const label     = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
      const tip = `${label}: ${fmtHours(secs)}`;
      let cls = 'fts-bar';
      if (isWeekend) cls += ' fts-bar--weekend';
      if (secs === 0) cls += ' fts-bar--empty';
      return `<div class="fts-bar-col${isToday ? ' fts-bar-col--today' : ''}" title="${tip}">
        <div class="${cls}" style="height:${pct}%"></div>
      </div>`;
    }).join('');

    if (labelsEl) {
      labelsEl.innerHTML = days.map(({ d }, i) => {
        const show = (29 - i) % 7 === 0;
        const label = show ? d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '';
        return `<div class="fts-bar-daylabel">${label}</div>`;
      }).join('');
    }

    // ── Timeline chart ──────────────────────────────────────────────
    const TL_DAYS    = 14;
    const TL_H_START = 7;
    const TL_H_END   = 22;
    const TL_H_RANGE = TL_H_END - TL_H_START;

    const toLocalKey = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    const localToday = toLocalKey(now);

    const sessionsByDate = {};
    for (const s of all) {
      if (!s.start_time || !s.duration_seconds) continue;
      const key = toLocalKey(new Date(s.start_time));
      (sessionsByDate[key] = sessionsByDate[key] || []).push(s);
    }

    const tlDays = [];
    for (let i = TL_DAYS - 1; i >= 0; i--) {
      const d = new Date(now); d.setDate(d.getDate() - i); tlDays.push(d);
    }

    const ticks    = [8, 10, 12, 14, 16, 18, 20];
    const yAxisEl  = document.getElementById('fts-tl-yaxis');
    const tlBodyEl = document.getElementById('fts-tl-body');
    const tlXEl    = document.getElementById('fts-tl-xlabels');
    if (!yAxisEl || !tlBodyEl) return;

    yAxisEl.innerHTML = ticks.map(h => {
      const pct = ((h - TL_H_START) / TL_H_RANGE) * 100;
      return `<div class="fts-tl-ylabel" style="top:${pct.toFixed(1)}%">${h}:00</div>`;
    }).join('');

    const gridLines = ticks.map(h => {
      const pct = ((h - TL_H_START) / TL_H_RANGE) * 100;
      return `<div class="fts-tl-gridline" style="top:${pct.toFixed(1)}%"></div>`;
    }).join('');

    const dayCols = tlDays.map(d => {
      const key      = toLocalKey(d);
      const sessions = (sessionsByDate[key] || []).slice().sort((a, b) => a.start_time.localeCompare(b.start_time));
      const isToday  = key === localToday;
      const isWkd    = d.getDay() === 0 || d.getDay() === 6;

      const blocks = sessions.map(s => {
        const dt       = new Date(s.start_time);
        const startMin = dt.getHours() * 60 + dt.getMinutes();
        const topPct   = ((startMin - TL_H_START * 60) / (TL_H_RANGE * 60)) * 100;
        const hPct     = (s.duration_seconds / 60 / (TL_H_RANGE * 60)) * 100;
        const t        = Math.max(0, Math.min(99, topPct));
        const h        = Math.max(0.8, Math.min(100 - t, hPct));
        const timeStr  = dt.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
        const tip      = `${timeStr} · ${fmtHours(s.duration_seconds)}`;
        return `<div class="fts-tl-block${isWkd ? ' fts-tl-block--wkd' : ''}" style="top:${t.toFixed(1)}%;height:${h.toFixed(1)}%" title="${tip}"></div>`;
      }).join('');

      let cls = 'fts-tl-col';
      if (isToday) cls += ' fts-tl-col--today';
      if (isWkd)   cls += ' fts-tl-col--wkd';
      return `<div class="${cls}">${blocks}</div>`;
    }).join('');

    tlBodyEl.innerHTML = gridLines + `<div class="fts-tl-cols">${dayCols}</div>`;

    if (tlXEl) {
      tlXEl.innerHTML = tlDays.map((d, i) => {
        const lbl = i % 2 === 0 ? d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '';
        return `<div class="fts-tl-xlabel">${lbl}</div>`;
      }).join('');
    }
  } catch (e) {}
}

// ── Diario (Home page) ────────────────────────────────────────────────────────

const DIARIO = { meetings: [], allEvents: null, clockInterval: null };
let _dState = null;
let _dViewDate = null;   // Date object (midnight local), null = uninitialised
let _dDragSec = null, _dDragItemId = null, _dDragSecId = null;

const DIARIO_DEFAULT_SECTIONS = [
  { id: 's_naturgy',  emoji: '⚡', name: 'Naturgy',            items: [] },
  { id: 's_engie',    emoji: '🔥', name: 'ENGIE',              items: [] },
  { id: 's_vega',     emoji: '🌌', name: 'Vega',               items: [] },
  { id: 's_salvador', emoji: '🏠', name: 'Salvador Madariaga', items: [] },
  { id: 's_personal', emoji: '🙋', name: 'Personal',           items: [] },
  { id: 's_other',    emoji: '📁', name: 'other',              items: [] },
];

const _SEC_COLORS = {
  's_naturgy':  '#f59e0b',
  's_engie':    '#ef4444',
  's_vega':     '#8b5cf6',
  's_salvador': '#22c55e',
  's_personal': '#6366f1',
  's_other':    '#6b7280',
};
const _SEC_PALETTE = ['#6366f1','#f59e0b','#8b5cf6','#22c55e','#ef4444','#3b82f6','#ec4899','#14b8a6'];
function dSecColor(id) {
  if (_SEC_COLORS[id]) return _SEC_COLORS[id];
  const hash = id.split('').reduce((s, c) => s + c.charCodeAt(0), 0);
  return _SEC_PALETTE[hash % _SEC_PALETTE.length];
}

function dId() { return Math.random().toString(36).slice(2, 9); }

function _dDateStr(d) {
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}
function _dMidnight(d) { const r = new Date(d); r.setHours(0,0,0,0); return r; }
function _dIsToday(d) { return _dDateStr(d) === _dDateStr(new Date()); }

async function dLoadState() {
  const dateStr = _dDateStr(_dViewDate);
  const isToday = _dIsToday(_dViewDate);
  // Today: try API first (for MCP interop), then localStorage
  if (isToday) {
    try {
      const data = await api('GET', '/api/diario');
      if (data.state && Array.isArray(data.state.sections)) return data.state;
    } catch (_) {}
  }
  // All dates: localStorage per-day key
  try {
    const raw = localStorage.getItem(`diario_state_${dateStr}`);
    if (raw) {
      const s = JSON.parse(raw);
      if (Array.isArray(s.sections)) return s;
    }
    // one-time migration from old single-key format
    if (isToday) {
      const old = localStorage.getItem('diario_state');
      if (old) { const s = JSON.parse(old); if (s.date === dateStr && Array.isArray(s.sections)) return s; }
    }
  } catch (_) {}
  return { date: dateStr, sections: DIARIO_DEFAULT_SECTIONS.map(s => ({ ...s, items: [] })) };
}

async function dPersist() {
  if (!_dState || !_dViewDate) return;
  const dateStr = _dDateStr(_dViewDate);
  _dState.date  = dateStr;
  try { localStorage.setItem(`diario_state_${dateStr}`, JSON.stringify(_dState)); } catch (_) {}
  if (_dIsToday(_dViewDate)) {
    try { await api('PUT', '/api/diario', { state: _dState }); } catch (_) {}
  }
}

function dFilterToViewDate() {
  const d0 = _dMidnight(_dViewDate);
  const d1 = new Date(d0); d1.setDate(d0.getDate() + 1);
  DIARIO.meetings = (DIARIO.allEvents || []).filter(e => {
    const s = new Date(e.start);
    return s >= d0 && s < d1;
  });
}

async function dNavDate(delta) {
  _dViewDate = new Date(_dViewDate);
  _dViewDate.setDate(_dViewDate.getDate() + delta);
  _dState = await dLoadState();
  dFilterToViewDate();
  dRender();
}

async function dGoToday() {
  _dViewDate = _dMidnight(new Date());
  _dState = await dLoadState();
  dFilterToViewDate();
  dRender();
}

function dFmtTime(d) {
  return d.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });
}

function dFmtDate(d) {
  const days   = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
  const months = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  return `${days[d.getDay()]}, ${d.getDate()} ${months[d.getMonth()]}`;
}

function dWeek(d) {
  const dt  = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = dt.getUTCDay() || 7;
  dt.setUTCDate(dt.getUTCDate() + 4 - day);
  const y = new Date(Date.UTC(dt.getUTCFullYear(), 0, 1));
  return 'W' + Math.ceil((((dt - y) / 86400000) + 1) / 7);
}

function dSortItems(items) {
  const p = items.filter(i => i.priority && !i.done);
  const n = items.filter(i => !i.priority && !i.done);
  const d = items.filter(i => i.done);
  return [...p, ...n, ...d];
}

async function homeInit() {
  if (DIARIO.clockInterval) clearInterval(DIARIO.clockInterval);
  // Preserve viewed date across page navigations; reset only on first load
  if (!_dViewDate) _dViewDate = _dMidnight(new Date());
  _dState = await dLoadState();

  // Fetch 30-day event window once (refreshed each homeInit)
  try {
    const data = await api('GET', '/api/calendar/events?days=30');
    if (data.ok) {
      DIARIO.allEvents = data.events || [];
      // calEvents for recording-page suggestions = today only
      const td = _dMidnight(new Date()), tm = new Date(td); tm.setDate(td.getDate()+1);
      S.calEvents = DIARIO.allEvents.filter(e => { const s=new Date(e.start); return s>=td && s<tm; });
      _updateMeetingSuggestions(new Date());
    }
  } catch (_) { DIARIO.allEvents = []; }

  dFilterToViewDate();
  dRender();

  DIARIO.clockInterval = setInterval(() => {
    const el = document.getElementById('d-time');
    if (el) el.textContent = dFmtTime(new Date());
  }, 30000);
}

function dRender() {
  const wrap = document.getElementById('diario-wrap');
  if (!wrap) return;
  const now   = new Date();
  const today = now.toISOString().slice(0, 10);

  // ── Meetings (old card style) ──
  let mtgRows = '';
  for (const e of DIARIO.meetings) {
    const start = new Date(e.start), end = new Date(e.end);
    const isActive   = start <= now && now < end;
    const isUpcoming = start > now;
    const isPast     = end <= now;
    const cls = isActive ? 'home-mtg-active' : isUpcoming ? 'home-mtg-upcoming' : 'home-mtg-past';
    const timeStr = `${dFmtTime(start)} – ${dFmtTime(end)}`;
    let hint = '';
    if (isActive) hint = 'Now';
    else if (isUpcoming) {
      const diffMin = Math.round((start - now) / 60000);
      hint = diffMin < 60 ? `in ${diffMin}m` : '';
    }
    mtgRows += `<div class="home-mtg-row ${cls}" onclick="showPage('calendar')">
      <div class="home-mtg-dot"></div>
      <div class="home-mtg-time">${timeStr}</div>
      <div class="home-mtg-title">${esc(e.subject||'Meeting')}</div>
      ${hint ? `<div class="home-mtg-hint">${hint}</div>` : ''}
    </div>`;
  }
  const mtgHtml = mtgRows
    ? `<div class="home-mtg-card">${mtgRows}</div>`
    : `<div class="d-empty-hint">No meetings today</div>`;

  // ── Today's recordings ──
  const viewDateStr = _dDateStr(_dViewDate);
  const todayRecs = [...S.recordings]
    .filter(r => r.start_time && r.start_time.startsWith(viewDateStr))
    .sort((a, b) => (b.start_time||'').localeCompare(a.start_time||''));
  let grabsHtml = '';
  if (!todayRecs.length) {
    grabsHtml = `<div class="d-empty-hint">No recordings yet today</div>`;
  } else {
    grabsHtml = todayRecs.map(r => {
      const color = projectColor(r.project) || 'var(--border2)';
      const title = r.meeting || r.project || 'Untitled';
      const parts = [];
      if (r.project) parts.push(esc(r.project));
      if (r.duration) parts.push(fmtDuration(r.duration));
      const meta = parts.join(' · ');
      const txBadge = r.has_transcript ? '<span class="home-rec-badge">transcript</span>' : '';
      return `<div class="home-rec-row" onclick="scrollToCard(${r.id})">
        <div class="home-rec-strip" style="background:${color}"></div>
        <div class="home-rec-info">
          <div class="home-rec-title">${esc(title)}</div>
          <div class="home-rec-meta"><span>${meta}</span>${txBadge}</div>
        </div>
        <div class="home-rec-time">${dFmtTime(new Date(r.start_time))}</div>
      </div>`;
    }).join('');
  }

  // ── Pending sections ──
  const secsHtml = _dState.sections.map(sec => {
    const color   = dSecColor(sec.id);
    const sorted  = dSortItems(sec.items);
    const total   = sec.items.length;
    const done    = sec.items.filter(i => i.done).length;
    const open    = total - done;
    const pct     = total ? Math.round((done / total) * 100) : 0;

    const itemsHtml = sorted.length ? sorted.map(item => {
      const pStar = item.priority
        ? `<span class="d-star d-star-on"  onclick="dPrio('${sec.id}','${item.id}')">★</span>`
        : `<span class="d-star d-star-off" onclick="dPrio('${sec.id}','${item.id}')">★</span>`;
      return `<div class="d-row${item.done?' d-done':''}${item.priority?' d-priority':''}" id="dr-${item.id}"
               ondragover="dDragOver(event)" ondrop="dDrop(event,'${sec.id}','${item.id}')">
        <span class="d-grip" draggable="true"
              ondragstart="dDragStart(event,'${sec.id}','${item.id}')"
              ondragend="dDragEnd()">⠿</span>
        <span class="d-cb${item.done?' d-cb-on':''}" onclick="dToggle('${sec.id}','${item.id}')"></span>
        ${pStar}
        <span class="d-txt" onclick="dEdit(event,'${sec.id}','${item.id}')">${esc(item.text)}</span>
        <span class="d-del" onclick="dDelItem('${sec.id}','${item.id}')">✕</span>
      </div>`;
    }).join('') : `<div class="d-empty-sec">Nothing here yet</div>`;

    return `<div class="d-sec" id="ds-${sec.id}" style="--sec-color:${color}"
             ondragover="dDragOver(event)" ondrop="dDrop(event,'${sec.id}',null)">
      <div class="d-sh">
        <span class="d-sec-grip" draggable="true"
              ondragstart="dSecDragStart(event,'${sec.id}')"
              ondragend="dSecDragEnd()">⠿</span>
        <span class="d-emoji">${sec.emoji}</span>
        <span class="d-sname">${esc(sec.name)}</span>
        ${open > 0 ? `<span class="d-count">${open}</span>` : ''}
        <span class="d-sdel" onclick="dDelSec('${sec.id}')">✕</span>
      </div>
      <div class="d-rows" id="drows-${sec.id}">${itemsHtml}</div>
      ${done > 0 ? `<div class="d-progress"><div class="d-progress-fill" style="width:${pct}%"></div></div>` : ''}
      <div class="d-add-item" onclick="dAddItem('${sec.id}')">+ item</div>
    </div>`;
  }).join('');

  const isToday  = _dIsToday(_dViewDate);
  const isPast   = _dViewDate < _dMidnight(now);
  const dayLabel = dFmtDate(_dViewDate);
  const todayBadge = isToday
    ? `<span class="d-today-badge">Today</span>`
    : `<span class="d-goto-today" onclick="dGoToday()">Go to today</span>`;

  wrap.innerHTML = `
    <div class="d-hdr">
      <div class="d-nav-row">
        <button class="d-nav-btn" onclick="dNavDate(-1)">‹</button>
        <div class="d-nav-center">
          <div class="d-date">${dayLabel}</div>
          <div class="d-meta">
            <span>${dWeek(_dViewDate)}</span>
            ${isToday ? `<span id="d-time">${dFmtTime(now)}</span>` : ''}
            <span id="d-saved" style="display:none">· saved</span>
            ${todayBadge}
          </div>
        </div>
        <button class="d-nav-btn" onclick="dNavDate(1)" ${isToday ? 'disabled' : ''}>›</button>
      </div>
    </div>

    <div class="d-two-col">
      <div class="d-section-block">
        <div class="home-section-label">Today's meetings</div>
        ${mtgHtml}
      </div>
      <div class="d-section-block">
        <div class="home-section-label">Today's recordings</div>
        ${grabsHtml}
      </div>
    </div>

    <div class="d-divider"></div>

    <div class="d-agenda-block">
      <div class="home-section-label">Agenda</div>
      <div id="d-secs">${secsHtml}</div>
      <div id="d-add-sec-wrap">
        <span class="d-add-sec" onclick="dAddSec()">+ section</span>
      </div>
    </div>
    `;
}

function dToggle(sid, iid) {
  const sec  = _dState.sections.find(s => s.id === sid);
  const item = sec?.items.find(i => i.id === iid);
  if (!item) return;
  item.done = !item.done;
  if (item.done) item.priority = false;
  dPersist(); dRender();
}

function dPrio(sid, iid) {
  const sec  = _dState.sections.find(s => s.id === sid);
  const item = sec?.items.find(i => i.id === iid);
  if (!item) return;
  item.priority = !item.priority;
  dPersist(); dRender();
}

function dDelItem(sid, iid) {
  const sec = _dState.sections.find(s => s.id === sid);
  if (!sec) return;
  sec.items = sec.items.filter(i => i.id !== iid);
  dPersist(); dRender();
}

function dDelSec(sid) {
  _dState.sections = _dState.sections.filter(s => s.id !== sid);
  dPersist(); dRender();
}

function dEdit(event, sid, iid) {
  event.stopPropagation();
  const sec  = _dState.sections.find(s => s.id === sid);
  const item = sec?.items.find(i => i.id === iid);
  if (!item) return;
  const span = event.currentTarget;
  const inp  = document.createElement('input');
  inp.style.cssText = 'all:unset;font-family:inherit;font-size:13.5px;color:var(--text);flex:1;background:transparent;border-bottom:1px solid #444;outline:none;width:100%;min-width:0;';
  inp.value = item.text;
  span.replaceWith(inp);
  inp.focus(); inp.select();
  let committed = false;
  const commit = () => {
    if (committed) return; committed = true;
    const t = inp.value.trim();
    if (t) item.text = t;
    dPersist(); dRender();
  };
  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    if (e.key === 'Escape') { dRender(); }
  });
  inp.addEventListener('blur', commit);
}

function dAddItem(sid) {
  const rows = document.getElementById(`drows-${sid}`);
  if (!rows) return;
  const div = document.createElement('div');
  div.style.cssText = 'display:flex;padding:4px 0;';
  const inp = document.createElement('input');
  inp.style.cssText = 'all:unset;font-family:inherit;font-size:13.5px;color:var(--text);flex:1;background:transparent;border-bottom:1px solid #444;outline:none;width:100%;';
  inp.placeholder = 'New item…';
  div.appendChild(inp);
  rows.appendChild(div);
  inp.focus();
  let done = false;
  const confirm = () => {
    if (done) return; done = true;
    const t = inp.value.trim();
    if (t) {
      const sec = _dState.sections.find(s => s.id === sid);
      if (sec) { sec.items.push({ id: dId(), text: t, done: false, priority: false }); dPersist(); }
    }
    dRender();
  };
  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); confirm(); }
    if (e.key === 'Escape') { dRender(); }
  });
  inp.addEventListener('blur', confirm);
}

function dAddSec() {
  const wrap = document.getElementById('d-add-sec-wrap');
  if (!wrap) return;
  wrap.innerHTML = `<div style="display:flex;align-items:center;padding:4px 0;">
    <input id="d-new-emoji" style="all:unset;font-family:inherit;font-size:20px;color:var(--text);width:28px;background:transparent;border-bottom:1px solid var(--border2);text-align:center;outline:none;" maxlength="2" placeholder="📌"/>
    <input id="d-new-name"  style="all:unset;font-family:inherit;font-size:13px;color:var(--text);flex:1;background:transparent;border-bottom:1px solid var(--border2);margin-left:10px;outline:none;" placeholder="Section name…"/>
  </div>`;
  const ei = document.getElementById('d-new-emoji');
  const ni = document.getElementById('d-new-name');
  ei.focus();
  let done = false;
  const confirm = () => {
    if (done) return; done = true;
    const name = ni.value.trim();
    if (name) {
      _dState.sections.push({ id: 's_'+dId(), emoji: ei.value.trim()||'📌', name, items: [] });
      dPersist();
    }
    dRender();
  };
  const onKey = e => {
    if (e.key === 'Enter') { e.preventDefault(); confirm(); }
    if (e.key === 'Escape') dRender();
  };
  ei.addEventListener('keydown', onKey);
  ni.addEventListener('keydown', onKey);
  ni.addEventListener('blur', () => setTimeout(confirm, 80));
}

function dDragStart(event, sid, iid) {
  _dDragSec = sid; _dDragItemId = iid;
  event.dataTransfer.effectAllowed = 'move';
  const el = document.getElementById(`dr-${iid}`);
  if (el) el.classList.add('d-dragging');
}

function dDragEnd() {
  document.querySelectorAll('.d-dragging').forEach(e => e.classList.remove('d-dragging'));
  _dDragSec = null; _dDragItemId = null;
}

function dSecDragStart(event, sid) {
  _dDragSecId = sid;
  event.dataTransfer.effectAllowed = 'move';
  event.stopPropagation();
  const el = document.getElementById(`ds-${sid}`);
  if (el) el.classList.add('d-sec-dragging');
}

function dSecDragEnd() {
  document.querySelectorAll('.d-sec-dragging').forEach(e => e.classList.remove('d-sec-dragging'));
  _dDragSecId = null;
}

function dDragOver(event) { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; }

function dDrop(event, tgtSid, tgtIid) {
  event.preventDefault();
  if (_dDragSecId) {
    if (_dDragSecId !== tgtSid) {
      const secs = _dState.sections;
      const fromIdx = secs.findIndex(s => s.id === _dDragSecId);
      const toIdx   = secs.findIndex(s => s.id === tgtSid);
      if (fromIdx !== -1 && toIdx !== -1) {
        const [sec] = secs.splice(fromIdx, 1);
        secs.splice(toIdx, 0, sec);
        dPersist(); dRender();
      }
    }
    dSecDragEnd();
    return;
  }
  if (!_dDragSec || !_dDragItemId) return;
  const src = _dState.sections.find(s => s.id === _dDragSec);
  const tgt = _dState.sections.find(s => s.id === tgtSid);
  if (!src || !tgt) return;
  const idx = src.items.findIndex(i => i.id === _dDragItemId);
  if (idx === -1) return;
  const [item] = src.items.splice(idx, 1);
  if (tgtIid) {
    const ti = tgt.items.findIndex(i => i.id === tgtIid);
    tgt.items.splice(ti >= 0 ? ti : tgt.items.length, 0, item);
  } else {
    tgt.items.push(item);
  }
  dPersist(); dDragEnd(); dRender();
}

function _updateMeetingSuggestions(now) {
  const dl = document.getElementById('meeting-suggestions');
  if (dl) {
    dl.innerHTML = S.calEvents
      .map(e => `<option value="${esc(e.subject)}">`)
      .join('');
  }

  const current  = S.calEvents.find(e => new Date(e.start) <= now && now < new Date(e.end));
  const soon     = S.calEvents.find(e => new Date(e.start) > now && (new Date(e.start) - now) < 30 * 60 * 1000);
  const suggested = current || soon;

  const chip  = document.getElementById('meeting-now-chip');
  const input = document.getElementById('new-meeting');
  if (!chip) return;

  if (suggested) {
    const label = current
      ? `Now: ${suggested.subject}`
      : `Soon: ${suggested.subject} · ${calFmtTime(suggested.start)}`;
    chip.textContent = `📅 ${label}`;
    chip.style.display = '';
    chip.onclick = () => {
      if (input) input.value = suggested.subject;
      chip.style.display = 'none';
    };
  } else {
    chip.style.display = 'none';
  }
}


function timeAgo(date, nowMs) {
  const diffMs = nowMs - date.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1)   return 'just now';
  if (diffMin < 60)  return `${diffMin}m ago`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24)    return `${diffH}h ago`;
  const diffD = Math.floor(diffH / 24);
  if (diffD === 1)   return 'yesterday';
  return `${diffD}d ago`;
}

// ── Tools & MCP page ─────────────────────────────────────────────────────────

const _VENV = String.raw`C:\Users\p.ledesma.jimenez\scripts\SmartRecorder\.venv\Scripts\python.exe`;
const _ROOT = String.raw`C:\Users\p.ledesma.jimenez\scripts\SmartRecorder`;

const _TOOLS_CONFIG = {
  mcpServers: {
    'hub-recordings': { command: _VENV, args: [_ROOT + String.raw`\mcp\recordings_server.py`] },
    'hub-focus':      { command: _VENV, args: [_ROOT + String.raw`\mcp\focus_server.py`] },
    'hub-calendar':   { command: _VENV, args: [_ROOT + String.raw`\mcp\calendar_server.py`] },
    'hub-onenote':    { command: _VENV, args: [_ROOT + String.raw`\mcp\onenote_server.py`] },
  },
};

function toolsInit() {
  const pre = document.getElementById('tools-config-pre');
  if (pre && !pre.textContent) pre.textContent = JSON.stringify(_TOOLS_CONFIG, null, 2);
  const dateInput = document.getElementById('tr-ws-date');
  if (dateInput && !dateInput.value) dateInput.value = new Date().toISOString().slice(0, 10);
}

async function toolsRun(method, url, body, outId) {
  const out = document.getElementById(outId);
  out.style.display = 'block';
  out.textContent = 'Loading…';
  try {
    const opts = { method };
    if (body !== null) { opts.headers = { 'Content-Type': 'application/json' }; opts.body = JSON.stringify(body); }
    const res = await fetch(url, opts);
    const data = await res.json();
    out.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    out.textContent = 'Error: ' + e.message;
  }
}

async function toolsRunId(inputId, urlTpl, outId, method = 'GET') {
  const id = document.getElementById(inputId)?.value?.trim();
  if (!id) { const o = document.getElementById(outId); o.style.display='block'; o.textContent='Enter an ID first.'; return; }
  const url = urlTpl.replace('{id}', id);
  const body = method === 'POST' ? {} : null;
  await toolsRun(method, url, body, outId);
}

function toolsFocusStats() {
  toolsRun('GET', '/api/work-sessions', null, 'tr-focus');
}

function toolsWorkSessions() {
  const date = document.getElementById('tr-ws-date')?.value || new Date().toISOString().slice(0, 10);
  toolsRun('GET', `/api/work-sessions?date=${date}`, null, 'tr-ws');
}

function toolsSearchLocal() {
  const q = (document.getElementById('tr-search-q')?.value || '').toLowerCase().trim();
  const out = document.getElementById('tr-search');
  out.style.display = 'block';
  if (!q) { out.textContent = 'Enter a keyword.'; return; }
  const hits = S.recordings.filter(r =>
    (r.project || '').toLowerCase().includes(q) ||
    (r.meeting || '').toLowerCase().includes(q)
  ).slice(0, 10).map(r => ({ id: r.id, start_time: r.start_time, project: r.project || '', meeting: r.meeting || '', duration_min: Math.round((r.duration || 0) / 60 * 10) / 10 }));
  out.textContent = hits.length ? JSON.stringify(hits, null, 2) : `No matches for "${q}" in project/meeting names.\nFull transcript search runs in MCP layer.`;
}

function toolsStartRec() {
  const source = document.getElementById('tr-start-src')?.value || 'both';
  toolsRun('POST', '/api/record/start', { source }, 'tr-start');
}

function toolsCalendar() {
  const days = document.getElementById('tr-cal-days')?.value || '1';
  toolsRun('GET', `/api/calendar/events?days=${days}`, null, 'tr-cal');
}

function toolsCopyConfig(btn) {
  navigator.clipboard.writeText(JSON.stringify(_TOOLS_CONFIG, null, 2)).then(() => {
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = orig; }, 1800);
  });
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  loadDevices();
  await loadRecordings();
  homeInit();
  connectWS();
  connectChatWS();
  initFocusTime();
  window.electronAPI?.onNavigate?.(name => showPage(name));
});
