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
  nextProject:     '',       // project to apply when recording stops
  nextMeeting:     '',       // meeting to apply when recording stops
  calEvents:       [],       // today's calendar events (cached)
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
  const proj = S.projects.find(p => p.name === name);
  if (proj && proj.color) return proj.color;
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
    // Auto-link to a currently-active Outlook meeting if the user left the field blank
    if (!S.nextMeeting && S.calEvents.length) {
      const now = new Date();
      const active = S.calEvents.find(e => new Date(e.start) <= now && now < new Date(e.end));
      if (active) S.nextMeeting = active.subject;
    }
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

  const statusMsg = isTranscribing ? 'Transcribing...' : '';

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
  } else {
    notesSection = `
      <div class="notes-section notes-empty">
        <p class="detail-empty">Transcribe first to generate notes.</p>
      </div>`;
  }

  const transcriptContent = hasTranscript
    ? `<div class="transcript-subtabs">
        <button class="transcript-subtab active" onclick="activateSubtab(this,'raw',${id})">Raw</button>
        ${data.corrected ? `<button class="transcript-subtab" onclick="activateSubtab(this,'corrected',${id})">Corrected</button>` : ''}
        ${data.speaker   ? `<button class="transcript-subtab" onclick="activateSubtab(this,'speaker',${id})">Speaker</button>`   : ''}
      </div>
      <div class="detail-transcript-text subtab-panel" data-subtab="raw">${esc(data.raw || '')}</div>
      ${data.corrected ? `<div class="detail-transcript-text subtab-panel" data-subtab="corrected" style="display:none">${esc(data.corrected)}</div>` : ''}
      ${data.speaker   ? `<div class="detail-transcript-text subtab-panel" data-subtab="speaker"    style="display:none">${esc(data.speaker)}</div>`   : ''}`
    : `<p class="detail-empty">No transcript yet — click Transcribe to generate one.</p>`;

  const userNotesSection = `
    <div class="notes-section user-notes-section">
      <div class="notes-header">
        <span class="notes-label user-notes-label">My Notes</span>
      </div>
      <textarea class="notes-textarea user-notes-textarea"
                onblur="saveUserNotes(${id}, this.value)"
                placeholder="Add your personal notes…">${esc(data.user_notes || '')}</textarea>
    </div>`;

  return `
    ${notesSection}
    ${userNotesSection}
    <div class="detail-tabs">
      <button class="detail-tab${hasTranscript ? '' : ' tab-empty'}" data-tab="transcript"
              onclick="toggleTranscriptPanel(${id})">Transcript</button>
    </div>
    <div class="detail-panel" data-tab="transcript" style="display:none">${transcriptContent}</div>
  `;
}

function toggleTranscriptPanel(id) {
  const card = document.querySelector(`.rec-card[data-id="${id}"]`);
  if (!card) return;
  const panel = card.querySelector('.detail-panel[data-tab="transcript"]');
  const btn   = card.querySelector('.detail-tab[data-tab="transcript"]');
  if (!panel || !btn) return;
  const isShown = panel.style.display !== 'none';
  panel.style.display = isShown ? 'none' : '';
  btn.classList.toggle('active', !isShown);
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
  }
}

async function saveNotes(id, text) {
  await api('PATCH', `/api/recordings/${id}/notes`, { text });
}

async function saveUserNotes(id, text) {
  await api('PATCH', `/api/recordings/${id}/user-notes`, { text });
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
    data = await (await fetch('/api/calendar/events?days=1')).json();
  } catch {
    list.innerHTML = '<div class="cal-error">Could not reach the server.</div>';
    return;
  }

  if (!data.ok) {
    list.innerHTML = `<div class="cal-error">Outlook is not available: ${esc(data.error || 'unknown error')}<br>Make sure Outlook is open and try again.</div>`;
    return;
  }

  const events = data.events || [];
  const now    = new Date();

  // Split into today and tomorrow
  const today    = now.toDateString();
  const tomorrow = new Date(now.getTime() + 86400000).toDateString();

  status.textContent = `${events.length} event${events.length !== 1 ? 's' : ''} · updated ${now.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}`;

  if (!events.length) {
    list.innerHTML = '<div class="cal-empty">No events today.</div>';
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
    dateEl.textContent = new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' });
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
  } else if (!isIdle && !FT.active) {
    FT.active = true;
    try {
      const sess = await api('POST', '/api/work-sessions');
      FT.sessionId = sess.id;
      FT.sessionStartMs = new Date(sess.start_time).getTime();
    } catch (e) {}
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

      if (gapSecs > 30) {
        // App was closed if any recorded start event falls inside this gap window
        const appWasClosed = FT.appEvents.some(e => {
          const ts = new Date(e.timestamp).getTime();
          return ts > gapStartMs && ts <= gapEndMs;
        });
        const gapStart     = fmtTimeOnly(prev.end_time || prev.start_time);
        const gapEnd       = fmtTimeOnly(s.start_time);
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
  } catch (e) {}
}

