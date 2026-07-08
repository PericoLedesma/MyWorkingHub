const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  toggle: () => ipcRenderer.send('toggle'),
  quit:   () => ipcRenderer.send('quit'),
});
