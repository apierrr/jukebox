/* Jukebox — front mobile (vanilla JS, sans build). */
(() => {
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = s => { s = Math.max(0, Math.floor(s || 0)); const m = Math.floor(s / 60), r = s % 60; return `${m}:${r < 10 ? '0' : ''}${r}`; };
const svg = d => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;

const I = {
  home: svg('<path d="M3 11l9-8 9 8v9a2 2 0 0 1-2 2h-4v-6H9v6H5a2 2 0 0 1-2-2z"/>'),
  search: svg('<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>'),
  library: svg('<path d="M4 5h4v14H4zM10 5h4v14h-4zM16.5 5.5l3.5 1-3 13-3.5-1z"/>'),
  queue: svg('<path d="M4 6h16M4 12h10M4 18h7"/><path d="M17 12v6l4-3z" fill="currentColor"/>'),
  play: svg('<path d="M7 5v14l12-7z" fill="currentColor" stroke="none"/>'),
  info: svg('<circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><circle cx="12" cy="7.6" r=".7" fill="currentColor"/>'),
  pause: svg('<path d="M7 5h4v14H7zM13 5h4v14h-4z" fill="currentColor" stroke="none"/>'),
  next: svg('<path d="M6 5l10 7-10 7z" fill="currentColor" stroke="none"/><path d="M18 5v14"/>'),
  prev: svg('<path d="M18 5L8 12l10 7z" fill="currentColor" stroke="none"/><path d="M6 5v14"/>'),
  plus: svg('<path d="M12 5v14M5 12h14"/>'),
  back: svg('<path d="M15 5l-7 7 7 7"/>'),
  down: svg('<path d="M6 9l6 6 6-6"/>'),
  chev: svg('<path d="M9 6l6 6-6 6"/>'),
  close: svg('<path d="M6 6l12 12M18 6L6 18"/>'),
  trash: svg('<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>'),
  volLo: svg('<path d="M4 10v4h4l5 4V6L8 10z"/>'),
  volHi: svg('<path d="M4 10v4h4l5 4V6L8 10z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>'),
  note: svg('<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>'),
  heart: svg('<path d="M12 20s-7-4.4-9-8.5C1.5 8 4 5 7 5c2 0 3.5 1.2 5 3 1.5-1.8 3-3 5-3 3 0 5.5 3 4 6.5-2 4.1-9 8.5-9 8.5z"/>'),
  mic: svg('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>'),
  list: svg('<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>'),
  sparkles: svg('<path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>'),
  grid: svg('<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>'),
  chart: svg('<path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/>'),
  news: svg('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 9h6M7 13h10M7 16h10"/>'),
  bag: svg('<path d="M6 8h12l-1 12H7zM9 8a3 3 0 0 1 6 0"/>'),
  folder: svg('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
};

const state = { me: null, status: null, queue: [], view: null, tabCtx: 'home', drag: {}, scroll: {}, lastHash: null, queueScrolled: false, offline: null };
const view = $('#view');

/* ------------------------------------------------------------ utilitaires */
async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: { 'Accept': 'application/json' } };
  if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
  const r = await fetch(path, init);
  if (r.status === 401) { showPin(); throw new Error('Code invité requis'); }
  if (!r.ok) { let m = `Erreur ${r.status}`; try { m = (await r.json()).detail || m; } catch {} throw new Error(m); }
  return r.json();
}
let toastT;
function toast(msg, kind = '') {
  const t = $('#toast'); t.textContent = msg; t.className = `toast ${kind}`;
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.add('hidden'), 2200);
}
const jsonAttr = o => esc(JSON.stringify({ id: o.id, title: o.title, subtitle: o.subtitle, image: o.image, kind: o.kind, url: o.url, pos: o.pos }));
const setTitle = t => { $('#title').textContent = t; };
const skeleton = kind => kind === 'rows'
  ? '<div class="skel skel-line" style="height:20px;width:60%;margin-top:16px"></div>' + '<div class="skel-row"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>'.repeat(3)
  : '<div class="skel skel-line"></div>'.repeat(7);
const emptyHTML = (msg, extra = '') => `<div class="empty">${I.note.replace('<svg', '<svg style="width:44px;height:44px;opacity:.5"')}<div>${msg}</div>${extra}</div>`;
const errorHTML = msg => `<div class="empty"><div>Impossible de charger.</div><div class="small">${esc(msg)}</div><button class="btn" onclick="location.reload()">Réessayer</button></div>`;

