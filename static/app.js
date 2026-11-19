const $ = s => document.querySelector(s);
const list = $('#list'), audio = $('#audio');
const seek = $('#seek'), vol = $('#vol');
let queue = [], qIndex = -1, currentTab = 'search';
let currentId = null, playToken = 0, isSeeking = false, streamTries = 0;
let playTimer = null, resolveTimer = null, lastResolvedId = '', SETTINGS = null;
let isShuffle = false;
let repeatMode = 0;   // 0 = desligado, 1 = repetir tudo, 2 = repetir uma
let _lastLibTab = 'playlists';

const ICON = {
  play:      { d: '<polygon points="6 4 20 12 6 20 6 4"/>', solid: true },
  pause:     { d: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>', solid: true },
  volHi:     '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>',
  volLo:     '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>',
  mute:      '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="22" y1="9" x2="16" y2="15"/><line x1="16" y1="9" x2="22" y2="15"/>',
  heart:     '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>',
  heartFill: { d: '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>', solid: true },
  dl:        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
  upload:    '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
  note:      '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  err:       '<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>',
  search:    '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  list:      '<path d="M21 15V6"/><path d="M18.5 18a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z"/><path d="M12 12H3"/><path d="M16 6H3"/><path d="M12 18H3"/>',
  plAdd:     '<path d="M11 12H3"/><path d="M16 6H3"/><path d="M16 18H3"/><path d="M18 9v6"/><path d="M21 12h-6"/>',
  trash:     '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
  close:     '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  folder:    '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  expand:    '<polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>',
  collapse:  '<polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/>',
  phone:     '<rect width="14" height="20" x="5" y="2" rx="2" ry="2"/><path d="M12 18h.01"/>',
  radio:     '<path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9"/><path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5"/><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5"/><path d="M19.1 4.9C23 8.8 23 15.1 19.1 19"/>',
  more:      '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
  edit:      '<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>',
  arrowLeft: '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
  plus:      '<path d="M5 12h14"/><path d="M12 5v14"/>',
  sparkles:  '<path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/>',
  user:      '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  palette:   '<circle cx="13.5" cy="6.5" r=".5" fill="currentColor"/><circle cx="17.5" cy="10.5" r=".5" fill="currentColor"/><circle cx="8.5" cy="7.5" r=".5" fill="currentColor"/><circle cx="6.5" cy="12.5" r=".5" fill="currentColor"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z"/>',
  database:  '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5V19A9 3 0 0 0 21 19V5"/><path d="M3 12A9 3 0 0 0 21 12"/>',
  sliders:   '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="2" y1="14" x2="6" y2="14"/><line x1="10" y1="8" x2="14" y2="8"/><line x1="18" y1="16" x2="22" y2="16"/>',
  globe:     '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
  activity:  '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  repeat:    '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
  repeatOne: '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/><path d="M11 10h1v4"/>',
};
function svgIcon(ic, cls = '') {
  const isObj = typeof ic === 'object' && ic !== null;
  const body = isObj ? ic.d : ic;
  const fill = (isObj && ic.solid) ? 'currentColor' : 'none';
  return `<svg viewBox="0 0 24 24" class="${cls}" fill="${fill}" stroke="currentColor" stroke-width="1.85" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
}

// ---------- Interações de UX ----------
$('#btn-toggle-menu').onclick = () => document.body.classList.toggle('sidebar-closed');
$('#btn-expand').onclick = () => toggleFullscreen();
$('#btn-queue').onclick = () => {
  if (document.body.classList.contains('player-fs') && window.innerWidth >= 980) {
    const open = document.body.classList.contains('fs-side-open');
    if (open && _fsSideTab === 'queue') {
      document.body.classList.remove('fs-side-open');
      syncFsButtons();
      return;
    }
    _fsSideTab = 'queue';
    document.body.classList.add('fs-side-open');
    renderFsSide();
    return;
  }
  document.body.classList.toggle('queue-open');
  renderQueuePanel();
};
$('#btn-close-queue').onclick = () => document.body.classList.remove('queue-open');

function setHidden(h) {
  document.body.classList.toggle('player-hidden', h);
  try { h ? localStorage.setItem('mw-player-hidden', '1') : localStorage.removeItem('mw-player-hidden'); } catch {}
}
$('#player-collapse').onclick = () => { if (!document.body.classList.contains('player-fs')) setHidden(true); };
$('#player-restore').onclick = () => setHidden(false);
$('#queue-backdrop').onclick = () => document.body.classList.remove('queue-open');
try { if (localStorage.getItem('mw-player-hidden') === '1') document.body.classList.add('player-hidden'); } catch {}

const scrollArea = $('#scroll');
scrollArea.addEventListener('scroll', () => {
  document.body.classList.toggle('scrolled', scrollArea.scrollTop > 6);
  document.body.classList.toggle('scrolled-dn', scrollArea.scrollTop > 650);
}, { passive: true });
$('#btn-top').onclick = () => scrollArea.scrollTo({ top: 0, behavior: 'smooth' });

function setVol(v) {
  audio.muted = false;
  applyVolume(Math.min(1, Math.max(0, v)));
  try { localStorage.setItem('mw-vol', vol.value); } catch {}
}

$('#btn-shuffle').onclick = function() { isShuffle = !isShuffle; this.classList.toggle('active', isShuffle); updatePrevNext(); };
$('#btn-repeat').onclick = function() { repeatMode = (repeatMode + 1) % 3; applyRepeatUI(); updatePrevNext(); };
function applyRepeatUI() {
  const b = $('#btn-repeat');
  b.classList.toggle('active', repeatMode > 0);
  b.title = repeatMode === 0 ? 'Repetir' : (repeatMode === 1 ? 'Repetir tudo' : 'Repetir esta faixa');
  b.innerHTML = svgIcon(repeatMode === 2 ? ICON.repeatOne : ICON.repeat);
}

function moveQueue(from, to) {
  if (from < 0 || to < 0 || from === to || from >= queue.length || to >= queue.length) return;
  const [moved] = queue.splice(from, 1);
  queue.splice(to, 0, moved);
  qIndex = queue.findIndex(x => x.videoId === currentId);
  renderQueuePanel();
  if (_fsSideTab === 'queue' && document.body.classList.contains('player-fs')) renderFsSide();
}

function movePlaylistTrack(pid, from, to) {
  if (from < 0 || to < 0 || from === to || from >= queue.length || to >= queue.length) return;
  const [moved] = queue.splice(from, 1);
  queue.splice(to, 0, moved);
  qIndex = queue.findIndex(x => x.videoId === currentId);
  const listEl = list.querySelector('.track-list');
  if (listEl) {
    listEl.outerHTML = renderList(queue, { inPlaylist: pid });
    bindTracks(queue, { inPlaylist: pid, ownQueue: true });
    enablePlaylistDrag(pid);
    markPlaying();
  }
  api(`/api/playlists/${pid}/reorder`, {
    method: 'POST', body: JSON.stringify({ order: queue.map(x => x.videoId) }),
    headers: { 'Content-Type': 'application/json' },
  }).catch(() => {});
}

function enableQueueDrag(container) {
  let dragEl = null;
  container.querySelectorAll('.q-item').forEach(el => {
    el.draggable = true;
    el.addEventListener('dragstart', e => {
      dragEl = el; el.classList.add('dragging');
      try { e.dataTransfer.setData('text/plain', el.dataset.qi); } catch {}
      e.dataTransfer.effectAllowed = 'move';
    });
    el.addEventListener('dragend', () => {
      el.classList.remove('dragging');
      container.querySelectorAll('.q-item').forEach(x => x.classList.remove('drag-over'));
      dragEl = null;
    });
    el.addEventListener('dragover', e => { e.preventDefault(); if (el !== dragEl) el.classList.add('drag-over'); });
    el.addEventListener('dragleave', () => el.classList.remove('drag-over'));
    el.addEventListener('drop', e => {
      e.preventDefault();
      el.classList.remove('drag-over');
      if (!dragEl || dragEl === el) return;
      moveQueue(+dragEl.dataset.qi, +el.dataset.qi);
    });
  });
}

function renderQueueHTML() {
  if (!queue.length) {
    return '<div style="color:var(--dim); padding:24px 16px; text-align:center;">Fila vazia</div>'
      + '<div style="text-align:center; padding:0 16px 20px;"><button class="btn ghost sm" id="btn-continue">' + svgIcon(ICON.sparkles) + ' Continuar ouvindo</button><div class="set-sub" style="margin-top:8px">Baseado no que você costuma ouvir em seguida</div></div>';
  }
  return queue.map((t, i) => `
    <div class="q-item ${i === qIndex ? 'active' : ''}" data-qi="${i}" title="Arraste para reordenar">
      <img src="${esc(thumbUrl(t))}" data-vid="${esc(t.videoId)}" onerror="fixImg(this)" alt="">
      <div class="q-info"><div class="q-title">${esc(t.title)}</div><div class="q-artist">${esc(t.artist)}</div></div>
      <div class="q-ops">
        <button class="q-op" data-qup="${i}" title="Subir" aria-label="Subir ${esc(t.title)}">▲</button>
        <button class="q-op" data-qdown="${i}" title="Descer" aria-label="Descer ${esc(t.title)}">▼</button>
      </div>
    </div>`).join('')
    + '<div style="text-align:center; padding:14px 16px 20px;"><button class="btn ghost sm" id="btn-continue">' + svgIcon(ICON.sparkles) + ' Sugerir próximas faixas</button></div>';
}

function bindQueueContainer(container) {
  const bc = container.querySelector('#btn-continue');
  if (bc) bc.onclick = smartContinue;
  container.querySelectorAll('.q-item').forEach(el => el.onclick = () => {
    const t = queue[+el.dataset.qi];
    if (t) play(t);
  });
  container.querySelectorAll('.q-op[data-qup]').forEach(b => b.onclick = e => {
    e.stopPropagation(); moveQueue(+b.dataset.qup, +b.dataset.qup - 1);
  });
  container.querySelectorAll('.q-op[data-qdown]').forEach(b => b.onclick = e => {
    e.stopPropagation(); moveQueue(+b.dataset.qdown, +b.dataset.qdown + 1);
  });
  enableQueueDrag(container);
  const active = container.querySelector('.q-item.active');
  if (active) active.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function renderQueuePanel() {
  const container = $('#queue-list-container');
  if (!container) return;
  container.innerHTML = renderQueueHTML();
  bindQueueContainer(container);
}

// ---------- Continuar ouvindo ----------
async function smartContinue() {
  const seed = currentId || (queue[qIndex] && queue[qIndex].videoId);
  if (!seed) { toast('Toque uma música primeiro para gerar recomendações'); return; }
  try {
    const d = await api('/api/smart/next/' + seed);
    const known = (d.tracks || []).filter(t => t.known && !queue.some(q => q.videoId === t.videoId));
    if (known.length > 0) {
      const add = known.slice(0, 5).map(t => ({ videoId: t.videoId, title: t.title, artist: t.artist, thumbnail: t.thumbnail, duration: '' }));
      queue = queue.concat(add);
      renderQueuePanel();
      if (_fsSideTab === 'queue' && document.body.classList.contains('player-fs')) renderFsSide();
      toast(`+${add.length} faixa${add.length !== 1 ? 's' : ''} na fila`);
    } else {
      toast('Buscando faixas parecidas…');
      const r = await api('/api/radio/' + seed);
      const radioTracks = (r.tracks || []).filter(t => t.videoId && t.videoId !== seed && !queue.some(q => q.videoId === t.videoId));
      if (!radioTracks.length) { toast('Nenhuma faixa similar encontrada agora'); return; }
      const add = radioTracks.slice(0, 5).map(t => ({ videoId: t.videoId, title: t.title, artist: t.artist, thumbnail: t.thumbnail, duration: t.duration || '' }));
      queue = queue.concat(add);
      renderQueuePanel();
      if (_fsSideTab === 'queue' && document.body.classList.contains('player-fs')) renderFsSide();
      toast(`+${add.length} músicas na fila`);
    }
  } catch (e) { toast(e.message); }
}

let _smartBusy = false, _learnNext = null;
async function smartAutoContinue() {
  if (_smartBusy || !currentId) return;
  _smartBusy = true;
  try {
    if (_learnNext === null) {
      try { _learnNext = (await api('/api/settings')).settings.learn_next !== false; }
      catch { _learnNext = false; }
    }
    if (!_learnNext) return;
    let nextTrack = null;
    try {
      const d = await api('/api/smart/next/' + currentId);
      const cand = (d.tracks || []).find(t => t.known && !queue.some(q => q.videoId === t.videoId));
      if (cand) {
        nextTrack = { videoId: cand.videoId, title: cand.title, artist: cand.artist, thumbnail: cand.thumbnail, duration: '' };
        toast(`A seguir: ${(cand.title || '').slice(0, 40)}`);
      }
    } catch {}
    if (!nextTrack) {
      try {
        const r = await api('/api/radio/' + currentId);
        const cand = (r.tracks || []).find(t => t.videoId && t.videoId !== currentId && !queue.some(q => q.videoId === t.videoId));
        if (cand) {
          nextTrack = { videoId: cand.videoId, title: cand.title, artist: cand.artist, thumbnail: cand.thumbnail, duration: cand.duration || '' };
          toast(`Rádio: ${(cand.title || '').slice(0, 40)}`);
        }
      } catch {}
    }
    if (!nextTrack) return;
    queue = queue.concat([nextTrack]);
    renderQueuePanel();
    play(nextTrack);
  } catch (err) {
    console.error('smartAutoContinue error:', err);
  } finally {
    setTimeout(() => { _smartBusy = false; }, 1500);
  }
}
$('#btn-smart-q').onclick = smartContinue;

// ---------- Utilidades ----------
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.style.display = 'block';
  clearTimeout(t._tm); t._tm = setTimeout(() => t.style.display = 'none', 3000);
}
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
const fmtTime = s => (!s || isNaN(s)) ? '0:00' : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

function thumbUrl(t) {
  if (!t) return '';
  if (t.thumbnail) return t.thumbnail;
  const id = t.videoId || t.seedId || t.id || '';
  if (!id) return '';
  if (id.startsWith('up_')) return t.art ? `/api/local/${id}/art` : '';
  return `https://i.ytimg.com/vi/${id}/mqdefault.jpg`;
}

function playlistCoverHTML(items, badge = '', extraInside = '') {
  const urls = [];
  for (const it of (items || [])) {
    const u = thumbUrl(it);
    if (u && !urls.includes(u)) urls.push(u);
    if (urls.length >= 4) break;
  }
  const badgeHtml = badge ? `<span class="pl-badge">${esc(badge)}</span>` : '';
  if (urls.length >= 4) {
    return `<div class="pl-cover"><div class="pl-mosaic">${urls.map(u => `<img src="${esc(u)}" loading="lazy" alt="" onerror="this.style.visibility='hidden'">`).join('')}</div>${badgeHtml}${extraInside}</div>`;
  }
  if (urls.length >= 1) {
    return `<div class="pl-cover"><img class="pl-cover-img" src="${esc(urls[0])}" loading="lazy" alt="" onerror="this.style.visibility='hidden'">${badgeHtml}${extraInside}</div>`;
  }
  return `<div class="pl-cover pl-empty-cover">${svgIcon(ICON.note)}${badgeHtml}${extraInside}</div>`;
}

function playUpload(it) {
  if (!it) return;
  const t = { videoId: it.id, title: it.title, artist: it.artist,
              thumbnail: it.art ? `/api/local/${it.id}/art` : '',
              duration: it.dur ? fmtTime(it.dur) : '', local: true };
  if (!queue.some(x => x.videoId === t.videoId)) queue = queue.concat([t]);
  play(t);
}

function fixImg(img) {
  const vid = img.dataset.vid || img.closest('.track')?.dataset?.id || '';
  if (!vid) { img.style.visibility = 'hidden'; return; }
  img.onerror = () => { img.onerror = null; img.style.visibility = 'hidden'; };
  if (vid.startsWith('up_')) {
    if (!img.src.includes('/api/thumb/') && !img.src.includes('/api/local/')) img.src = `/api/thumb/${vid}`;
    else img.style.visibility = 'hidden';
    return;
  }
  if (img.src.includes('/api/thumb/')) img.src = `https://i.ytimg.com/vi/${vid}/mqdefault.jpg`;
  else img.src = `/api/thumb/${vid}`;
}

let CURRENT_USER = null;
const isAdmin = () => Boolean(CURRENT_USER && CURRENT_USER.is_admin);
async function refreshCurrentUser() {
  try {
    const res = await api('/api/me');
    if (res && res.user) {
      CURRENT_USER = res.user;
      const chip = $('#user-chip');
      const chipName = $('#user-chip-name');
      const chipAv = $('#user-chip-avatar');
      if (chip && chipName) {
        chipName.textContent = res.user.name;
        if (chipAv) chipAv.textContent = (res.user.name || 'U').charAt(0).toUpperCase();
        chip.style.display = 'inline-flex';
        chip.onclick = () => nav('settings');
      }
      const hp = $('#btn-host-panel');
      if (hp) {
        const isHostLocal = ['127.0.0.1', 'localhost', '::1'].includes(location.hostname);
        hp.style.display = (isHostLocal && isAdmin()) ? '' : 'none';
      }
    }
  } catch {}
}

async function api(url, opts) {
  let r;
  try { r = await fetch(url, opts); }
  catch (e) { if (e?.name === 'AbortError') throw e; throw new Error('Sem resposta do servidor. O app.py está rodando?'); }
  let data;
  try { data = await r.json(); }
  catch { throw new Error(`Resposta inválida do servidor (HTTP ${r.status})`); }
  if (!r.ok) throw new Error(data.detail || data.error || `Erro HTTP ${r.status}`);
  return data;
}
function showError(e, contexto) {
  list.innerHTML = `<div class="error"><b>${esc(contexto)}</b>\n\n${esc(e.message)}</div>`;
}

const LIB_TABS = ['playlists', 'favorites', 'history', 'uploads', 'downloads'];
function activateNav(tab) {
  document.querySelectorAll('nav button[data-tab]').forEach(x => {
    const on = x.dataset.tab === tab;
    x.classList.toggle('active', on);
    if (on) x.setAttribute('aria-current', 'page'); else x.removeAttribute('aria-current');
  });
  const isLib = LIB_TABS.includes(tab);
  if (isLib) _lastLibTab = tab;
  const mLib = $('#nav-mobile-lib');
  if (mLib) mLib.classList.toggle('active', isLib && window.innerWidth <= 900);
  const mBar = $('#mobile-lib-bar');
  if (mBar) {
    mBar.classList.toggle('show', isLib);
    mBar.querySelectorAll('.mlib-pill').forEach(p => p.classList.toggle('on', p.dataset.lib === tab));
  }
}
function nav(tab) {
  _tabScroll[currentTab] = $('#scroll').scrollTop;
  activateNav(tab);
  currentTab = tab;
  const done = loadTab();
  const alvo = _tabScroll[currentTab] || 0;
  Promise.resolve(done).then(() => { $('#scroll').scrollTop = alvo; }).catch(() => {});
}

// ---------- Biblioteca offline NO APARELHO (IndexedDB) ----------
const Offline = (() => {
  let dbp = null;
  function db() {
    if (!dbp) dbp = new Promise((res, rej) => {
      const rq = indexedDB.open('stremmusic', 1);
      rq.onupgradeneeded = () => rq.result.createObjectStore('tracks', { keyPath: 'videoId' });
      rq.onsuccess = () => res(rq.result);
      rq.onerror = () => rej(rq.error);
    }).catch(() => null);
    return dbp;
  }
  async function tx(mode, fn) {
    const d = await db();
    if (!d) throw new Error('IndexedDB indisponível neste navegador');
    return new Promise((res, rej) => {
      const t = d.transaction('tracks', mode);
      const rq = fn(t.objectStore('tracks'));
      t.oncomplete = () => res(rq && rq.result);
      t.onerror = () => rej(t.error);
    });
  }
  return {
    save: rec => tx('readwrite', s => s.put(rec)),
    get: async id => { try { return await tx('readonly', s => s.get(id)); } catch { return undefined; } },
    del: id => tx('readwrite', s => s.delete(id)),
    list: async () => { try { return (await tx('readonly', s => s.getAll())) || []; } catch { return []; } },
  };
})();

let offlineIds = new Set();
async function refreshOfflineIds() {
  try { offlineIds = new Set((await Offline.list()).map(x => x.videoId)); markOffButtons(); } catch {}
}
function markOffButtons() {
  document.querySelectorAll('.btn-off').forEach(b => {
    const tr = b.closest('.track');
    b.classList.toggle('saved', !!(tr && offlineIds.has(tr.dataset.id)));
  });
}

let _blobUrl = null;
function setOfflineBadge(on) { const c = $('#now-offline'); if (c) c.classList.toggle('on', !!on); }

const OffQ = { items: [], cur: null, running: false };
let _offqEl = null;
function offqUI() {
  if (!_offqEl) {
    _offqEl = document.createElement('div');
    _offqEl.className = 'offq';
    _offqEl.innerHTML = `<span class="oq-txt"></span><div class="oq-bar"><div class="oq-fill"></div></div>`;
    document.body.appendChild(_offqEl);
  }
  return _offqEl;
}
function offqPaint() {
  const el = offqUI();
  const pending = (OffQ.cur ? 1 : 0) + OffQ.items.length;
  if (!pending) { el.classList.remove('on'); return; }
  el.classList.add('on');
  el.querySelector('.oq-txt').textContent = OffQ.cur
    ? `Salvando: ${OffQ.cur.t.title || OffQ.cur.t.videoId}`
    : `${pending} na fila offline…`;
  el.querySelector('.oq-fill').style.width = (OffQ.cur ? (OffQ.cur.pct || 0) : 0) + '%';
}
async function offqPump() {
  if (OffQ.running) return;
  OffQ.running = true;
  while (OffQ.items.length) {
    const job = OffQ.items.shift();
    if (offlineIds.has(job.t.videoId)) { offqPaint(); continue; }
    OffQ.cur = { t: job.t, pct: 0 }; offqPaint();
    try {
      const r = await fetch(`/api/stream/${job.t.videoId}`);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      let blob;
      const len = +(r.headers.get('content-length') || 0);
      if (r.body && len) {
        const reader = r.body.getReader(); const chunks = []; let got = 0;
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          chunks.push(value); got += value.length;
          OffQ.cur.pct = Math.min(99, Math.round(got / len * 100)); offqPaint();
        }
        blob = new Blob(chunks, { type: r.headers.get('content-type') || 'audio/mp4' });
      } else {
        blob = await r.blob();
      }
      if (!blob.size || blob.size < 4096) throw new Error('o áudio veio vazio');
      await Offline.save({
        videoId: job.t.videoId, title: job.t.title || '', artist: job.t.artist || '',
        thumbnail: job.t.thumbnail || '', duration: job.t.duration || '',
        blob, size: blob.size, type: blob.type || 'audio/mp4', savedAt: Date.now(),
      });
      offlineIds.add(job.t.videoId);
      markOffButtons();
      toast(`Salva offline (${(blob.size / 1048576).toFixed(1)} MB) — ${job.t.title || ''}`);
    } catch (e) {
      toast(`Falha ao salvar ${job.t.title || ''}: ${e.message || e}`.slice(0, 110));
    }
    OffQ.cur = null; offqPaint();
  }
  OffQ.running = false;
  offqPaint();
}
function downloadOffline(t) {
  if (!t || !t.videoId) return;
  if (offlineIds.has(t.videoId)) { toast('Já está salva no aparelho'); return; }
  if (OffQ.cur?.t.videoId === t.videoId || OffQ.items.some(x => x.t.videoId === t.videoId)) {
    toast('Já está na fila de download'); return;
  }
  OffQ.items.push({ t }); offqPaint(); offqPump();
}
function downloadOfflineMany(tracks) {
  let n = 0;
  for (const t of tracks || []) {
    if (!t?.videoId || offlineIds.has(t.videoId)) continue;
    if (OffQ.cur?.t.videoId === t.videoId || OffQ.items.some(x => x.t.videoId === t.videoId)) continue;
    OffQ.items.push({ t }); n++;
  }
  if (n) { toast(`${n} música${n !== 1 ? 's' : ''} na fila offline`); offqPaint(); offqPump(); }
  else toast('Todas já estão salvas no aparelho');
}

