// StremMusic Service Worker (PWA Shell Cache)
const CACHE_NAME = 'stremmusic-shell-v2';
const SHELL_ASSETS = [
  '/',
  '/manifest.webmanifest',
  '/icon-192.png',
  '/icon-512.png',
  '/icon-maskable-512.png'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(SHELL_ASSETS).catch(err => console.debug('Cache addAll:', err));
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => {
      return Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // 1. Áudio em streaming, downloads, painel do host e endpoints com Range:
  // SEMPRE bypass total do Service Worker para permitir streaming HTTP 206 nativo
  // e nunca armazenar o painel administrativo no cache do app.
  if (
    url.pathname.startsWith('/api/stream') ||
    url.pathname.startsWith('/api/local') ||
    url.pathname.startsWith('/downloads') ||
    url.pathname.startsWith('/painel') ||
    req.headers.has('range')
  ) {
    return;
  }

  // 2. Chamadas de API dinâmicas (/api/*):
  // Direto na rede com resposta JSON amigável quando offline
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(req).catch(() => {
        return new Response(JSON.stringify({ error: 'offline', detail: 'Sem conexão com o servidor — você pode ouvir as músicas salvas na aba "No celular".' }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' }
        });
      })
    );
    return;
  }

  // 3. Navegação principal (/):
  // Network first com fallback para o cache do shell (permite abrir o PWA mesmo sem internet)
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then(res => {
          if (res.ok && url.pathname === '/') {
            const clone = res.clone();
            caches.open(CACHE_NAME).then(cache => cache.put('/', clone));
          }
          return res;
        })
        .catch(() => caches.match('/'))
    );
    return;
  }

  // 4. Arquivos estáticos (ícones, manifest, fontes):
  // Stale-While-Revalidate
  event.respondWith(
    caches.match(req).then(cached => {
      const fetchPromise = fetch(req).then(networkRes => {
        if (networkRes.ok) {
          const clone = networkRes.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(req, clone));
        }
        return networkRes;
      }).catch(() => cached);
      return cached || fetchPromise;
    })
  );
});