/* ----------------------------------------------------------------- rendu */
function browseHref(it) {
  const p = new URLSearchParams();
  if (it.title) p.set('t', it.title);
  if (it.subtitle) p.set('s', it.subtitle);
  if (it.hires) p.set('h', '1');
  if (it.quality) p.set('q', it.quality);
  if (it.image_large || it.image) p.set('i', it.image_large || it.image);
  return `#/browse/${encodeURIComponent(it.id)}?${p}`;
}
const coverHTML = it => it.image ? `<img loading="lazy" src="${esc(it.image)}" alt="">` : `<div class="ph">${I.note}</div>`;
// Hi-Res : étiquette dans le coin des pochettes, version compacte dans les listes.
const hrHTML = it => it.hires ? '<span class="q-badge">Hi-Res</span>' : '';
const cardHTML = it => `<a class="card" href="${browseHref(it)}"><div class="cover">${coverHTML(it)}${it.hires ? '<span class="q-badge q-over">Hi-Res</span>' : ''}</div><div class="c-title one">${esc(it.title)}</div><div class="c-sub one muted">${esc(it.subtitle)}</div></a>`;
function trackRowHTML(it, n, ctx) {
  const lead = n != null ? `<div class="t-num">${n}</div>` : '';
  const cover = it.image ? `<img class="t-cover" loading="lazy" src="${esc(it.image)}" alt="">` : (n != null ? '' : `<div class="t-cover ph">${I.note}</div>`);
  const ctxAttr = ctx ? ` data-ctx='${esc(JSON.stringify(ctx))}'` : '';
  return `<div class="track" data-item='${jsonAttr(it)}'${ctxAttr}>${lead}${cover}<div class="t-meta"><div class="t-title one">${esc(it.title)}</div><div class="t-sub one muted">${n == null ? hrHTML(it) : ''}${esc(it.subtitle)}</div></div><button class="icon-btn t-add" data-act="add" aria-label="Ajouter à la file">${I.plus}</button></div>`;
}
function folderRowHTML(it) {
  const lead = it.icon ? `<div class="row-ico">${I[it.icon] || I.folder}</div>` : (it.image ? `<img class="t-cover" loading="lazy" src="${esc(it.image)}" alt="">` : `<div class="avatar">${esc((it.title || '?').trim()[0].toUpperCase())}</div>`);
  return `<a class="row" href="${browseHref(it)}">${lead}<div class="t-meta"><div class="t-title one">${esc(it.title)}</div>${it.subtitle || it.hires ? `<div class="t-sub one muted">${hrHTML(it)}${esc(it.subtitle)}</div>` : ''}</div><span class="chev">${I.chev}</span></a>`;
}
function sectionRow(sec, moreTitle) {
  return `<section class="sec"><div class="sec-head"><h2>${esc(sec.title)}</h2><a class="more" href="${browseHref({ id: sec.id, title: moreTitle || sec.title })}">Tout voir</a></div><div class="hscroll">${sec.items.map(it => it.kind === 'track' ? trackRowHTML(it) : cardHTML(it)).join('')}</div></section>`;
}

/* ------------------------------------------------------------ actions */
async function doPlay(it, mode) {
  if (!it || !it.id) return;
  const starting = mode === 'play' || mode === 'context';
  if (starting) toast('Un instant, l’enceinte se prépare…');
  try {
    const body = { id: it.id, mode };
    if (mode === 'context' && it.index != null) body.index = it.index;
    if (it.url) body.url = it.url;   // un titre seul se lance par son adresse
    if (it.search) body.search = it.search;
    const s = await api('/api/play', { method: 'POST', body });
    applyState(s); applyQueue(s.queue);
    const label = mode === 'add' ? `Ajouté : ${it.title}` : mode === 'insert' ? `Ensuite : ${it.title}` : `Lecture : ${it.title}`;
    toast(label, 'ok');
  } catch (e) { toast(e.message, 'err'); }
}
async function control(cmd, value) {
  try { const s = await api('/api/control', { method: 'POST', body: { cmd, value } }); applyState(s); applyQueue(s.queue); }
  catch (e) { toast(e.message, 'err'); }
}
view.addEventListener('click', e => {
  const act = e.target.closest('[data-act]');
  if (act) {
    e.preventDefault(); e.stopPropagation();
    const host = act.closest('[data-item]');
    const it = host ? JSON.parse(host.dataset.item) : null;
    if (act.dataset.act === 'more') return loadMore();
    if (act.dataset.act === 'about') return openAbout(browseCtx && browseCtx.about);
    return doPlay(it, act.dataset.act);
  }
  const tr = e.target.closest('.track[data-item]');
  if (tr) {
    const it = JSON.parse(tr.dataset.item);
    if (tr.dataset.ctx) {              // piste dans un album/playlist : lecture directe à partir d'ici
      const ctx = JSON.parse(tr.dataset.ctx);
      return doPlay({ id: ctx.id, index: ctx.index, url: it.url, title: it.title, search: ctx.search }, 'context');
    }
    return openSheet(it);             // piste isolée (recherche, favoris) : choix add / ensuite / maintenant
  }
});