// ---------- Sheet de ações da faixa (mobile + contextual) ----------
function openTrackSheet(t, opts = {}) {
  if (!t) return;
  const ov = $('#track-sheet-ov'), box = $('#track-sheet');
  const saved = offlineIds.has(t.videoId);
  box.innerHTML = `
    <div class="sheet-head">
      <img src="${esc(thumbUrl(t))}" data-vid="${esc(t.videoId)}" onerror="fixImg(this)" alt="">
      <div style="min-width:0;flex:1">
        <div class="sheet-title">${esc(t.title)}</div>
        <div class="sheet-sub">${esc(t.artist || '')}</div>
      </div>
    </div>
    <button type="button" class="sheet-btn" data-sa="fav">${svgIcon(opts.fav ? ICON.heartFill : ICON.heart)}<span>${opts.inFavorites ? 'Remover das favoritas' : 'Favoritar'}</span></button>
    ${opts.inPlaylist ? '' : `<button type="button" class="sheet-btn" data-sa="pl">${svgIcon(ICON.plAdd)}<span>Adicionar à playlist</span></button>`}
    <button type="button" class="sheet-btn" data-sa="radio">${svgIcon(ICON.radio)}<span>Iniciar rádio desta faixa</span></button>
    ${t.artist ? `<button type="button" class="sheet-btn" data-sa="artist">${svgIcon(ICON.user)}<span>Ir para o artista</span></button>` : ''}
    ${isAdmin() ? `<button type="button" class="sheet-btn" data-sa="dl">${svgIcon(ICON.dl)}<span>Baixar MP3 no servidor</span></button>` : ''}
    <button type="button" class="sheet-btn" data-sa="off">${svgIcon(ICON.phone)}<span>${saved ? 'Já salva no aparelho' : 'Salvar offline no celular'}</span></button>
    ${opts.inPlaylist ? `<button type="button" class="sheet-btn danger" data-sa="rmpl">${svgIcon(ICON.trash)}<span>Remover da playlist</span></button>` : ''}`;
  ov.classList.add('open');
  const close = () => ov.classList.remove('open');
  ov.onclick = e => { if (e.target === ov) close(); };
  box.querySelector('[data-sa="fav"]')?.addEventListener('click', () => { close(); triggerTrackAction(t, 'fav', opts); });
  box.querySelector('[data-sa="pl"]')?.addEventListener('click', () => { close(); openPlModal(t); });
  box.querySelector('[data-sa="radio"]')?.addEventListener('click', async () => {
    close();
    try {
      const d = await api('/api/radio/' + t.videoId);
      const add = (d.tracks || []).filter(x => !queue.some(q => q.videoId === x.videoId));
      if (!add.length) return toast('Nenhuma faixa nova encontrada');
      queue = queue.concat(add); renderQueuePanel(); updatePrevNext();
      toast(`Rádio: +${add.length} faixas na fila`);
    } catch (e) { toast(e.message); }
  });
  box.querySelector('[data-sa="artist"]')?.addEventListener('click', () => { close(); openArtist(t.artist, t.channelId || ''); });
  box.querySelector('[data-sa="dl"]')?.addEventListener('click', () => { close(); triggerServerDownload(t); });
  box.querySelector('[data-sa="off"]')?.addEventListener('click', () => { close(); downloadOffline(t); });
  box.querySelector('[data-sa="rmpl"]')?.addEventListener('click', () => { close(); triggerTrackAction(t, 'rmpl', opts); });
}

function triggerServerDownload(cur, btnEl) {
  if (!cur || (btnEl && btnEl.dataset.busy)) return;
  if (btnEl) { btnEl.dataset.busy = '1'; btnEl.classList.add('busy'); }
  toast('Baixando: ' + cur.title);
  fetch(`/api/download/${cur.videoId}?title=${encodeURIComponent(cur.title || '')}`, { method: 'POST' })
    .then(r => r.json().then(d => ({ d })))
    .then(({ d }) => d.ok
      ? toast(d.source === 'cache' ? 'Salva do cache: ' + d.file : (d.cached ? 'Já baixada: ' : 'Download concluído: ') + d.file)
      : toast((d.detail || 'Erro no download').slice(0, 120)))
    .catch(() => toast('Erro no download'))
    .finally(() => { if (btnEl) { delete btnEl.dataset.busy; btnEl.classList.remove('busy'); } });
}

async function triggerTrackAction(cur, kind, opts = {}, rowEl = null) {
  if (!cur) return;
  const id = cur.videoId;
  if (kind === 'fav') {
    if (opts.inFavorites) {
      try {
        await api('/api/favorites/' + id, { method: 'DELETE' });
        queue = queue.filter(x => x.videoId !== id);
        if (rowEl) rowEl.remove();
        toast('Removida das favoritas'); renderQueuePanel();
      } catch { toast('Erro ao remover'); }
    } else {
      try {
        await api(`/api/favorites/${id}?title=${encodeURIComponent(cur.title || '')}&artist=${encodeURIComponent(cur.artist || '')}&thumbnail=${encodeURIComponent(cur.thumbnail || '')}`, { method: 'POST' });
        const fb = rowEl?.querySelector('.btn-fav');
        if (fb) { fb.classList.add('fav'); fb.innerHTML = svgIcon(ICON.heartFill); }
        toast('Adicionada às favoritas');
      } catch { toast('Erro ao favoritar'); }
    }
  } else if (kind === 'rmpl' && opts.inPlaylist) {
    try {
      await api(`/api/playlists/${opts.inPlaylist}/items/${id}`, { method: 'DELETE' });
      queue = queue.filter(x => x.videoId !== id);
      const targetRow = rowEl || list.querySelector(`.track[data-id="${CSS.escape(id)}"]`);
      if (targetRow) targetRow.remove();
      toast('Removida da playlist'); renderQueuePanel();
    } catch (e) { toast(e.message); }
  }
}

// ---------- Renderização de listas ----------
function trackHTML(t, i, opts = {}) {
  const mainBtn = opts.inPlaylist
    ? `<button class="btn-rmpl" title="Remover da playlist">${svgIcon(ICON.trash)}</button>`
    : `<button class="btn-fav ${opts.fav ? 'fav' : ''}" title="Favoritar">${svgIcon(opts.fav ? ICON.heartFill : ICON.heart)}</button>`;
  return `<div class="track" data-id="${esc(t.videoId)}">
    <div class="t-num">
      <span class="num">${i + 1}</span>
      <span class="eq"><span></span><span></span><span></span><span></span></span>
      ${svgIcon(ICON.play)}
    </div>
    <img src="${esc(thumbUrl(t))}" data-vid="${esc(t.videoId)}" onerror="fixImg(this)" loading="lazy" alt="">
    <div class="t-info"><div class="t-title">${esc(t.title)}</div><div class="t-artist"><span class="artist-link" data-artist="${esc(t.artist)}" data-cid="${esc(t.channelId || '')}" title="Abrir artista">${esc(t.artist)}</span></div></div>
    <div class="t-dur">${esc(t.duration || '')}</div>
    <div class="t-actions">
      ${mainBtn}
      ${opts.inPlaylist ? '' : `<button class="btn-pl" title="Adicionar à playlist">${svgIcon(ICON.plAdd)}</button>`}
      ${opts.inPlaylist ? `<button class="btn-qup" title="Subir na playlist">▲</button><button class="btn-qdown" title="Descer na playlist">▼</button>` : ''}
      ${isAdmin() ? `<button class="btn-dl" title="Baixar MP3 no servidor">${svgIcon(ICON.dl)}</button>` : ''}
      <button class="btn-off" title="Salvar no aparelho">${svgIcon(ICON.phone)}</button>
      <button class="btn-more" title="Mais ações" aria-label="Mais ações">${svgIcon(ICON.more)}</button>
    </div>
  </div>`;
}

function renderList(items, opts = {}) {
  if (!items.length) return '';
  return `<div class="track-list">
    <div class="track-list-header"><div>#</div><div></div><div>Título</div><div>Duração</div><div></div></div>
    ${items.map((t, i) => trackHTML(t, i, opts)).join('')}
  </div>`;
}

function bindTracks(items, opts = {}) {
  const scope = opts.container || list;
  scope.querySelectorAll('.track[data-id]').forEach(el => {
    const id = el.dataset.id;
    const get = () => items.find(x => x.videoId === id);
    const startPlay = () => {
      const t = get(); if (!t) return;
      if (!opts.ownQueue) queue = items.slice();
      play(t);
    };
    el.onclick = e => { if (e.target.closest('button')) return; startPlay(); };
    el.tabIndex = 0;
    el.setAttribute('role', 'button');
    el.setAttribute('aria-label', 'Tocar ' + ((get() || {}).title || 'faixa'));
    el.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); startPlay(); } };

    el.querySelectorAll('.artist-link').forEach(a => {
      a.onclick = ev => { ev.stopPropagation(); openArtist(a.dataset.artist, a.dataset.cid); };
    });

    if (opts.inPlaylist) {
      const idx = () => queue.findIndex(x => x.videoId === id);
      const qup = el.querySelector('.btn-qup'), qdn = el.querySelector('.btn-qdown');
      if (qup) qup.onclick = e => { e.stopPropagation(); movePlaylistTrack(opts.inPlaylist, idx(), idx() - 1); };
      if (qdn) qdn.onclick = e => { e.stopPropagation(); movePlaylistTrack(opts.inPlaylist, idx(), idx() + 1); };
    }

    const favBtn = el.querySelector('.btn-fav');
    if (favBtn) favBtn.onclick = async () => {
      const cur = get();
      if (!cur || favBtn.dataset.busy) return;
      favBtn.dataset.busy = '1';
      try { await triggerTrackAction(cur, 'fav', opts, el); }
      finally { delete favBtn.dataset.busy; }
    };

    const plBtn = el.querySelector('.btn-pl');
    if (plBtn) plBtn.onclick = () => { const cur = get(); if (cur) openPlModal(cur); };

    const rmBtn = el.querySelector('.btn-rmpl');
    if (rmBtn) rmBtn.onclick = () => triggerTrackAction(get(), 'rmpl', opts, el);

    const dlBtn = el.querySelector('.btn-dl');
    if (dlBtn) dlBtn.onclick = () => triggerServerDownload(get(), dlBtn);

    const offBtn = el.querySelector('.btn-off');
    if (offBtn) offBtn.onclick = () => downloadOffline(get());

    const moreBtn = el.querySelector('.btn-more');
    if (moreBtn) moreBtn.onclick = e => { e.stopPropagation(); openTrackSheet(get(), opts); };
  });
  markPlaying();
  markOffButtons();
}

function markPlaying() {
  list.querySelectorAll('.track[data-id]').forEach(el =>
    el.classList.toggle('playing', el.dataset.id === currentId));
}

function enablePlaylistDrag(pid) {
  let dragEl = null;
  list.querySelectorAll('.track[data-id]').forEach(el => {
    el.draggable = true;
    el.addEventListener('dragstart', e => {
      dragEl = el; el.classList.add('dragging');
      try { e.dataTransfer.setData('text/plain', el.dataset.id); } catch {}
      e.dataTransfer.effectAllowed = 'move';
    });
    el.addEventListener('dragend', () => {
      el.classList.remove('dragging');
      list.querySelectorAll('.track').forEach(x => x.classList.remove('drag-over'));
      dragEl = null;
    });
    el.addEventListener('dragover', e => { e.preventDefault(); if (el !== dragEl) el.classList.add('drag-over'); });
    el.addEventListener('dragleave', () => el.classList.remove('drag-over'));
    el.addEventListener('drop', async e => {
      e.preventDefault();
      el.classList.remove('drag-over');
      if (!dragEl || dragEl === el) return;
      const from = queue.findIndex(x => x.videoId === dragEl.dataset.id);
      const to = queue.findIndex(x => x.videoId === el.dataset.id);
      if (from < 0 || to < 0) return;
      const [moved] = queue.splice(from, 1);
      queue.splice(to, 0, moved);
      qIndex = queue.findIndex(x => x.videoId === currentId);
      const listEl = list.querySelector('.track-list');
      if (listEl) {
        listEl.outerHTML = renderList(queue, { inPlaylist: pid });
        bindTracks(queue, { inPlaylist: pid, ownQueue: true });
        enablePlaylistDrag(pid);
      }
      try {
        await api(`/api/playlists/${pid}/reorder`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ order: queue.map(x => x.videoId) }),
        });
        toast('Ordem salva');
      } catch (e) { toast(e.message); }
    });
  });
}

