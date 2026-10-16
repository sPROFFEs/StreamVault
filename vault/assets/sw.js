// Only public static assets are cached. Device media is explicitly saved in IDB.
importScripts("/assets/js/offline-store.js?v=20261016-1");
const CACHE_NAME = "streamvault-shell-v2";
const APP_SHELL = [
  "/assets/icon.png",
  "/assets/favicon.ico",
  "/assets/site.webmanifest",
  "/assets/css/app.css",
  "/assets/css/ui-extras.css",
  "/assets/offline.html",
  "/assets/css/offline.css?v=20261016-1",
  "/assets/js/offline-store.js?v=20261016-1",
  "/assets/js/offline-ui.js?v=20261016-1",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => Promise.all(APP_SHELL.map(async url => {
      const response = await fetch(url, {cache: "reload"});
      if (!response.ok || response.redirected) throw new Error("Offline shell unavailable");
      await cache.put(url, response);
    }))).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k.startsWith("streamvault-shell-") && k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("message", event => {
  if (event.data?.type === "SV_OFFLINE_READY") event.ports[0]?.postMessage({offline: true});
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/__offline_media/") && ["GET", "HEAD"].includes(event.request.method)) {
    event.respondWith(SVOffline.serve(event.request));
    return;
  }
  if (event.request.method !== "GET") return;
  if (url.pathname === "/assets/offline.html") {
    // The explicit device-library entry point must not depend on a reachable
    // origin, even when the browser reports an online but unusable network.
    event.respondWith(caches.match("/assets/offline.html").then(cached => cached || fetch(event.request)));
    return;
  }
  // Never cache personalized HTML, login, admin pages or APIs. When a normal
  // library navigation loses the network, show the public device-only shell.
  const libraryPage = ["/", "/portal", "/browse", "/downloads"].includes(url.pathname) || /^\/media\/\d+$/.test(url.pathname);
  if (event.request.mode === "navigate" && libraryPage) {
    event.respondWith(fetch(event.request).then(async response =>
      response.status >= 500 ? (await caches.match("/assets/offline.html")) || response : response).catch(async () =>
      (await caches.match("/assets/offline.html")) || Response.error()));
    return;
  }
  // Only cache same-origin static assets.
  if (url.origin !== self.location.origin || !url.pathname.startsWith("/assets/")) return;
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const fetchPromise = fetch(event.request)
        .then((response) => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        })
        .catch(() => cached);
      return cached || fetchPromise;
    })
  );
});