function openSheet(it) {
  const sh = $('#sheet');
  sh.innerHTML = `<div class="sheet-bg" data-close></div><div class="sheet-body">
    <div class="sheet-item">${it.image ? `<img src="${esc(it.image)}" alt="">` : `<div class="t-cover ph">${I.note}</div>`}<div><div class="t-title one">${esc(it.title)}</div><div class="muted small one">${esc(it.subtitle)}</div></div></div>
    <button class="sheet-btn" data-m="add">${I.plus}<span>Ajouter à la file</span></button>
    <button class="sheet-btn" data-m="insert">${I.next}<span>Lire ensuite</span></button>
    <button class="sheet-btn" data-m="play">${I.play}<span>Lire maintenant</span></button>
    <button class="sheet-btn cancel" data-close>Annuler</button></div>`;
  sh.classList.remove('hidden'); requestAnimationFrame(() => sh.classList.add('open'));
  sh.onclick = e => {
    if (e.target.closest('[data-close]')) return closeSheet();
    const b = e.target.closest('[data-m]'); if (b) { closeSheet(); doPlay(it, b.dataset.m); }
  };
}
// Confirmation dans l'app (plutôt que la fenêtre confirm() du navigateur)
function confirmSheet({ title, text, label, icon, onConfirm }) {
  const sh = $('#sheet');
  sh.innerHTML = `<div class="sheet-bg" data-close></div><div class="sheet-body confirm"><h3>${esc(title)}</h3>${text ? `<p class="muted">${esc(text)}</p>` : ''}
    <button class="sheet-btn danger" data-ok>${icon || ''}<span>${esc(label)}</span></button>
    <button class="sheet-btn cancel" data-close>Annuler</button></div>`;
  sh.classList.remove('hidden'); requestAnimationFrame(() => sh.classList.add('open'));
  sh.onclick = e => {
    if (e.target.closest('[data-close]')) return closeSheet();
    if (e.target.closest('[data-ok]')) { closeSheet(); onConfirm(); }
  };
}
// Présentation d'un album ou biographie d'un artiste (bouton « i »)
function openAbout(a) {
  if (!a) return;
  const sh = $('#sheet');
  const facts = (a.facts || []).map(([k, v]) => `<div class="fact"><span class="muted">${esc(k)}</span><span>${esc(v)}</span></div>`).join('');
  const text = (a.text || '').split('\n\n').map(p => `<p>${esc(p)}</p>`).join('');
  sh.innerHTML = `<div class="sheet-bg" data-close></div><div class="sheet-body about"><h3>${esc(a.title)}</h3>${facts ? `<div class="facts">${facts}</div>` : ''}${text ? `<div class="about-text">${text}</div>` : ''}<button class="sheet-btn cancel" data-close>Fermer</button></div>`;
  sh.classList.remove('hidden'); requestAnimationFrame(() => sh.classList.add('open'));
  sh.onclick = e => { if (e.target.closest('[data-close]')) closeSheet(); };
}
function closeSheet() { const sh = $('#sheet'); sh.classList.remove('open'); setTimeout(() => sh.classList.add('hidden'), 220); }