// ---------- Navegação ----------
document.querySelectorAll('nav button[data-tab]').forEach(b => b.onclick = () => nav(b.dataset.tab));
const mLibBtn = $('#nav-mobile-lib');
if (mLibBtn) mLibBtn.onclick = () => nav(_lastLibTab || 'playlists');
document.querySelectorAll('#mobile-lib-bar .mlib-pill').forEach(p => {
  p.onclick = () => nav(p.dataset.lib);
});

$('#search').onkeydown = e => {
  if (e.key === 'Enter') { closeSugg(); activateNav('search'); currentTab = 'search'; search(); }
  if (e.key === 'Escape') closeSugg();
};
let _searchCtl = null, _searchQ = '';

// ---------- Página do artista ----------
let _artistPrevTab = null;
async function openArtist(name, channelId) {
  if (!name) return;
  if (!_artistPrevTab) _artistPrevTab = currentTab;
  activateNav('search'); currentTab = 'search';
  list.innerHTML = skeletonList(7);
  try {
    const d = await api('/api/artist?id=' + encodeURIComponent(channelId || '') + '&name=' + encodeURIComponent(name));
    const tracks = d.tracks || [];
    list.innerHTML = `<div class="sect-header artist-page"><div><div class="sect-sub">Artista</div><div class="sect-title">${esc(d.name || name)}</div></div>
      <div class="pl-actions" style="margin:0">
        <button class="btn" id="art-play">${svgIcon(ICON.play)} Reproduzir</button>
        <button class="btn ghost" id="art-back">${svgIcon(ICON.arrowLeft)} Voltar</button>
      </div></div>`
      + (tracks.length
        ? renderList(tracks)
        : `<div class="state"><div class="state-icon">${svgIcon(ICON.search)}</div><h3>Nada encontrado</h3><p>Não encontramos faixas deste artista.</p></div>`);
    const ap = $('#art-play');
    if (ap) ap.onclick = () => { if (tracks.length) { queue = tracks.slice(); play(tracks[0]); } };
    const ab = $('#art-back');
    if (ab) ab.onclick = () => {
      const prev = _artistPrevTab || 'search';
      _artistPrevTab = null;
      nav(prev);
    };
    bindTracks(tracks);
  } catch (e) { showError(e, 'Falha ao abrir o artista'); }
}

// ---------- Explorar / Busca ----------
async function renderHome() {
  list.innerHTML = skeletonCards(6);
  const [histRes, plsRes, stsRes, favRes] = await Promise.allSettled([
    api('/api/history'), api('/api/playlists'), api('/api/stations'), api('/api/favorites')
  ]);
  if ($('#search').value.trim() || currentTab !== 'search') return;
  const hist = histRes.status === 'fulfilled' ? (histRes.value || []) : [];
  const pls = plsRes.status === 'fulfilled' ? (plsRes.value || []) : [];
  const sts = stsRes.status === 'fulfilled' ? (stsRes.value || []) : [];
  const favs = favRes.status === 'fulfilled' ? (favRes.value || []) : [];

  const uniqueHist = [];
  const seen = new Set();
  for (const t of hist) {
    if (t?.videoId && !seen.has(t.videoId)) { seen.add(t.videoId); uniqueHist.push(t); }
    if (uniqueHist.length >= 10) break;
  }

  if (!uniqueHist.length && !pls.length && !sts.length && !favs.length) {
    const moods = ['Lo-fi Beats', 'MPB', 'Rock Clássico', 'Eletrônica', 'Jazz', 'Acústico'];
    list.innerHTML = `<div class="state">
      <div class="state-icon">${svgIcon(ICON.note)}</div>
      <h3>Sua música em um só lugar</h3>
      <p>Busque qualquer música ou artista acima, cole um link de playlist do YouTube ou escolha um estilo para começar.</p>
      <div class="mood-chips">${moods.map(m => `<button type="button" class="mood-chip" data-mq="${esc(m)}">${esc(m)}</button>`).join('')}</div>
    </div>`;
    list.querySelectorAll('.mood-chip').forEach(b => b.onclick = () => {
      $('#search').value = b.dataset.mq;
      $('#search-wrap').classList.add('has-val');
      search();
    });
    return;
  }

  let html = `<div class="sect-header"><div><div class="sect-sub">Início</div><div class="sect-title">Explorar</div></div></div>`;

  // Acesso rápido a Playlists e Estações
  const quickItems = [
    ...sts.slice(0, 3).map(s => ({ type: 'st', data: s })),
    ...pls.slice(0, 6).map(p => ({ type: 'pl', data: p }))
  ].slice(0, 6);
  if (quickItems.length) {
    html += `<div class="home-sect"><div class="home-sect-head"><div class="home-sect-title">Acesso rápido</div><button type="button" class="home-link" data-go="playlists">Ver tudo</button></div><div class="quick-grid">`
      + quickItems.map(q => {
        if (q.type === 'st') {
          const s = q.data;
          const u = thumbUrl({ thumbnail: s.thumbnail, videoId: s.seedId });
          return `<div class="quick-card" data-qst="${s.id}">
            <div class="quick-thumb">${u ? `<img src="${esc(u)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">` : svgIcon(ICON.radio)}</div>
            <div class="quick-info"><div class="quick-title">${esc(s.name)}</div><div class="quick-sub">Estação · Rádio</div></div>
          </div>`;
        }
        const p = q.data;
        const u = p.items?.length ? thumbUrl(p.items[0]) : '';
        return `<div class="quick-card" data-qpl="${p.id}">
          <div class="quick-thumb">${u ? `<img src="${esc(u)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">` : svgIcon(ICON.list)}</div>
          <div class="quick-info"><div class="quick-title">${esc(p.name)}</div><div class="quick-sub">${p.items.length} faixa${p.items.length !== 1 ? 's' : ''}</div></div>
        </div>`;
      }).join('')
      + `</div></div>`;
  }

  // Continuar ouvindo em cards visuais
  if (uniqueHist.length) {
    html += `<div class="home-sect"><div class="home-sect-head"><div class="home-sect-title">Continuar ouvindo</div><button type="button" class="home-link" data-go="history">Histórico</button></div><div class="media-grid">`
      + uniqueHist.map((t, i) => `
        <div class="media-card" data-hi="${i}">
          <div class="media-card-art">
            <img src="${esc(thumbUrl(t))}" data-vid="${esc(t.videoId)}" loading="lazy" onerror="fixImg(this)" alt="">
            <div class="media-card-play">${svgIcon(ICON.play)}</div>
          </div>
          <div class="media-card-title">${esc(t.title)}</div>
          <div class="media-card-sub">${esc(t.artist || '')}</div>
        </div>`).join('')
      + `</div></div>`;
  }

  // Favoritas em destaque
  const favSlice = favs.slice(0, 6);
  if (favSlice.length) {
    html += `<div class="home-sect"><div class="home-sect-head"><div class="home-sect-title">Favoritas</div><button type="button" class="home-link" data-go="favorites">Ver todas</button></div><div id="home-fav-list">${renderList(favSlice, { fav: true })}</div></div>`;
  }

  list.innerHTML = html;

  list.querySelectorAll('[data-go]').forEach(b => b.onclick = () => nav(b.dataset.go));
  list.querySelectorAll('[data-hi]').forEach(c => c.onclick = () => {
    const t = uniqueHist[+c.dataset.hi];
    if (!t) return;
    queue = uniqueHist.slice();
    play(t);
  });
  list.querySelectorAll('[data-qpl]').forEach(c => c.onclick = () => {
    const p = pls.find(x => x.id == c.dataset.qpl);
    if (p) { activateNav('playlists'); currentTab = 'playlists'; openPlaylistDetail(p); }
  });
  list.querySelectorAll('[data-qst]').forEach(c => c.onclick = () => {
    const s = sts.find(x => x.id == c.dataset.qst);
    if (s) playStation(s);
  });
  const hFavBox = $('#home-fav-list');
  if (hFavBox && favSlice.length) bindTracks(favSlice, { fav: true, container: hFavBox });
}

async function search() {
  const q = $('#search').value.trim();
  const sw = $('#search-wrap');
  if (sw) sw.classList.toggle('has-val', q.length > 0);
  if (!q) return renderHome();

  const plm = q.match(/[?&]list=([A-Za-z0-9_-]+)/)
    || q.match(/\/(?:browse|playlist)\/(?:VL)?([A-Za-z0-9_-]{8,})/)
    || (/^(VL)?(PL|OLAK5uy_|RD|UU|LL|FL|TL)[A-Za-z0-9_-]{8,}$/.test(q) ? [null, q] : null);
  if (plm) {
    activateNav('playlists'); currentTab = 'playlists';
    return loadYtPlaylist(q);
  }
  if (q === _searchQ && _searchCtl) return;
  _searchQ = q;
  if (_searchCtl) _searchCtl.abort();
  const ctl = new AbortController();
  _searchCtl = ctl;
  list.innerHTML = skeletonList(8);
  try {
    const res = await api('/api/search?q=' + encodeURIComponent(q), { signal: ctl.signal });
    list.innerHTML = res.length
      ? `<div class="sect-header"><div><div class="sect-sub">${res.length} resultado${res.length !== 1 ? 's' : ''}</div><div class="sect-title">Resultados para “${esc(q)}”</div></div></div>` + renderList(res)
      : `<div class="state"><div class="state-icon">${svgIcon(ICON.search)}</div><h3>Nenhum resultado</h3><p>Tente outros termos de busca.</p></div>`;
    bindTracks(res);
  } catch (e) {
    if (e?.name !== 'AbortError') showError(e, 'Falha na busca');
  } finally {
    if (_searchCtl === ctl) _searchCtl = null;
  }
}

// ---------- Sugestões de busca ----------
let _suggTm = null, _suggSeq = 0;
const suggBox = document.createElement('div');
suggBox.className = 'sugg'; suggBox.id = 'sugg';
$('.search-wrap').appendChild(suggBox);
function closeSugg() { _suggSeq++; suggBox.classList.remove('open'); suggBox.innerHTML = ''; }
$('#search').addEventListener('input', () => {
  clearTimeout(_suggTm);
  const q = $('#search').value.trim();
  if (q.length < 2 || /[?&]list=/.test(q)) { closeSugg(); return; }
  const my = ++_suggSeq;
  _suggTm = setTimeout(async () => {
    if (my !== _suggSeq) return;
    try {
      const d = await api('/api/suggest?q=' + encodeURIComponent(q));
      if (my !== _suggSeq) return;
      if (!d.items || !d.items.length) { closeSugg(); return; }
      suggBox.innerHTML = d.items.map(s =>
        `<div class="sugg-item">${svgIcon(ICON.search)}<span>${esc(s)}</span></div>`).join('');
      suggBox.classList.add('open');
      suggBox.querySelectorAll('.sugg-item').forEach((el, i) => el.onmousedown = e => {
        e.preventDefault();
        $('#search').value = d.items[i];
        closeSugg();
        activateNav('search'); currentTab = 'search'; search();
      });
    } catch { closeSugg(); }
  }, 250);
});
document.addEventListener('click', e => { if (!e.target.closest('.search-wrap')) closeSugg(); });

// ---------- Importação de arquivos ----------
async function importFiles(files) {
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  toast(`Importando ${files.length} arquivo${files.length !== 1 ? 's' : ''}…`);
  try {
    const d = await api('/api/uploads', { method: 'POST', body: fd });
    toast(`${d.added} importado${d.added !== 1 ? 's' : ''}`
      + (d.duplicates ? ` · ${d.duplicates} duplicado(s)` : '')
      + (d.errors?.length ? ` · ${d.errors.length} falha(s): ${d.errors[0]}` : ''));
    if (window._openPlaylistId && d.tracks?.length) {
      await api(`/api/playlists/${window._openPlaylistId}/items_bulk`, {
        method: 'POST', body: JSON.stringify({ tracks: d.tracks }),
        headers: { 'Content-Type': 'application/json' },
      });
      const pid = window._openPlaylistId;
      toast(`${d.tracks.length} adicionada${d.tracks.length !== 1 ? 's' : ''} à playlist`);
      currentTab = 'playlists';
      await loadTab();
      const card = document.querySelector(`.pl-card[data-pid="${pid}"]`);
      if (card) card.click();
      return;
    }
  } catch (e) { toast(e.message); }
  if (currentTab === 'uploads') loadTab();
}

let _dragDepth = 0;
window.addEventListener('dragenter', e => {
  if (!isAdmin() || ![...(e.dataTransfer?.types || [])].includes('Files')) return;
  _dragDepth++;
  const ov = $('#drop-ov'); if (ov) ov.classList.add('on');
});
window.addEventListener('dragleave', () => {
  if (!isAdmin()) return;
  if (--_dragDepth <= 0) { _dragDepth = 0; const ov = $('#drop-ov'); if (ov) ov.classList.remove('on'); }
});
window.addEventListener('dragover', e => { if (isAdmin()) e.preventDefault(); });
window.addEventListener('drop', async e => {
  if (!isAdmin()) return;
  e.preventDefault(); _dragDepth = 0;
  const ov = $('#drop-ov'); if (ov) ov.classList.remove('on');
  const files = [...(e.dataTransfer?.files || [])].filter(f => /\.(mp3|m4a|mp4|opus|ogg|oga|flac|wav|webm)$/i.test(f.name));
  if (files.length) await importFiles(files);
});

// ---------- Modal: editar faixa importada ----------
let _editUp = null;
function openUpEdit(it) {
  if (!it) return;
  _editUp = it;
  $('#upm-title').value = it.title || '';
  $('#upm-artist').value = it.artist || '';
  $('#upm-album').value = it.album || '';
  $('#upm-year').value = it.year || '';
  $('#upm-genre').value = it.genre || '';
  $('#upm-search-q').value = ((it.artist || '').split(',')[0].trim() + ' ' + (it.title || '')).trim();
  $('#upm-results').innerHTML = '';
  const art = $('#upm-art');
  art.src = it.art ? `/api/local/${it.id}/art?v=${Date.now()}` : '';
  art.style.visibility = it.art ? 'visible' : 'hidden';
  $('#up-modal').classList.add('open');
}
$('#upm-close').onclick = () => $('#up-modal').classList.remove('open');
$('#up-modal').onclick = e => { if (e.target.id === 'up-modal') $('#up-modal').classList.remove('open'); };
$('#upm-search').onclick = async () => {
  const btn = $('#upm-search');
  const q = $('#upm-search-q').value.trim();
  if (!q || btn.dataset.busy) return;
  btn.dataset.busy = '1'; btn.textContent = 'Buscando…';
  try {
    const d = await api('/api/uploads/guess?title=' + encodeURIComponent(q));
    const res = d.results || [];
    if (!res.length) { $('#upm-results').innerHTML = '<div class="set-sub" style="padding:8px 4px">Nada encontrado — refine os termos.</div>'; return; }
    $('#upm-results').innerHTML = res.map((r, i) => `
      <div class="upm-res" data-i="${i}">
        <img src="${esc(r.art_url)}" onerror="this.style.visibility='hidden'" alt="">
        <div style="min-width:0"><div class="r-t">${esc(r.title)}</div>
        <div class="r-s">${esc(r.artist)}${r.album ? ' · ' + esc(r.album) : ''}${r.year ? ' · ' + esc(r.year) : ''}${r.dur ? ' · ' + fmtTime(r.dur) : ''}</div></div>
      </div>`).join('');
    _editUp._guess = res;
    $('#upm-results').querySelectorAll('.upm-res').forEach(el => el.onclick = () => {
      const r = (_editUp._guess || [])[+el.dataset.i];
      if (!r) return;
      $('#upm-title').value = r.title || $('#upm-title').value;
      $('#upm-artist').value = r.artist || $('#upm-artist').value;
      $('#upm-album').value = r.album || $('#upm-album').value;
      $('#upm-year').value = r.year || $('#upm-year').value;
      _editUp._artUrl = r.art_url || '';
      const art = $('#upm-art');
      if (r.art_url) { art.src = r.art_url; art.style.visibility = 'visible'; }
    });
  } catch (e) { toast(e.message); }
  finally { delete btn.dataset.busy; btn.textContent = 'Buscar'; }
};
$('#upm-save').onclick = async () => {
  if (!_editUp || $('#upm-save').dataset.busy) return;
  $('#upm-save').dataset.busy = '1';
  try {
    const body = {
      title: $('#upm-title').value.trim(), artist: $('#upm-artist').value.trim(),
      album: $('#upm-album').value.trim(), year: parseInt($('#upm-year').value, 10) || 0,
      genre: $('#upm-genre').value.trim(), art_url: _editUp._artUrl || '',
    };
    const d = await api('/api/uploads/' + _editUp.id + '/edit', {
      method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' },
    });
    toast('Metadados salvos');
    $('#up-modal').classList.remove('open');
    if (currentId === _editUp.id) setNowPlaying({ ..._editUp, ...d, videoId: _editUp.id });
    if (currentTab === 'uploads') loadTab();
  } catch (e) { toast(e.message); }
  finally { delete $('#upm-save').dataset.busy; }
};

