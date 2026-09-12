/* GOAT Gauge service worker: 仅用于让 Chrome 识别为可安装应用。
   不做离线缓存, 所有请求直接走本地服务, 避免展示过期用量数据。 */

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", () => {
  // 空实现即可满足 PWA 的可安装条件; 不拦截请求。
});
