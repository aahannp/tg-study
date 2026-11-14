// TG Study study workspace (Phase 4): /player.html?ch=<channel_id>&msg=<message_id>.
// One Alpine component, `player()`. The API contract lives in tg-study/README.md.
// Open with ?mock=1 to run against the in-file fake backend at the bottom of this file.

const QS = new URLSearchParams(location.search);
const MOCK = QS.has('mock');
const CH = Number(QS.get('ch'));
const MSG = Number(QS.get('msg'));

async function api(method, path, body) {
  const init = { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body && JSON.stringify(body) };
  const res = MOCK ? await mockFetch(method, path, body) : await fetch(path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail; // FastAPI: a string, or a list of {msg} for validation errors
    throw new Error(typeof d === 'string' ? d : d?.[0]?.msg || `Request failed (${res.status})`);
  }
  return data;
}

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode: just don't remember */ } },
};

const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
const pad = (n) => String(n).padStart(2, '0');
function fmtTime(sec) {
  if (!Number.isFinite(sec)) return '00:00';
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec % 60;
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}
const fmtSpeed = (r) => `${+r.toFixed(2)}×`;

function player() {
  return {
    mock: MOCK,
    SPEEDS: [1, 1.25, 1.5, 1.75, 2, 2.5, 3],
    SHORTCUTS: [
      ['Space / K', 'Play or pause'], ['← / →', 'Back / forward 10s'], ['J / L', 'Back / forward 30s'],
      ['[ / ]', 'Speed −0.1 / +0.1'], ['M', 'Mute'], ['F', 'Full screen'], ['N', 'Write a note at this moment'],
      ['Esc', 'Leave the note box'], ['?', 'This list'],
    ],
    item: null,
    error: '',
    toast: '',
    flashText: '',

    // video
    playing: false, muted: false, fs: false,
    cur: 0, dur: 0,
    speed: clamp(Number(store.get('tgstudy.speed')) || 1, 0.5, 4),
    completed: false,
    countdown: 0,

    // side pane
    tab: 'notes', pdf: null, pdfMenu: false,
    paneW: Number(store.get('tgstudy.paneWidth')) || 420,
    dragging: false,

    // notes
    notes: [], noteDraft: '', editingId: null, editDraft: '',

    fmtTime, fmtSpeed,
    get isVideo() { return this.item?.kind === 'video'; },
    get v() { return this.$refs.video; },
    mediaUrl: (id) => (MOCK ? mockMediaUrl(id) : `/api/media/${CH}/${id}`),
    itemHref: (id) => `/player.html?ch=${CH}&msg=${id}${MOCK ? '&mock=1' : ''}`,

    async init() {
      if (!CH || !MSG) return (this.error = 'Open a lecture from the library.');
      document.addEventListener('fullscreenchange', () => (this.fs = !!document.fullscreenElement));
      document.addEventListener('visibilitychange', () => document.visibilityState === 'hidden' && this.saveOnExit());
      addEventListener('pagehide', () => this.saveOnExit());
      try {
        const [item, notes] = await Promise.all([api('GET', `/api/items/${CH}/${MSG}`), api('GET', `/api/notes/${CH}/${MSG}`)]);
        this.item = item;
        this.completed = !!item.completed;
        this.notes = notes;
        document.title = `${item.title} · TG Study`;
      } catch (e) {
        this.error = e.message;
      }
    },

    // ── Keyboard ──
    onKey(e) {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target, tag = t.tagName;
      const typing = tag === 'TEXTAREA' || tag === 'SELECT' || t.isContentEditable
        || (tag === 'INPUT' && !['range', 'checkbox', 'radio', 'button'].includes(t.type));
      if (typing) return e.key === 'Escape' && t.blur();
      if (this.$refs.help.open) return; // the <dialog> handles Esc itself
      const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      if (k === '?') return e.preventDefault(), this.$refs.help.showModal();
      if (k === 'n') return e.preventDefault(), this.$refs.note.focus();
      if (!this.isVideo) return;
      if (k.startsWith('Arrow') && t.type === 'range') return; // the seek slider moves itself
      if (k === ' ' && /^(BUTTON|A)$/.test(tag)) return; // Space presses the focused button
      const act = {
        ' ': () => this.togglePlay(), k: () => this.togglePlay(),
        ArrowLeft: () => this.seekBy(-10), ArrowRight: () => this.seekBy(10),
        j: () => this.seekBy(-30), l: () => this.seekBy(30),
        '[': () => this.speedBy(-0.1), ']': () => this.speedBy(0.1),
        m: () => this.toggleMute(), f: () => this.toggleFs(),
      }[k];
      if (!act) return;
      e.preventDefault();
      act();
    },

    flash(text) {
      this.flashText = text;
      clearTimeout(this._flashTimer);
      this._flashTimer = setTimeout(() => (this.flashText = ''), 800);
    },

    // ── Video ──
    togglePlay() { this.v.paused ? this.v.play().catch(() => {}) : this.v.pause(); },
    seekBy(d) {
      this.v.currentTime = clamp(this.v.currentTime + d, 0, this.v.duration || 0);
      this.flash(`${d > 0 ? '+' : '−'}${Math.abs(d)}s`);
    },
    seekTo(sec) {
      if (!this.isVideo) return;
      this.v.currentTime = sec;
      this.v.play().catch(() => {});
    },
    setSpeed(r) {
      r = Math.round(clamp(r, 0.5, 4) * 100) / 100; // 1.7 + 0.1 would otherwise be 1.8000000000000003
      this.speed = r;
      this.v.defaultPlaybackRate = this.v.playbackRate = r;
      store.set('tgstudy.speed', r);
      this.flash(fmtSpeed(r));
    },
    speedBy(d) { this.setSpeed(this.speed + d); },
    toggleMute() {
      this.v.muted = !this.v.muted;
      this.flash(this.v.muted ? 'Muted' : 'Sound on');
    },
    toggleFs() { document.fullscreenElement ? document.exitFullscreen() : this.$refs.stage.requestFullscreen().catch(() => {}); },

    onMeta() {
      const v = this.v;
      this.dur = v.duration;
      v.defaultPlaybackRate = v.playbackRate = this.speed;
      const p = this.item.position_sec;
      if (p > 5 && p < v.duration - 5) {
        v.currentTime = p;
        this.flash(`Resumed at ${fmtTime(p)}`);
      }
      if (QS.has('autoplay')) v.play().catch(() => {}); // blocked without a user gesture: Space starts it
    },
    onPlay() {
      this.playing = true;
      this.cancelNextUp();
      clearInterval(this._saveTimer);
      this._saveTimer = setInterval(() => this.saveProgress(), 5000);
    },
    onPause() {
      this.playing = false;
      clearInterval(this._saveTimer);
      this.saveProgress();
    },

    // ── Progress ──
    progressBody() {
      return Number.isFinite(this.v?.duration) ? { position_sec: Math.floor(this.v.currentTime), duration_sec: Math.floor(this.v.duration) } : null;
    },
    async saveProgress() {
      const body = this.isVideo && this.progressBody();
      if (!body) return;
      try {
        this.completed = (await api('PUT', `/api/progress/${CH}/${MSG}`, body)).completed;
      } catch (e) {
        this.notify(`Couldn’t save progress: ${e.message}`);
      }
    },
    // Tab hidden or page leaving: the request has to outlive the page. sendBeacon is POST-only and the
    // contract is PUT, so a keepalive fetch carries it; sendBeacon is the fallback where fetch can't.
    saveOnExit() {
      const body = this.isVideo && this.progressBody();
      if (!body) return;
      const url = `/api/progress/${CH}/${MSG}`;
      if (MOCK) return void mockFetch('PUT', url, body);
      const json = JSON.stringify(body);
      fetch(url, { method: 'PUT', keepalive: true, headers: { 'Content-Type': 'application/json' }, body: json })
        .catch(() => navigator.sendBeacon(url, new Blob([json], { type: 'application/json' })));
    },
    async toggleComplete() {
      try {
        this.completed = (await api('PUT', `/api/progress/${CH}/${MSG}`, { completed: !this.completed })).completed;
      } catch (e) {
        this.notify(e.message);
      }
    },

    // ── Next up ──
    onEnded() {
      if (!this.item.next) return;
      this.countdown = 5;
      clearInterval(this._cdTimer);
      this._cdTimer = setInterval(() => --this.countdown <= 0 && this.goNext(), 1000);
    },
    cancelNextUp() {
      clearInterval(this._cdTimer);
      this.countdown = 0;
    },
    goNext() {
      this.cancelNextUp();
      location.href = `${this.itemHref(this.item.next.message_id)}&autoplay=1`;
    },

    // ── Side pane ──
    openPdf(p) {
      this.pdf = p;
      this.tab = 'pdf';
      this.pdfMenu = false;
    },
    closePdf() {
      this.pdf = null;
      this.tab = 'notes';
    },
    setPaneW(w) { this.paneW = Math.round(clamp(w, 280, Math.max(280, innerWidth - 360))); },
    startDrag(e) {
      e.currentTarget.setPointerCapture(e.pointerId);
      this.dragging = true;
    },
    onDrag(e) { if (this.dragging) this.setPaneW(innerWidth - e.clientX); },
    endDrag() {
      if (!this.dragging) return;
      this.dragging = false;
      store.set('tgstudy.paneWidth', this.paneW);
    },
    resizeBy(d) {
      this.setPaneW(this.paneW + d);
      store.set('tgstudy.paneWidth', this.paneW);
    },

    // ── Notes ──
    sortNotes() { this.notes.sort((a, b) => (a.timestamp_sec ?? Infinity) - (b.timestamp_sec ?? Infinity)); },
    async addNote() {
      const body = this.noteDraft.trim();
      if (!body) return;
      const timestamp_sec = this.isVideo ? Math.floor(this.v.currentTime) : null;
      try {
        this.notes.push(await api('POST', `/api/notes/${CH}/${MSG}`, { body, timestamp_sec }));
        this.sortNotes();
        this.noteDraft = '';
        this.$refs.note.blur(); // back to watching: shortcuts work again
        this.flash(timestamp_sec == null ? 'Note saved' : `Note at ${fmtTime(timestamp_sec)}`);
      } catch (e) {
        this.notify(e.message); // the draft stays in the box
      }
    },
    startEdit(n) {
      this.editingId = n.id;
      this.editDraft = n.body;
    },
    async saveEdit(n) {
      if (this.editingId !== n.id) return; // Enter already saved, or Escape cancelled
      this.editingId = null;
      const body = this.editDraft.trim();
      if (!body || body === n.body) return;
      try {
        Object.assign(n, await api('PATCH', `/api/notes/${n.id}`, { body }));
      } catch (e) {
        this.notify(e.message);
        this.editingId = n.id; // reopen with the draft so the edit isn't lost
      }
    },
    async deleteNote(n) {
      if (!confirm('Delete this note?')) return;
      try {
        await api('DELETE', `/api/notes/${n.id}`);
        this.notes = this.notes.filter((x) => x.id !== n.id);
      } catch (e) {
        this.notify(e.message);
      }
    },

    notify(msg) {
      this.toast = msg;
      clearTimeout(this._toastTimer);
      this._toastTimer = setTimeout(() => (this.toast = ''), 4500);
    },
  };
}