function openPlaylistDetail(p) {
  window._openPlaylistId = p.id;
  queue = p.items;
  list.innerHTML = `<div class="pl-hero">
      <div class="pl-hero-cover">${playlistCoverHTML(p.items)}</div>
      <div class="pl-hero-info">
        <div class="sect-sub">Playlist · ${queue.length} faixa${queue.length !== 1 ? 's' : ''}</div>
        <div class="sect-title">${esc(p.name)}</div>
        <div class="pl-actions" style="margin:10px 0 0">
          ${queue.length ? `<button class="btn" id="pl-play-all">${svgIcon(ICON.play)} Reproduzir</button>` : ''}
          ${(queue.length && isAdmin()) ? `<button class="btn ghost sm" id="pl-dl-all">${svgIcon(ICON.dl)} Baixar MP3</button>` : ''}
          ${queue.length ? `<button class="btn ghost sm" id="pl-off-all">${svgIcon(ICON.phone)} Salvar offline</button>` : ''}
          <button class="btn ghost sm" id="back-pl">${svgIcon(ICON.arrowLeft)} Voltar</button>
        </div>
        <div class="set-sub" id="pl-dl-progress" style="margin-top:8px"></div>
      </div>
    </div>`
    + (queue.length
      ? renderList(queue, { inPlaylist: p.id })
      : `<div class="state"><div class="state-icon">${svgIcon(ICON.list)}</div><h3>Playlist vazia</h3><p>Adicione músicas pelo botão de playlist na busca ou arraste arquivos aqui.</p></div>`);
  $('#back-pl').onclick = () => loadTab();
  const playAll = $('#pl-play-all');
  if (playAll) playAll.onclick = () => { if (queue.length) play(queue[0]); };
  const dlAll = $('#pl-dl-all');
  if (dlAll) dlAll.onclick = async () => {
    if (dlAll.dataset.busy) return;
    dlAll.dataset.busy = '1';
    try {
      const d = await api(`/api/playlists/${p.id}/download`, { method: 'POST' });
      toast(`Baixando ${d.total} faixa${d.total !== 1 ? 's' : ''}`);
      pollPlJob(d.jobId, 'pl-dl-progress', 'pl-dl-all');
    } catch (e) { toast(e.message); delete dlAll.dataset.busy; }
  };
  bindTracks(queue, { inPlaylist: p.id, ownQueue: true });
  enablePlaylistDrag(p.id);
  const offAll = $('#pl-off-all');
  if (offAll) offAll.onclick = () => downloadOfflineMany(queue);
}

async function playStation(s) {
  if (!s?.seedId) return;
  toast('Carregando estação…');
  try {
    const d = await api(`/api/radio/${s.seedId}?known=1`);
    queue = d.tracks || [];
    const seed = queue.find(x => x.videoId === s.seedId) || queue[0];
    activateNav('playlists'); currentTab = 'playlists';
    list.innerHTML = `<div class="sect-header"><div><div class="sect-sub">Estação · Mix contínuo</div><div class="sect-title">${esc(s.name)}</div></div>
        <button class="btn ghost sm" id="back-st">${svgIcon(ICON.arrowLeft)} Voltar</button></div>` + renderList(queue);
    $('#back-st').onclick = () => loadTab();
    bindTracks(queue, { ownQueue: true });
    if (seed) play(seed);
  } catch (e) { toast(e.message); loadTab(); }
}

async function loadTab() {
  window._openPlaylistId = null;
  if (window._plPoll) {
    clearInterval(window._plPoll); window._plPoll = null;
    document.querySelectorAll('[data-busy]').forEach(b => delete b.dataset.busy);
  }
  if (currentTab === 'search') return search();
  list.innerHTML = currentTab === 'playlists' ? skeletonCards(8) : skeletonList(7);
  try {
    if (currentTab === 'favorites') {
      const items = await api('/api/favorites');
      list.innerHTML = items.length
        ? `<div class="sect-header"><div><div class="sect-sub">${items.length} faixas</div><div class="sect-title">Favoritas</div></div></div>` + renderList(items, { fav: true })
        : `<div class="state"><div class="state-icon">${svgIcon(ICON.heart)}</div><h3>Nenhuma favorita ainda</h3><p>Toque no coração em qualquer música para guardá-la aqui.</p></div>`;
      bindTracks(items, { inFavorites: true });

    } else if (currentTab === 'history') {
      const items = await api('/api/history');
      list.innerHTML = items.length
        ? `<div class="sect-header"><div><div class="sect-sub">Recentes</div><div class="sect-title">Histórico</div></div><button class="btn ghost sm" id="hist-clear">${svgIcon(ICON.trash)} Limpar</button></div>` + renderList(items)
        : `<div class="state"><div class="state-icon">${svgIcon(ICON.list)}</div><h3>Histórico vazio</h3><p>As músicas que você ouvir aparecem aqui.</p></div>`;
      bindTracks(items);
      const hc = $('#hist-clear');
      if (hc) hc.onclick = async () => {
        if (!confirm('Apagar todo o histórico de reprodução?')) return;
        try { const r = await api('/api/history', { method: 'DELETE' }); toast(`Histórico limpo (${r.removed} removidas)`); }
        catch (e) { toast(e.message); }
        loadTab();
      };

    } else if (currentTab === 'playlists') {
      const [pls, sts] = await Promise.all([api('/api/playlists'), api('/api/stations')]);
      const create = `<div class="pl-create">
          <input id="newpl" placeholder="Nome da nova playlist…">
          <button class="btn" id="addpl">Criar</button>
        </div>
        <div class="pl-create">
          <input id="ytpl-url" placeholder="Colar link de playlist do YouTube / YT Music…">
          <button class="btn ghost" id="ytpl-load">Importar</button>
        </div>`;
      let html = `<div class="sect-header"><div><div class="sect-sub">Sua coleção</div><div class="sect-title">Playlists</div></div></div>${create}`;
      if (sts.length) {
        html += `<div class="sect-header" style="margin-top:24px"><div><div class="sect-sub">Rádios salvas</div><div class="sect-title" style="font-size:20px">Estações</div></div></div><div class="pl-grid">`
          + sts.map(s => `
          <div class="pl-card" data-sid="${s.id}">
            <button class="pl-del" title="Apagar estação" data-del-st="${s.id}">${svgIcon(ICON.trash)}</button>
            <div class="pl-cover">
              ${s.thumbnail || s.seedId ? `<img class="pl-cover-img" src="${esc(thumbUrl({ thumbnail: s.thumbnail, videoId: s.seedId }))}" loading="lazy" alt="" onerror="this.style.visibility='hidden'">` : svgIcon(ICON.radio)}
              <span class="pl-badge">Rádio</span>
              <div class="media-card-play">${svgIcon(ICON.play)}</div>
            </div>
            <div class="pl-title">${esc(s.name)}</div>
            <div class="pl-meta">${esc(s.seedTitle || s.seedId || '')}</div>
          </div>`).join('') + '</div>';
      }
      if (!pls.length) {
        html += `<div class="state"><div class="state-icon">${svgIcon(ICON.list)}</div><h3>Nenhuma playlist</h3><p>Crie uma acima ou cole o link de uma playlist do YouTube Music.</p></div>`;
      } else {
        html += `<div class="sect-header" style="margin-top:24px"><div><div class="sect-title" style="font-size:20px">Suas Playlists</div></div></div><div class="pl-grid">`
          + pls.map(p => `
          <div class="pl-card" data-pid="${p.id}">
            <button class="pl-del" title="Apagar" data-del="${p.id}">${svgIcon(ICON.trash)}</button>
            ${playlistCoverHTML(p.items, '', `<div class="media-card-play" data-play-pid="${p.id}">${svgIcon(ICON.play)}</div>`)}
            <div class="pl-title">${esc(p.name)}</div>
            <div class="pl-meta">${p.items.length} faixa${p.items.length !== 1 ? 's' : ''}</div>
          </div>`).join('') + '</div>';
      }
      list.innerHTML = html;

      $('#addpl').onclick = async () => {
        const btn = $('#addpl');
        const n = $('#newpl').value.trim(); if (!n) return;
        if (btn.dataset.busy) return;
        btn.dataset.busy = '1';
        try {
          await api('/api/playlists?name=' + encodeURIComponent(n), { method: 'POST' });
          toast('Playlist criada'); $('#newpl').value = ''; loadTab();
        } catch (e) { toast(e.message); }
        finally { delete btn.dataset.busy; }
      };
      $('#newpl').onkeydown = e => { if (e.key === 'Enter') $('#addpl').click(); };
      $('#ytpl-load').onclick = () => loadYtPlaylist($('#ytpl-url').value);
      $('#ytpl-url').onkeydown = e => { if (e.key === 'Enter') $('#ytpl-load').click(); };

      list.querySelectorAll('.pl-card[data-pid]').forEach(el => {
        const p = pls.find(x => x.id == el.dataset.pid);
        el.onclick = e => {
          if (e.target.closest('.pl-del')) return;
          if (e.target.closest('[data-play-pid]')) {
            e.stopPropagation();
            if (p?.items?.length) { queue = p.items.slice(); play(p.items[0]); }
            return;
          }
          if (p) openPlaylistDetail(p);
        };
        el.querySelector('.pl-del').onclick = async e => {
          e.stopPropagation();
          if (!confirm('Apagar esta playlist?')) return;
          try { await api('/api/playlists/' + el.dataset.pid, { method: 'DELETE' }); loadTab(); }
          catch (err) { toast(err.message || 'Erro ao apagar playlist'); }
        };
      });

      list.querySelectorAll('.pl-card[data-sid]').forEach(el => {
        el.onclick = e => {
          if (e.target.closest('.pl-del')) return;
          const s = sts.find(x => x.id == el.dataset.sid);
          if (s) playStation(s);
        };
      });
      list.querySelectorAll('[data-del-st]').forEach(b => b.onclick = async e => {
        e.stopPropagation();
        if (!confirm('Apagar esta estação?')) return;
        try { await api('/api/stations/' + b.dataset.delSt, { method: 'DELETE' }); } catch (e) { toast(e.message); }
        loadTab();
      });

    } else if (currentTab === 'downloads') {
      if (!CURRENT_USER) await refreshCurrentUser();
      const admin = isAdmin();
      const [files, folders] = await Promise.all([api('/api/downloads'), api('/api/downloads/folders')]);
      let html = `<div class="sect-header"><div><div class="sect-sub">Biblioteca local no servidor</div><div class="sect-title">Downloads</div></div></div>`;
      html += folders.map(f => `
        <div class="pl-folder">
          <div class="pl-folder-head">
            <div class="dl-icon">${svgIcon(ICON.folder)}</div>
            <div class="dl-info"><div class="dl-name">${esc(f.name)}</div>
              <div class="dl-sub">${f.count} faixa${f.count !== 1 ? 's' : ''}</div></div>
            ${admin ? `<button class="fd-del" title="Excluir esta pasta" data-fd="${esc(f.name)}">${svgIcon(ICON.trash)}</button>` : ''}
          </div>
          ${f.files.map(fl => `
          <div class="dl-row sub">
            <div class="dl-icon">${svgIcon(ICON.note)}</div>
            <div class="dl-info"><div class="dl-name">${esc(fl.name)}</div><div class="dl-sub">MP3 local</div></div>
            ${fl.videoId ? `<button class="dl-play" data-vid="${esc(fl.videoId)}" data-name="${esc(fl.name)}">${svgIcon(ICON.play)} Tocar</button>` : ''}
            ${admin ? `<button class="dl-del" data-fdl="${esc(fl.name)}" data-fd="${esc(f.name)}" title="Excluir do PC">${svgIcon(ICON.trash)}</button>` : ''}
            <a class="dl-save" href="/downloads/playlists/${encodeURIComponent(f.name)}/${encodeURIComponent(fl.name)}" download>Salvar</a>
          </div>`).join('')}
        </div>`).join('');
      html += files.length
        ? files.map(fl => `
          <div class="dl-row">
            <div class="dl-icon">${svgIcon(ICON.note)}</div>
            <div class="dl-info"><div class="dl-name">${esc(fl.name)}</div><div class="dl-sub">MP3 local</div></div>
            ${fl.videoId ? `<button class="dl-play" data-vid="${esc(fl.videoId)}" data-name="${esc(fl.name)}">${svgIcon(ICON.play)} Tocar</button>` : ''}
            ${admin ? `<button class="dl-del" data-dl="${esc(fl.name)}" title="Excluir do PC">${svgIcon(ICON.trash)}</button>` : ''}
            <a class="dl-save" href="/downloads/${encodeURIComponent(fl.name)}" download>Salvar</a>
          </div>`).join('')
        : (!folders.length ? `<div class="state"><div class="state-icon">${svgIcon(ICON.dl)}</div><h3>Nenhum download</h3><p>Baixe músicas individuais ou playlists completas em MP3 para manter uma cópia local.</p></div>` : '');
      list.innerHTML = html;
      list.querySelectorAll('.dl-play').forEach(b => b.onclick = () => {
        const t = { videoId: b.dataset.vid, title: (b.dataset.name || '').replace(/\.[a-z0-9]+$/i, ''),
                    artist: 'Download local', thumbnail: `https://i.ytimg.com/vi/${b.dataset.vid}/mqdefault.jpg`, duration: '' };
        if (!queue.some(x => x.videoId === t.videoId)) queue = queue.concat([t]);
        play(t);
      });
      list.querySelectorAll('.dl-del').forEach(b => b.onclick = async () => {
        const inFolder = b.dataset.fdl;
        const nome = inFolder || b.dataset.dl;
        if (!confirm(`Excluir "${nome}" do computador?`)) return;
        try {
          if (inFolder) await api(`/api/downloads/folders/${encodeURIComponent(b.dataset.fd)}/${encodeURIComponent(inFolder)}`, { method: 'DELETE' });
          else await api('/api/downloads/' + encodeURIComponent(b.dataset.dl), { method: 'DELETE' });
          toast('Arquivo excluído');
        } catch (e) { toast(e.message); }
        loadTab();
      });
      list.querySelectorAll('.fd-del').forEach(b => b.onclick = async () => {
        if (!confirm(`Excluir a pasta "${b.dataset.fd}" com todas as músicas dela?`)) return;
        try {
          const r = await api('/api/downloads/folders/' + encodeURIComponent(b.dataset.fd), { method: 'DELETE' });
          toast(`Pasta excluída (${r.removed} faixas)`);
        } catch (e) { toast(e.message); }
        loadTab();
      });

    } else if (currentTab === 'uploads') {
      if (!CURRENT_USER) await refreshCurrentUser();
      const admin = isAdmin();
      const items = await api('/api/uploads');
      let rows = items.map((it, i) => {
        const thumb = it.art ? `/api/local/${it.id}/art` : '';
        return `<div class="track" data-id="${esc(it.id)}">
          <div class="t-num"><span class="num">${i + 1}</span><span class="eq"><span></span><span></span><span></span><span></span></span>${svgIcon(ICON.play)}</div>
          <img src="${esc(thumb)}" alt="" loading="lazy" ${thumb ? `onerror="this.style.visibility='hidden'"` : 'style="visibility:hidden"'}>
          <div class="t-info"><div class="t-title">${esc(it.title)}<span class="local-tag">Local</span></div><div class="t-artist">${esc(it.artist)}${it.album ? ' · ' + esc(it.album) : ''}${it.year ? ' · ' + esc(it.year) : ''}</div></div>
          <div class="t-dur">${it.dur ? fmtTime(it.dur) : ''}</div>
          ${admin ? `<div class="t-actions">
            <button class="up-del" data-upedit="${esc(it.id)}" title="Editar metadados">${svgIcon(ICON.edit)}</button>
            <button class="up-del" data-updel="${esc(it.id)}" title="Excluir arquivo">${svgIcon(ICON.trash)}</button>
          </div>` : '<div></div>'}
        </div>`;
      }).join('');
      const upBtnHtml = admin
        ? `<button class="btn" id="up-pick">${svgIcon(ICON.upload)} Importar áudio</button>
           <input type="file" id="up-input" accept=".mp3,.m4a,.mp4,.opus,.ogg,.oga,.flac,.wav,.webm" multiple style="display:none">`
        : '';
      const emptyUpText = admin
        ? 'Envie seus próprios arquivos MP3, FLAC, M4A, OGG ou WAV (ou arraste-os para esta janela).'
        : 'Ainda não há arquivos pessoais enviados na biblioteca deste servidor.';
      list.innerHTML = `<div class="sect-header"><div><div class="sect-sub">${items.length} arquivo${items.length !== 1 ? 's' : ''} enviado${items.length !== 1 ? 's' : ''}</div><div class="sect-title">Minhas músicas</div></div>
          ${upBtnHtml}
        </div>`
        + (rows ? `<div class="track-list"><div class="track-list-header"><div>#</div><div></div><div>Título</div><div>Duração</div><div></div></div>${rows}</div>`
          : `<div class="state"><div class="state-icon">${svgIcon(ICON.note)}</div><h3>Nenhum arquivo pessoal</h3><p>${emptyUpText}</p></div>`);
      const upPick = $('#up-pick'), upInp = $('#up-input');
      if (upPick && upInp) {
        upPick.onclick = () => upInp.click();
        upInp.onchange = async () => {
          if (!upInp.files?.length) return;
          const files = [...upInp.files];
          upInp.value = '';
          await importFiles(files);
        };
      }
      list.querySelectorAll('.track[data-id]').forEach(el => {
        const get = () => items.find(x => x.id === el.dataset.id);
        el.onclick = e => { if (e.target.closest('button')) return; playUpload(get()); };
        el.tabIndex = 0; el.setAttribute('role', 'button');
        el.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); playUpload(get()); } };
        const del = el.querySelector('[data-updel]');
        if (del) del.onclick = async () => {
          if (!confirm('Excluir este arquivo da sua biblioteca?')) return;
          try { await api('/api/uploads/' + del.dataset.updel, { method: 'DELETE' }); toast('Arquivo excluído'); loadTab(); } catch (e) { toast(e.message); }
        };
        const ed = el.querySelector('[data-upedit]');
        if (ed) ed.onclick = () => openUpEdit(get());
      });

    } else if (currentTab === 'offline') {
      await refreshOfflineIds();
      const items = (await Offline.list()).sort((a, b) => (b.savedAt || 0) - (a.savedAt || 0));
      const totalMB = items.reduce((s, x) => s + (x.size || 0), 0) / 1048576;
      let html = `<div class="sect-header"><div><div class="sect-sub">${items.length} faixa${items.length !== 1 ? 's' : ''} · ${totalMB.toFixed(1)} MB no aparelho</div><div class="sect-title">No celular</div></div></div>`;
      if (!items.length) {
        const isOff = navigator.onLine === false;
        html += `<div class="state"><div class="state-icon">${svgIcon(ICON.phone)}</div>`
          + `<h3>${isOff ? 'Você está offline' : 'Nenhuma faixa salva'}</h3>`
          + `<p>${isOff ? 'Sem internet para buscar novas faixas. Quando conectar, salve suas músicas favoritas offline para ouvir em qualquer lugar.' : 'Salve músicas ou playlists no aparelho para ouvir mesmo sem conexão ou com o computador desligado.'}</p></div>`;
      } else {
        html += items.map(it => `
          <div class="off-row" data-vid="${esc(it.videoId)}">
            <img src="${esc(it.thumbnail || '')}" data-vid="${esc(it.videoId)}" alt="" loading="lazy" onerror="fixImg(this)">
            <div class="dl-info"><div class="dl-name">${esc(it.title || it.videoId)}</div>
              <div class="dl-sub">${esc(it.artist || '')}${it.size ? ' · ' + (it.size / 1048576).toFixed(1) + ' MB' : ''}</div></div>
            <button class="off-play" data-play="${esc(it.videoId)}">${svgIcon(ICON.play)} Tocar</button>
            <button class="off-del" data-del="${esc(it.videoId)}" title="Remover do aparelho">${svgIcon(ICON.trash)}</button>
          </div>`).join('');
      }
      list.innerHTML = html;
      const asTrack = it => ({ videoId: it.videoId, title: it.title, artist: it.artist, thumbnail: it.thumbnail, duration: it.duration || '' });
      list.querySelectorAll('.off-play').forEach(b => b.onclick = () => {
        const it = items.find(x => x.videoId === b.dataset.play);
        if (!it) return;
        const t = asTrack(it);
        if (!queue.some(x => x.videoId === t.videoId)) queue = queue.concat([t]);
        play(t);
      });
      list.querySelectorAll('.off-del').forEach(b => b.onclick = async () => {
        if (!confirm('Remover esta música do aparelho?')) return;
        try {
          await Offline.del(b.dataset.del);
          offlineIds.delete(b.dataset.del);
          toast('Removida do aparelho'); loadTab();
        } catch (e) { toast(e.message || 'Erro ao remover'); }
      });

    } else if (currentTab === 'settings') {
      await renderSettings();
    }
  } catch (e) { showError(e, 'Erro ao carregar esta aba'); }
}

