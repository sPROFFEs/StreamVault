// StreamVault Service Worker — minimal app-shell caching for PWA install.
// Content is NOT cached (auth-gated, user-specific). Only static assets.
const CACHE_NAME = "streamvault-shell-v1";
const APP_SHELL = [
  "/assets/icon.png",
  "/assets/favicon.ico",
  "/assets/site.webmanifest",
  "/assets/css/app.css",
  "/assets/css/ui-extras.css",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
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
