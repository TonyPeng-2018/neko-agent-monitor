// neko overlay: a transparent, click-through, always-on-top window covering the
// primary display's work area. It shows the daemon's web UI in overlay mode
// (http://127.0.0.1:8765/?overlay=1) so the cats walk along your desktop.
const { app, BrowserWindow, Menu, Tray, ipcMain, nativeImage, screen, shell } = require('electron');
const path = require('path');

const PORT = parseInt(process.env.NEKO_PORT || '8765', 10);
const BASE = `http://127.0.0.1:${PORT}`;
const OVERLAY_URL = `${BASE}/?overlay=1`;
const RETRY_MS = 3000;

let win = null;
let tray = null;
let retryTimer = null;
let hovering = false;

// Only one overlay at a time.
if (!app.requestSingleInstanceLock()) {
  app.quit();
}

function setClickThrough(through) {
  if (!win || win.isDestroyed()) return;
  hovering = !through;
  if (through) win.setIgnoreMouseEvents(true, { forward: true });
  else win.setIgnoreMouseEvents(false);
}

function fitToWorkArea() {
  if (!win || win.isDestroyed()) return;
  const { x, y, width, height } = screen.getPrimaryDisplay().workArea;
  win.setBounds({ x, y, width, height });
}

function scheduleRetry() {
  clearTimeout(retryTimer);
  retryTimer = setTimeout(() => {
    if (win && !win.isDestroyed()) win.loadURL(OVERLAY_URL).catch(() => {});
  }, RETRY_MS);
}

function createWindow() {
  const { x, y, width, height } = screen.getPrimaryDisplay().workArea;
  win = new BrowserWindow({
    x, y, width, height,
    type: process.platform === 'darwin' ? 'panel' : 'toolbar', // non-activating, floats over fullscreen apps
    transparent: true,
    backgroundColor: '#00000000',
    frame: false,
    hasShadow: false,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    focusable: false,
    alwaysOnTop: true,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false,
    },
  });
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true, skipTransformProcessType: true });
  setClickThrough(true);

  win.webContents.on('did-fail-load', (_e, code, desc, url, isMainFrame) => {
    if (isMainFrame) {
      console.log(`[neko] daemon not reachable (${desc}); retrying in ${RETRY_MS / 1000}s`);
      scheduleRetry();
    }
  });
  win.webContents.on('did-finish-load', () => {
    if (!win.isVisible()) win.showInactive();
    // dev aid: NEKO_OVERLAY_SHOT=/path.png [NEKO_OVERLAY_SHOT_DELAY=ms] saves what the overlay draws, then quits
    const shot = process.env.NEKO_OVERLAY_SHOT;
    if (shot) {
      setTimeout(async () => {
        const img = await win.webContents.capturePage();
        require('fs').writeFileSync(shot, img.toPNG());
        console.log(`[neko] overlay capture saved to ${shot}`);
        app.quit();
      }, parseInt(process.env.NEKO_OVERLAY_SHOT_DELAY || '8000', 10));
    }
  });
  if (process.env.NEKO_DEBUG) {
    win.webContents.on('console-message', (e) => console.log(`[page ${e.level}] ${e.message}`));
  }
  win.webContents.on('render-process-gone', () => scheduleRetry());
  // Links open in the real browser, never inside the overlay.
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith(BASE + '/')) { e.preventDefault(); shell.openExternal(url); }
  });

  win.loadURL(OVERLAY_URL).catch(() => {});
}

// 16pt template icon: a cat head silhouette drawn into a bitmap (no asset files).
function catIcon() {
  const S = 32; // @2x
  const buf = Buffer.alloc(S * S * 4);
  const inTri = (px, py, ax, ay, bx, by, cx, cy) => {
    const d = (x1, y1, x2, y2, x3, y3) => (x1 - x3) * (y2 - y3) - (x2 - x3) * (y1 - y3);
    const d1 = d(px, py, ax, ay, bx, by), d2 = d(px, py, bx, by, cx, cy), d3 = d(px, py, cx, cy, ax, ay);
    return !((d1 < 0 || d2 < 0 || d3 < 0) && (d1 > 0 || d2 > 0 || d3 > 0));
  };
  for (let y = 0; y < S; y++) {
    for (let x = 0; x < S; x++) {
      const px = x + 0.5, py = y + 0.5;
      const head = ((px - 16) / 13) ** 2 + ((py - 19) / 11) ** 2 <= 1;
      const ears = inTri(px, py, 4, 14, 6, 2, 14, 10) || inTri(px, py, 28, 14, 26, 2, 18, 10);
      const eye = ((px - 11) ** 2 + (py - 18) ** 2 <= 4) || ((px - 21) ** 2 + (py - 18) ** 2 <= 4);
      const on = (head || ears) && !eye;
      const i = (y * S + x) * 4;
      buf[i] = 0; buf[i + 1] = 0; buf[i + 2] = 0; buf[i + 3] = on ? 255 : 0; // BGRA
    }
  }
  const img = nativeImage.createFromBitmap(buf, { width: S, height: S, scaleFactor: 2.0 });
  img.setTemplateImage(true);
  return img;
}

function buildTray() {
  tray = new Tray(catIcon());
  tray.setToolTip('neko agent monitor');
  const menu = () => Menu.buildFromTemplate([
    { label: 'Show room', click: () => shell.openExternal(`${BASE}/`) },
    {
      label: win && win.isVisible() ? 'Hide overlay' : 'Show overlay',
      click: () => {
        if (!win) return;
        if (win.isVisible()) win.hide(); else { fitToWorkArea(); win.showInactive(); }
        tray.setContextMenu(menu());
      },
    },
    { label: 'Reload overlay', click: () => win && win.loadURL(OVERLAY_URL).catch(() => {}) },
    { type: 'separator' },
    { label: 'Quit neko overlay', click: () => app.quit() },
  ]);
  tray.setContextMenu(menu());
  // keep the Show/Hide label in sync
  tray.on('mouse-enter', () => tray.setContextMenu(menu()));
}

ipcMain.on('neko:hover', (_e, on) => {
  if (!!on !== hovering) setClickThrough(!on);
});
ipcMain.on('neko:open-room', () => shell.openExternal(`${BASE}/`));

app.whenReady().then(() => {
  if (process.platform === 'darwin' && app.dock) app.dock.hide();
  createWindow();
  buildTray();
  screen.on('display-metrics-changed', fitToWorkArea);
  screen.on('display-added', fitToWorkArea);
  screen.on('display-removed', fitToWorkArea);
});

app.on('window-all-closed', (e) => e.preventDefault()); // tray app: stay alive