// ---------- Playlist do YouTube via link ----------
let ytPl = null;
async function loadYtPlaylist(url) {
  url = (url || '').trim();
  if (!url) { toast('Cole o link da playlist'); return false; }
  list.innerHTML = skeletonList(8);
  try {
    ytPl = await api('/api/ytplaylist?url=' + encodeURIComponent(url));
  } catch (e) {
    list.innerHTML = `<div class="sect-header"><div><div class="sect-sub">Importar Playlist</div><div class="sect-title">Erro ao carregar</div></div>
      <button class="btn ghost sm" id="plk-err-back">${svgIcon(ICON.arrowLeft)} Voltar</button></div>
      <div class="error" style="margin-top:12px;">${esc(e.message)}</div>`;
    const b = $('#plk-err-back');
    if (b) b.onclick = () => loadTab();
    return true;
  }
  queue = ytPl.tracks;
  list.innerHTML = `<div class="pl-hero">
      <div class="pl-hero-cover">${playlistCoverHTML(ytPl.tracks)}</div>
      <div class="pl-hero-info">
        <div class="sect-sub">Playlist do YouTube · ${ytPl.tracks.length} faixa${ytPl.tracks.length !== 1 ? 's' : ''}${ytPl.capped ? ' (máx. 500)' : ''}${ytPl.uploader ? ' · ' + esc(ytPl.uploader) : ''}</div>
        <div class="sect-title">${esc(ytPl.title)}</div>
        <div class="pl-actions" style="margin:10px 0 0">
          <button class="btn" id="plk-save">${svgIcon(ICON.plus)} Salvar playlist</button>
          ${isAdmin() ? `<button class="btn ghost sm" id="plk-dl">${svgIcon(ICON.dl)} Baixar MP3</button>` : ''}
          <button class="btn ghost sm" id="plk-off">${svgIcon(ICON.phone)} Salvar offline</button>
          <button class="btn ghost sm" id="plk-back">${svgIcon(ICON.arrowLeft)} Voltar</button>
        </div>
        <div class="set-sub" id="plk-progress" style="margin-top:8px"></div>
      </div>
    </div>` + renderList(ytPl.tracks);
  $('#plk-back').onclick = () => loadTab();
  $('#plk-save').onclick = saveImportedPlaylist;
  const plkDl = $('#plk-dl');
  if (plkDl) plkDl.onclick = downloadImportedPlaylist;
  $('#plk-off').onclick = () => downloadOfflineMany(ytPl.tracks);
  bindTracks(ytPl.tracks, { ownQueue: true });
  return true;
}

async function saveImportedPlaylist() {
  const btn = $('#plk-save');
  if (!ytPl || (btn && btn.dataset.busy)) return;
  if (btn) btn.dataset.busy = '1';
  const name = (ytPl.title || 'Playlist do YouTube').slice(0, 60);
  try {
    const d = await api('/api/playlists?name=' + encodeURIComponent(name), { method: 'POST' });
    if (!d.id) throw new Error('não consegui criar a playlist');
    const r = await api(`/api/playlists/${d.id}/items_bulk`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tracks: ytPl.tracks }),
    });
    toast(`"${name}" salva (${r.added} faixas)`);
  } catch (e) { toast(e.message); }
  finally { if (btn) delete btn.dataset.busy; }
}

async function downloadImportedPlaylist() {
  const btn = $('#plk-dl');
  if (!ytPl || (btn && btn.dataset.busy)) return;
  if (btn) btn.dataset.busy = '1';
  try {
    const d = await api('/api/ytplaylist/download', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: ytPl.id, folder: ytPl.title, tracks: ytPl.tracks }),
    });
    toast('Download da playlist iniciado');
    pollPlJob(d.jobId);
  } catch (e) { toast(e.message); if (btn) delete btn.dataset.busy; }
}

function pollPlJob(jobId, progId = 'plk-progress', btnId = 'plk-dl') {
  if (window._plPoll) clearInterval(window._plPoll);
  window._plPoll = setInterval(async () => {
    let j;
    try { j = await api('/api/ytplaylist/job/' + jobId); }
    catch {
      clearInterval(window._plPoll); window._plPoll = null;
      const b = btnId ? document.getElementById(btnId) : null;
      if (b) delete b.dataset.busy;
      return;
    }
    const done = ['concluída', 'concluída com erros', 'cancelada', 'erro'].includes(j.status);
    const prog = progId ? document.getElementById(progId) : null;
    if (prog && !done) {
      prog.innerHTML = `<span>Baixando ${j.done}/${j.total} · ${esc(j.current || j.status || '')}</span> `
        + `<button type="button" class="btn ghost sm" id="btn-cancel-${esc(jobId)}" style="margin-left:8px;padding:2px 8px;font-size:11px">Cancelar</button>`;
      const cBtn = document.getElementById(`btn-cancel-${jobId}`);
      if (cBtn) cBtn.onclick = async () => {
        cBtn.disabled = true;
        try { await api(`/api/ytplaylist/job/${jobId}/cancel`, { method: 'POST' }); toast('Cancelando download…'); }
        catch (e) { toast(e.message); }
      };
    }
    if (done) {
      clearInterval(window._plPoll); window._plPoll = null;
      const btn = btnId ? document.getElementById(btnId) : null;
      if (btn) delete btn.dataset.busy;
      if (prog) prog.textContent = j.status === 'concluída'
        ? `${j.done} faixas salvas em downloads/${j.folder}`
        : `${j.status} — ${j.failed} falhas · downloads/${j.folder}`;
      toast('Playlist: ' + j.status + (j.failed ? ` (${j.failed} falharam)` : ''));
    }
  }, 2500);
}

// ---------- Aba: Configurações ----------
async function refreshCacheStats() {
  try {
    const st = await api('/api/cache/stats');
    const fill = $('#cache-fill');
    if (!fill) return;
    fill.style.width = Math.min(100, (st.used_mb / st.limit_mb) * 100).toFixed(1) + '%';
    $('#cache-used').textContent = `${st.used_mb} MB de ${st.limit_mb} MB usados`;
    $('#cache-count').textContent = st.enabled ? `${st.count} música${st.count !== 1 ? 's' : ''} em cache` : 'Cache desativado';
    $('#cache-items').innerHTML = (st.items || []).map(it => `
      <div class="cache-item">
        <div class="ci-t">${esc(it.title || it.video_id)}${it.artist ? ` <span class="ci-a">· ${esc(it.artist)}</span>` : ''}</div>
        <div class="ci-s">${it.size_mb} MB</div>
        <button class="ci-x" data-vid="${esc(it.video_id)}" title="Remover do cache">${svgIcon(ICON.close)}</button>
      </div>`).join('');
    $('#cache-items').querySelectorAll('.ci-x').forEach(b => b.onclick = async () => {
      try { await api('/api/cache/' + b.dataset.vid, { method: 'DELETE' }); refreshCacheStats(); }
      catch (e) { toast(e.message); }
    });
  } catch {}
}

