const { app, BrowserWindow, screen, ipcMain, Menu, session } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');

const TAB_W   = 48;
const TAB_H   = 316;
const PORT    = 8765;
const ANIM_MS = 220;
const PANEL_W = 780;

let tabWin      = null;
let appWin      = null;
let python      = null;
let expanded    = false;
let animating   = false;
let currentPage = 'home';
let focusActive = false;

// ---------------------------------------------------------------------------
// Python server
// ---------------------------------------------------------------------------

function startPython() {
  const exe    = path.join(__dirname, '..', '.venv', 'Scripts', 'python.exe');
  const script = path.join(__dirname, '..', 'main.py');
  python = spawn(exe, [script], { cwd: path.join(__dirname, '..') });
  python.stdout.on('data', d => process.stdout.write(d));
  python.stderr.on('data', d => process.stderr.write(d));
  python.on('error', err => console.error('Python spawn error:', err.message));
}

function waitForServer(maxMs = 15000) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + maxMs;
    function poll() {
      const req = http.get(`http://127.0.0.1:${PORT}/`, () => resolve());
      req.on('error', () => {
        if (Date.now() > deadline) reject(new Error('Python server did not start in time'));
        else setTimeout(poll, 500);
      });
      req.end();
    }
    poll();
  });
}

// ---------------------------------------------------------------------------
// Layout helpers
// ---------------------------------------------------------------------------

function getLayout() {
  const display = screen.getPrimaryDisplay();
  const wa      = display.workArea;
  return {
    workRight: wa.x + wa.width,
    top:       wa.y,
    height:    wa.height,
    tabY:      wa.y + Math.floor(wa.height / 2) - Math.floor(TAB_H / 2),
  };
}

// ---------------------------------------------------------------------------
// Window creation
// ---------------------------------------------------------------------------

function createTabWin() {
  const { workRight, tabY } = getLayout();
  tabWin = new BrowserWindow({
    width: TAB_W, height: TAB_H,
    x: workRight - TAB_W, y: tabY,
    frame: false, transparent: true, alwaysOnTop: true,
    skipTaskbar: true, resizable: false, movable: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  });
  tabWin.loadFile(path.join(__dirname, 'tab.html'));
  tabWin.setAlwaysOnTop(true, 'screen-saver');
  tabWin.webContents.on('did-finish-load', () => {
    tabWin.webContents.send('focus-state', focusActive);
  });

  tabWin.webContents.on('context-menu', () => {
    Menu.buildFromTemplate([
      { label: 'Quit My Working Hub', click: () => app.quit() },
    ]).popup({ window: tabWin });
  });
}

function createAppWin() {
  const { workRight, top, height } = getLayout();
  appWin = new BrowserWindow({
    width: PANEL_W, height,
    x: workRight - TAB_W - PANEL_W, y: top,
    frame: false, alwaysOnTop: true,
    skipTaskbar: false, resizable: false,
    show: false,   // hidden until first expand
    webPreferences: {
      preload: path.join(__dirname, 'app-preload.js'),
      contextIsolation: true,
    },
  });
  appWin.loadURL(`http://127.0.0.1:${PORT}`);
  appWin.setAlwaysOnTop(true, 'floating');
  appWin.setIgnoreMouseEvents(true);
}

// ---------------------------------------------------------------------------
// Width animation (right edge stays pinned — never bleeds onto other monitors)
// ---------------------------------------------------------------------------

// Animate width from current to targetWidth; right edge stays at workRight - TAB_W.
function animateWidth(win, targetWidth, onDone) {
  if (!win || win.isDestroyed()) return;
  animating = true;
  const { workRight } = getLayout();
  const rightEdge = workRight - TAB_W;
  const { y, width: startWidth, height } = win.getBounds();
  const t0 = Date.now();
  function step() {
    if (!win || win.isDestroyed()) { animating = false; return; }
    const t    = Math.min((Date.now() - t0) / ANIM_MS, 1);
    const ease = 1 - Math.pow(1 - t, 3);
    const w    = Math.max(1, Math.round(startWidth + (targetWidth - startWidth) * ease));
    win.setBounds({ x: rightEdge - w, y, width: w, height });
    if (t < 1) {
      setTimeout(step, 14);
    } else {
      animating = false;
      if (onDone) onDone();
    }
  }
  step();
}

// ---------------------------------------------------------------------------
// Expand / collapse / resize
// ---------------------------------------------------------------------------

function expand() {
  if (expanded || animating) return;
  expanded = true;
  const { workRight, top, height } = getLayout();
  // Start at 1 px wide at the right edge, then grow left — never off-screen
  appWin.setBounds({ x: workRight - TAB_W - 1, y: top, width: 1, height });
  appWin.setIgnoreMouseEvents(false);
  appWin.show();
  animateWidth(appWin, PANEL_W, () => appWin.focus());
}

function collapse() {
  if (!expanded || animating) return;
  expanded = false;
  // Shrink to 1 px, then hide — right edge stays within primary display
  animateWidth(appWin, 1, () => {
    appWin.setIgnoreMouseEvents(true);
    appWin.hide();
  });
}

// ---------------------------------------------------------------------------
// IPC
// ---------------------------------------------------------------------------

ipcMain.on('toggle', () => expanded ? collapse() : expand());
ipcMain.on('quit',   () => app.quit());
ipcMain.on('page-change', (_event, name) => {
  currentPage = name;
});
ipcMain.on('focus-state', (_event, active) => {
  focusActive = active;
  tabWin?.webContents.send('focus-state', active);
});
ipcMain.on('open-page', (_event, name) => {
  currentPage = name;
  appWin?.webContents.send('navigate', name);
  if (!expanded) expand();
});

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

app.whenReady().then(async () => {
  session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
    callback(permission === 'media');
  });
  await session.defaultSession.clearCache();
  startPython();
  try { await waitForServer(); }
  catch (e) { console.error(e.message); }
  createTabWin();
  createAppWin();
});

app.on('will-quit',         () => python?.kill());
app.on('window-all-closed', () => { python?.kill(); app.quit(); });
