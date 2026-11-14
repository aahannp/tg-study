// TG Study frontend (Phase 1–3). One Alpine component, `app()`, drives every screen.
// The API contract lives in tg-study/README.md. Open with ?mock=1 to run against the
// in-file fake backend at the bottom of this file.

const MOCK = new URLSearchParams(location.search).has('mock');

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

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
function ago(iso) {
  if (!iso) return 'never';
  if (!/[zZ]|[+-]\d\d:?\d\d$/.test(iso)) iso = iso.replace(' ', 'T') + 'Z'; // SQLite timestamps are UTC without a zone
  let s = (new Date(iso) - Date.now()) / 1000;
  for (const [unit, n] of [['second', 60], ['minute', 60], ['hour', 24], ['day', 30], ['month', 12]]) {
    if (Math.abs(s) < n) return rtf.format(Math.round(s), unit);
    s /= n;
  }
  return rtf.format(Math.round(s), 'year');
}

// 05:12, or 1:02:05 from an hour up
function mmss(sec) {
  if (sec == null) return '';
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = String(Math.floor(sec / 60) % 60).padStart(2, '0'), s = String(sec % 60).padStart(2, '0');
  return h ? `${h}:${m}:${s}` : `${m}:${s}`;
}

const pct = (part, whole) => (whole ? Math.min(100, Math.round((100 * part) / whole)) : 0);
const playerUrl = (ch, msg) => `/player.html?ch=${ch}&msg=${msg}${MOCK ? '&mock=1' : ''}`;

const FOLDER_KINDS = {
  chapter: { label: 'Chapter', dot: 'bg-sky-400' },
  planners: { label: 'Weekly planners', dot: 'bg-violet-400' },
  worksheets: { label: 'Worksheets & sheets', dot: 'bg-emerald-400' },
  unsorted: { label: 'Unsorted', dot: 'bg-neutral-500' },
  custom: { label: 'Custom', dot: 'bg-amber-400' },
};
const ITEM_KINDS = {
  video: { label: 'Video', cls: 'text-sky-300 bg-sky-400/10' },
  pdf: { label: 'PDF', cls: 'text-rose-300 bg-rose-400/10' },
  image: { label: 'Image', cls: 'text-emerald-300 bg-emerald-400/10' },
};

