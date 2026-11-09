// StremMusic Service Worker (PWA Shell Cache)
const CACHE_NAME = 'stremmusic-shell-v3';
const SHELL_ASSETS = [
  '/',
  '/manifest.webmanifest',
  '/icon-192.png',
  '/icon-512.png',
  '/icon-maskable-512.png'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(async cache => {
      // Salva cada recurso individualmente com credenciais (cookies)
      // evitando que a falha de um arquivo cancele todos os outros.
      for (const asset of SHELL_ASSETS) {
        try {
          const res = await fetch(asset, { credentials: 'include' });
          if (res.ok) await cache.put(asset, res);
        } catch (err) {
          console.debug('Falha ao cachear asset:', asset, err);
        }
      }
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
        return new Response(JSON.stringify({
          error: 'offline',
          detail: 'Sem conexão com a internet. Você pode ouvir as músicas salvas na aba "No celular".'
        }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' }
        });
      })
    );
    return;
  }

  // 3. Navegação principal (/):
  // Network first com fallback resiliente para o cache do shell (permite abrir o PWA mesmo em modo avião)
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then(res => {
          if (res.ok && (url.pathname === '/' || url.pathname === '')) {
            const clone = res.clone();
            caches.open(CACHE_NAME).then(cache => {
              cache.put(req, clone.clone());
              cache.put('/', clone);
            });
          }
          return res;
        })
        .catch(async () => {
          // Busca no cache ignorando query params (ex: ?sk=...)
          const matchReq = await caches.match(req, { ignoreSearch: true });
          if (matchReq) return matchReq;
          const matchSlash = await caches.match('/', { ignoreSearch: true });
          if (matchSlash) return matchSlash;
          const keys = await caches.keys();
          for (const k of keys) {
            const c = await caches.open(k);
            const m = await c.match('/', { ignoreSearch: true });
            if (m) return m;
          }
          return new Response(
            '<!doctype html><meta charset="utf-8"><title>StremMusic Offline</title>' +
            '<style>body{background:#020617;color:#f1f5f9;font-family:sans-serif;padding:30px;text-align:center}</style>' +
            '<h1>StremMusic</h1><p>Conecte-se à internet uma vez para instalar a casca do app.</p>',
            { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
          );
        })
    );
    return;
  }

  // 4. Arquivos estáticos (ícones, manifest, fontes):
  // Stale-While-Revalidate com fallback
  event.respondWith(
    caches.match(req, { ignoreSearch: true }).then(cached => {
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