// ════════════════════════════ MOCK BACKEND (?mock=1) ════════════════════════════
// In-memory fake of the README study API, kept in sessionStorage so resume and notes survive
// next/prev navigation. Every call is logged to the console and to window.mockLog.
// Add &video=<url> to try another video file.

const MOCK_VIDEO = QS.get('video') || 'mock-sample.webm'; // generated 90s clock, 66 KB, VP8 so any Chromium plays it
const MOCK_ITEMS = [
  { message_id: 1, title: 'Lecture 1 · Introduction and SI units', lecture_no: 1, kind: 'video' },
  { message_id: 2, title: 'Lecture 2 · Dimensional analysis', lecture_no: 2, kind: 'video' },
  { message_id: 3, title: 'Ch 1 class notes', lecture_no: null, kind: 'pdf' },
  { message_id: 4, title: 'DPP 01 Units', lecture_no: null, kind: 'pdf' },
  { message_id: 5, title: 'Units cheat sheet', lecture_no: null, kind: 'image' },
];
window.mockLog = [];

function mockDb() {
  try {
    const db = JSON.parse(sessionStorage.getItem('tgstudy.mock'));
    if (db) return db;
  } catch { /* fall through to the seed */ }
  return {
    progress: { 1: { position_sec: 20, duration_sec: 90, completed: false } },
    notes: { 1: [{ id: 1, timestamp_sec: 8, body: 'Seven base SI units: m, kg, s, A, K, mol, cd', created_at: '2026-11-15 09:00:00' },
      { id: 2, timestamp_sec: 42, body: 'Derived units come from base units', created_at: '2026-11-15 09:01:00' }] },
    nextId: 3,
  };
}
function mockSave(db) { try { sessionStorage.setItem('tgstudy.mock', JSON.stringify(db)); } catch { /* memory only */ } }