function app() {
  return {
    mock: MOCK,
    screen: 'loading', // loading | setup | picker | library
    me: null,
    toast: '',
    menuOpen: false,

    // setup
    step: 'creds', // creds | code | password
    form: { api_id: '', api_hash: '', phone: '', code: '', password: '' },
    busy: false,
    error: '',

    // picker
    dialogs: [],
    query: '',
    dialogsLoading: false,
    pickerError: '',

    // library
    channels: [],
    activeId: null,
    folders: [],
    foldersLoading: false,
    syncing: {},
    editingId: null, // a channel id, or 'f<folder id>' / 'i<message id>' while renaming those
    draft: '',
    openIds: null, // folder id -> expanded
    stats: {}, // channel id -> [done, total], remembered when leaving a subject
    cont: [], // Continue Watching
    menu: null, // 'f<id>' or 'i<id>': the open ⋯ menu
    newFolder: null, // draft name while the New folder form is open
    drag: null, // { type: 'item', msg } | { type: 'folder', id }
    dropAt: null, // item drag: { folder, index, header? }; folder drag: { index }
    dl: {}, // 'ch:msg' -> { status, progress } for downloads touched this session

    ago, mmss, pct, playerUrl,
    folderMeta: (kind) => FOLDER_KINDS[kind] || FOLDER_KINDS.custom,
    itemMeta: (kind) => ITEM_KINDS[kind] || { label: kind, cls: 'text-neutral-300 bg-neutral-400/10' },

    init() { this.refreshStatus(); },

    async refreshStatus() {
      try {
        const s = await api('GET', '/api/auth/status');
        this.me = s.me;
        if (s.authorized) return this.goLibrary();
        this.screen = 'setup';
        this.step = 'creds';
      } catch (e) {
        this.screen = 'setup';
        this.error = e.message;
      }
    },

    // ── Setup / login ──
    async submit() {
      this.busy = true;
      this.error = '';
      const f = this.form;
      try {
        if (this.step === 'creds') {
          await api('POST', '/api/auth/start', { api_id: Number(f.api_id), api_hash: f.api_hash.trim(), phone: f.phone.trim() });
          this.step = 'code';
        } else if (this.step === 'code') {
          const r = await api('POST', '/api/auth/code', { code: f.code.trim() });
          f.code = '';
          if (r.password_required) this.step = 'password';
          else await this.refreshStatus();
        } else {
          await api('POST', '/api/auth/password', { password: f.password });
          f.password = '';
          await this.refreshStatus();
        }
      } catch (e) {
        this.error = e.message;
      }
      this.busy = false;
    },

    restartLogin() {
      Object.assign(this.form, { code: '', password: '' });
      this.error = '';
      this.step = 'creds';
    },

    async logout() {
      this.menuOpen = false;
      try {
        await api('POST', '/api/auth/logout');
      } catch (e) {
        return this.notify(e.message);
      }
      Object.assign(this, { me: null, channels: [], folders: [], dialogs: [], activeId: null, error: '' });
      this.restartLogin();
      this.screen = 'setup';
    },

    // ── Channel picker ──
    async goPicker() {
      this.screen = 'picker';
      this.pickerError = '';
      this.dialogsLoading = true;
      try {
        this.dialogs = await api('GET', '/api/dialogs');
      } catch (e) {
        this.pickerError = e.message;
      }
      this.dialogsLoading = false;
    },

    get filteredDialogs() {
      const q = this.query.trim().toLowerCase();
      return q ? this.dialogs.filter((d) => `${d.title} ${d.username || ''}`.toLowerCase().includes(q)) : this.dialogs;
    },

    async toggleDialog(d) {
      const selected = !d.selected;
      d.selected = selected;
      try {
        await api('PUT', `/api/channels/${d.id}`, { selected, title: d.title, username: d.username });
      } catch (e) {
        d.selected = !selected;
        this.notify(e.message);
      }
    },

    // ── Library ──
    get active() { return this.channels.find((c) => c.id === this.activeId); },

    async loadChannels() {
      try {
        this.channels = await api('GET', '/api/channels');
      } catch (e) {
        this.notify(e.message);
      }
    },

    async goLibrary() {
      await this.loadChannels();
      if (!this.channels.length) return this.goPicker();
      this.screen = 'library';
      if (!this.active) this.activeId = this.channels[0].id;
      this.loadFolders();
      this.loadContinue();
    },

    selectChannel(c) {
      if (c.id === this.activeId) return;
      if (this.folders.length) this.stats[this.activeId] = this.counts();
      this.activeId = c.id;
      this.folders = [];
      this.openIds = null;
      this.loadFolders();
    },

    async loadContinue() {
      try {
        this.cont = await api('GET', '/api/continue');
      } catch {
        this.cont = []; // no row beats an error toast on the home screen
      }
    },

    done: (f) => f.items.filter((i) => i.completed).length,
    counts() { return this.folders.reduce(([d, t], f) => [d + this.done(f), t + f.items.length], [0, 0]); },

    // % complete for the sidebar: the backend's numbers if it sends them, else what we've seen
    subjectPct(c) {
      const [d, t] = c.total != null ? [c.done, c.total]
        : c.id === this.activeId && this.folders.length ? this.counts() : this.stats[c.id] || [];
      return t ? pct(d, t) : null;
    },

    async loadFolders() {
      const id = this.activeId;
      this.foldersLoading = true;
      try {
        const folders = await api('GET', `/api/channels/${id}/folders`);
        if (id === this.activeId) { // ignore a stale response after switching subjects
          this.folders = folders;
          this.openIds ??= folders.length ? { [folders[0].id]: true } : null;
        }
      } catch (e) {
        this.notify(e.message);
      }
      this.foldersLoading = false;
    },

    async sync(c) {
      this.syncing[c.id] = true;
      try {
        const r = await api('POST', `/api/channels/${c.id}/sync`);
        const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
        this.notify([plural(r.new_messages, 'new message'), r.mode, plural(r.folders, 'folder')].filter(Boolean).join(' · '));
        await this.loadChannels();
        if (c.id === this.activeId) await this.loadFolders();
      } catch (e) {
        this.notify(e.message);
      }
      this.syncing[c.id] = false;
    },

    async updateChannel(c, patch) {
      try {
        Object.assign(c, await api('PUT', `/api/channels/${c.id}`, patch));
      } catch (e) {
        this.notify(e.message);
        await this.loadChannels(); // puts toggles back to the server's truth
      }
    },

    startRename(c) {
      this.editingId = c.id;
      this.draft = c.subject || c.title;
    },

    async saveRename(c) {
      if (this.editingId !== c.id) return; // Enter already saved, or Escape cancelled
      this.editingId = null;
      const subject = this.draft.trim();
      if (subject !== (c.subject || c.title)) await this.updateChannel(c, { subject: subject || null });
    },

    // ── Library edits (optimistic: change the UI now, put it back if the server says no) ──
    async edit(change, request) {
      const ch = this.activeId, snap = JSON.stringify(this.folders);
      change();
      try {
        await request();
        return true;
      } catch (e) {
        if (ch === this.activeId) this.folders = JSON.parse(snap);
        this.notify(e.message);
        return false;
      }
    },

    toggleFolder(f) { this.openIds = { ...this.openIds, [f.id]: !this.openIds?.[f.id] }; },

    // index = insertion point in the target folder's current list
    async moveItem(msg, toId, index) {
      const from = this.folders.find((f) => f.items.some((i) => i.message_id === msg));
      const to = this.folders.find((f) => f.id === toId);
      const at = from.items.findIndex((i) => i.message_id === msg);
      if (from === to && index > at) index--;
      if (from === to && index === at) return;
      this.openIds = { ...this.openIds, [toId]: true };
      const ok = await this.edit(() => to.items.splice(index, 0, ...from.items.splice(at, 1)),
        () => api('PATCH', `/api/items/${this.activeId}/${msg}`, { folder_id: toId, position: index }));
      if (ok && from !== to) this.notify(`Moved to ${to.name}`);
    },

    async moveFolder(id, index) {
      const at = this.folders.findIndex((f) => f.id === id);
      if (index > at) index--;
      if (index === at) return;
      await this.edit(() => this.folders.splice(index, 0, ...this.folders.splice(at, 1)),
        () => api('PATCH', `/api/folders/${id}`, { position: index }));
    },

    startEdit(key, value) {
      this.menu = null;
      this.editingId = key;
      this.draft = value;
    },

    // Folder name or item title. refocus: Enter/Escape put focus back on the row's ⋯ button.
    async saveName(key, obj, field, url, refocus) {
      if (this.editingId !== key) return;
      this.editingId = null;
      if (refocus) this.focusMenu(key);
      const v = this.draft.trim();
      if (v && v !== obj[field]) await this.edit(() => (obj[field] = v), () => api('PATCH', url, { [field]: v }));
    },

    cancelEdit(key) {
      this.editingId = null;
      this.focusMenu(key);
    },

    async createFolder() {
      const name = this.newFolder.trim();
      if (!name) return;
      try {
        const f = await api('POST', `/api/channels/${this.activeId}/folders`, { name });
        this.newFolder = null;
        await this.loadFolders();
        this.openIds = { ...this.openIds, [f.id]: true };
        this.notify(`Created ${f.name}`);
        this.$nextTick(() => document.getElementById('new-folder-btn')?.focus());
      } catch (e) {
        this.notify(e.message);
      }
    },

    async deleteFolder(f) {
      this.closeMenu();
      if (!confirm(`Delete the folder “${f.name}”? Its items move to General / Unsorted.`)) return;
      try {
        await api('DELETE', `/api/folders/${f.id}`);
        this.notify(`Deleted ${f.name}`);
      } catch (e) {
        this.notify(e.message);
      }
      await this.loadFolders();
    },

    async setWatched(it, completed) {
      this.closeMenu();
      const url = `/api/progress/${this.activeId}/${it.message_id}`;
      if (await this.edit(() => (it.completed = completed), async () => Object.assign(it, await api('PUT', url, { completed })))) {
        this.loadContinue();
      }
    },

    // ── ⋯ menus (disclosure buttons; Escape or focus leaving closes them) ──
    toggleMenu(key) {
      this.menu = this.menu === key ? null : key;
      if (this.menu) this.$nextTick(() => [...document.querySelectorAll(`#menu-${key} button`)].find((b) => b.offsetParent)?.focus());
    },

    closeMenu() {
      if (this.menu) this.focusMenu(this.menu);
      this.menu = null;
    },

    // after the next render; the extra macrotask lets a moved row finish re-rendering in its new folder
    focusMenu(key) { this.$nextTick(() => setTimeout(() => document.querySelector(`[data-menu="${key}"]`)?.focus())); },

    // ── Drag and drop (the ⋯ menu's Move entries are the keyboard/touch alternative) ──
    dragStart(e, d) {
      this.drag = d;
      this.menu = null;
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', d.type === 'item' ? `item:${d.msg}` : `folder:${d.id}`); // Firefox needs data to start
      if (d.type === 'item') e.dataTransfer.setDragImage(e.currentTarget, 16, 16); // the row, not just the link text
    },

    dragEnd() { this.drag = this.dropAt = null; },

    setDrop(d) { if (JSON.stringify(d) !== JSON.stringify(this.dropAt)) this.dropAt = d; },

    below: (e) => { const r = e.currentTarget.getBoundingClientRect(); return e.clientY > r.top + r.height / 2; },

    overItem(e, f, i) { if (this.drag?.type === 'item') this.setDrop({ folder: f.id, index: this.below(e) ? i + 1 : i }); },
    overHeader(f) { if (this.drag?.type === 'item') this.setDrop({ folder: f.id, index: f.items.length, header: true }); },
    overFolder(e, fi) { if (this.drag?.type === 'folder') this.setDrop({ index: this.below(e) ? fi + 1 : fi }); },

    showItemLine(f, index) { return this.drag?.type === 'item' && this.dropAt?.folder === f.id && !this.dropAt.header && this.dropAt.index === index; },
    showFolderLine(index) { return this.drag?.type === 'folder' && this.dropAt?.index === index; },

    drop() {
      const { drag, dropAt } = this;
      this.dragEnd();
      if (!drag || !dropAt) return;
      if (drag.type === 'item' && dropAt.folder != null) this.moveItem(drag.msg, dropAt.folder, dropAt.index);
      if (drag.type === 'folder' && dropAt.folder == null) this.moveFolder(drag.id, dropAt.index);
    },

    // ── Offline copies ──
    dlState(it) {
      return this.dl[`${this.activeId}:${it.message_id}`] || (it.downloaded ? { status: 'done', progress: 1 } : { status: 'none', progress: 0 });
    },

    dlLabel(it) {
      const s = this.dlState(it);
      return { none: `Save ${it.title} offline`, error: `Download of ${it.title} failed, retry`, done: `${it.title} is saved offline, options`,
        downloading: `Downloading ${it.title}, ${pct(s.progress, 1)}%` }[s.status];
    },

    dlClick(it) {
      const s = this.dlState(it).status;
      if (s === 'done') this.toggleMenu(`i${it.message_id}`);
      else if (s !== 'downloading') this.download(it);
    },

    async download(it) {
      const key = `${this.activeId}:${it.message_id}`, url = `/api/media/${this.activeId}/${it.message_id}/download`;
      try {
        this.dl[key] = await api('POST', url);
        while (this.dl[key].status === 'downloading') {
          await new Promise((r) => setTimeout(r, 1000));
          this.dl[key] = await api('GET', url);
        }
      } catch (e) {
        this.dl[key] = { status: 'error', progress: 0 };
        return this.notify(e.message);
      }
      it.downloaded = this.dl[key].status === 'done';
      this.notify(it.downloaded ? `Saved offline: ${it.title}` : `Download of ${it.title} failed`);
    },

    async removeOffline(it) {
      this.closeMenu();
      try {
        this.dl[`${this.activeId}:${it.message_id}`] = await api('DELETE', `/api/media/${this.activeId}/${it.message_id}/download`);
        it.downloaded = false;
        this.notify(`Removed offline copy of ${it.title}`);
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
// In-memory fake of the README API so every screen can be walked without FastAPI or
// Telegram. Login: any 5-digit OTP leads to 2FA; code 00000 and password "wrong" fail.
// Set `mockDb.failNext = true` in the console to make the next write fail (tests rollback).

const mockDb = {
  authorized: false,
  codeSent: false,
  awaitingPassword: false,
  dialogs: [
    { id: 1001, title: 'Physics Class 12 Lectures', username: 'phy12lectures', kind: 'channel' },
    { id: 1002, title: 'Organic Chemistry Crash Course', username: null, kind: 'channel' },
    { id: 1003, title: 'Maths Doubt Group', username: 'mathsdoubts', kind: 'group' },
    { id: 1004, title: 'Biology NEET 2027', username: 'bioneet', kind: 'channel' },
    { id: 1005, title: 'Study Buddies', username: null, kind: 'group' },
  ],
  channels: {}, // id -> channels row, created on first PUT
  folders: {}, // channel id -> folders (with items), created on first read so edits stick
  downloads: {}, // 'ch:msg' -> started-at ms
  nextFolderId: 1,
  failNext: false,
};

function mockFolders(channelId) {
  const v = (message_id, lecture_no, title, duration, confidence = 0.9, position_sec = 0) =>
    ({ message_id, title, lecture_no, kind: 'video', file_name: `${title}.mp4`, duration, confidence, manual: 0,
      position_sec, completed: position_sec >= 0.9 * duration, downloaded: false, updated_at: new Date(Date.now() - message_id * 6e4).toISOString() });
  const doc = (message_id, title, kind = 'pdf', confidence = 0.8) =>
    ({ message_id, title, lecture_no: null, kind, file_name: `${title}.${kind === 'pdf' ? 'pdf' : 'jpg'}`, duration: null, confidence, manual: 0,
      position_sec: 0, completed: false, downloaded: false });
  const base = channelId * 100;
  return [
    { id: base + 1, name: 'Ch 1 · Units and Measurements', kind: 'chapter', position: 0, items: [
      v(base + 11, 1, 'Introduction and SI units', 3125, 0.9, 3125), v(base + 12, 2, 'Dimensional analysis', 2710, 0.9, 1290),
      doc(base + 13, 'Ch 1 class notes'), v(base + 14, 3, 'Errors in measurement', 3590, 0.42) ] },
    { id: base + 2, name: 'Ch 2 · Motion in a Straight Line', kind: 'chapter', position: 1, items: [
      v(base + 21, 4, 'Distance, displacement, speed', 2944), v(base + 22, 5, 'Equations of motion', 4012, 0.9, 600),
      doc(base + 23, 'Graphs cheat sheet', 'image', 0.35) ] },
    { id: base + 3, name: 'Weekly Planners', kind: 'planners', position: 2, items: [
      doc(base + 31, 'Week 1 planner', 'image'), doc(base + 32, 'Week 2 planner', 'image') ] },
    { id: base + 4, name: 'Worksheets & Sheets', kind: 'worksheets', position: 3, items: [
      doc(base + 41, 'DPP 01 Units'), doc(base + 42, 'DPP 02 Kinematics'), doc(base + 43, 'Answer key DPP 01-02', 'pdf', 0.3) ] },
    { id: base + 5, name: 'General / Unsorted', kind: 'unsorted', position: 4, items: [
      v(base + 51, null, 'Live doubt session', 5400, 0.2, 2400), doc(base + 52, 'Announcement poster', 'image', 0.45) ] },
  ];
}

async function mockFetch(method, path, body = {}) {
  await new Promise((r) => setTimeout(r, 350)); // feel the spinners
  const ok = (data) => ({ ok: true, status: 200, json: async () => structuredClone(data) }); // fresh objects, like real fetch
  const fail = (status, detail) => ({ ok: false, status, json: async () => ({ detail }) });
  const db = mockDb;
  const [id, id2] = (path.match(/\d+/g) || []).map(Number);
  const ch = db.channels[id];
  const foldersOf = (cid) => (db.folders[cid] ??= mockFolders(cid));
  const withCounts = (f) => ({ ...f, done: f.items.filter((i) => i.completed).length, total: f.items.length });
  const renumber = (list) => list.forEach((x, i) => (x.position = i));
  const findFolder = (fid) => {
    for (const fs of Object.values(db.folders)) for (const f of fs) if (f.id === fid) return [fs, f];
    return [];
  };
  const findItem = () => {
    for (const f of db.folders[id] || []) for (const it of f.items) if (it.message_id === id2) return [f, it];
    return [];
  };

  const route = `${method} ${path.replace(/\/\d+/g, '/:id')}`;
  switch (route) {
    case 'GET /api/auth/status':
      return ok({ configured: db.codeSent, authorized: db.authorized, me: db.authorized ? { name: 'Mock Student', phone: '+91 90000 00000' } : null });
    case 'POST /api/auth/start':
      if (!body.api_id || !body.api_hash || !body.phone) return fail(422, 'api_id, api_hash and phone are required');
      db.codeSent = true;
      return ok({ code_sent: true });
    case 'POST /api/auth/code':
      if (!db.codeSent) return fail(400, 'Request a code first');
      if (!/^\d{5}$/.test(body.code) || body.code === '00000') return fail(400, 'That code is invalid or has expired');
      db.awaitingPassword = true;
      return ok({ password_required: true });
    case 'POST /api/auth/password':
      if (!db.awaitingPassword) return fail(400, 'No password step pending');
      if (body.password === 'wrong') return fail(400, 'The password is incorrect');
      db.authorized = true;
      return ok({ authorized: true });
    case 'POST /api/auth/logout':
      Object.assign(db, { authorized: false, codeSent: false, awaitingPassword: false });
      return ok({ ok: true });
  }
  if (!db.authorized) return fail(401, 'Not logged in');
  if (db.failNext && method !== 'GET') {
    db.failNext = false;
    return fail(500, 'Mock failure (mockDb.failNext)');
  }
  switch (route) {
    case 'GET /api/dialogs':
      return ok(db.dialogs.map((d) => ({ ...d, selected: !!db.channels[d.id]?.selected })));
    case 'GET /api/channels':
      return ok(Object.values(db.channels).filter((c) => c.selected));
    case 'PUT /api/channels/:id': {
      const row = ch || (db.channels[id] = { id, title: '', username: null, subject: null, selected: 0, ignore_stickers: 1,
        last_synced_at: null, parse_mode: null, message_count: 0, item_count: 0 });
      for (const k of ['selected', 'ignore_stickers']) if (k in body) row[k] = body[k] ? 1 : 0;
      for (const k of ['title', 'username', 'subject']) if (k in body) row[k] = body[k];
      return ok(row);
    }
    case 'POST /api/channels/:id/sync': {
      if (!ch) return fail(404, 'Unknown channel');
      const n = ch.message_count ? Math.floor(Math.random() * 6) : 48;
      Object.assign(ch, { message_count: ch.message_count + n, item_count: 16, parse_mode: id % 2 ? 'index' : 'sequential',
        last_synced_at: new Date().toISOString() });
      return ok({ new_messages: n, mode: ch.parse_mode, folders: 5, items: 16 });
    }
    case 'GET /api/channels/:id/folders':
      if (!ch) return fail(404, 'Unknown channel');
      return ok(ch.message_count ? foldersOf(id).map(withCounts) : []);
    case 'POST /api/channels/:id/folders': {
      if (!ch) return fail(404, 'Unknown channel');
      const fs = foldersOf(id);
      if (fs.some((f) => f.name === body.name)) return fail(409, 'A folder with that name already exists');
      const f = { id: db.nextFolderId++, name: body.name, kind: 'custom', position: fs.length, items: [] };
      fs.push(f);
      return ok(withCounts(f));
    }
    case 'PATCH /api/folders/:id': {
      const [fs, f] = findFolder(id);
      if (!f) return fail(404, 'Unknown folder');
      if ('name' in body) f.name = body.name;
      if ('position' in body) fs.splice(body.position, 0, ...fs.splice(fs.indexOf(f), 1));
      renumber(fs);
      return ok(withCounts(f));
    }
    case 'DELETE /api/folders/:id': {
      const [fs, f] = findFolder(id);
      if (!f) return fail(404, 'Unknown folder');
      fs.splice(fs.indexOf(f), 1);
      let unsorted = fs.find((x) => x.kind === 'unsorted');
      if (!unsorted) fs.push((unsorted = { id: db.nextFolderId++, name: 'General / Unsorted', kind: 'unsorted', items: [] }));
      unsorted.items.push(...f.items.map((it) => ({ ...it, manual: 1 })));
      renumber(fs);
      return ok({ ok: true });
    }
    case 'PATCH /api/items/:id/:id': {
      const [f, it] = findItem();
      if (!it) return fail(404, 'Unknown item');
      const to = 'folder_id' in body ? db.folders[id].find((x) => x.id === body.folder_id) : f;
      if (!to) return fail(404, 'Unknown folder');
      if ('folder_id' in body || 'position' in body) {
        f.items.splice(f.items.indexOf(it), 1);
        to.items.splice(body.position ?? to.items.length, 0, it);
      }
      if ('title' in body) it.title = body.title;
      it.manual = 1;
      return ok(it);
    }
    case 'PUT /api/progress/:id/:id': {
      const [, it] = findItem();
      if (!it) return fail(404, 'Unknown item');
      if ('position_sec' in body) it.position_sec = body.position_sec;
      if ('duration_sec' in body) it.duration = body.duration_sec;
      if ('completed' in body) it.completed = !!body.completed;
      if (it.duration && it.position_sec >= 0.9 * it.duration) it.completed = true;
      it.updated_at = new Date().toISOString();
      return ok({ position_sec: it.position_sec, duration_sec: it.duration, completed: it.completed });
    }
    case 'GET /api/continue': {
      const rows = [];
      for (const [cid, fs] of Object.entries(db.folders)) {
        const c = db.channels[cid];
        if (!c?.selected) continue;
        for (const it of fs.flatMap((f) => f.items)) {
          if (it.position_sec > 0 && !it.completed) rows.push({ channel_id: Number(cid), message_id: it.message_id, title: it.title,
            subject: c.subject || c.title, kind: it.kind, position_sec: it.position_sec, duration_sec: it.duration, updated_at: it.updated_at });
        }
      }
      return ok(rows.sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 12));
    }
  }
  if (route.endsWith(' /api/media/:id/:id/download')) {
    const [, it] = findItem();
    if (!it) return fail(404, 'Unknown item');
    const key = `${id}:${id2}`;
    if (method === 'DELETE') {
      delete db.downloads[key];
      it.downloaded = false;
      return ok({ status: 'none' });
    }
    if (method === 'POST' && !it.downloaded) db.downloads[key] = Date.now();
    if (it.downloaded) return ok({ status: 'done', progress: 1 });
    if (!(key in db.downloads)) return ok({ status: 'none', progress: 0 });
    const progress = Math.min(1, (Date.now() - db.downloads[key]) / 4000);
    if (progress === 1) it.downloaded = true;
    return ok({ status: progress === 1 ? 'done' : 'downloading', progress });
  }
  return fail(404, `Mock has no route for ${method} ${path}`);
}
