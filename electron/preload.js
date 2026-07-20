const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  toggle:       () => ipcRenderer.send('toggle'),
  quit:         () => ipcRenderer.send('quit'),
  openPage:     (name) => ipcRenderer.send('open-page', name),
  onFocusState: (cb) => ipcRenderer.on('focus-state', (_e, active) => cb(active)),
});
