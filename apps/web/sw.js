const CACHE_NAME = "beenco-connect-v24";
const APP_SHELL = [
  "./",
  "./index.html",
  "./styles.css",
  "./app.js",
  "./manifest.webmanifest",
  "./assets/beenco-command-hero.svg",
  "./icons/icon-192.svg"
];

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(fetch(event.request));
    return;
  }
  const isAppAsset = url.pathname.endsWith("/index.html") ||
    url.pathname.endsWith("/app.js") ||
    url.pathname.endsWith("/styles.css") ||
    url.pathname.endsWith("/assets/beenco-command-hero.svg") ||
    url.pathname === "/";
  if (isAppAsset) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (!url.pathname.startsWith("/api/")) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          }
          return response;
        })
        .catch(() => caches.match(event.request).then((cached) => cached || caches.match("./index.html")))
    );
    return;
  }
  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request).catch(() => caches.match("./index.html"));
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const params = new URLSearchParams();
  if (data.workspaceMode) params.set("view", data.workspaceMode);
  if (data.channelId) params.set("channel", data.channelId);
  if (data.messageId) params.set("message", data.messageId);
  if (data.url) params.set("external", data.url);
  const targetUrl = `/${params.toString() ? `?${params.toString()}` : ""}`;
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if ("focus" in client) {
          client.focus();
          if ("navigate" in client) return client.navigate(targetUrl);
          return client;
        }
      }
      if (clients.openWindow) return clients.openWindow(targetUrl);
      return null;
    })
  );
});