/* ------------------------------------------------------------- lecteur */
function applyState(s) {
  const prev = state.status; state.status = s;
  const cur = s.current;
  if (cur) {
    $('#mini').classList.remove('hidden'); document.body.classList.add('has-mini');
    setImg($('#mini-img'), cur.image); setImg($('#np-img'), cur.image); setImg($('#np-bg'), cur.image);
    $('#mini-title').textContent = cur.title; $('#np-title').textContent = cur.title;
    const sub = [cur.artist, cur.album].filter(Boolean).join(' · ');
    $('#mini-sub').textContent = sub; $('#np-sub').textContent = sub;
  } else { $('#mini').classList.add('hidden'); document.body.classList.remove('has-mini'); closeNP(); }
  const playing = s.mode === 'play';
  // "other" : une autre source (Qobuz Connect…) joue sur l'enceinte ; lecture = la reprendre
  const other = s.mode === 'other';
  $('#mini-toggle').innerHTML = playing ? I.pause : I.play;
  $('#np-toggle').innerHTML = playing ? I.pause : I.play;
  if (!state.drag.vol) $('#vol').value = s.volume;
  $('#np-player').textContent = other ? 'Une autre source joue sur l’enceinte · ▶ pour reprendre' : (s.player || '');
  $('#playerchip').textContent = !s.connected ? 'Enceinte hors ligne' : other ? 'Autre source en cours' : (s.player || '');
  const b = $('#qbadge'); if (s.count > 0) { b.textContent = s.count; b.classList.remove('hidden'); } else b.classList.add('hidden');
  updateProgress();
  if (state.view === 'queue' && (!prev || prev.index !== s.index || prev.mode !== s.mode)) renderQueueRows();
}
function setImg(el, src) { if (el.dataset.src !== (src || '')) { el.dataset.src = src || ''; el.src = src || ''; } }
function applyQueue(q) { state.queue = q || []; if (state.view === 'queue') renderQueueRows(); }
function updateProgress() {
  const s = state.status; if (!s) return;
  const d = s.duration || 0, t = d ? Math.min(s.time || 0, d) : (s.time || 0);
  $('#mini-prog').style.width = (d ? t / d * 100 : 0) + '%';
  if (!state.drag.seek) { $('#seek').max = Math.max(1, Math.floor(d)); $('#seek').value = Math.floor(t); $('#t-cur').textContent = fmt(t); }
  $('#t-dur').textContent = d ? fmt(d) : '--:--';
}
// Entre deux messages du serveur, la position avance localement — mais seulement
// si le serveur a parlé il y a peu : sinon la barre filerait sur un état périmé.
setInterval(() => { const s = state.status; if (s && s.mode === 'play' && Date.now() - lastMsg < 5000) { s.time = (s.time || 0) + 0.5; updateProgress(); } }, 500);

function openNP() { const np = $('#np'); np.classList.remove('hidden'); requestAnimationFrame(() => np.classList.add('open')); }
function closeNP() { const np = $('#np'); if (!np.classList.contains('open')) return; np.classList.remove('open'); setTimeout(() => np.classList.add('hidden'), 320); }
$('#mini').addEventListener('click', openNP);
$('#mini-toggle').addEventListener('click', e => { e.stopPropagation(); control('toggle'); });
$('#mini-next').addEventListener('click', e => { e.stopPropagation(); control('next'); });
$('#np-close').addEventListener('click', closeNP);
$('#np-queue').addEventListener('click', () => { closeNP(); location.hash = '#/queue'; });
$('#np-toggle').addEventListener('click', () => control('toggle'));
$('#np-prev').addEventListener('click', () => control('prev'));
$('#np-next').addEventListener('click', () => control('next'));
const seek = $('#seek');
seek.addEventListener('input', () => { state.drag.seek = true; $('#t-cur').textContent = fmt(+seek.value); });
seek.addEventListener('change', () => { state.drag.seek = false; if (state.status) state.status.time = +seek.value; control('seek', +seek.value); });
const vol = $('#vol'); let volT = null, volPending = null;
function sendVol(v) { volPending = v; if (volT) return; volT = setTimeout(() => { volT = null; const p = volPending; volPending = null; control('volume', p); }, 180); }
vol.addEventListener('input', () => { state.drag.vol = true; sendVol(+vol.value); });
vol.addEventListener('change', () => { state.drag.vol = false; sendVol(+vol.value); });
// glisser vers le bas pour fermer le lecteur
let ty0 = null;
$('#np').addEventListener('touchstart', e => { ty0 = e.target.closest('input') ? null : e.touches[0].clientY; }, { passive: true });
$('#np').addEventListener('touchend', e => { if (ty0 != null && e.changedTouches[0].clientY - ty0 > 90) closeNP(); ty0 = null; }, { passive: true });

