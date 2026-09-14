'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  pageChange:       (name)         => ipcRenderer.send('page-change', name),
  setFocusState:    (active)       => ipcRenderer.send('focus-state', active),
  onNavigate:       (cb)           => ipcRenderer.on('navigate', (_e, name) => cb(name)),
  showNotification: (title, body)  => ipcRenderer.send('show-notification', { title, body }),
});