async function renderSettings() {
  list.innerHTML = skeletonList(5);
  if (!CURRENT_USER) await refreshCurrentUser();
  const admin = isAdmin();
  let data = { settings: {}, ytdlp_version: '' };
  if (admin) {
    try { data = await api('/api/settings'); }
    catch (e) { return showError(e, 'Erro ao carregar configurações'); }
    SETTINGS = data.settings;
  }
  const s = data.settings || {};
  const isHostLocal = ['127.0.0.1', 'localhost', '::1'].includes(location.hostname);
  const isStandalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;

  const hostCardHtml = (admin && isHostLocal) ? `
    <div class="set-card">
      <div class="set-card-title">${svgIcon(ICON.globe)} Conexão Remota &amp; Túnel 4G/5G</div>
      <div class="set-row" style="border-top:none">
        <div class="set-info">
          <div class="set-name">Cloudflare Quick Tunnel</div>
          <div class="set-sub" id="host-cf-sub">Verificando status do túnel…</div>
          <div id="host-cf-url-wrap"></div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn ghost sm" id="btn-host-cf-toggle">Alternar Túnel</button>
          <a class="btn sm" href="/painel" target="_blank">Abrir Painel</a>
        </div>
      </div>
    </div>` : '';

  const pwaStatusHtml = isStandalone
    ? `<span class="pwa-badge">Instalado no dispositivo</span>`
    : `<button class="btn sm" id="btn-install" style="${_bipEvt ? '' : 'display:none'}">Instalar App</button>`;

  const pwaSubHtml = isStandalone
    ? 'O StremMusic está rodando em modo aplicativo dedicado.'
    : (isIOS
      ? 'No iPhone/iPad: toque em <b>Compartilhar</b> no Safari e selecione <b>“Adicionar à Tela de Início”</b>.'
      : 'Abre em janela própria sem barra do navegador e com controles na tela de bloqueio.');

  const adminCardsHtml = admin ? `
    <div class="set-card">
      <div class="set-card-title">${svgIcon(ICON.database)} Cache &amp; Reprodução</div>
      <div class="cache-meta"><span id="cache-used">…</span><span id="cache-count"></span></div>
      <div class="cache-bar"><div class="cache-fill" id="cache-fill" style="width:0%"></div></div>
      <div class="set-row">
        <div class="set-info">
          <div class="set-name">Cache local no servidor</div>
          <div class="set-sub">Guarda faixas completas em disco para replay instantâneo.</div>
        </div>
        <label class="switch"><input type="checkbox" id="set-cache"${s.cache_enabled ? ' checked' : ''}><span class="sl"></span></label>
      </div>
      <div class="set-row">
        <div class="set-info">
          <div class="set-name">Limite de espaço do cache</div>
          <div class="set-sub">Remove automaticamente faixas menos ouvidas ao atingir o limite.</div>
        </div>
        <span class="set-val" id="cache-limit-val">${s.cache_limit_mb} MB</span>
        <input type="range" id="set-cache-limit" class="set-range" min="100" max="10240" step="50" value="${s.cache_limit_mb}">
      </div>
      <div class="set-row">
        <div class="set-info">
          <div class="set-name">Pré-carregar próximas da fila</div>
          <div class="set-sub">Prepara antecipadamente as próximas músicas para evitar pausas.</div>
        </div>
        <label class="switch"><input type="checkbox" id="set-prefetch"${s.prefetch_next ? ' checked' : ''}><span class="sl"></span></label>
      </div>
      <div class="set-row">
        <div class="set-info">
          <div class="set-name">Priorizar favoritas e mais ouvidas</div>
          <div class="set-sub">Mantém músicas recorrentes protegidas no cache por mais tempo.</div>
        </div>
        <label class="switch"><input type="checkbox" id="set-smart"${s.smart_cache ? ' checked' : ''}><span class="sl"></span></label>
      </div>
      <div class="set-row">
        <div class="set-info">
          <div class="set-name">Reprodução contínua inteligente</div>
          <div class="set-sub">Continua a fila automaticamente com base na sua sequência de escuta.</div>
        </div>
        <label class="switch"><input type="checkbox" id="set-learn"${s.learn_next ? ' checked' : ''}><span class="sl"></span></label>
      </div>
      <div class="set-row">
        <div class="set-info"><div class="set-name">Arquivos em cache</div><div class="set-sub">Gerencie ou limpe faixas armazenadas temporariamente.</div></div>
        <button class="btn ghost sm" id="btn-clear-cache">Limpar cache</button>
      </div>
      <div id="cache-items" style="margin-top:8px"></div>
    </div>

    <div class="set-card">
      <div class="set-card-title">${svgIcon(ICON.sliders)} Áudio, Rede &amp; Extração</div>
      <div class="set-row" style="border-top:none">
        <div class="set-info">
          <div class="set-name">Qualidade dos downloads MP3</div>
          <div class="set-sub">Taxa de bits ao converter faixas para a pasta Downloads.</div>
        </div>
        <select class="set-select" id="set-mp3">
          <option value="128">128 kbps</option>
          <option value="192">192 kbps</option>
          <option value="320">320 kbps</option>
        </select>
      </div>
      <div class="set-row">
        <div class="set-info">
          <div class="set-name">Clients paralelos por tentativa</div>
          <div class="set-sub">2 equilibra velocidade de resposta e estabilidade.</div>
        </div>
        <select class="set-select" id="set-race">
          <option value="1">1 · econômico</option>
          <option value="2">2 · equilibrado</option>
          <option value="3">3 · rápido</option>
          <option value="4">4 · máximo</option>
        </select>
      </div>
      <div class="set-row">
        <div class="set-info">
          <div class="set-name">Tempo limite por tentativa</div>
          <div class="set-sub">Espera máxima antes de tentar o próximo client.</div>
        </div>
        <select class="set-select" id="set-wave-to">
          <option value="10">10s</option>
          <option value="12">12s · padrão</option>
          <option value="15">15s</option>
          <option value="20">20s</option>
          <option value="25">25s</option>
          <option value="30">30s</option>
          <option value="45">45s</option>
          <option value="60">60s</option>
        </select>
      </div>
      <div class="set-row">
        <div class="set-info">
          <div class="set-name">Forçar IPv4</div>
          <div class="set-sub">Evita falhas em redes onde a rota IPv6 é instável.</div>
        </div>
        <label class="switch"><input type="checkbox" id="set-ipv4"${s.force_ipv4 ? ' checked' : ''}><span class="sl"></span></label>
      </div>
      <div class="set-row">
        <div class="set-info">
          <div class="set-name">Usar cookies.txt</div>
          <div class="set-sub">Autentica extrações caso o servidor receba verificação de bot.</div>
        </div>
        <label class="switch"><input type="checkbox" id="set-cookies"${s.use_cookies ? ' checked' : ''}><span class="sl"></span></label>
      </div>
      <div class="set-row">
        <div class="set-info">
          <div class="set-name">Diagnóstico dos extratores</div>
          <div class="set-sub">Latência e disponibilidade atual dos clients do YouTube.</div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn ghost sm" id="btn-cli-refresh">Atualizar</button>
          <button class="btn ghost sm" id="btn-cli-probe">${svgIcon(ICON.activity)} Medir</button>
          <button class="btn ghost sm" id="btn-cli-reset">Resetar</button>
        </div>
      </div>
      <div id="cli-health" style="margin-top:6px"></div>
    </div>

    <div class="set-save">
      <button class="btn" id="btn-save-settings">Salvar configurações</button>
    </div>` : '';

  const subHeader = admin
    ? `Motor yt-dlp ${esc(data.ytdlp_version || '')}`
    : 'Preferências da conta e do player';

  list.innerHTML = `
    <div class="sect-header"><div><div class="sect-sub">${subHeader}</div><div class="sect-title">Ajustes</div></div>
      <span class="set-sub" id="set-hint" style="color:var(--txt-pure);font-weight:500"></span>
    </div>

    <div class="set-card">
      <div class="set-card-title">${svgIcon(ICON.user)} Perfil Ativo</div>
      <div class="set-row" style="border-top:none">
        <div style="display:flex;align-items:center;gap:12px">
          <div style="width:38px;height:38px;border-radius:50%;background:var(--bg-elevated);border:1px solid var(--border-strong);display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:700;color:var(--txt-pure)" id="set-u-avatar">${CURRENT_USER ? esc(CURRENT_USER.name.charAt(0).toUpperCase()) : 'P'}</div>
          <div>
            <div style="font-weight:600;font-size:14.5px;color:var(--txt-pure)">${CURRENT_USER ? esc(CURRENT_USER.name) : 'Perfil Local'} ${CURRENT_USER?.is_admin ? '<span style="font-size:11px;padding:2px 7px;border-radius:99px;background:rgba(255,255,255,.08);color:var(--txt-main);font-weight:600;margin-left:6px">Admin</span>' : ''}</div>
            <div class="set-sub">Favoritas, histórico e playlists isoladas neste perfil.</div>
          </div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn ghost sm" id="btn-logout">Sair / Trocar perfil</button>
          ${(admin && isHostLocal) ? `<a class="btn sm" href="/painel" target="_blank">Gerenciar Usuários</a>` : ''}
        </div>
      </div>
    </div>

    ${hostCardHtml}

    <div class="set-card">
      <div class="set-card-title">${svgIcon(ICON.palette)} Aparência</div>
      <div class="set-row" style="border-top:none;display:block">
        <div class="set-info" style="margin-bottom:10px">
          <div class="set-name">Layout</div>
          <div class="set-sub">Ajusta a densidade da interface (atalhos: Alt+1 a Alt+4).</div>
        </div>
        <div class="layout-pick" role="group" aria-label="Layout do app">
          <button type="button" class="layout-opt" data-set-layout="auto" aria-pressed="false">
            <span class="lp-shot lp-auto"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></span>
            <span class="lp-name">Automático <span class="tick" aria-hidden="true">✓</span></span>
            <span class="lp-desc">Adapta sozinho entre celular, notebook e monitor.</span>
          </button>
          <button type="button" class="layout-opt" data-set-layout="classic" aria-pressed="false">
            <span class="lp-shot lp-classic"><i></i><i></i><i></i><i></i></span>
            <span class="lp-name">Clássico <span class="tick" aria-hidden="true">✓</span></span>
            <span class="lp-desc">Menu lateral e player inferior equilibrado.</span>
          </button>
          <button type="button" class="layout-opt" data-set-layout="studio" aria-pressed="false">
            <span class="lp-shot lp-studio"><i></i><i></i><i></i><i></i><i></i></span>
            <span class="lp-name">Estúdio <span class="tick" aria-hidden="true">✓</span></span>
            <span class="lp-desc">Capas maiores e visual espaçado.</span>
          </button>
          <button type="button" class="layout-opt" data-set-layout="compact" aria-pressed="false">
            <span class="lp-shot lp-compact"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></span>
            <span class="lp-name">Compacto <span class="tick" aria-hidden="true">✓</span></span>
            <span class="lp-desc">Lista densa para navegar por muitas faixas.</span>
          </button>
        </div>
      </div>
      <div class="set-row">
        <div class="set-info">
          <div class="set-name">Tema</div>
          <div class="set-sub">Noturno aplica fundo preto puro (OLED) com contraste quente em âmbar.</div>
        </div>
        <div class="seg" role="group" aria-label="Tema">
          <button type="button" class="seg-opt" data-set-theme="default">Padrão</button>
          <button type="button" class="seg-opt" data-set-theme="night">Noturno</button>
        </div>
      </div>
      <div class="set-row">
        <div class="set-info">
          <div class="set-name">Desempenho Visual</div>
          <div class="set-sub">Modo leve desativa desfoque e animações para economizar bateria.</div>
        </div>
        <div class="seg" role="group" aria-label="Desempenho">
          <button type="button" class="seg-opt" data-set-perf="full">Completo</button>
          <button type="button" class="seg-opt" data-set-perf="lite">Leve</button>
        </div>
      </div>
    </div>

    <div class="set-card">
      <div class="set-card-title">${svgIcon(ICON.phone)} Aplicativo (PWA) &amp; Atalhos</div>
      <div class="set-row" style="border-top:none">
        <div class="set-info">
          <div class="set-name">Modo Aplicativo</div>
          <div class="set-sub">${pwaSubHtml}</div>
        </div>
        ${pwaStatusHtml}
      </div>
      <div class="set-row">
        <div class="set-info">
          <div class="set-name">Atalhos de teclado</div>
          <div class="set-sub">Espaço, setas, M, F, H, Q, L, / (ou ? a qualquer momento).</div>
        </div>
        <button class="btn ghost sm" id="btn-help">Ver atalhos</button>
      </div>
    </div>

    ${adminCardsHtml}`;

  list.querySelectorAll('[data-set-layout]').forEach(b => { b.onclick = () => setLayout(b.dataset.setLayout, true); });
  list.querySelectorAll('[data-set-theme]').forEach(b => { b.onclick = () => setTheme(b.dataset.setTheme, true); });
  list.querySelectorAll('[data-set-perf]').forEach(b => { b.onclick = () => setPerf(b.dataset.setPerf, true); });
  paintLayoutPicker();
  const btnLogout = $('#btn-logout');
  if (btnLogout) {
    btnLogout.onclick = async () => {
      if (!confirm('Deseja sair deste perfil?')) return;
      try { await api('/api/logout', { method: 'POST' }); } catch {}
      localStorage.removeItem('sm-sk');
      location.href = '/';
    };
  }
  $('#btn-help').onclick = openHelp;
  const bi = $('#btn-install');
  if (bi && _bipEvt && !isStandalone) {
    bi.style.display = '';
    bi.onclick = () => { _bipEvt.prompt(); _bipEvt = null; bi.style.display = 'none'; };
  }

  if (!admin) return;

  $('#set-race').value = String(s.race_wave);
  $('#set-wave-to').value = String(s.extract_timeout);
  $('#set-mp3').value = String(s.mp3_quality);

  if (isHostLocal) {
    const refreshHostTunnel = async () => {
      try {
        const st = await api('/api/tunnel/status');
        const sub = $('#host-cf-sub');
        const wrap = $('#host-cf-url-wrap');
        const btn = $('#btn-host-cf-toggle');
        if (!sub || !btn) return;
        if (st.url) {
          sub.innerHTML = '<b>Túnel ativo</b> — clique no endereço para copiar ou abra o painel para QR Code:';
          wrap.innerHTML = `<div class="host-url-box" title="Clique para copiar">${esc(st.url)}</div>`;
          const box = wrap.querySelector('.host-url-box');
          if (box) box.onclick = () => { navigator.clipboard.writeText(st.url); toast('Link copiado'); };
          btn.textContent = 'Desligar Túnel';
        } else if (st.active) {
          sub.textContent = 'Conectando aos servidores da Cloudflare…';
          wrap.innerHTML = '';
          btn.textContent = 'Cancelar';
          setTimeout(refreshHostTunnel, 1800);
        } else {
          sub.textContent = 'Desligado · Gere um link HTTPS seguro para ouvir fora do Wi-Fi.';
          wrap.innerHTML = '';
          btn.textContent = 'Ligar Túnel';
        }
      } catch {}
    };
    const btnCf = $('#btn-host-cf-toggle');
    if (btnCf) btnCf.onclick = async () => {
      btnCf.disabled = true;
      try {
        const r = await api('/painel/tunnel/toggle', { method: 'POST' });
        toast(r.active ? 'Iniciando Túnel Cloudflare…' : 'Túnel desligado');
        setTimeout(refreshHostTunnel, 800);
      } catch (e) { toast(e.message); }
      finally { btnCf.disabled = false; }
    };
    refreshHostTunnel();
  }

  const lim = $('#set-cache-limit');
  const fmtCacheMb = mb => mb >= 1024 ? `${(mb / 1024).toFixed(1).replace(/\.0$/, '')} GB (${mb} MB)` : `${mb} MB`;
  const updLim = () => { $('#cache-limit-val').textContent = fmtCacheMb(parseInt(lim.value, 10) || 0); paintRange(lim); };
  lim.oninput = updLim;
  updLim();

  $('#btn-clear-cache').onclick = async () => {
    if (!confirm('Apagar todas as músicas do cache? (seus downloads não são afetados)')) return;
    try {
      const r = await api('/api/cache', { method: 'DELETE' });
      toast(`Cache limpo (${r.removed} itens)`);
      refreshCacheStats();
    } catch (e) { toast(e.message); }
  };

  async function saveSettingsNow(silent = false) {
    const btn = $('#btn-save-settings');
    if (!$('#set-cache') || (btn && btn.dataset.busy)) return;
    if (btn) btn.dataset.busy = '1';
    const payload = {
      cache_enabled: $('#set-cache').checked,
      cache_limit_mb: parseInt($('#set-cache-limit').value, 10),
      prefetch_next: $('#set-prefetch').checked,
      smart_cache: $('#set-smart').checked,
      learn_next: $('#set-learn').checked,
      race_wave: parseInt($('#set-race').value, 10),
      extract_timeout: parseInt($('#set-wave-to').value, 10),
      force_ipv4: $('#set-ipv4').checked,
      use_cookies: $('#set-cookies').checked,
      mp3_quality: $('#set-mp3').value,
    };
    try {
      await api('/api/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      Object.assign(SETTINGS, payload);
      _learnNext = null;
      const hint = $('#set-hint');
      if (hint) hint.textContent = 'Salvo às ' + new Date().toLocaleTimeString();
      if (!silent) toast('Configurações salvas');
      refreshCacheStats();
    } catch (e) { toast(e.message); }
    finally { if (btn) delete btn.dataset.busy; }
  }

  $('#btn-save-settings').onclick = () => saveSettingsNow(false);
  ['#set-cache', '#set-prefetch', '#set-smart', '#set-learn', '#set-ipv4', '#set-cookies',
   '#set-race', '#set-wave-to', '#set-mp3', '#set-cache-limit'].forEach(sel => {
    const el = $(sel);
    if (el) el.addEventListener('change', () => saveSettingsNow(true));
  });

  async function renderClientsHealth() {
    try {
      const d = await api('/api/clients/health');
      const box = $('#cli-health');
      if (!box) return;
      const dot = st => `<span class="status-dot ${st === 'de pé' ? 'ok' : (st === 'folga' ? 'rest' : '')}"></span>`;
      const ms = v => (v === null || v === undefined) ? '' : `${(v / 1000).toFixed(1)}s`;
      box.innerHTML = d.clients.map(c => {
        const extra = c.state === 'folga'
          ? `volta em ${Math.max(1, Math.ceil(c.resting_s / 60))} min`
          : (c.fails ? `${c.fails} falha${c.fails !== 1 ? 's' : ''}` : 'pronto');
        const lat = c.ms ? ` · ${ms(c.ms)}` : '';
        return `<div class="cache-item"><div class="ci-t">${dot(c.state)}<span>${esc(c.label)}${c.label === d.preferido ? ' <span class="ci-a">· preferido</span>' : ''}</span></div><div class="ci-s">${esc(extra + lat)}</div></div>`;
      }).join('')
        + `<div class="set-sub" style="margin-top:8px">Cookies: ${d.cookies ? 'ativos' : 'inativos'} · PO Token: ${d.po_token ? 'ativo' : 'indisponível'} · EJS: ${d.ejs === true ? 'ligado' : d.ejs === false ? 'dispensado' : 'não medido'}${d.probe_running ? ' · <b>medindo…</b>' : ''}</div>`;
    } catch {}
  }
  const btnCliR = $('#btn-cli-refresh');
  if (btnCliR) btnCliR.onclick = renderClientsHealth;
  const btnCliP = $('#btn-cli-probe');
  if (btnCliP) btnCliP.onclick = async () => {
    try {
      const r = await api('/api/clients/probe', { method: 'POST' });
      toast(r.started ? 'Medindo latência em segundo plano…' : (r.motivo || 'Já está medindo'));
    } catch (e) { toast(e.message); }
    setTimeout(renderClientsHealth, 1500);
    setTimeout(renderClientsHealth, 12000);
  };
  const btnCliX = $('#btn-cli-reset');
  if (btnCliX) btnCliX.onclick = async () => {
    if (!confirm('Resetar o histórico de falhas de todos os clients?')) return;
    try {
      const r = await api('/api/clients/reset', { method: 'POST' });
      toast(`${r.cleared} client${r.cleared !== 1 ? 's' : ''} resetado(s)`);
    } catch (e) { toast(e.message); }
    renderClientsHealth();
  };
  renderClientsHealth();
  refreshCacheStats();
}

// ---------- Modal: adicionar à playlist ----------
let plmTrack = null;
async function openPlModal(t) {
  plmTrack = t;
  $('#pl-modal').classList.add('open');
  $('#plm-list').innerHTML = '<div class="hint" style="padding:16px"><span class="loader"></span></div>';
  $('#plm-name').value = '';
  try {
    const pls = await api('/api/playlists');
    $('#plm-list').innerHTML = pls.length ? pls.map(p => `
      <div class="plm-item" data-pid="${p.id}">
        <div>${esc(p.name)}</div>
        <div class="set-sub" style="margin:0">${p.items.length} faixa${p.items.length !== 1 ? 's' : ''}</div>
      </div>`).join('')
      : '<div class="set-sub" style="text-align:center;padding:16px 0">Você ainda não tem playlists. Crie uma abaixo.</div>';
    $('#plm-list').querySelectorAll('.plm-item').forEach(el => el.onclick = async () => {
      await addToPlaylist(+el.dataset.pid, plmTrack);
      closePlModal();
    });
  } catch (e) { $('#plm-list').innerHTML = `<div class="error">${esc(e.message)}</div>`; }
}
function closePlModal() { $('#pl-modal').classList.remove('open'); plmTrack = null; }
async function addToPlaylist(pid, t) {
  if (!t) return;
  try {
    await api(`/api/playlists/${pid}/items/${t.videoId}?title=${encodeURIComponent(t.title || '')}&artist=${encodeURIComponent(t.artist || '')}&thumbnail=${encodeURIComponent(t.thumbnail || '')}`, { method: 'POST' });
    toast('Adicionada à playlist');
  } catch (e) { toast(e.message); }
}
$('#plm-create').onclick = async () => {
  const n = $('#plm-name').value.trim();
  if (!n || !plmTrack) return;
  try {
    const d = await api('/api/playlists?name=' + encodeURIComponent(n), { method: 'POST' });
    if (d.id) { await addToPlaylist(d.id, plmTrack); closePlModal(); }
    else toast('Não foi possível criar a playlist');
  } catch (e) { toast(e.message); }
};
$('#plm-name').onkeydown = e => { if (e.key === 'Enter') $('#plm-create').click(); };
$('#plm-close').onclick = closePlModal;
$('#pl-modal').onclick = e => { if (e.target.id === 'pl-modal') closePlModal(); };

// ---------- Ações do player ----------
$('#player .now').onclick = e => {
  if (e.target.closest('button') || e.target.closest('#now-artist')) return;
  if (!currentId) return;
  if (!document.body.classList.contains('player-fs')) $('#btn-expand').click();
};
$('#player-fav').onclick = async function() {
  if (!currentId) return;
  const t = queue[qIndex];
  if (!t) return;
  try {
    await api(`/api/favorites/${currentId}?title=${encodeURIComponent(t.title || '')}&artist=${encodeURIComponent(t.artist || '')}&thumbnail=${encodeURIComponent(t.thumbnail || '')}`, { method: 'POST' });
    this.innerHTML = svgIcon(ICON.heartFill); this.style.color = 'var(--accent-base)';
    toast('Adicionada às favoritas');
  } catch { toast('Erro ao favoritar'); }
};
$('#player-add').onclick = () => { if (currentId && queue[qIndex]) openPlModal(queue[qIndex]); };
$('#now-artist').onclick = function(e) {
  const t = queue[qIndex];
  if (!t || !t.artist) return;
  e.stopPropagation();
  if (document.body.classList.contains('player-fs')) toggleFullscreen();
  openArtist(t.artist, t.channelId || '');
};
$('#player-radio').onclick = async function() {
  if (!currentId) { toast('Toque uma música primeiro'); return; }
  if (this.dataset.busy) return;
  this.dataset.busy = '1';
  try {
    const d = await api('/api/radio/' + currentId);
    const add = (d.tracks || []).filter(t => !queue.some(x => x.videoId === t.videoId));
    if (!add.length) { toast('A rádio não trouxe faixas novas'); return; }
    queue = queue.concat(add);
    toast(`Rádio: +${add.length} faixas na fila`);
    renderQueuePanel(); updatePrevNext();
    if (_fsSideTab === 'queue' && document.body.classList.contains('player-fs')) renderFsSide();
    const ps = $('#player-station');
    if (ps) ps.style.display = '';
  } catch (e) { toast(e.message); }
  finally { delete this.dataset.busy; }
};
$('#player-station').onclick = async function() {
  if (!currentId) return;
  const t = queue[qIndex];
  if (!t || t.local) { toast('Estação nasce de uma faixa do YouTube'); return; }
  const name = (prompt('Nome da estação:') || '').trim();
  if (!name) return;
  try {
    const d = await api('/api/stations', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, videoId: t.videoId, title: t.title || '', artist: t.artist || '', thumbnail: t.thumbnail || '' }),
    });
    toast(d.duplicate ? 'Estação já existia' : `Estação "${name}" salva`);
  } catch (e) { toast(e.message); }
};

// ---------- Cor dominante & Reprodução ----------
let _tintTm = null;
function applyCoverTint(src) {
  const fsBg = $('#fs-bg');
  if (fsBg) fsBg.style.backgroundImage = src ? `url("${src.replace(/"/g, '\\"')}")` : 'none';
  if (!src || src.startsWith('blob:')) return;
  if (document.documentElement.dataset.perf === 'lite') return;
  clearTimeout(_tintTm);
  _tintTm = setTimeout(() => {
    try {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => {
        try {
          const c = document.createElement('canvas'); c.width = c.height = 24;
          const g = c.getContext('2d'); g.drawImage(img, 0, 0, 24, 24);
          const d = g.getImageData(0, 0, 24, 24).data;
          let best = -1, pr = 161, pg = 161, pb = 170, br = 0, bg = 0, bb = 0, bn = 0;
          for (let i = 0; i < d.length; i += 4) {
            const r = d[i], gg = d[i + 1], b = d[i + 2];
            const lum = (r + gg + b) / 3, sat = Math.max(r, gg, b) - Math.min(r, gg, b);
            br += r; bg += gg; bb += b; bn++;
            const w = (lum > 35 && lum < 225 ? 1 : 0.25) * sat;
            if (w > best) { best = w; pr = r; pg = gg; pb = b; }
          }
          if (bn && best <= 0) { pr = Math.round(br / bn); pg = Math.round(bg / bn); pb = Math.round(bb / bn); }
          document.documentElement.style.setProperty('--cover-tint', `${pr},${pg},${pb}`);
        } catch {}
      };
      img.onerror = () => {};
      img.src = src;
    } catch {}
  }, 350);
}