const mockBlobs = {};
function mockMediaUrl(id) {
  const it = MOCK_ITEMS.find((x) => x.message_id === id);
  if (it?.kind === 'video') return mockBlobs.video || MOCK_VIDEO;
  if (mockBlobs[id]) return mockBlobs[id];
  const text = it?.title || `Item ${id}`;
  let blob;
  if (it?.kind === 'image') {
    blob = new Blob([`<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500"><rect width="100%" height="100%" fill="#1e293b"/>`
      + `<text x="50%" y="50%" fill="#e2e8f0" font-family="sans-serif" font-size="36" text-anchor="middle">${text}</text></svg>`], { type: 'image/svg+xml' });
  } else { // a one-page PDF with the title on it
    const stream = `BT /F1 24 Tf 72 760 Td (${text.replace(/[^\x20-\x7e]/g, '-').replace(/[()\\]/g, '\\$&')}) Tj ET`;
    const objs = ['<</Type/Catalog/Pages 2 0 R>>', '<</Type/Pages/Kids[3 0 R]/Count 1>>',
      '<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>',
      '<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>', `<</Length ${stream.length}>>stream\n${stream}\nendstream`];
    let pdf = '%PDF-1.4\n';
    const offsets = objs.map((o, i) => [pdf.length, (pdf += `${i + 1} 0 obj\n${o}\nendobj\n`)][0]);
    const xref = pdf.length;
    pdf += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n${offsets.map((o) => `${String(o).padStart(10, '0')} 00000 n \n`).join('')}`
      + `trailer\n<</Size ${objs.length + 1}/Root 1 0 R>>\nstartxref\n${xref}\n%%EOF`;
    blob = new Blob([pdf], { type: 'application/pdf' });
  }
  return (mockBlobs[id] = URL.createObjectURL(blob));
}

async function mockFetch(method, path, body) {
  window.mockLog.push({ t: Date.now(), method, path, body });
  console.info('[mock]', method, path, body ?? '');
  await new Promise((r) => setTimeout(r, 120));
  const ok = (data) => ({ ok: true, status: 200, json: async () => structuredClone(data) });
  const fail = (status, detail) => ({ ok: false, status, json: async () => ({ detail }) });
  const db = mockDb();
  const done = (data) => (mockSave(db), ok(data));
  let m;

  if ((m = path.match(/^\/api\/items\/(\d+)\/(\d+)$/)) && method === 'GET') {
    const ch = +m[1], i = MOCK_ITEMS.findIndex((x) => x.message_id === +m[2]);
    if (i < 0) return fail(404, 'Item not found');
    // python -m http.server ignores Range, so Chrome can't seek a plain URL from it. A Blob URL always can.
    if (!mockBlobs.video) mockBlobs.video = await fetch(MOCK_VIDEO).then((r) => r.blob()).then(URL.createObjectURL).catch(() => null);
    const it = MOCK_ITEMS[i], p = db.progress[it.message_id] || {};
    const brief = (x) => x && { message_id: x.message_id, title: x.title, kind: x.kind };
    return ok({ ...it, channel_id: ch, mime_type: { video: 'video/webm', pdf: 'application/pdf', image: 'image/svg+xml' }[it.kind],
      file_name: `${it.title}.${it.kind === 'video' ? 'webm' : it.kind === 'pdf' ? 'pdf' : 'svg'}`, duration: it.kind === 'video' ? 90 : null,
      subject: 'Physics Class 12', folder_id: 101, folder_name: 'Ch 1 · Units and Measurements',
      position_sec: p.position_sec || 0, completed: !!p.completed, downloaded: false,
      prev: brief(MOCK_ITEMS[i - 1]), next: brief(MOCK_ITEMS[i + 1]),
      folder_pdfs: MOCK_ITEMS.filter((x) => x.kind === 'pdf').map(brief).map(({ message_id, title }) => ({ message_id, title })) });
  }
  if ((m = path.match(/^\/api\/progress\/\d+\/(\d+)$/)) && method === 'PUT') {
    const p = (db.progress[m[1]] ||= { position_sec: 0, duration_sec: 0, completed: false });
    if ('position_sec' in body) Object.assign(p, { position_sec: body.position_sec, duration_sec: body.duration_sec });
    if ('completed' in body) p.completed = !!body.completed;
    else if (p.duration_sec && p.position_sec >= 0.9 * p.duration_sec) p.completed = true;
    return done(p);
  }
  if ((m = path.match(/^\/api\/notes\/\d+\/(\d+)$/))) {
    const list = (db.notes[m[1]] ||= []);
    if (method === 'GET') return ok([...list].sort((a, b) => (a.timestamp_sec ?? Infinity) - (b.timestamp_sec ?? Infinity)));
    if (method === 'POST') {
      if (!body.body?.trim()) return fail(422, 'Note body is required');
      const n = { id: db.nextId++, timestamp_sec: body.timestamp_sec, body: body.body, created_at: new Date().toISOString() };
      list.push(n);
      return done(n);
    }
  }
  if ((m = path.match(/^\/api\/notes\/(\d+)$/))) {
    for (const list of Object.values(db.notes)) {
      const i = list.findIndex((n) => n.id === +m[1]);
      if (i < 0) continue;
      if (method === 'PATCH') return done(Object.assign(list[i], { body: body.body }));
      if (method === 'DELETE') return list.splice(i, 1), done({ ok: true });
    }
    return fail(404, 'Note not found');
  }
  return fail(404, `Mock has no route for ${method} ${path}`);
}