// ── Home page ─────────────────────────────────────────────────────────────────
async function homeInit() {
  const now = new Date();
  const hour = now.getHours();
  const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  const helloEl = document.getElementById('home-hello');
  const dateEl  = document.getElementById('home-date');
  if (helloEl) helloEl.textContent = greeting;
  if (dateEl)  dateEl.textContent  = now.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });

  const recCountEl = document.getElementById('home-rec-count');
  if (recCountEl) recCountEl.textContent = S.recordings.length;
  homeRenderRecent();

  try {
    const today = now.toISOString().slice(0, 10);
    const [sessions, current] = await Promise.all([
      api('GET', `/api/work-sessions?date=${today}`),
      api('GET', '/api/work-sessions/current'),
    ]);
    const completed = sessions.filter(s => s.duration_seconds != null);
    const activeStartMs = FT.sessionStartMs
      || (current?.start_time ? new Date(current.start_time).getTime() : null);
    const activeSecs = activeStartMs ? Math.floor((Date.now() - activeStartMs) / 1000) : 0;
    const totalSecs = completed.reduce((sum, s) => sum + s.duration_seconds, 0) + activeSecs;
    const focusEl = document.getElementById('home-focus-time');
    if (focusEl) focusEl.textContent = totalSecs > 0 ? fmtDuration(totalSecs) : '0m';
  } catch (_) {}

  try {
    const data = await api('GET', '/api/calendar/events?days=1');
    S.calEvents = data.ok ? (data.events || []) : [];
    _updateMeetingSuggestions(now);
    if (data.ok) {
      const upcoming = S.calEvents.filter(e => new Date(e.end) > now);
      const card = document.getElementById('home-next-card');
      if (card && upcoming.length) {
        const e = upcoming[0];
        const start = new Date(e.start);
        const startStr = start.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
        const diffMs = start - now;
        const diffMin = Math.round(diffMs / 60000);
        const timeHint = diffMs < 0
          ? 'In progress'
          : diffMin < 60 ? `In ${diffMin}m` : `At ${startStr}`;
        document.getElementById('home-next-title').textContent = e.subject || 'Meeting';
        document.getElementById('home-next-time').textContent = `${startStr} · ${timeHint}`;
        card.style.display = '';
      } else if (card) {
        card.style.display = 'none';
      }
    }
  } catch (_) {}
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

function homeRenderRecent() {
  const el = document.getElementById('home-recent-list');
  if (!el) return;
  const recent = [...S.recordings]
    .sort((a, b) => (b.start_time || '').localeCompare(a.start_time || ''))
    .slice(0, 5);
  if (!recent.length) {
    el.innerHTML = '<div class="home-empty">No recordings yet.</div>';
    return;
  }
  const now = Date.now();
  let html = '';
  for (const r of recent) {
    const color = projectColor(r.project) || 'var(--border2)';
    const title = r.meeting || r.project || 'Untitled';
    const parts = [];
    if (r.project) parts.push(esc(r.project));
    if (r.duration) parts.push(fmtDuration(r.duration));
    const meta = parts.join(' · ');
    const ago = r.start_time ? timeAgo(new Date(r.start_time), now) : '';
    const noteBadge = r.has_notes ? '<span class="home-rec-badge">notes</span>' : '';
    const txBadge = r.has_transcript ? '<span class="home-rec-badge">transcript</span>' : '';
    html += `<div class="home-rec-row" onclick="scrollToCard(${r.id})">
      <div class="home-rec-strip" style="background:${color}"></div>
      <div class="home-rec-info">
        <div class="home-rec-title">${esc(title)}</div>
        <div class="home-rec-meta"><span>${meta}</span>${noteBadge}${txBadge}</div>
      </div>
      <div class="home-rec-time">${esc(ago)}</div>
    </div>`;
  }
  el.innerHTML = html;
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
  initVuMeter();
  initFocusTime();
  window.electronAPI?.onNavigate?.(name => showPage(name));
});
