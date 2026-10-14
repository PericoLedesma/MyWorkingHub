const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  toggle:       () => ipcRenderer.send('toggle'),
  quit:         () => ipcRenderer.send('quit'),
  openPage:     (name) => ipcRenderer.send('open-page', name),
  onFocusState: (cb) => ipcRenderer.on('focus-state', (_e, active) => cb(active)),
  collapseTab:  () => ipcRenderer.send('collapse-tab'),
  restoreTab:   () => ipcRenderer.send('restore-tab'),
  onTabMode:    (cb) => ipcRenderer.on('tab-mode', (_e, mode) => cb(mode)),
});