/* --------------------------------------------------------- temps réel */
// Le serveur envoie l'état à chaque changement et un "ping" toutes les 10 s.
// Un téléphone qui met la page en veille peut tuer la connexion sans erreur :
// sans nouvelles depuis 25 s, ou au retour au premier plan, on se reconnecte
// et on relit l'état complet.
let es = null, lastMsg = 0;
function connectEvents() {
  if (es) es.close();
  lastMsg = Date.now();
  es = new EventSource('/api/events');
  const seen = () => { lastMsg = Date.now(); };
  es.addEventListener('state', e => { seen(); const s = JSON.parse(e.data); setOffline(null); applyState({ ...s, queue: state.queue }); });
  es.addEventListener('queue', e => { seen(); applyQueue(JSON.parse(e.data).queue); });
  es.addEventListener('ping', seen);
  es.addEventListener('lmserror', e => { seen(); try { setOffline(JSON.parse(e.data).message); } catch {} });
  es.onerror = () => { /* le navigateur retente seul ; le chien de garde couvre le reste */ };
}
function resync() {
  connectEvents();
  api('/api/status').then(s => { lastMsg = Date.now(); applyState(s); applyQueue(s.queue); }).catch(() => {});
}
setInterval(() => { if (started && document.visibilityState === 'visible' && Date.now() - lastMsg > 25000) resync(); }, 5000);
document.addEventListener('visibilitychange', () => { if (started && document.visibilityState === 'visible') resync(); });
window.addEventListener('pageshow', e => { if (started && e.persisted) resync(); });
window.addEventListener('online', () => { if (started) resync(); });
function setOffline(msg) {
  if (state.offline === msg) return; state.offline = msg;
  const old = $('.offline', view); if (old) old.remove();
  if (msg) view.insertAdjacentHTML('afterbegin', `<div class="offline">Serveur musical injoignable : ${esc(msg)}</div>`);
}

/* ---------------------------------------------------------------- vues */
function parseHash() {
  const raw = (location.hash || '#/home').slice(1);
  const qi = raw.indexOf('?');
  const path = qi >= 0 ? raw.slice(0, qi) : raw;
  const params = new URLSearchParams(qi >= 0 ? raw.slice(qi + 1) : '');
  const parts = path.split('/').filter(Boolean);
  return { name: parts[0] || 'home', arg: parts.length > 1 ? decodeURIComponent(parts.slice(1).join('/')) : '', params };
}
let renderSeq = 0;
async function render() {
  const r = parseHash(); const seq = ++renderSeq;
  if (state.lastHash != null) state.scroll[state.lastHash] = view.scrollTop;
  state.lastHash = location.hash;
  closeSheet();
  if (r.name !== 'browse') state.tabCtx = r.name;
  $$('#tabs a').forEach(a => a.classList.toggle('active', a.dataset.tab === state.tabCtx));
  $('#back').classList.toggle('hidden', r.name !== 'browse');
  state.view = r.name; state.queueScrolled = false;
  try {
    if (r.name === 'home') await viewHome(seq);
    else if (r.name === 'search') await viewSearch(r.params, seq);
    else if (r.name === 'library') await viewLibrary(seq);
    else if (r.name === 'queue') viewQueue();
    else if (r.name === 'browse') await viewBrowse(r.arg, r.params, seq);
    else { location.hash = '#/home'; return; }
  } catch (e) { if (seq === renderSeq) view.innerHTML = errorHTML(e.message); }
  if (seq === renderSeq) view.scrollTop = state.scroll[location.hash] || 0;
}
window.addEventListener('hashchange', render);
$('#back').addEventListener('click', () => { if (history.length > 1) history.back(); else location.hash = '#/home'; });

async function viewHome(seq) {
  setTitle(state.me.app || 'Jukebox');
  view.innerHTML = skeleton('rows');
  const d = await api('/api/home'); if (seq !== renderSeq) return;
  if (!d.sections.length) { view.innerHTML = emptyHTML('Rien à afficher pour le moment.'); return; }
  const player = state.status && state.status.player ? state.status.player : 'l’enceinte';
  view.innerHTML = `<div class="hero"><p class="muted">Choisissez un titre, ajoutez-le à la file : il passera sur ${esc(player)}.</p></div>` + d.sections.map(s => sectionRow(s)).join('');
}

async function viewLibrary(seq) {
  setTitle('Bibliothèque');
  view.innerHTML = skeleton('list');
  const d = await api('/api/library'); if (seq !== renderSeq) return;
  view.innerHTML = `<div class="list">${d.entries.map(folderRowHTML).join('')}</div>`;
}

