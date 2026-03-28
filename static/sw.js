// Nova DSO Tracker - Minimal Service Worker
// PWA compliance only - network-first strategy for real-time data

const CACHE_NAME = 'nova-mobile-v1';

// App shell URLs to cache on install
const APP_SHELL = [
    '/m',
    '/static/css/mobile.css',
    '/static/manifest.json'
];

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

// Activate event - clean up old caches
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

// Fetch event - network-first strategy
self.addEventListener('fetch', (event) => {
    // Network-first for all requests
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
