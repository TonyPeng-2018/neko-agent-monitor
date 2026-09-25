// Bridge between the web page (web/, loaded with ?overlay=1) and the overlay window.
// The window is click-through by default; the page calls neko.setHover(true)
// while the pointer is over a cat so it can be hovered / clicked.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('neko', {
  overlay: true,
  setHover: (on) => ipcRenderer.send('neko:hover', !!on),
  openRoom: () => ipcRenderer.send('neko:open-room'),
});