let searchSeq = 0;
async function viewSearch(params, seq) {
  setTitle('Recherche');
  const q = params.get('q') || '';
  view.innerHTML = `<div class="searchbar"><span class="ico">${I.search}</span><input id="q" type="search" placeholder="Artiste, album, titre…" value="${esc(q)}" autocomplete="off" autocorrect="off" autocapitalize="off" enterkeyhint="search"><button class="icon-btn ${q ? '' : 'hidden'}" id="q-clear" aria-label="Effacer">${I.close}</button></div><div id="results"></div>`;
  const input = $('#q'); let tm;
  const update = () => {
    const v = input.value.trim();
    $('#q-clear').classList.toggle('hidden', !v);
    history.replaceState(null, '', v ? `#/search?q=${encodeURIComponent(v)}` : '#/search');
    state.lastHash = location.hash;
    runSearch(v);
  };
  input.addEventListener('input', () => { clearTimeout(tm); tm = setTimeout(update, 250); });
  input.addEventListener('keydown', e => { if (e.key === 'Enter') { clearTimeout(tm); update(); input.blur(); } });
  $('#q-clear').addEventListener('click', () => { input.value = ''; update(); input.focus(); });
  if (q) runSearch(q); else { $('#results').innerHTML = `<div class="hint">Tapez le nom d’un artiste, d’un album ou d’un morceau.<br>Tout le catalogue Qobuz est disponible.</div>`; setTimeout(() => input.focus(), 60); }
}
async function runSearch(q) {
  const my = ++searchSeq; const box = $('#results'); if (!box) return;
  if (q.length < 2) { box.innerHTML = `<div class="hint">Tapez au moins deux lettres.</div>`; return; }
  box.innerHTML = skeleton('list');
  try {
    const d = await api(`/api/search?q=${encodeURIComponent(q)}`); if (my !== searchSeq) return;
    if (!d.sections.length) { box.innerHTML = emptyHTML(`Aucun résultat pour « ${esc(q)} »`); return; }
    box.innerHTML = d.sections.map(sec => {
      let body;
      if (sec.key === 'songs') body = `<div class="tracks">${sec.items.slice(0, 6).map(it => trackRowHTML(it)).join('')}</div>`;
      else if (sec.key === 'artists') body = `<div class="list">${sec.items.slice(0, 5).map(folderRowHTML).join('')}</div>`;
      else body = `<div class="hscroll">${sec.items.map(cardHTML).join('')}</div>`;
      return `<section class="sec"><div class="sec-head"><h2>${esc(sec.title)}</h2><a class="more" href="${browseHref({ id: sec.id, title: `${sec.title} · ${q}` })}">Tout voir</a></div>${body}</section>`;
    }).join('');
  } catch (e) { if (my === searchSeq) box.innerHTML = errorHTML(e.message); }
}

