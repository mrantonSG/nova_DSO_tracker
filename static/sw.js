// Nova DSO Tracker - Minimal Service Worker
// Network-first for real-time data; fonts cache-first; mobile CSS network-first
// with a cached copy so /m keeps its styling and fonts offline.

const CACHE_NAME = 'nova-mobile-v2';

// App shell URLs to cache on install. Pages ask for CSS with a ?v=<version>
// query; cacheKey() drops the query, so these plain paths are what gets matched.
//
// Fonts are cache-first and their cache keys ignore ?v=, so a cached font is
// never refetched: replacing a font file under the same name needs a
// CACHE_NAME bump, or clients keep the old file.
const APP_SHELL = [
    '/m',
    '/static/css/mobile.css',
    '/static/css/fonts.css',
    '/static/manifest.json',
    '/static/fonts/dm-sans-latin-400-normal.woff2',
    '/static/fonts/dm-sans-latin-400-italic.woff2',
    '/static/fonts/dm-sans-latin-500-normal.woff2',
    '/static/fonts/dm-sans-latin-600-normal.woff2',
    '/static/fonts/dm-sans-latin-700-normal.woff2',
    '/static/fonts/dm-sans-latin-ext-400-normal.woff2',
    '/static/fonts/dm-sans-latin-ext-400-italic.woff2',
    '/static/fonts/dm-sans-latin-ext-500-normal.woff2',
    '/static/fonts/dm-sans-latin-ext-600-normal.woff2',
    '/static/fonts/dm-sans-latin-ext-700-normal.woff2',
    '/static/fonts/dm-mono-latin-400-normal.woff2',
    '/static/fonts/dm-mono-latin-500-normal.woff2',
    '/static/fonts/dm-mono-latin-ext-400-normal.woff2',
    '/static/fonts/dm-mono-latin-ext-500-normal.woff2'
];

// Stylesheets fetched network-first, so a new release's CSS lands when online,
// with the last good copy served when offline.
const NETWORK_FIRST_CSS = new Set([
    '/static/css/mobile.css',
    '/static/css/fonts.css'
]);

// One cache entry per path: ignore the ?v= cache-buster, so the entry matches
// whatever version the page asks for and old versions don't pile up.
function cacheKey(url) {
    return url.origin + url.pathname;
}

// Install event - cache the app shell
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(APP_SHELL).catch((error) => {
                // Silently fail if app shell URLs aren't available yet
                console.error('[SW] Failed to cache app shell:', error);
            });
        })
    );
    // Activate immediately
    self.skipWaiting();
});

// Activate event - delete every cache but the current one (nova-mobile-v1 etc.)
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    if (cacheName !== CACHE_NAME) {
                        return caches.delete(cacheName);
                    }
                })
            );
        })
    );
    // Take control of all clients immediately
    self.clients.claim();
});

// Fetch event
self.addEventListener('fetch', (event) => {
    // Leave non-GET and cross-origin requests (sky survey tiles, CDS, CDNs) to the
    // browser: without respondWith a failed request fails as a network error, not as
    // an empty 503 that callers would take for a real answer.
    if (event.request.method !== 'GET') return;
    const url = new URL(event.request.url);
    if (url.origin !== self.location.origin) return;
    // Survey tiles are served by the server's own /hips/ proxy; pass them through
    // untouched. Responding with a synthetic empty 503 here would be mistaken by
    // the sky viewer for a real answer, exactly what the cross-origin pass-through
    // above avoids.
    if (url.pathname.startsWith('/hips/')) return;

    // Fonts: cache-first. A miss goes to the network and is stored; offline with
    // no copy, the request fails as a network error and the browser falls back.
    if (url.pathname.startsWith('/static/fonts/') && url.pathname.endsWith('.woff2')) {
        const key = cacheKey(url);
        event.respondWith(
            caches.open(CACHE_NAME).then((cache) =>
                cache.match(key).then((cached) => {
                    if (cached) return cached;
                    return fetch(event.request).then((response) => {
                        if (response.ok) {
                            event.waitUntil(cache.put(key, response.clone()));
                        }
                        return response;
                    });
                })
            )
        );
        return;
    }

    // mobile.css / fonts.css: network-first, refresh the cached copy on success,
    // serve the cached copy when offline.
    if (NETWORK_FIRST_CSS.has(url.pathname)) {
        const key = cacheKey(url);
        event.respondWith(
            fetch(event.request)
                .then((response) => {
                    if (response.ok) {
                        const copy = response.clone();
                        event.waitUntil(
                            caches.open(CACHE_NAME).then((cache) => cache.put(key, copy))
                        );
                    }
                    return response;
                })
                .catch(() =>
                    caches.match(key).then((cached) => cached || Response.error())
                )
        );
        return;
    }

    // Network-first for all other same-origin GET requests
    event.respondWith(
        fetch(event.request)
            .then((response) => {
                // If network succeeds, return the response
                return response;
            })
            .catch(() => {
                // If network fails and it's a navigation request, return cached /m
                if (event.request.mode === 'navigate') {
                    return caches.match('/m').then((cachedResponse) => {
                        return cachedResponse || new Response('Offline', {
                            status: 503,
                            statusText: 'Service Unavailable'
                        });
                    });
                }
                // For non-navigation requests, fail silently
                return new Response(null, { status: 503, statusText: 'Service Unavailable' });
            })
    );
});
