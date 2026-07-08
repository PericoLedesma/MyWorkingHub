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
  expandedCardId:  null,     // id of currently expanded card, or null
  nextProject:     '',       // project to apply when recording stops
  nextMeeting:     '',       // meeting to apply when recording stops
};

// ── Page navigation ───────────────────────────────────────────────────────────
function showPage(name) {
  window.electronAPI?.pageChange(name);
  document.querySelectorAll('.page').forEach(p => { p.style.display = 'none'; });
  document.getElementById('page-' + name).style.display = '';
  document.querySelectorAll('.nav-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.page === name);
  });
  if (name === 'history') renderHistoryBar();
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

const PROJECT_PALETTE = [
  '#f472b6','#fb923c','#fbbf24','#a3e635',
  '#2dd4bf','#38bdf8','#a78bfa','#e879f9',
  '#34d399','#f87171'
];
function projectColor(name) {
  if (!name) return null;
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return PROJECT_PALETTE[h % PROJECT_PALETTE.length];
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
      (msg.transcribing     || []).forEach(id => addJob(id, 'transcribe'));
      (msg.summarizing      || []).forEach(id => addJob(id, 'summarize'));
      (msg.notes_generating || []).forEach(id => addJob(id, 'notes'));
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
      document.getElementById('live-transcript-panel').style.display = '';
      break;

    case 'live_transcript':
      appendLiveTranscript(msg.timestamp, msg.text);
      break;

    case 'recording_stopped':
      S.recording = false;
      S.recordingStart = null;
      updateRecordButton();
      stopTimer();
      // Stay on Recordings page — live transcript remains visible for any in-flight chunks.
      // The recording is saved in the background; user can navigate to History when ready.
      if (msg.meta) {
        const newRec = msg.meta;
        S.recordings.unshift(newRec);
        (async () => {
          if (S.nextProject) { await saveTag(newRec.id, 'project', S.nextProject); S.nextProject = ''; }
          if (S.nextMeeting) { await saveTag(newRec.id, 'meeting', S.nextMeeting); S.nextMeeting = ''; }
          filterRecordings();
          updateProjectFilter();
          renderHistoryBar();
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
      break;

    case 'summarized':
      removeJob(msg.id, 'summarize');
      refreshCardFromServer(msg.id);
      break;

    case 'notes_generated':
      removeJob(msg.id, 'notes');
      if (S.expandedCardId === msg.id) refreshExpandedDetail(msg.id);
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
  if (S.recording) {
    await api('POST', '/api/record/stop');
  } else {
    const sel = document.getElementById('device-select');
    const deviceIndex = sel.value !== '' ? parseInt(sel.value) : null;
    S.nextProject = document.getElementById('new-project').value.trim();
    S.nextMeeting = document.getElementById('new-meeting').value.trim();
    document.getElementById('new-project').value = '';
    document.getElementById('new-meeting').value = '';
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

// ── Live transcript ───────────────────────────────────────────────────────────
function clearLiveTranscript() {
  document.getElementById('live-tx-body').innerHTML = '';
}

function appendLiveTranscript(timestamp, text) {
  const body = document.getElementById('live-tx-body');
  const line = document.createElement('div');
  line.className = 'live-tx-line';
  const ts = document.createElement('span');
  ts.className = 'live-tx-ts';
  ts.textContent = timestamp;
  const tx = document.createElement('span');
  tx.className = 'live-tx-text';
  tx.textContent = ' ' + text;
  line.appendChild(ts);
  line.appendChild(tx);
  body.appendChild(line);
  body.scrollTop = body.scrollHeight;
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
  renderHistoryBar();
}

function updateProjectFilter() {
  const projects = [...new Set(S.recordings.map(r => r.project).filter(Boolean))].sort();
  const container = document.getElementById('project-bubbles');
  const active = new Set(
    [...container.querySelectorAll('.proj-bubble.active')].map(b => b.dataset.project)
  );
  container.innerHTML = '';
  projects.forEach(p => {
    const b = document.createElement('button');
    const isActive = active.has(p);
    b.className = 'proj-bubble' + (isActive ? ' active' : '');
    b.dataset.project = p;
    b.textContent = p;
    const color = projectColor(p);
    if (color) {
      b.style.setProperty('--proj-color', color);
      if (isActive) b.style.background = hexAlpha(color, 0.15);
    }
    b.onclick = () => {
      b.classList.toggle('active');
      if (color) b.style.background = b.classList.contains('active') ? hexAlpha(color, 0.15) : '';
      filterRecordings();
    };
    container.appendChild(b);
  });
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
  if (S.expandedCardId !== null) {
    const idToExpand = S.expandedCardId;
    S.expandedCardId = null;
    toggleCardExpand(idToExpand);
  }
}

function renderCard(r) {
  const jobs = S.pendingJobs[r.id] || new Set();
  const isTranscribing = jobs.has('transcribe');
  const isSummarizing  = jobs.has('summarize');

  const statusMsg = isTranscribing ? 'Transcribing...'
                  : isSummarizing  ? 'Summarizing...' : '';

  const cardClass = ['rec-card', r.file_missing ? 'missing' : '', r.has_transcript ? 'has-transcript' : ''].filter(Boolean).join(' ');
  const projColor  = projectColor(r.project);
  const asideBg    = projColor ? `background:${hexAlpha(projColor, 0.28)}` : '';
  const asideColor = projColor ? `color:${projColor}` : '';
  const projStyle  = projColor ? `style="color:${projColor}"` : '';

  return `
<div class="${cardClass}" data-id="${r.id}">
  <div class="rec-card-aside" style="${asideBg}">
    <span class="rec-aside-label" style="${asideColor}">${esc(r.project || '·')}</span>
  </div>
  <div class="rec-card-content">
    <div class="rec-main-row">
      <div class="rec-identity">
        <div class="tag-pair">
          <span class="tag-label">Project</span>
          <span class="tag-value" contenteditable="true" data-field="project" data-placeholder="--" onclick="event.stopPropagation()" ${projStyle}>${esc(r.project || '')}</span>
        </div>
        <div class="tag-pair">
          <span class="tag-label">Meeting</span>
          <span class="tag-value tag-value-meeting" contenteditable="true" data-field="meeting" data-placeholder="--" onclick="event.stopPropagation()">${esc(r.meeting || '')}</span>
        </div>
      </div>
      <div class="rec-meta">
        <div class="rec-datetime">
          <span class="tag-label">Recorded</span>
          <span class="rec-date-only">${esc(fmtDateOnly(r.start_time))}</span>
          <span class="rec-time-only">${esc(fmtTimeOnly(r.start_time))}</span>
        </div>
        <div class="rec-meta-chips">
          <span class="rec-duration">${esc(fmtDuration(r.duration))}</span>
          <span class="rec-source">${esc(srcLabel(r.source))}</span>
          ${r.language ? `<span class="rec-lang">${esc(r.language)}</span>` : ''}
        </div>
        <div class="rec-actions">
          <button class="act-btn${isTranscribing?' spinning':''}" ${isTranscribing?'disabled':''} onclick="transcribe(${r.id})">${r.has_transcript ? 'Re-transcribe' : 'Transcribe'}</button>
          ${r.has_transcript ? `<button class="act-btn${isSummarizing?' spinning':''}" ${isSummarizing?'disabled':''} onclick="summarize(${r.id})">${r.has_summary ? 'Re-summarize' : 'Summarize'}</button>` : ''}
          <span class="act-sep"></span>
          <button class="act-btn" onclick="openFolder(${r.id})">Open folder</button>
          <button class="act-btn danger" onclick="deleteRecording(${r.id})">Delete</button>
        </div>
        ${statusMsg ? `<div class="rec-status" style="text-align:right">${esc(statusMsg)}</div>` : ''}
      </div>
    </div>
    ${!r.file_missing ? `
    <div class="rec-audio-row">
      <audio class="rec-audio" src="/audio/${r.id}" preload="none"></audio>
      <div class="audio-player">
        <button class="audio-play-btn" aria-label="Play">▶</button>
        <span class="audio-time">0:00</span>
        <input type="range" class="audio-seek" value="0" min="0" max="100" step="0.1">
        <span class="audio-dur">--:--</span>
      </div>
    </div>` : `<div class="rec-status" style="color:#ef4444;margin-bottom:8px;">Audio file missing</div>`}
    <button class="rec-expand-btn" onclick="toggleCardExpand(${r.id})" aria-label="Expand">
      <span class="rec-expand-chevron">▾</span>
    </button>
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
    const seek  = row.querySelector('.audio-seek');
    const time  = row.querySelector('.audio-time');
    const dur   = row.querySelector('.audio-dur');

    btn.addEventListener('click', () => {
      if (audio.paused) { audio.play(); btn.textContent = '⏸'; }
      else              { audio.pause(); btn.textContent = '▶'; }
    });

    audio.addEventListener('loadedmetadata', () => {
      seek.max = audio.duration;
      dur.textContent = fmtPlayerTime(audio.duration);
    });

    audio.addEventListener('timeupdate', () => {
      time.textContent = fmtPlayerTime(audio.currentTime);
      if (!seek._seeking) seek.value = audio.currentTime;
    });

    seek.addEventListener('mousedown', () => { seek._seeking = true; });
    seek.addEventListener('input',     () => { time.textContent = fmtPlayerTime(+seek.value); });
    seek.addEventListener('change',    () => { audio.currentTime = +seek.value; seek._seeking = false; });

    audio.addEventListener('ended', () => {
      btn.textContent = '▶';
      seek.value = 0;
      time.textContent = '0:00';
    });
  });
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
  if (field === 'project') refreshCard(id);
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
    S.expandedCardId = null;
  }

  // Toggle current card
  if (card.classList.contains('expanded')) {
    card.classList.remove('expanded');
    card.querySelector('.rec-detail').innerHTML = '';
    S.expandedCardId = null;
    return;
  }

  const data = await fetch(`/api/recordings/${id}/transcript`).then(r => r.json()).catch(() => ({}));
  card.classList.add('expanded');
  S.expandedCardId = id;

  const detail = card.querySelector('.rec-detail');
  detail.innerHTML = buildDetailHTML(id, data);
  activateDetailTab(detail.querySelector('.detail-tab[data-tab="transcript"]'), 'transcript', id);
}

function buildDetailHTML(id, data) {
  const hasTranscript = !!(data.raw || data.corrected);
  const isGeneratingNotes = (S.pendingJobs[id] || new Set()).has('notes');

  let notesSection = '';
  if (hasTranscript) {
    if (data.notes) {
      notesSection = `
        <div class="notes-section">
          <div class="notes-header">
            <span class="notes-label">Notes</span>
            <button class="act-btn${isGeneratingNotes ? ' spinning' : ''}" ${isGeneratingNotes ? 'disabled' : ''} onclick="generateNotes(${id})">Regenerate</button>
          </div>
          <textarea class="notes-textarea" onblur="saveNotes(${id}, this.value)" placeholder="AI-generated notes will appear here…">${esc(data.notes)}</textarea>
        </div>`;
    } else {
      notesSection = `
        <div class="notes-section notes-empty">
          <button class="act-btn primary${isGeneratingNotes ? ' spinning' : ''}" ${isGeneratingNotes ? 'disabled' : ''} onclick="generateNotes(${id})">Generate notes</button>
        </div>`;
    }
  }

  const transcriptPanel = hasTranscript
    ? `<div class="transcript-subtabs">
        <button class="transcript-subtab active" onclick="activateSubtab(this,'raw',${id})">Raw</button>
        ${data.corrected ? `<button class="transcript-subtab" onclick="activateSubtab(this,'corrected',${id})">Corrected</button>` : ''}
        ${data.speaker   ? `<button class="transcript-subtab" onclick="activateSubtab(this,'speaker',${id})">Speaker</button>`   : ''}
      </div>
      <div class="detail-transcript-text subtab-panel" data-subtab="raw">${esc(data.raw || '')}</div>
      ${data.corrected ? `<div class="detail-transcript-text subtab-panel" data-subtab="corrected" style="display:none">${esc(data.corrected)}</div>` : ''}
      ${data.speaker   ? `<div class="detail-transcript-text subtab-panel" data-subtab="speaker"    style="display:none">${esc(data.speaker)}</div>`   : ''}`
    : `<p class="detail-empty">No transcript yet — click Transcribe to generate one.</p>`;

  const summaryPanel = data.summary
    ? `<div class="detail-transcript-text">${esc(data.summary)}</div>`
    : `<p class="detail-empty">No summary yet — click Summarize to generate one.</p>`;

  return `
    ${notesSection}
    <div class="detail-tabs">
      <button class="detail-tab active${hasTranscript ? '' : ' tab-empty'}" data-tab="transcript"
              onclick="activateDetailTab(this,'transcript',${id})">Transcript</button>
      <button class="detail-tab${data.summary ? '' : ' tab-empty'}" data-tab="summary"
              onclick="activateDetailTab(this,'summary',${id})">Summary</button>
    </div>
    <div class="detail-panel" data-tab="transcript">${transcriptPanel}</div>
    <div class="detail-panel" data-tab="summary" style="display:none">${summaryPanel}</div>
  `;
}

function activateDetailTab(btn, tab, id) {
  const card = document.querySelector(`.rec-card[data-id="${id}"]`);
  if (!card) return;
  const detail = card.querySelector('.rec-detail');
  detail.querySelectorAll('.detail-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  detail.querySelectorAll('.detail-panel').forEach(p => {
    p.style.display = p.dataset.tab === tab ? '' : 'none';
  });
}

function activateSubtab(btn, subtab, id) {
  const card = document.querySelector(`.rec-card[data-id="${id}"]`);
  if (!card) return;
  const panel = card.querySelector('.detail-panel[data-tab="transcript"]');
  if (!panel) return;
  panel.querySelectorAll('.transcript-subtab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  panel.querySelectorAll('.subtab-panel').forEach(p => {
    p.style.display = p.dataset.subtab === subtab ? '' : 'none';
  });
}

async function refreshExpandedDetail(id) {
  const card = document.querySelector(`.rec-card[data-id="${id}"]`);
  if (!card || !card.classList.contains('expanded')) return;
  const data = await fetch(`/api/recordings/${id}/transcript`).then(r => r.json()).catch(() => ({}));
  const detail = card.querySelector('.rec-detail');
  if (detail) {
    detail.innerHTML = buildDetailHTML(id, data);
    const tab = detail.querySelector('.detail-tab[data-tab="transcript"]');
    if (tab) activateDetailTab(tab, 'transcript', id);
  }
}

async function saveNotes(id, text) {
  await api('PATCH', `/api/recordings/${id}/notes`, { text });
}

async function generateNotes(id) {
  addJob(id, 'notes');
  const card = document.querySelector(`.rec-card[data-id="${id}"]`);
  if (card) {
    const btn = card.querySelector('.notes-section .act-btn');
    if (btn) { btn.disabled = true; btn.classList.add('spinning'); }
  }
  await api('POST', `/api/recordings/${id}/generate-notes`);
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

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadDevices();
  loadRecordings();
  connectWS();
  connectChatWS();
  initVuMeter();
});