let browseCtx = null;
async function viewBrowse(id, params, seq) {
  const t = params.get('t') || '', s = params.get('s') || '', i = params.get('i') || '';
  setTitle(t || 'Parcourir');
  view.innerHTML = skeleton('list');
  const d = await api(`/api/browse?id=${encodeURIComponent(id)}&start=0&count=100`); if (seq !== renderSeq) return;
  // Titre de la carte d'abord (« Album » / « Artiste · année ») : l'en-tête LMS
  // est moins lisible (« Artiste - Album », année seule).
  browseCtx = { id, seq, sections: d.sections, about: d.about, title: t || d.title, subtitle: s || d.subtitle, hires: d.hires || params.get('h') === '1', quality: d.quality || params.get('q') || '', image: i || d.image, count: d.count, items: d.items };
  setTitle(browseCtx.title || 'Parcourir');
  renderBrowse();
}
// Page en sections (artiste via l'API Qobuz) : même présentation que la recherche.
function browseSectionHTML(sec) {
  let body;
  if (sec.key === 'songs') body = `<div class="tracks">${sec.items.map((it, k) => trackRowHTML(it, null, sec.id ? { id: sec.id, index: it.pos != null ? it.pos : k } : null)).join('')}</div>`;
  else if (sec.key === 'artists') body = `<div class="list">${sec.items.slice(0, 6).map(folderRowHTML).join('')}</div>`;
  else body = `<div class="hscroll">${sec.items.map(cardHTML).join('')}</div>`;
  const more = sec.id ? `<a class="more" href="${browseHref({ id: sec.id, title: sec.title })}">Tout voir</a>` : '';
  return `<section class="sec"><div class="sec-head"><h2>${esc(sec.title)}</h2>${more}</div>${body}</section>`;
}
function renderBrowse() {
  const c = browseCtx, items = c.items;
  if (c.sections) {
    view.innerHTML = `<div class="artist-head">${c.image ? `<img class="artist-pic" src="${esc(c.image)}" alt="">` : ''}<h2 class="page-title">${esc(c.title)}</h2>${c.about ? `<button class="btn icon-round" data-act="about" aria-label="Biographie">${I.info}</button>` : ''}</div>`
      + (c.sections.length ? c.sections.map(browseSectionHTML).join('') : emptyHTML('Rien ici.'));
    return;
  }
  const tracks = items.filter(x => x.kind === 'track'), colls = items.filter(x => x.kind === 'collection');
  const folders = items.filter(x => x.kind === 'folder'), texts = items.filter(x => x.kind === 'text');
  // Liste de résultats de recherche : pas un album, chaque titre se lance seul
  const isSearchList = String(c.id).startsWith('qz:search:');
  const isAlbum = !isSearchList && tracks.length > 0 && colls.length === 0;   // album ou playlist : pistes (+ infos annexes)
  let html = '';
  if (!items.length) html = emptyHTML('Rien ici.');
  else if (isAlbum) {
    const nb = c.count > items.length ? c.count - folders.length : tracks.length;
    html += `<div class="album-head" data-item='${jsonAttr({ id: c.id, title: c.title, subtitle: c.subtitle, image: c.image, kind: 'collection' })}'>
      ${c.image ? `<img class="album-art" src="${esc(c.image)}" alt="">` : ''}
      <div class="album-meta"><h2>${esc(c.title)}</h2>${c.subtitle ? `<div class="muted">${esc(c.subtitle)}</div>` : ''}${c.hires ? `<div class="q-line">${hrHTML(c)}${c.quality ? `<span>${esc(c.quality)}</span>` : ''}</div>` : ''}<div class="muted small">${nb} titre${nb > 1 ? 's' : ''}</div>
      <div class="album-actions"><button class="btn primary" data-act="play">${I.play}<span>Lire</span></button><button class="btn" data-act="add">${I.plus}<span>Ajouter à la file</span></button>${c.about ? `<button class="btn icon-round" data-act="about" aria-label="À propos de l'album">${I.info}</button>` : ''}</div></div></div>`;
    html += `<div class="tracks">${tracks.map((it, k) => trackRowHTML(it, k + 1, { id: c.id, index: it.pos != null ? it.pos : k })).join('')}</div>`;
    if (texts.length) html += texts.map(x => `<p class="text-item">${esc(x.title)}</p>`).join('');
    if (folders.length) html += `<h3 class="sub-title">À propos</h3><div class="list">${folders.map(folderRowHTML).join('')}</div>`;
  } else {
    html += `<h2 class="page-title">${esc(c.title)}</h2>`;
    if (folders.length) html += `<div class="list">${folders.map(folderRowHTML).join('')}</div>`;
    if (colls.length) html += `<div class="grid" style="margin-top:14px">${colls.map(cardHTML).join('')}</div>`;
    if (tracks.length) html += `<div class="tracks">${tracks.map(it => trackRowHTML(it)).join('')}</div>`;
    if (texts.length) html += texts.map(x => `<p class="text-item">${esc(x.title)}</p>`).join('');
  }
  if (c.count > items.length) html += `<div class="center"><button class="btn" data-act="more">Afficher plus</button></div>`;
  view.innerHTML = html;
}
async function loadMore() {
  const c = browseCtx; if (!c || c.loading) return;
  c.loading = true; const btn = $('[data-act=more]', view); if (btn) btn.textContent = 'Chargement…';
  try {
    const d = await api(`/api/browse?id=${encodeURIComponent(c.id)}&start=${c.items.length}&count=100`);
    if (browseCtx !== c) return;
    if (!d.items.length) c.count = c.items.length; else c.items = c.items.concat(d.items);
    const y = view.scrollTop; renderBrowse(); view.scrollTop = y;
  } catch (e) { toast(e.message, 'err'); } finally { c.loading = false; }
}

