from __future__ import annotations

from django.http import HttpResponse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET


@require_GET
@cache_control(public=True, max_age=3600)
def web_manifest(request) -> HttpResponse:
    content = """{
  "name": "The Academic Mirror",
  "short_name": "Academic Mirror",
  "description": "AI-powered virtual academic gown fitting kiosk",
  "start_url": "/kiosk/",
  "display": "fullscreen",
  "orientation": "portrait",
  "background_color": "#f5f0e6",
  "theme_color": "#f5f0e6",
  "icons": [{
    "src": "/assets/kiosk-mark.svg",
    "sizes": "any",
    "type": "image/svg+xml",
    "purpose": "any maskable"
  }]
}"""
    return HttpResponse(content, content_type="application/manifest+json")


@require_GET
@cache_control(public=True, max_age=3600)
def service_worker(request) -> HttpResponse:
    content = """
const CACHE = 'academic-mirror-django-v3';
const STATIC = [
  '/assets/ceremonial-arches.svg',
  '/assets/kiosk-mark.svg',
  '/assets/professor-rashidah-cutout.webp',
  '/assets/professor-rashidah-mark.webp',
  '/assets/professor-rashidah-portrait.jpg',
  '/assets/mirror/css/kiosk.css',
  '/assets/mirror/css/app.css',
  '/manifest.webmanifest'
];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(STATIC)));
  self.skipWaiting();
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key !== CACHE).map(key => caches.delete(key))
  )));
  self.clients.claim();
});
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  const cacheable = url.origin === self.location.origin && (
    url.pathname.startsWith('/assets/') || /^\\/gowns\\/[^/]+\\/image\\/$/.test(url.pathname)
  );
  if (!cacheable) return;
  // Stylesheets and scripts: network first, so an update is live on the very next load
  // (cache-first served the previous version until a second reload). Cache is the offline fallback.
  if (/\\.(css|js)$/.test(url.pathname)) {
    event.respondWith(caches.open(CACHE).then(cache => fetch(event.request).then(response => {
      if (response.ok) cache.put(event.request, response.clone());
      return response;
    }).catch(() => cache.match(event.request))));
    return;
  }
  event.respondWith(caches.open(CACHE).then(async cache => {
    const cached = await cache.match(event.request);
    const network = fetch(event.request).then(response => {
      if (response.ok) cache.put(event.request, response.clone());
      return response;
    }).catch(() => cached);
    return cached || network;
  }));
});
"""
    return HttpResponse(content, content_type="application/javascript")
