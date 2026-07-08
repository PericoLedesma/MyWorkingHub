'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  pageChange: (name) => ipcRenderer.send('page-change', name),
});