function viewQueue() {
  setTitle('File d’attente');
  view.innerHTML = `<div class="q-head"><div><h2 id="q-count"></h2><div class="muted small">Touchez un titre pour y sauter</div></div><button class="btn danger small" id="q-clear">${I.trash}<span>Vider</span></button></div><div id="q-rows" class="tracks"></div>`;
  $('#q-clear').addEventListener('click', () => {
    const n = state.queue.length;
    confirmSheet({
      title: 'Vider la file d’attente ?',
      text: `${n} titre${n > 1 ? 's' : ''} ${n > 1 ? 'seront retirés' : 'sera retiré'}, et la lecture s’arrête.`,
      label: 'Vider la file', icon: I.trash,
      onConfirm: async () => { await control('clear'); toast('File vidée', 'ok'); },
    });
  });
  $('#q-rows').addEventListener('click', e => {
    const rm = e.target.closest('[data-rm]'); if (rm) { e.stopPropagation(); control('remove', +rm.dataset.rm); return; }
    const row = e.target.closest('[data-idx]'); if (row) control('jump', +row.dataset.idx);
  });
  renderQueueRows();
}
function renderQueueRows() {
  const rows = $('#q-rows'); if (!rows) return;
  const q = state.queue, cur = state.status ? state.status.index : -1, playing = state.status && state.status.mode === 'play';
  $('#q-count').textContent = q.length ? `${q.length} titre${q.length > 1 ? 's' : ''}` : 'File vide';
  $('#q-clear').classList.toggle('hidden', !q.length);
  if (!q.length) { rows.innerHTML = emptyHTML('La file est vide.<br>Cherchez un titre et ajoutez-le !', `<a class="btn primary" href="#/search">${I.search}<span>Rechercher</span></a>`); return; }
  // Après le titre en cours : d'abord les titres ajoutés (« voulus »), puis la
  // suite du lancement (« par défaut »), chacun sous son intertitre.
  // Intertitres seulement si les deux blocs coexistent après le titre en cours.
  const both = q.some(t => t.index > cur && t.auto) && q.some(t => t.index > cur && !t.auto);
  let sawWanted = false, sawAuto = false;
  const sep = t => {
    if (!both || t.index <= cur) return '';
    if (!t.auto && !sawWanted && !sawAuto) { sawWanted = true; return '<div class="q-sep">Ajoutés à la file</div>'; }
    if (t.auto && !sawAuto) { sawAuto = true; return '<div class="q-sep">Suite de la lecture</div>'; }
    return '';
  };
  rows.innerHTML = q.map(t => `${sep(t)}<div class="track ${t.index === cur ? 'current' : ''}" data-idx="${t.index}">
    ${t.index === cur ? `<div class="t-num eq ${playing ? 'on' : ''}"><i></i><i></i><i></i></div>` : `<div class="t-num">${t.index + 1}</div>`}
    ${t.image ? `<img class="t-cover" loading="lazy" src="${esc(t.image)}" alt="">` : `<div class="t-cover ph">${I.note}</div>`}
    <div class="t-meta"><div class="t-title one">${esc(t.title)}</div><div class="t-sub one muted">${esc([t.artist, t.album].filter(Boolean).join(' · '))}</div></div>
    <span class="muted small">${t.duration ? fmt(t.duration) : ''}</span>
    <button class="icon-btn" data-rm="${t.index}" aria-label="Retirer de la file">${I.close}</button></div>`).join('');
  const c = $('.current', rows);
  if (c && !state.queueScrolled) { c.scrollIntoView({ block: 'center' }); state.queueScrolled = true; }
}

/* ------------------------------------------------------------ démarrage */
function showPin() { $('#pin').classList.remove('hidden'); setTimeout(() => $('#pin-input').focus(), 60); }
$('#pin-form').addEventListener('submit', async e => {
  e.preventDefault();
  const pin = $('#pin-input').value.trim(); $('#pin-err').textContent = '';
  try {
    const r = await fetch('/api/auth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin }) });
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || 'Code incorrect'); }
    $('#pin').classList.add('hidden'); state.me.authenticated = true; start();
  } catch (err) { $('#pin-err').textContent = err.message; $('#pin-input').select(); }
});
let started = false;
function start() {
  if (started) return; started = true;
  connectEvents();
  api('/api/status').then(s => { applyState(s); applyQueue(s.queue); }).catch(() => {});
  render();
  if ('serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost')) {
    let refreshing = false;
    navigator.serviceWorker.addEventListener('controllerchange', () => { if (refreshing) return; refreshing = true; location.reload(); });
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }
}
async function boot() {
  $$('[data-ico]').forEach(el => { el.innerHTML = I[el.dataset.ico] || ''; });
  $('#back').innerHTML = I.back; $('#np-close').innerHTML = I.down; $('#np-queue').innerHTML = I.queue;
  $('#np-prev').innerHTML = I.prev; $('#np-next').innerHTML = I.next; $('#mini-next').innerHTML = I.next;
  $('#np-toggle').innerHTML = I.play; $('#mini-toggle').innerHTML = I.play;
  try { state.me = await (await fetch('/api/me')).json(); } catch { state.me = { app: 'Jukebox', pin_required: false, authenticated: true, max_volume: 100 }; }
  document.title = state.me.app || 'Jukebox';
  $('#vol').max = state.me.max_volume || 100;
  if (state.me.pin_required && !state.me.authenticated) { showPin(); return; }
  start();
}
boot();
})();