function setNowPlaying(t) {
  const img = $('#now-img');
  img.dataset.vid = t.videoId || '';
  img.style.visibility = '';
  const src = thumbUrl(t);
  img.src = src;
  $('#now-title').textContent = t.title || '';
  $('#now-artist').textContent = t.artist || '';
  $('#now-title').title = t.title || '';
  $('#now-artist').title = t.artist || '';
  document.title = t.title ? `${t.title} · ${t.artist || 'StremMusic'}` : 'StremMusic';
  applyCoverTint(src);
  const pf = $('#player-fav');
  if (pf) { pf.innerHTML = svgIcon(ICON.heart); pf.style.color = ''; }
  if ('mediaSession' in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: t.title || '', artist: t.artist || '',
      artwork: src ? [{ src, sizes: '512x512', type: 'image/jpeg' }] : [],
    });
  }
  markPlaying();
  renderQueuePanel();
  if (document.body.classList.contains('player-fs')) renderFsSide();
}

function setBusy(on) { $('#play-btn').classList.toggle('busy', on); }

$('#now-img').onerror = function() {
  this.onerror = () => { this.removeAttribute('src'); };
  if (this.dataset.vid && !this.src.includes('/api/thumb/')) this.src = `/api/thumb/${this.dataset.vid}`;
};

function updatePrevNext() {
  $('#btn-prev').disabled = qIndex <= 0;
  const canContinue = _learnNext !== false;
  $('#btn-next').disabled = qIndex < 0 || (!canContinue && (queue.length < 2 || (!isShuffle && repeatMode === 0 && qIndex >= queue.length - 1)));
  $('#play-btn').disabled = currentId === null;
}

function nextIndex(dir) {
  if (dir === 1) {
    if (isShuffle && queue.length > 1) {
      let n;
      do { n = Math.floor(Math.random() * queue.length); } while (n === qIndex);
      return n;
    }
    if (qIndex >= 0 && qIndex < queue.length - 1) return qIndex + 1;
    return repeatMode === 1 && queue.length ? 0 : -1;
  }
  return qIndex > 0 ? qIndex - 1 : -1;
}

async function play(t) {
  if (!t || !t.videoId) { toast('Dados da música inválidos.'); return; }
  const token = ++playToken;
  currentId = t.videoId;
  qIndex = queue.findIndex(x => x.videoId === t.videoId);
  streamTries = 0;
  setNowPlaying(t);
  setBusy(true);
  updatePrevNext();

  clearTimeout(playTimer);
  clearTimeout(resolveTimer);
  playTimer = setTimeout(async () => {
    if (token !== playToken) return;
    const isLocal = t.local || (t.videoId || '').startsWith('up_');
    let src = isLocal ? `/api/local/${t.videoId}` : `/api/stream/${t.videoId}`;
    let isOff = false;
    if (!isLocal) {
      try {
        const rec = await Offline.get(t.videoId);
        if (token !== playToken) return;
        if (rec && rec.blob && rec.blob.size > 0) {
          if (_blobUrl) URL.revokeObjectURL(_blobUrl);
          _blobUrl = URL.createObjectURL(rec.blob);
          src = _blobUrl;
          isOff = true;
        }
      } catch {}
    }
    setOfflineBadge(isOff);
    audio.src = src;
    audio.load();
    audio.play().catch(e => {
      if (token !== playToken) return;
      setBusy(false);
      console.error('Erro ao reproduzir:', e);
      toast('Toque na página para liberar o áudio.');
    });
    if (!t.local) {
      fetch(`/api/history/${t.videoId}?title=${encodeURIComponent(t.title || '')}&artist=${encodeURIComponent(t.artist || '')}&thumbnail=${encodeURIComponent(t.thumbnail || '')}`, { method: 'POST' }).catch(() => {});
    }
  }, 250);

  // Só pré-resolve/aquece próximas faixas e busca letras depois que a escolha estabiliza,
  // evitando tempestade de extrações paralelas quando o usuário troca de faixa rápido.
  resolveTimer = setTimeout(() => {
    if (token !== playToken) return;
    const nxt = queue[qIndex + 1];
    if (nxt?.videoId && nxt.videoId !== lastResolvedId) {
      lastResolvedId = nxt.videoId;
      fetch(`/api/resolve/${nxt.videoId}?title=${encodeURIComponent(nxt.title || '')}&artist=${encodeURIComponent(nxt.artist || '')}`).catch(() => {});
    }
    const ahead = queue.slice(qIndex + 2, qIndex + 5).map(x => x.videoId).filter(Boolean);
    if (ahead.length) {
      fetch('/api/cache/warm', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: ahead }) }).catch(() => {});
    }
    lyrPrefetch(t);
  }, 1400);
}

audio.addEventListener('error', () => {
  if (!currentId || !audio.src.includes(currentId)) { setBusy(false); return; }
  const token = playToken;
  if (streamTries >= 1) {
    setBusy(false);
    toast('Não foi possível tocar agora — tente novamente em alguns segundos.');
    return;
  }
  streamTries++;
  setTimeout(() => {
    if (token !== playToken || !currentId) return;
    audio.src = `/api/stream/${currentId}?r=${Date.now()}`;
    audio.load();
    audio.play().catch(() => {});
  }, 2500);
});

audio.addEventListener('playing', () => { streamTries = 0; setBusy(false); document.body.classList.add('is-playing'); saveResume(true); });
audio.addEventListener('waiting', () => setBusy(true));
audio.addEventListener('play', () => {
  $('#play-i').innerHTML = ICON.pause.d;
  document.body.classList.add('is-playing');
  if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'playing';
  const t = queue[qIndex]; if (t?.title) document.title = `${t.title} · ${t.artist || 'StremMusic'}`;
});
audio.addEventListener('pause', () => {
  $('#play-i').innerHTML = ICON.play.d;
  document.body.classList.remove('is-playing');
  if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'paused';
  const t = queue[qIndex]; if (t?.title) document.title = `⏸ ${t.title} · ${t.artist || 'StremMusic'}`;
  saveResume(true);
});
audio.addEventListener('ended', () => {
  if (repeatMode === 2 && currentId) { audio.currentTime = 0; audio.play().catch(() => {}); return; }
  const n = nextIndex(1);
  if (n >= 0 && queue[n]) play(queue[n]);
  else {
    document.body.classList.remove('is-playing'); $('#play-i').innerHTML = ICON.play.d;
    smartAutoContinue();
  }
});

function paintRange(input) {
  const pct = (input.value - input.min) / (input.max - input.min) * 100;
  input.style.setProperty('--p', pct + '%');
}

audio.addEventListener('timeupdate', () => {
  saveResume();
  syncMediaPos();
  updateLyricsSync();
  if (!isSeeking) {
    $('#cur-time').textContent = fmtTime(audio.currentTime);
    if (audio.duration) {
      seek.value = (audio.currentTime / audio.duration) * 1000;
      paintRange(seek);
    }
  }
});
audio.addEventListener('loadedmetadata', () => {
  $('#tot-time').textContent = fmtTime(audio.duration);
  _lastPosSync = 0; syncMediaPos();
  if (pendingSeek != null) {
    if (audio.duration && pendingSeek > audio.duration - 8) pendingSeek = 0;
    if (pendingSeek) { try { audio.currentTime = pendingSeek; } catch {} }
    pendingSeek = null;
  }
});
audio.addEventListener('durationchange', () => { $('#tot-time').textContent = fmtTime(audio.duration); });

seek.addEventListener('input', () => {
  isSeeking = true; paintRange(seek);
  if (audio.duration) $('#cur-time').textContent = fmtTime((seek.value / 1000) * audio.duration);
});
seek.addEventListener('change', () => {
  if (audio.duration) audio.currentTime = (seek.value / 1000) * audio.duration;
  isSeeking = false;
});

function applyVolume(v) { audio.volume = v; vol.value = v; paintRange(vol); updateVolIcon(); }
function updateVolIcon() {
  const muted = audio.muted || audio.volume === 0;
  $('#vol-i').innerHTML = muted ? ICON.mute : (audio.volume < 0.5 ? ICON.volLo : ICON.volHi);
}
vol.addEventListener('input', () => {
  audio.muted = false; applyVolume(parseFloat(vol.value));
  try { localStorage.setItem('mw-vol', vol.value); } catch {}
});
$('#mute-btn').onclick = () => { audio.muted = !audio.muted; updateVolIcon(); };
audio.addEventListener('volumechange', updateVolIcon);

let savedVol = 0.8;
try { savedVol = parseFloat(localStorage.getItem('mw-vol') ?? '0.8'); } catch {}
applyVolume(isNaN(savedVol) ? 0.8 : savedVol);
paintRange(seek);

$('#play-btn').onclick = () => {
  if (!currentId) return;
  if (!audio.src) { if (queue[qIndex]) play(queue[qIndex]); return; }
  if (audio.paused) audio.play().catch(() => {}); else audio.pause();
};
$('#btn-next').onclick = () => { const n = nextIndex(1); if (n >= 0 && queue[n]) play(queue[n]); else smartAutoContinue(); };
$('#btn-prev').onclick = () => {
  if (audio.currentTime > 3) { audio.currentTime = 0; return; }
  const n = nextIndex(-1);
  if (n >= 0 && queue[n]) play(queue[n]);
};

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if ($('#track-sheet-ov').classList.contains('open')) $('#track-sheet-ov').classList.remove('open');
    else if ($('#lyr-modal').classList.contains('open')) closeLyrics();
    else if ($('#help-modal').classList.contains('open')) closeHelp();
    else if ($('#pl-modal').classList.contains('open')) closePlModal();
    else if ($('#up-modal').classList.contains('open')) $('#up-modal').classList.remove('open');
    else if (document.body.classList.contains('queue-open')) document.body.classList.remove('queue-open');
    else if (document.body.classList.contains('player-fs')) $('#btn-expand').click();
    else if (document.body.classList.contains('player-hidden')) setHidden(false);
    return;
  }
  const ae = document.activeElement;
  if (ae && ['INPUT', 'TEXTAREA', 'SELECT'].includes(ae.tagName)) return;
  if (ae && ae.tagName === 'BUTTON' && e.code === 'Space') return;
  if (e.code === 'Space') { e.preventDefault(); $('#play-btn').click(); }
  else if (e.code === 'ArrowRight') { audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + (e.shiftKey ? 10 : 5)); }
  else if (e.code === 'ArrowLeft') { audio.currentTime = Math.max(0, audio.currentTime - (e.shiftKey ? 10 : 5)); }
  else if (e.shiftKey && e.code === 'ArrowUp') { e.preventDefault(); setVol(audio.volume + 0.05); }
  else if (e.shiftKey && e.code === 'ArrowDown') { e.preventDefault(); setVol(audio.volume - 0.05); }
  else if (e.key === '?') { e.preventDefault(); openHelp(); }
  else if (e.key.toLowerCase() === 'l') { openLyrics(); }
  else if (e.key === '/') { e.preventDefault(); $('#search').focus(); $('#search').select(); }
  else if (e.key.toLowerCase() === 'm') { $('#mute-btn').click(); }
  else if (e.key.toLowerCase() === 'h') { setHidden(!document.body.classList.contains('player-hidden')); }
  else if (e.key.toLowerCase() === 'f') { $('#btn-expand').click(); }
  else if (e.key.toLowerCase() === 'q') { $('#btn-queue').click(); }
});

// ---------- APK / PWA / MediaSession ----------
const inStremApp = typeof window.StremApp !== 'undefined';
let _keepAliveOn = false;
function stremKeepAlive() {
  if (!inStremApp || _keepAliveOn) return;
  _keepAliveOn = true;
  try { window.StremApp.keepAlive(true); } catch { _keepAliveOn = false; }
}
audio.addEventListener('playing', stremKeepAlive);
if (inStremApp) window.addEventListener('beforeinstallprompt', e => e.preventDefault(), { capture: true });

let _bipEvt = null;
window.addEventListener('beforeinstallprompt', e => {
  e.preventDefault(); _bipEvt = e;
  try {
    const bi = $('#btn-install');
    if (bi && !window.matchMedia('(display-mode: standalone)').matches) {
      bi.style.display = '';
      bi.onclick = () => { _bipEvt.prompt(); _bipEvt = null; bi.style.display = 'none'; };
    }
  } catch {}
});

const _mqLand = window.matchMedia('(orientation: landscape) and (max-height: 480px)');
_mqLand.addEventListener?.('change', e => {
  if (e.matches && currentId && !document.body.classList.contains('player-hidden')
      && !document.body.classList.contains('player-fs')) $('#btn-expand').click();
});

if ('mediaSession' in navigator) {
  navigator.mediaSession.setActionHandler('play', () => audio.play().catch(() => {}));
  navigator.mediaSession.setActionHandler('pause', () => audio.pause());
  navigator.mediaSession.setActionHandler('previoustrack', () => $('#btn-prev').click());
  navigator.mediaSession.setActionHandler('nexttrack', () => $('#btn-next').click());
  try { navigator.mediaSession.setActionHandler('stop', () => { audio.pause(); audio.currentTime = 0; }); } catch {}
  try { navigator.mediaSession.setActionHandler('seekbackward', d => { audio.currentTime = Math.max(0, audio.currentTime - (d.seekOffset || 5)); }); } catch {}
  try { navigator.mediaSession.setActionHandler('seekforward', d => { audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + (d.seekOffset || 5)); }); } catch {}
  try { navigator.mediaSession.setActionHandler('seekto', d => { if (d.seekTime != null) audio.currentTime = d.seekTime; }); } catch {}
}
let _lastPosSync = 0;
function syncMediaPos() {
  if (!('mediaSession' in navigator) || !navigator.mediaSession.setPositionState) return;
  if (!isFinite(audio.duration) || audio.duration <= 0) return;
  const now = Date.now();
  if (now - _lastPosSync < 1000) return;
  _lastPosSync = now;
  try {
    navigator.mediaSession.setPositionState({
      duration: audio.duration, playbackRate: audio.playbackRate || 1,
      position: Math.min(audio.currentTime || 0, audio.duration),
    });
  } catch {}
}

// ---------- Letras (LRCLIB + YT Music) & Painel Fullscreen ----------
let lyrReq = 0, lyrSync = null, lyrLastIdx = -1, _fsSideTab = 'lyrics', _lastLyr = null;
function parseLRC(s) {
  const out = [];
  for (const line of String(s || '').split('\n')) {
    const times = [...line.matchAll(/\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g)];
    if (!times.length) continue;
    const text = line.replace(/\[[^\]]*\]/g, '').trim();
    for (const m of times) {
      const frac = m[3] ? (+m[3]) / Math.pow(10, m[3].length) : 0;
      out.push({ t: (+m[1]) * 60 + (+m[2]) + frac, text });
    }
  }
  out.sort((a, b) => a.t - b.t);
  return out;
}

function updateLyricsSync() {
  if (!lyrSync) return;
  const modalOpen = $('#lyr-modal').classList.contains('open');
  const fsOpen = document.body.classList.contains('player-fs') && _fsSideTab === 'lyrics' && window.innerWidth >= 980;
  if (!modalOpen && !fsOpen) return;
  const ct = audio.currentTime + 0.25;
  let idx = -1;
  for (let i = 0; i < lyrSync.length; i++) { if (lyrSync[i].t <= ct) idx = i; else break; }
  if (idx === lyrLastIdx) return;
  lyrLastIdx = idx;
  const targets = [];
  if (modalOpen) targets.push($('#lyr-body'));
  if (fsOpen) targets.push($('#fs-side-body .lyr-body'));
  for (const body of targets) {
    if (!body) continue;
    body.querySelectorAll('.lyr-line').forEach((el, i) => el.classList.toggle('on', i === idx));
    const on = body.querySelector('.lyr-line.on');
    if (on) on.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }
}

const lyrCache = new Map();
function lyrPrefetch(t) {
  if (!t || !t.videoId || t.local) return;
  if (lyrCache.has(t.videoId)) return;
  if (lyrCache.size > 24) lyrCache.clear();
  let dur = 0;
  if (t.duration && /^\d+:\d\d?$/.test(t.duration)) {
    const p = t.duration.split(':'); dur = (+p[0]) * 60 + (+p[1]);
  }
  const url = `/api/lyrics/${t.videoId}?title=${encodeURIComponent(t.title || '')}&artist=${encodeURIComponent(t.artist || '')}`
    + (dur ? `&duration=${dur.toFixed(1)}` : '');
  lyrCache.set(t.videoId, { promise: api(url) });
}

async function fetchLyricsForCurrent() {
  if (!currentId) return null;
  if (_lastLyr && _lastLyr.id === currentId) return _lastLyr.data;
  const t = queue[qIndex] || {};
  let dur = isFinite(audio.duration) && audio.duration ? audio.duration : 0;
  if (!dur && t.duration && /^\d+:\d\d?$/.test(t.duration)) {
    const p = t.duration.split(':'); dur = (+p[0]) * 60 + (+p[1]);
  }
  const pre = lyrCache.get(currentId);
  lyrCache.delete(currentId);
  const d = pre ? await pre.promise
    : await api(`/api/lyrics/${currentId}?title=${encodeURIComponent(t.title || '')}&artist=${encodeURIComponent(t.artist || '')}`
      + (dur ? `&duration=${dur.toFixed(1)}` : ''));
  if (d) _lastLyr = { id: currentId, data: d };
  return d;
}

function renderLyricsInto(bodyEl, srcEl, d) {
  lyrLastIdx = -1;
  if (d.synced && parseLRC(d.synced).length) {
    lyrSync = parseLRC(d.synced);
    bodyEl.classList.add('sync');
    bodyEl.innerHTML = lyrSync.map((l, i) => l.text
      ? `<div class="lyr-line" data-i="${i}">${esc(l.text)}</div>`
      : `<div class="lyr-line gap" data-i="${i}">···</div>`).join('');
    bodyEl.querySelectorAll('.lyr-line:not(.gap)').forEach(el => el.onclick = () => {
      const l = lyrSync[+el.dataset.i];
      if (l) { audio.currentTime = l.t; lyrLastIdx = -1; updateLyricsSync(); }
    });
  } else {
    lyrSync = null;
    bodyEl.classList.remove('sync');
    bodyEl.textContent = d.lyrics || '';
  }
  if (!bodyEl.textContent && !bodyEl.querySelector('.lyr-line')) {
    bodyEl.innerHTML = '<div class="lyr-empty">Letra não disponível para esta faixa.</div>';
  }
  bodyEl.scrollTop = 0;
  if (srcEl) srcEl.textContent = 'Fonte: ' + (d.source || '?') + (d.synced ? ' · sincronizada' : '');
}

function syncFsButtons() {
  const fs = document.body.classList.contains('player-fs');
  const sideOpen = fs && window.innerWidth >= 980 && document.body.classList.contains('fs-side-open');
  const bl = $('#btn-lyr'), bq = $('#btn-queue');
  if (bl) bl.classList.toggle('active', sideOpen && _fsSideTab === 'lyrics');
  if (bq) bq.classList.toggle('active', sideOpen && _fsSideTab === 'queue');
}

async function renderFsSide() {
  syncFsButtons();
  const body = $('#fs-side-body');
  if (!body || window.innerWidth < 980 || !document.body.classList.contains('fs-side-open')) return;
  document.querySelectorAll('.fs-tab').forEach(b => b.classList.toggle('on', b.dataset.fstab === _fsSideTab));
  if (_fsSideTab === 'queue') {
    body.innerHTML = renderQueueHTML();
    bindQueueContainer(body);
    return;
  }
  if (!currentId) {
    body.innerHTML = '<div class="lyr-empty">Nenhuma faixa em reprodução.</div>';
    return;
  }
  const req = ++lyrReq;
  body.innerHTML = '<div class="hint" style="padding:32px"><span class="loader"></span></div>';
  try {
    const d = await fetchLyricsForCurrent();
    if (req !== lyrReq || !d) return;
    body.innerHTML = '<div class="lyr-body"></div><div class="lyr-src" style="margin-top:12px"></div>';
    renderLyricsInto(body.querySelector('.lyr-body'), body.querySelector('.lyr-src'), d);
    updateLyricsSync();
  } catch (e) {
    if (req !== lyrReq) return;
    body.innerHTML = `<div class="lyr-empty">${esc(e.message)}</div>`;
  }
}

document.querySelectorAll('.fs-tab').forEach(b => b.onclick = () => {
  _fsSideTab = b.dataset.fstab;
  renderFsSide();
});
const _fsClose = $('#fs-side-close');
if (_fsClose) _fsClose.onclick = () => {
  document.body.classList.remove('fs-side-open');
  syncFsButtons();
};

async function openLyrics() {
  if (!currentId) { toast('Toque uma música primeiro'); return; }
  if (document.body.classList.contains('player-fs') && window.innerWidth >= 980) {
    const open = document.body.classList.contains('fs-side-open');
    if (open && _fsSideTab === 'lyrics') {
      document.body.classList.remove('fs-side-open');
      syncFsButtons();
      return;
    }
    _fsSideTab = 'lyrics';
    document.body.classList.add('fs-side-open');
    renderFsSide();
    return;
  }
  const t = queue[qIndex] || {};
  const req = ++lyrReq;
  lyrSync = null; lyrLastIdx = -1;
  $('#lyr-modal').classList.add('open');
  $('#lyr-t').textContent = t.title || '';
  $('#lyr-a').textContent = t.artist || '';
  const body = $('#lyr-body');
  body.classList.remove('sync');
  body.innerHTML = '<div class="hint" style="padding:28px"><span class="loader"></span></div>';
  $('#lyr-src').textContent = '';
  try {
    const d = await fetchLyricsForCurrent();
    if (req !== lyrReq || !d) return;
    renderLyricsInto(body, $('#lyr-src'), d);
    updateLyricsSync();
  } catch (e) {
    if (req !== lyrReq) return;
    body.innerHTML = `<div class="lyr-empty">${esc(e.message)}</div>`;
  }
}
function closeLyrics() { $('#lyr-modal').classList.remove('open'); lyrReq++; }
$('#lyr-close').onclick = closeLyrics;
$('#lyr-modal').onclick = e => { if (e.target.id === 'lyr-modal') closeLyrics(); };
document.querySelectorAll('.btn-lyr').forEach(b => b.onclick = openLyrics);

// ---------- Ajuda de atalhos (?) ----------
const SHORTCUTS = [
  ['Espaço', 'Tocar / pausar'],
  ['← / →', 'Voltar / avançar 5s'],
  ['Shift + ← / →', 'Pular 10s'],
  ['Shift + ↑ / ↓', 'Volume −5% / +5%'],
  ['M', 'Mudo'],
  ['F', 'Tela cheia'],
  ['H', 'Esconder / mostrar player'],
  ['Q', 'Fila de reprodução'],
  ['L', 'Letras'],
  ['/', 'Focar busca'],
  ['?', 'Ajuda'],
  ['Esc', 'Fechar painel / tela cheia'],
];
function openHelp() {
  $('#help-list').innerHTML = SHORTCUTS.map(([k, d]) => `<div class="kbd-row"><kbd>${k}</kbd><span>${d}</span></div>`).join('');
  $('#help-modal').classList.add('open');
}
function closeHelp() { $('#help-modal').classList.remove('open'); }
$('#help-close').onclick = closeHelp;
$('#help-modal').onclick = e => { if (e.target.id === 'help-modal') closeHelp(); };

// ---------- Retomar sessão ----------
let pendingSeek = null, _lastResumeSave = 0;
function saveResume(force) {
  try {
    const now = Date.now();
    if (!force && now - _lastResumeSave < 3000) return;
    _lastResumeSave = now;
    if (!currentId) return;
    const i = queue.findIndex(x => x?.videoId === currentId);
    if (i < 0) return;
    const pos = (isFinite(audio.currentTime) && audio.currentTime > 0) ? audio.currentTime : 0;
    localStorage.setItem('mw-resume', JSON.stringify({
      q: queue.slice(0, 500).map(x => ({ videoId: x.videoId, title: x.title, artist: x.artist, thumbnail: x.thumbnail, duration: x.duration || '' })),
      i, t: pos, sh: isShuffle, rp: repeatMode,
    }));
  } catch {}
}
window.addEventListener('beforeunload', () => saveResume(true));

(function restoreResume() {
  let d = null;
  try { d = JSON.parse(localStorage.getItem('mw-resume') || 'null'); } catch {}
  if (!d || !Array.isArray(d.q)) return;
  queue = d.q.filter(x => x && x.videoId);
  if (!queue.length) return;
  qIndex = Math.min(Math.max(d.i | 0, 0), queue.length - 1);
  isShuffle = !!d.sh;
  repeatMode = Math.min(Math.max(d.rp | 0, 0), 2);
  if (isShuffle) $('#btn-shuffle').classList.add('active');
  applyRepeatUI();
  const t = queue[qIndex];
  if (!t) return;
  currentId = t.videoId;
  setNowPlaying(t);
  const warmIds = queue.slice(qIndex, qIndex + 3).map(x => x.videoId).filter(Boolean);
  if (warmIds.length) {
    setTimeout(() => {
      fetch('/api/cache/warm', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: warmIds }) }).catch(() => {});
    }, 3500);
  }
  if (d.t > 5) pendingSeek = d.t;
  updatePrevNext();
  setTimeout(() => toast('Sessão anterior pronta — aperte ▶ para continuar'), 700);
})();

// ---------- Aparência (Layouts / Tema / Desempenho) ----------
const LAYOUTS = [
  { id: 'auto', name: 'Automático', hint: 'escolhe pela tela' },
  { id: 'classic', name: 'Clássico', hint: 'equilibrado' },
  { id: 'studio', name: 'Estúdio', hint: 'capas maiores' },
  { id: 'compact', name: 'Compacto', hint: 'lista densa' },
];
const THEMES = [{ id: 'default', name: 'Padrão' }, { id: 'night', name: 'Noturno' }];
const PERFS = [{ id: 'full', name: 'Completo' }, { id: 'lite', name: 'Leve' }];
const store = {
  get(k, d) { try { return localStorage.getItem(k) || d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
};
const _coarse = () => window.matchMedia('(pointer: coarse)').matches;

function autoLayout() {
  const w = window.innerWidth, h = window.innerHeight;
  if (_coarse() && w < 1500) return 'compact';
  if (w >= 1680 && h >= 800) return 'studio';
  if (w >= 1180) return 'classic';
  return 'compact';
}

let _layoutChoice = LAYOUTS.some(l => l.id === store.get('sm-layout', 'auto')) ? store.get('sm-layout', 'auto') : 'auto';
let _theme = THEMES.some(t => t.id === store.get('sm-theme', 'default')) ? store.get('sm-theme', 'default') : 'default';
let _perf = PERFS.some(p => p.id === store.get('sm-perf', 'full')) ? store.get('sm-perf', 'full') : 'full';
function resolvedLayout() { return _layoutChoice === 'auto' ? autoLayout() : _layoutChoice; }

function paintLayoutPicker() {
  document.querySelectorAll('[data-set-layout]').forEach(b => {
    const on = b.dataset.setLayout === _layoutChoice;
    b.classList.toggle('on', on); b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  document.querySelectorAll('[data-set-theme]').forEach(b => {
    const on = b.dataset.setTheme === _theme;
    b.classList.toggle('on', on); b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  document.querySelectorAll('[data-set-perf]').forEach(b => {
    const on = b.dataset.setPerf === _perf;
    b.classList.toggle('on', on); b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  const btn = $('#btn-layout');
  if (btn) {
    const meta = LAYOUTS.find(l => l.id === _layoutChoice) || LAYOUTS[0];
    const real = LAYOUTS.find(l => l.id === resolvedLayout()) || LAYOUTS[1];
    btn.dataset.layout = resolvedLayout();
    btn.title = `Layout: ${meta.name}` + (meta.id === 'auto' ? ` (${real.name})` : '') + ' · Alt+1..4';
    btn.setAttribute('aria-label', `Layout atual: ${meta.name}. Alternar layout`);
  }
}

function applyLayout() {
  document.documentElement.dataset.layout = resolvedLayout();
  document.documentElement.dataset.layoutChoice = _layoutChoice;
  paintLayoutPicker();
  const s = $('#scroll');
  if (s) s.style.paddingBottom = '';
}
function setLayout(id, announce) {
  const meta = LAYOUTS.find(l => l.id === id) || LAYOUTS[0];
  if (meta.id === _layoutChoice) { if (announce) toast(`Já está no layout ${meta.name}`); return; }
  _layoutChoice = meta.id; store.set('sm-layout', meta.id); applyLayout();
  if (announce) {
    const real = LAYOUTS.find(l => l.id === resolvedLayout()) || LAYOUTS[1];
    toast(meta.id === 'auto' ? `Layout automático (${real.name})` : `Layout ${meta.name}`);
  }
}
function cycleLayout() {
  const i = LAYOUTS.findIndex(l => l.id === _layoutChoice);
  setLayout(LAYOUTS[(i + 1) % LAYOUTS.length].id, true);
}
function setTheme(id, announce) {
  _theme = THEMES.some(t => t.id === id) ? id : 'default';
  store.set('sm-theme', _theme);
  if (_theme === 'night') document.documentElement.dataset.theme = 'night';
  else delete document.documentElement.dataset.theme;
  const tc = document.querySelector('meta[name=theme-color]');
  if (tc) tc.content = _theme === 'night' ? '#000000' : '#09090b';
  paintLayoutPicker();
  if (announce) toast(_theme === 'night' ? 'Modo noturno ativado' : 'Tema padrão ativado');
}
function setPerf(id, announce) {
  _perf = PERFS.some(p => p.id === id) ? id : 'full';
  store.set('sm-perf', _perf);
  if (_perf === 'lite') document.documentElement.dataset.perf = 'lite';
  else delete document.documentElement.dataset.perf;
  paintLayoutPicker();
  if (announce) toast(_perf === 'lite' ? 'Modo leve ativado' : 'Modo completo ativado');
}

if ($('#btn-layout')) $('#btn-layout').onclick = cycleLayout;
document.addEventListener('keydown', e => {
  if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
  const i = ['1', '2', '3', '4'].indexOf(e.key);
  if (i >= 0) { e.preventDefault(); setLayout(LAYOUTS[i].id, true); }
});

let _resizeTm = null;
function _onViewportChange() {
  clearTimeout(_resizeTm);
  _resizeTm = setTimeout(() => { if (_layoutChoice === 'auto') applyLayout(); }, 160);
}
window.addEventListener('resize', _onViewportChange);
window.addEventListener('orientationchange', _onViewportChange);

// ---------- Tela cheia ----------
function syncFsIcon(on) {
  const b = $('#btn-expand');
  if (!b) return;
  b.innerHTML = svgIcon(on ? ICON.collapse : ICON.expand);
  b.title = on ? 'Sair da tela cheia (F ou Esc)' : 'Tela cheia (F)';
  b.setAttribute('aria-pressed', on ? 'true' : 'false');
}
function toggleFullscreen() {
  const on = !document.body.classList.contains('player-fs');
  document.body.classList.toggle('player-fs', on);
  if (!on) document.body.classList.remove('fs-side-open');
  syncFsIcon(on);
  syncFsButtons();
  if (on && document.body.classList.contains('fs-side-open')) renderFsSide();
}
document.addEventListener('fullscreenchange', () => {
  if (!document.fullscreenElement && document.body.classList.contains('player-fs')) syncFsIcon(true);
});

if (_theme === 'night') document.documentElement.dataset.theme = 'night';
if (_perf === 'lite') document.documentElement.dataset.perf = 'lite';
applyLayout();
syncFsIcon(document.body.classList.contains('player-fs'));

// ---------- Skeletons ----------
function skeletonList(n) {
  let out = '';
  for (let i = 0; i < (n || 7); i++) {
    out += `<div class="skel-row" aria-hidden="true">
      <span class="skel sm" style="width:14px;margin:0 auto"></span>
      <span class="skel sq"></span>
      <span><span class="skel" style="width:${52 + ((i * 7) % 30)}%;display:block"></span>
      <span class="skel sm" style="width:${25 + ((i * 11) % 25)}%;display:block;margin-top:6px"></span></span>
      <span class="skel sm" style="width:32px;margin-left:auto"></span>
    </div>`;
  }
  return `<div class="track-list" role="status" aria-label="Carregando">${out}</div>`;
}
function skeletonCards(n) {
  let out = '';
  for (let i = 0; i < (n || 6); i++) {
    out += `<div aria-hidden="true"><div class="skel" style="aspect-ratio:1;height:auto;border-radius:10px"></div>
      <div class="skel" style="width:70%;margin-top:10px;height:11px"></div>
      <div class="skel sm" style="width:40%;margin-top:6px"></div></div>`;
  }
  return `<div class="pl-grid" role="status" aria-label="Carregando">${out}</div>`;
}
const _tabScroll = {};

// ---------- Boot ----------
(async function boot() {
  try {
    const r = await fetch('/api/auth/check');
    if (r.ok) {
      const d = await r.json();
      if (d.required && !d.ok) {
        localStorage.removeItem('sm-token');
        document.cookie = 'sm_token=; path=/; max-age=0';
        let tok = null;
        while (!tok) {
          const a = prompt('Este servidor pede um token de acesso (APP_TOKEN):');
          if (a === null) {
            $('#now-title').textContent = 'Acesso bloqueado';
            document.title = 'StremMusic — sem acesso';
            return;
          }
          tok = a.trim();
        }
        localStorage.setItem('sm-token', tok);
        document.cookie = `sm_token=${encodeURIComponent(tok)}; path=/; max-age=31536000; SameSite=Strict`;
        location.reload();
        return;
      }
    }
  } catch {}

  try {
    const u = new URL(location.href);
    if (u.searchParams.has('sk')) {
      localStorage.setItem('sm-sk', u.searchParams.get('sk'));
      u.searchParams.delete('sk');
      history.replaceState(null, '', u.pathname + (u.search ? u.search : '') + u.hash);
    }
  } catch {}
  await refreshCurrentUser();

  if (['127.0.0.1', 'localhost', '::1'].includes(location.hostname) && isAdmin()) {
    const hp = $('#btn-host-panel');
    if (hp) hp.style.display = '';
  }

  const sInp = $('#search'), sWrap = $('#search-wrap'), sClr = $('#search-clear');
  if (sInp && sWrap && sClr) {
    sInp.addEventListener('input', () => sWrap.classList.toggle('has-val', sInp.value.length > 0));
    sClr.onclick = () => {
      sInp.value = ''; sWrap.classList.remove('has-val'); closeSugg(); _searchQ = '';
      if (currentTab === 'search') search();
      sInp.focus();
    };
  }

  const netChip = $('#net-chip'), netTxt = $('#net-txt');
  function syncNetStatus() {
    if (!netChip || !netTxt) return;
    const on = navigator.onLine !== false;
    netChip.classList.toggle('offline', !on);
    netTxt.textContent = on ? 'Online' : 'Offline';
    netChip.title = on ? 'Conectado ao servidor' : 'Sem conexão — ver faixas salvas no aparelho';
  }
  if (netChip) {
    netChip.onclick = () => {
      if (navigator.onLine === false || netChip.classList.contains('offline')) nav('offline');
      else if (['127.0.0.1', 'localhost', '::1'].includes(location.hostname) && isAdmin()) window.open('/painel', '_blank');
    };
  }
  window.addEventListener('online', syncNetStatus);
  window.addEventListener('offline', syncNetStatus);
  syncNetStatus();

  let _touchY0 = null, _touchX0 = null;
  const plEl = $('#player');
  if (plEl) {
    plEl.addEventListener('touchstart', e => {
      if (!document.body.classList.contains('player-fs') || e.touches.length !== 1) return;
      _touchY0 = e.touches[0].clientY; _touchX0 = e.touches[0].clientX;
    }, { passive: true });
    plEl.addEventListener('touchend', e => {
      if (_touchY0 === null || !document.body.classList.contains('player-fs')) return;
      const dy = (e.changedTouches[0]?.clientY || 0) - _touchY0;
      const dx = Math.abs((e.changedTouches[0]?.clientX || 0) - (_touchX0 || 0));
      _touchY0 = _touchX0 = null;
      if (dy > 70 && dy > dx * 1.5) toggleFullscreen();
    }, { passive: true });
  }

  updatePrevNext();
  await refreshOfflineIds();
  if (navigator.onLine === false) { currentTab = 'offline'; activateNav('offline'); }
  loadTab();
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(err => console.debug('SW reg fail:', err));
    });
  }
})();
