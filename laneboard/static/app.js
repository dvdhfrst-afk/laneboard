"use strict";
// Laneboard front end: shows one board at a time and saves every change through the local JSON API.

const APP_VERSION = "1.0.0";
const COLORS = ["blue", "teal", "violet", "amber", "rose", "green", "slate"];
const NOTES_DELAY = 900;
const POLL_EVERY = 5000;
const COLUMN_PAGE = 300;   // cards drawn per column before a "show more" button; keeps huge boards quick
const ARCHIVE_PAGE = 100;
const SEARCH_DELAY = 150;  // on boards over LARGE_BOARD cards; smaller boards search on every key
const LARGE_BOARD = 500;
const MAX_IMPORT_BYTES = 64000000;
const LAST_BOARD = "laneboard:last-board";
const COLLAPSED = "laneboard:collapsed:";
const START_COMMAND = "python -m laneboard";

const state = {
  boards: [],          // [{id, name, cards, archived, rev}]
  board: null,         // the open board, as the server last sent it
  query: "",
  groups: new Set(),   // group filter; empty shows every card, "" stands for cards with no group
  blockedOnly: false,
  drag: null,          // key of the card being dragged
  shown: new Map(),    // column id -> how many cards to draw
  focusKey: null,      // the card that keyboard focus belongs on
  wantFocus: false,    // put focus on focusKey at the next draw
  offline: false,
  problem: null,       // why the server can't use the boards file, if it can't
  versionNote: null,
};

// Lookup tables for the open board, rebuilt whenever it changes. Search text is filled in as needed.
let index = { byKey: new Map(), byColumn: new Map(), groups: new Map(), checkIds: new Set(), archived: [], text: new Map() };

// The last revision seen per board. A queued change must carry its own board's revision even if
// the person has moved on to another board by the time it runs.
const revs = new Map();

// Notes that couldn't be saved after their card was closed. Nothing typed gets thrown away.
const drafts = new Map();

// What the side sheet is showing. `dirty` guards unsaved form edits; `onClose` flushes card notes.
const sheet = { mode: null, key: null, dirty: false, onClose: null, unsaved: null, retry: null, sync: null };

// ---- small helpers ---------------------------------------------------------------------------

const $ = (sel, root = document) => root.querySelector(sel);

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k.startsWith("data-") || k.startsWith("aria-") || k === "for" || !(k in el)) el.setAttribute(k, v === true ? "" : v);
    else el[k] = v;
  }
  for (const c of children.flat(Infinity)) {
    if (c !== undefined && c !== null && c !== false) el.append(c instanceof Node ? c : String(c));
  }
  return el;
}

function countBy(items, fn) {
  const m = new Map();
  for (const x of items) m.set(fn(x), (m.get(fn(x)) || 0) + 1);
  return m;
}

const byRank = (a, b) => a.rank - b.rank || a.key.localeCompare(b.key, undefined, { numeric: true });
const cardBy = (key) => index.byKey.get(key);
const cardsIn = (column) => index.byColumn.get(column) || [];
const activeCards = () => state.board.cards.filter((c) => !c.archived);
const groupOf = (card) => index.groups.get(card.group) || null;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const isWebLink = (url) => /^https?:\/\//i.test(url);

function setBoard(board) {
  state.board = board;
  if (board) revs.set(board.id, board.rev);
  reindex();
}

function reindex() {
  const b = state.board;
  index = { byKey: new Map(), byColumn: new Map(), groups: new Map(), checkIds: new Set(), archived: [], text: new Map() };
  if (!b) return;
  for (const col of b.columns) index.byColumn.set(col.id, []);
  for (const g of b.groups) index.groups.set(g.id, g);
  for (const cl of b.checklists) for (const item of cl.items) if (!item.heading) index.checkIds.add(item.id);
  for (const card of b.cards) {
    index.byKey.set(card.key, card);
    if (card.archived) index.archived.push(card);
    else if (index.byColumn.has(card.column)) index.byColumn.get(card.column).push(card);
  }
  for (const list of index.byColumn.values()) list.sort(byRank);
  index.archived.sort((a, x) => String(x.archived).localeCompare(String(a.archived)));
}

function when(iso) {
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function stored(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value === null ? fallback : value;
  } catch { return fallback; }
}

function store(key, value) {
  try { localStorage.setItem(key, value); } catch { /* storage may be blocked; these are only conveniences */ }
}

const remember = (id) => store(LAST_BOARD, id);
const recall = () => stored(LAST_BOARD, null);

function announce(message) {
  const live = $("#live");
  live.textContent = "";
  requestAnimationFrame(() => { live.textContent = message; });
}

// The clipboard API can be refused (embedded browsers, missing permission); the older
// select-and-copy route covers those.
function copyBySelection(text) {
  const dlg = $("#sheet");
  const box = h("textarea", { value: text, readOnly: true, class: "offscreen", "aria-hidden": "true" });
  (dlg.open ? dlg : document.body).append(box);  // outside an open modal sheet, nothing can be selected
  box.select();
  try { return document.execCommand("copy"); } catch { return false; } finally { box.remove(); }
}

async function copyText(text) {
  let ok = false;
  try {
    await navigator.clipboard.writeText(text);
    ok = true;
  } catch {
    ok = copyBySelection(text);
  }
  if (ok) toast("Copied");
  else toast("Couldn't copy. Select the text and press Ctrl+C.", { error: true });
}

// Text from cards: `backticks` become code with a Copy button, and web addresses become links.
// Never parsed as HTML.
function rich(text) {
  const parts = String(text).split("`");
  if (parts.length % 2 === 0) parts.push(parts.splice(-2).join("`"));
  const out = [];
  parts.forEach((part, i) => {
    if (i % 2) {
      return out.push(h("code", { text: part }), h("button", {
        type: "button", class: "copy", text: "Copy", "aria-label": `Copy ${part}`, onclick: () => copyText(part) }));
    }
    part.split(/(https?:\/\/[^\s<>"'`)\]]+)/).forEach((bit, j) => {
      if (j % 2) {
        const end = bit.match(/[.,;:!?]+$/)?.[0] || "";  // the period ending a sentence isn't part of the address
        const url = end ? bit.slice(0, -end.length) : bit;
        out.push(h("a", { href: url, target: "_blank", rel: "noopener noreferrer", text: url }));
        if (end) out.push(end);
      } else if (bit) out.push(bit);
    });
  });
  return out;
}

// "Label: value" lines, as used for card fields and group details.
function parsePairs(text, what) {
  return text.split("\n").map((l) => l.trim()).filter(Boolean).map((line) => {
    const i = line.indexOf(":");
    if (i < 1) throw new Error(`${what}: write each line as Label: value. "${line.slice(0, 40)}" has no label.`);
    return { label: line.slice(0, i).trim(), value: line.slice(i + 1).trim() };
  });
}

const pairsText = (rows) => rows.map((r) => `${r.label}: ${r.value.replace(/\s*\n\s*/g, " ")}`).join("\n");

function upper(input) {
  input.autocapitalize = "characters";
  input.addEventListener("input", () => {
    const at = input.selectionStart;
    input.value = input.value.toUpperCase();
    input.setSelectionRange(at, at);
  });
  return input;
}

function autoPrefix(name) {
  const words = name.toUpperCase().replace(/[^A-Z0-9]+/g, " ").trim().split(" ").filter(Boolean);
  const raw = words.length > 1 ? words.map((w) => w[0]).join("") : (words[0] || "").slice(0, 3);
  return raw.replace(/^[0-9]+/, "").slice(0, 8) || (name.trim() ? "G" : "");
}

function field(label, control, hint) {
  return h("label", { class: "field" }, h("span", { class: "lbl", text: label }), control,
    hint && h("span", { class: "hint", text: hint }));
}

function groupTag(g) {
  return h("span", { class: "grp" }, h("span", { class: `dot c-${g.color}` }), g.name);
}

function groupLink(g) {
  return g.link && isWebLink(g.link) ? h("a", { href: g.link, target: "_blank", rel: "noopener noreferrer" }, groupTag(g)) : groupTag(g);
}

let toastTimer = null;
function toast(message, { error = false, action = null } = {}) {
  const el = $("#toast");
  const dlg = $("#sheet");
  (dlg.open ? dlg : document.body).append(el);  // a modal sheet covers everything outside it
  el.className = error ? "toast error" : "toast";
  el.replaceChildren(...[h("span", { text: message }),
    action && h("button", { type: "button", text: action.label, onclick: () => { el.hidden = true; action.run(); } }),
    h("button", { type: "button", "aria-label": "Dismiss", text: "×", onclick: () => { el.hidden = true; } })].filter(Boolean));
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, error ? 9000 : action ? 7000 : 4000);
}

function renderBanner() {
  const el = $("#banner");
  const message = state.offline
    ? `Can't reach Laneboard, so changes can't be saved right now. If it has stopped, start it again with: ${START_COMMAND}`
    : state.problem || state.versionNote;
  el.hidden = !message;
  el.textContent = message || "";
  el.className = state.offline || state.problem ? "banner bad" : "banner";
}

// ---- server ----------------------------------------------------------------------------------

async function api(method, path, body, rev) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = typeof body === "string" ? body : JSON.stringify(body);
  }
  if (rev !== undefined && rev !== null) opts.headers["If-Match"] = String(rev);
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    setOffline(true);
    throw Object.assign(new Error(`Can't reach the board server. Start it again with: ${START_COMMAND}`), { status: 0 });
  }
  setOffline(false);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || `The server answered ${res.status}.`), { status: res.status });
  return data;
}

function setOffline(offline) {
  if (state.offline === offline) return;
  state.offline = offline;
  renderBanner();
  if (!offline) {
    checkVersion();
    sheet.retry?.();
    saveDrafts();
  }
}

async function checkVersion() {
  try {
    const health = await api("GET", "/api/health");
    state.health = health;
    state.versionNote = health.version === APP_VERSION ? null
      : `This page is version ${APP_VERSION} and the running Laneboard is version ${health.version}. Stop Laneboard, start it again, then reload this page.`;
    renderBanner();
  } catch { /* the banner already says the server is gone */ }
}

// Changes run one at a time, so each request carries the revision the previous one produced.
let queue = Promise.resolve();
let pending = 0;
function enqueue(task) {
  pending++;
  const run = queue.then(task);
  queue = run.then(() => {}, () => {}).then(() => { pending--; });
  return run;
}

// Send a change to the open board. `body` can be a function of the board (null if another board
// is open by then), so a retry is worked out from what the server has now.
// On a stale revision the board is reloaded; `retry` (true, or a function of the fresh board
// saying whether it's safe) then re-sends the change, otherwise the caller gets the error.
function send(method, path, body, { retry = false } = {}) {
  const id = state.board.id;
  return enqueue(async () => {
    const open = () => (state.board?.id === id ? state.board : null);
    const attempt = () => api(method, `/api/boards/${id}${path}`, typeof body === "function" ? body(open()) : body,
      open()?.rev ?? revs.get(id));
    let data;
    try {
      data = await attempt();
    } catch (e) {
      if (e.status !== 412) throw e;
      const fresh = (await api("GET", `/api/boards/${id}`)).board;
      revs.set(id, fresh.rev);
      if (open()) {
        setBoard(fresh);
        syncListEntry();
        render();
      }
      if (retry === true || (retry && retry(fresh))) {
        data = await attempt();
      } else {
        sheet.sync?.();
        throw Object.assign(new Error("This changed while you were editing (in another tab or in the file). "
          + "The board has been reloaded. Save again to keep your version, or cancel to keep the other one."), { status: 412 });
      }
    }
    if (data.board) {
      revs.set(id, data.board.rev);
      if (open()) {
        setBoard(data.board);
        syncListEntry();
        render();
        if (pending === 1) sheet.sync?.();  // later changes are still on their way; the last one does this
      }
    }
    return data;
  });
}

async function loadBoards() {
  try {
    state.boards = (await api("GET", "/api/boards")).boards;
    state.problem = null;
    renderBanner();
  } catch (e) {
    if (e.status === 503) {
      state.problem = e.message;
      renderBanner();
    }
    throw e;
  }
}

async function loadBoard(id) {
  setBoard((await api("GET", `/api/boards/${encodeURIComponent(id)}`)).board);
  syncListEntry();
  remember(id);
}

function syncListEntry() {
  const entry = state.boards.find((b) => b.id === state.board.id);
  if (entry) Object.assign(entry, { name: state.board.name, cards: activeCards().length, archived: index.archived.length, rev: state.board.rev });
}

async function saveDrafts() {
  for (const [ref, value] of [...drafts]) {
    const [id, key] = ref.split("/");
    if (state.board?.id !== id || !cardBy(key)) continue;
    drafts.delete(ref);
    const base = cardBy(key).notes;
    try {
      await send("PATCH", `/cards/${key}`, { notes: value }, { retry: (fresh) => fresh.cards.find((c) => c.key === key)?.notes === base });
      toast(`Saved your notes on ${key}.`);
    } catch (e) {
      keepDraft(id, key, value, e.message);
    }
  }
}

function keepDraft(id, key, value, why) {
  const onBoard = state.board?.id === id && cardBy(key);
  if (onBoard) drafts.set(`${id}/${key}`, value);
  toast(`Your notes on ${key} weren't saved: ${why}`, {
    error: true,
    action: onBoard ? { label: "Open them", run: () => openCard(key) } : { label: "Copy them", run: () => copyText(value) },
  });
}

// ---- routing: #<board id> or #<board id>/<card key> -----------------------------------------

function parseHash() {
  let raw = location.hash.slice(1);
  try { raw = decodeURIComponent(raw); } catch { raw = ""; }
  const [board = "", key = ""] = raw.split("/");
  return { board, key };
}

function setHash(id, key) {
  history.replaceState(null, "", `#${id}${key ? "/" + key : ""}`);
}

async function route() {
  if (sheet.dirty) {  // arrived here by the back button or a typed address; don't drop what's being typed
    const want = location.hash;
    if (state.board) setHash(state.board.id, sheet.key);
    askDiscard(() => {
      sheet.dirty = false;
      closeSheet(true);
      location.hash = want;
    });
    return;
  }
  const want = parseHash();
  const ids = state.boards.map((b) => b.id);
  const id = [want.board, recall()].find((x) => ids.includes(x)) || ids[0];
  if (!id) {
    setBoard(null);
    closeSheet(true);
    render();
    return;
  }
  try {
    if (!state.board || state.board.id !== id) {
      closeSheet(true);
      state.groups.clear();
      state.blockedOnly = false;
      state.shown.clear();
      state.focusKey = null;
      await loadBoard(id);
    }
  } catch (e) {
    toast(e.message, { error: true });
    return;
  }
  const key = want.board === id ? want.key : "";
  setHash(id, key);
  render();
  if (key) openCard(key);
  else if (sheet.mode === "view") closeSheet(true);
}

async function goToBoard(id) {
  sheet.dirty = false;
  closeSheet(true);
  await loadBoards();
  state.groups.clear();
  state.blockedOnly = false;
  state.shown.clear();
  state.focusKey = null;
  await loadBoard(id);
  setHash(id);
  render();
}

// ---- board ------------------------------------------------------------------------------------

function render() {
  const b = state.board;
  reindex();
  document.title = b ? `${b.name} - Laneboard` : "Laneboard";
  $("#board-name").textContent = b ? b.name : "Laneboard";
  renderPicker();
  renderMenu();
  const desc = $("#board-desc");
  desc.textContent = b?.description || "";
  desc.hidden = !b?.description;
  $("#new-card").disabled = !b;
  $("#board-settings").disabled = !b;
  $("#tools").hidden = !b;
  if (!b) {
    $("#groups").hidden = true;
    $("#welcome").hidden = true;
    renderEmpty();
    return;
  }
  renderWelcome();
  renderFilters();
  renderColumns();
  renderGroups();
}

function renderPicker() {
  const pick = $("#board-pick");
  const others = state.boards.filter((b) => b.id !== state.board?.id);
  const signature = JSON.stringify(others.map((b) => [b.id, b.name, b.cards]));
  if (pick.dataset.signature === signature) return;  // redrawing would close the list if it's open
  pick.dataset.signature = signature;
  pick.hidden = !others.length;
  pick.replaceChildren(h("option", { value: "", text: "Switch board" }),
    ...others.map((b) => h("option", { value: b.id, text: `${b.name} (${plural(b.cards, "card")})` })));
  pick.value = "";
}

function renderMenu() {
  const b = state.board;
  const item = (label, run, disabled) => h("button", { type: "button", role: "menuitem", text: label, disabled, onclick: () => { closeMenu(); run(); } });
  const link = (label, href) => h("a", { role: "menuitem", href, download: "", text: label, onclick: closeMenu });
  const boardItems = b ? [
    item(`Archived cards (${index.archived.length})`, openArchive),
    link("Export this board", `/api/boards/${b.id}/export`),
    item("Duplicate this board", duplicateBoard),
  ] : [];
  $("#menu-items").replaceChildren(...boardItems,
    link("Export all boards", "/api/export"),
    item("Import a file", chooseImportFile),
    item("Keyboard shortcuts and help", openHelp));
}

function closeMenu() {
  $("#menu").removeAttribute("open");
}

function renderEmpty() {
  const main = $("#board");
  main.style.gridTemplateColumns = "";
  main.replaceChildren(h("div", { class: "start" },
    h("p", { text: "There are no boards yet." }),
    h("div", { class: "start-actions" },
      h("button", { type: "button", class: "primary", text: "Create a board", onclick: openNewBoard }),
      h("button", { type: "button", text: "Import a file", onclick: chooseImportFile })),
    startingPoints()));
}

// First run: an empty board says what to do next and offers the examples.
function renderWelcome() {
  const box = $("#welcome");
  const empty = !state.board.cards.length;
  box.hidden = !empty;
  if (!empty || box.dataset.board === state.board.id) return;
  box.dataset.board = state.board.id;
  box.replaceChildren(
    h("p", { class: "welcome-lead", text: "This board is empty." }),
    h("p", { text: "Press n or use New card to add your first card. Drag cards between columns, click one to open it, and press ? for keyboard shortcuts." }),
    startingPoints());
}

function startingPoints() {
  const box = h("div", { class: "start-actions" });
  loadSamples().then((samples) => {
    if (!samples.length) return;
    box.append(h("span", { class: "hint", text: "Or look around an example:" }),
      ...samples.map((s) => h("button", { type: "button", text: s.title, title: s.description, onclick: () => addSample(s) })));
  });
  return box;
}

let samplesLoaded = null;
function loadSamples() {
  samplesLoaded = samplesLoaded || api("GET", "/api/samples").then((d) => d.samples).catch(() => { samplesLoaded = null; return []; });
  return samplesLoaded;
}

async function addSample(sample) {
  try {
    const { board } = await api("POST", `/api/samples/${sample.name}`);
    await goToBoard(board.id);
    toast(`Added the example ${board.name}. Delete it from Board settings when you're done with it.`);
  } catch (e) {
    toast(e.message, { error: true });
  }
}

function renderFilters() {
  const b = state.board;
  const active = activeCards();
  const counts = countBy(active, (c) => c.group || "");
  for (const id of [...state.groups]) {
    if (id && !b.groups.some((g) => g.id === id)) state.groups.delete(id);
  }
  const chip = (id, name, color, n) => h("button", {
    type: "button", "aria-pressed": String(state.groups.has(id)),
    onclick: () => { state.groups.has(id) ? state.groups.delete(id) : state.groups.add(id); render(); },
  }, h("span", { class: `dot c-${color}` }), name, h("span", { class: "count", text: String(n) }));
  const chips = b.groups.map((g) => chip(g.id, g.name, g.color, counts.get(g.id) || 0));
  if (b.groups.length && counts.get("")) chips.push(chip("", `No ${b.groupLabel.toLowerCase()}`, "slate", counts.get("")));
  const blocked = active.filter((c) => c.blocked).length;
  if (blocked || state.blockedOnly) {
    chips.push(h("button", {
      type: "button", "aria-pressed": String(state.blockedOnly),
      onclick: () => { state.blockedOnly = !state.blockedOnly; render(); },
    }, "Blocked", h("span", { class: "count", text: String(blocked) })));
  }
  if (state.groups.size || state.blockedOnly) {
    chips.push(h("button", { type: "button", class: "link", text: "Show all", onclick: () => { state.groups.clear(); state.blockedOnly = false; render(); } }));
  }
  const box = $("#filters");
  box.hidden = !chips.length;
  box.replaceChildren(...chips);
}

function searchText(card) {
  let text = index.text.get(card);
  if (text === undefined) {
    text = [card.key, card.title, card.summary, card.blocked, card.notes, groupOf(card)?.name,
      ...card.fields.map((f) => `${f.label} ${f.value}`), ...card.sections.map((s) => `${s.label} ${s.body}`)]
      .join("\n").toLowerCase();
    index.text.set(card, text);
  }
  return text;
}

function matchesQuery(card, words) {
  return words.every((w) => searchText(card).includes(w));
}

function queryWords() {
  return state.query.toLowerCase().split(/\s+/).filter(Boolean);
}

function matches(card, words = queryWords()) {
  if (state.groups.size && !state.groups.has(card.group || "")) return false;
  if (state.blockedOnly && !card.blocked) return false;
  return !words.length || matchesQuery(card, words);
}

function collapsedColumns() {
  try { return new Set(JSON.parse(stored(COLLAPSED + state.board.id, "[]"))); } catch { return new Set(); }
}

function toggleCollapsed(columnId) {
  const set = collapsedColumns();
  set.has(columnId) ? set.delete(columnId) : set.add(columnId);
  store(COLLAPSED + state.board.id, JSON.stringify([...set]));
  renderColumns();
  $(`[data-col="${CSS.escape(columnId)}"] .collapse`)?.focus();
}

// Redrawing replaces every card, so remember where the reader is: the card holding focus and how
// far each column is scrolled.
function renderColumns() {
  const b = state.board;
  const main = $("#board");
  const focusedCard = document.activeElement?.closest?.("#board .card")?.dataset.key;
  if (focusedCard) {
    state.focusKey = focusedCard;
    state.wantFocus = true;
  }
  const scroll = new Map([...main.querySelectorAll(".col")].map((c) => [c.dataset.col, c.scrollTop]));
  const words = queryWords();
  const filtering = state.groups.size > 0 || state.blockedOnly || words.length > 0;
  const collapsed = collapsedColumns();
  main.style.gridTemplateColumns = b.columns.map((c) => (collapsed.has(c.id) ? "minmax(130px, 150px)" : "minmax(230px, 1fr)")).join(" ");
  let matched = 0;
  let total = 0;
  const columns = b.columns.map((col) => {
    const all = cardsIn(col.id);
    const shown = filtering ? all.filter((c) => matches(c, words)) : all;
    total += all.length;
    matched += shown.length;
    const over = col.limit && all.length > col.limit;
    const closed = collapsed.has(col.id);
    const limit = state.shown.get(col.id) || COLUMN_PAGE;
    const drawn = shown.length > limit ? shown.slice(0, limit) : shown;
    return h("section", { class: closed ? "col collapsed" : "col", "data-col": col.id, "aria-label": col.name },
      h("div", { class: "col-head" },
        h("button", { type: "button", class: "collapse", "aria-expanded": String(!closed), title: `${closed ? "Show" : "Hide"} the cards in ${col.name}`,
          "aria-label": `${closed ? "Show" : "Hide"} the cards in ${col.name}`, text: closed ? "▸" : "▾", onclick: () => toggleCollapsed(col.id) }),
        h("h2", { text: col.name }),
        h("div", { class: "right" },
          h("span", { class: over ? "count over" : "count", title: col.limit ? `Limit ${col.limit}` : null,
            text: filtering ? `${shown.length} of ${all.length}` : col.limit ? `${all.length} / ${col.limit}` : String(all.length) }),
          h("button", { type: "button", title: `Add a card to ${col.name}`, "aria-label": `Add a card to ${col.name}`,
            text: "+", onclick: () => openCardForm(null, { column: col.id }) }))),
      !closed && (drawn.length ? drawn.map(cardEl)
        : h("div", { class: "empty", text: filtering && all.length ? "No matching cards" : "No cards" })),
      !closed && shown.length > drawn.length && h("button", {
        type: "button", class: "more", text: `Show ${Math.min(COLUMN_PAGE, shown.length - drawn.length)} more of ${shown.length - drawn.length}`,
        onclick: () => { state.shown.set(col.id, limit + COLUMN_PAGE); renderColumns(); },
      }));
  });
  main.replaceChildren(...columns);
  for (const el of columns) {
    if (scroll.get(el.dataset.col)) el.scrollTop = scroll.get(el.dataset.col);
  }
  settleFocus(main);
  const count = $("#search-count");
  count.textContent = words.length || state.groups.size || state.blockedOnly ? `${matched} of ${plural(total, "card")}` : "";
}

// Only one card is a tab stop; arrow keys go between cards. Otherwise a big board takes a thousand Tab presses to cross.
function settleFocus(main) {
  const keyed = state.focusKey ? main.querySelector(`.card[data-key="${CSS.escape(state.focusKey)}"]`) : null;
  const stop = keyed || main.querySelector(".card");
  if (stop) stop.tabIndex = 0;
  if (keyed && state.wantFocus) {
    keyed.focus({ preventScroll: true });
    keyed.scrollIntoView({ block: "nearest" });
  }
  state.wantFocus = false;
}

function cardEl(card) {
  const g = groupOf(card);
  const first = card.fields.find((f) => f.value);
  const ticks = progress(card);
  return h("a", {
    class: "card", href: `#${state.board.id}/${card.key}`, draggable: true, "data-key": card.key, tabIndex: -1,
    onclick: (e) => {
      if (e.button || e.ctrlKey || e.metaKey || e.shiftKey) return;  // let the browser open a new tab
      e.preventDefault();
      openCard(card.key);
    },
  },
  h("span", { class: "key", text: card.key }),
  h("span", { class: "title", text: card.title }),
  (g || first || card.blocked || ticks.total > 0) && h("span", { class: "meta" },
    g && groupTag(g),
    first && h("span", { class: "first-field", text: first.value }),
    ticks.total > 0 && h("span", { class: `ticks${ticks.done === ticks.total ? " all" : ""}`,
      title: "Checklist lines ticked", text: `${ticks.done}/${ticks.total}` }),
    card.blocked && h("span", { class: "flag", text: "Blocked" })));
}

function renderGroups() {
  const b = state.board;
  $("#groups").hidden = !b.groups.length;
  if (!b.groups.length) return;
  $("#groups-title").textContent = `${b.groupLabel} details`;
  const labels = [...new Set(b.groups.flatMap((g) => g.details.map((d) => d.label)))];
  const counts = countBy(activeCards(), (c) => c.group);
  $("#groups-table").replaceChildren(
    h("thead", {}, h("tr", {}, [b.groupLabel, "Key", "Cards", ...labels].map((t) => h("th", { scope: "col", text: t })))),
    h("tbody", {}, b.groups.map((g) => h("tr", {},
      h("td", {}, groupLink(g)),
      h("td", {}, h("code", { text: g.prefix })),
      h("td", { text: String(counts.get(g.id) || 0) }),
      labels.map((l) => h("td", {}, rich(g.details.find((d) => d.label === l)?.value || "")))))));
}

// ---- moving cards -----------------------------------------------------------------------------

function bindDrag() {
  const main = $("#board");
  const line = h("div", { class: "drop-line" });
  const nextCard = (el, dir) => {
    let n = el[dir];
    while (n && !(n.classList.contains("card") && !n.classList.contains("dragging"))) n = n[dir];
    return n;
  };
  const clear = (except) => {
    for (const col of main.querySelectorAll(".drop-target")) if (col !== except) col.classList.remove("drop-target");
  };
  const end = () => {
    state.drag = null;
    line.remove();
    clear(null);
    main.querySelector(".dragging")?.classList.remove("dragging");
  };

  main.addEventListener("dragstart", (e) => {
    const el = e.target.closest?.(".card");
    if (!el) return;
    state.drag = el.dataset.key;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", el.dataset.key);
    requestAnimationFrame(() => el.classList.add("dragging"));
  });
  main.addEventListener("dragover", (e) => {
    if (!state.drag) return;
    const col = e.target.closest(".col");
    clear(col);
    if (!col) {
      line.remove();
      return;
    }
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    col.classList.add("drop-target");
    const before = [...col.querySelectorAll(".card:not(.dragging)")].find((c) => {
      const r = c.getBoundingClientRect();
      return e.clientY < r.top + r.height / 2;
    }) || col.querySelector(".more");
    if (line.parentNode !== col || line.nextElementSibling !== before) col.insertBefore(line, before);
  });
  main.addEventListener("dragleave", (e) => {
    if (!main.contains(e.relatedTarget)) {
      line.remove();
      clear(null);
    }
  });
  main.addEventListener("drop", (e) => {
    const col = e.target.closest(".col");
    if (!state.drag || !col) return;
    e.preventDefault();
    const key = state.drag;
    const prev = line.parentNode ? nextCard(line, "previousElementSibling") : null;
    const next = line.parentNode ? nextCard(line, "nextElementSibling") : null;
    end();
    moveCard(key, col.dataset.col, prev?.dataset.key, next?.dataset.key);
  });
  main.addEventListener("dragend", end);
}

// Put a card in `column` right after prevKey, or right before nextKey, or at the bottom. Those are
// the neighbours on screen; cards hidden by a filter or a "show more" button may sit between them.
function moveCard(key, column, prevKey, nextKey) {
  const card = cardBy(key);
  if (!card) return;
  const current = cardsIn(column);
  const others = current.filter((c) => c.key !== key);
  let at = others.length;
  if (prevKey && others.some((c) => c.key === prevKey)) at = others.findIndex((c) => c.key === prevKey) + 1;
  else if (nextKey && others.some((c) => c.key === nextKey)) at = others.findIndex((c) => c.key === nextKey);
  if (card.column === column && current.indexOf(card) === at) return;
  const p = others[at - 1]?.rank;
  const n = others[at]?.rank;
  const rank = p !== undefined && n !== undefined ? (p + n) / 2 : p !== undefined ? p + 1 : n !== undefined ? n - 1 : card.rank;
  saveMove(card, column, rank);
}

function setColumn(key, column) {
  const card = cardBy(key);
  if (!card || card.column === column) return;
  moveCard(key, column);
}

// Move up or down among the cards showing, as the buttons on a card and Shift+arrow keys do.
function shiftCard(key, delta) {
  const card = cardBy(key);
  if (!card) return false;
  const words = queryWords();
  const visible = cardsIn(card.column).filter((c) => c.key === key || matches(c, words));
  const at = visible.indexOf(card);
  const target = visible[at + delta];
  if (!target) return false;
  if (delta < 0) moveCard(key, card.column, null, target.key);
  else moveCard(key, card.column, target.key, null);
  return true;
}

function shiftColumn(key, delta) {
  const card = cardBy(key);
  const columns = state.board.columns;
  const at = columns.findIndex((c) => c.id === card?.column);
  const target = columns[at + delta];
  if (!card || !target) return false;
  moveCard(key, target.id);
  announce(`${key} moved to ${target.name}.`);
  return true;
}

function saveMove(card, column, rank) {
  const before = { column: card.column, rank: card.rank };
  Object.assign(card, { column, rank });
  render();
  send("PATCH", `/cards/${card.key}`, { column, rank }, { retry: true })
    .then(() => {
      const col = state.board?.columns.find((c) => c.id === column);
      const n = cardsIn(column).length;
      if (col?.limit && n > col.limit) toast(`${col.name} now holds ${n} cards; its limit is ${col.limit}.`);
    })
    .catch((e) => {
      Object.assign(card, before);
      if (state.board) render();
      toast(e.message, { error: true });
    });
}

// ---- side sheet -------------------------------------------------------------------------------

function showSheet(mode, key, content, { onClose = null, unsaved = null, retry = null, sync = null } = {}) {
  const dlg = $("#sheet");
  sheet.onClose?.();
  Object.assign(sheet, { mode, key, dirty: false, onClose, unsaved, retry, sync });
  $("#sheet-body").replaceChildren(content);
  if (!dlg.open) dlg.showModal();
  dlg.scrollTop = 0;
}

function closeSheet(force = false) {
  const dlg = $("#sheet");
  if (!dlg.open) return;
  if (!force && sheet.dirty) {
    askDiscard();
    return;
  }
  finishSheet();
  dlg.close();
}

// Runs when the sheet closes, whether through closeSheet or the browser (Escape).
function finishSheet() {
  if (!sheet.mode) return;
  const backTo = sheet.key;
  sheet.onClose?.();
  Object.assign(sheet, { mode: null, key: null, dirty: false, onClose: null, unsaved: null, retry: null, sync: null });
  $("#sheet-body").replaceChildren();
  document.body.append($("#toast"));
  if (state.board && parseHash().key) setHash(state.board.id);
  // The card that was clicked has usually been redrawn since, so the browser can't return to it.
  if (backTo) {
    state.focusKey = backTo;
    requestAnimationFrame(() => $(`.card[data-key="${CSS.escape(backTo)}"]`)?.focus());
  }
}

function bindSheet() {
  const dlg = $("#sheet");
  let downOutside = false;
  const outside = (e) => {
    const r = dlg.getBoundingClientRect();
    return e.target === dlg && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom);
  };
  dlg.addEventListener("pointerdown", (e) => { downOutside = outside(e); });
  dlg.addEventListener("click", (e) => {
    if (downOutside && outside(e)) closeSheet();
    downOutside = false;
  });
  dlg.addEventListener("cancel", (e) => {
    if (sheet.dirty) {
      e.preventDefault();
      askDiscard();
    }
  });
  dlg.addEventListener("close", () => {
    if (!dlg.open) finishSheet();  // the sheet may have been reopened before this event arrived
  });
}

function askDiscard(then = () => closeSheet(true)) {
  const body = $("#sheet-body");
  body.querySelector(".confirm")?.remove();
  const keep = h("button", { type: "button", text: "Keep editing", onclick: () => box.remove() });
  const box = h("div", { class: "confirm", role: "alert" },
    h("span", { text: "Discard your changes?" }),
    h("button", { type: "button", class: "danger solid", text: "Discard", onclick: () => { sheet.dirty = false; then(); } }),
    keep);
  const actions = body.querySelector(".form-actions");
  if (actions) actions.after(box);
  else body.prepend(box);
  keep.focus();
  box.scrollIntoView({ block: "nearest" });
}

function confirmBox(anchor, message, label, run) {
  $("#sheet-body").querySelector(".confirm")?.remove();
  const err = h("span", { class: "form-error" });
  const keep = h("button", { type: "button", text: "Keep it", onclick: () => box.remove() });
  const go = h("button", {
    type: "button", class: "danger solid", text: label,
    onclick: async () => {
      go.disabled = true;
      try {
        await run();
      } catch (e) {
        err.textContent = e.message;
        go.disabled = false;
      }
    },
  });
  const box = h("div", { class: "confirm", role: "alert" }, h("span", { text: message }), go, keep, err);
  anchor.after(box);
  keep.focus();
}

function sheetHead(title, key, ...buttons) {
  return h("div", { class: "sheet-head" },
    h("div", { class: "sheet-title" }, key && h("div", { class: "key", text: key }), h("h2", { id: "sheet-title", text: title })),
    h("div", { class: "actions" }, buttons, h("button", { type: "button", text: "Close", onclick: () => closeSheet() })));
}

// ---- card view --------------------------------------------------------------------------------

// The parts of a card view that can only be redrawn whole. Column, ticks and notes update in place.
function viewPart(card) {
  const b = state.board;
  return JSON.stringify([card.title, card.group, card.summary, card.blocked, card.fields, card.sections, card.archived,
    b.columns, b.checklists, b.groups, b.groupLabel]);
}

function openCard(key, { keepPlace = false } = {}) {
  const card = cardBy(key);
  if (!card) {
    toast(`There's no card ${key} on this board.`, { error: true });
    setHash(state.board.id);
    return;
  }
  const dlg = $("#sheet");
  const place = keepPlace ? { top: dlg.scrollTop, focusNotes: document.activeElement?.id === "card-notes",
    start: $("#card-notes")?.selectionStart, end: $("#card-notes")?.selectionEnd } : null;
  setHash(state.board.id, key);
  const boardId = state.board.id;
  const draftRef = `${boardId}/${key}`;
  const notes = h("textarea", { id: "card-notes", rows: 6, value: drafts.get(draftRef) ?? card.notes, placeholder: "Working notes. They save as you type." });
  drafts.delete(draftRef);
  const status = h("div", { class: "savestate", "aria-live": "polite" });
  let timer = null;
  let saved = card.notes;     // the text as last saved, to tell whether there's anything new to save
  let baseline = card.notes;  // what the card held then, to tell whether someone else has changed it since
  const differs = () => notes.value.trimEnd() !== saved.trimEnd();
  const busy = () => timer !== null || differs();
  const otherNotes = () => cardBy(key)?.notes ?? "";
  const otherChanged = () => otherNotes() !== baseline;
  const showConflict = () => {
    if (status.querySelector("button")) return;  // clicking one of them blurs the box, which lands here again
    status.replaceChildren("These notes also changed somewhere else. Your text is still here. ",
      h("button", { type: "button", text: "Keep my text", onclick: () => { baseline = otherNotes(); status.textContent = ""; save(); } }),
      " ",
      h("button", { type: "button", text: "Use the other text", onclick: () => { saved = baseline = otherNotes(); notes.value = saved; status.textContent = ""; } }));
  };
  const save = () => {
    clearTimeout(timer);
    timer = null;
    if (!differs()) return;
    if (otherChanged()) {
      showConflict();
      return;
    }
    const value = notes.value;
    const base = baseline;
    status.textContent = "Saving…";
    send("PATCH", `/cards/${key}`, { notes: value }, { retry: (fresh) => fresh.cards.find((c) => c.key === key)?.notes === base })
      .then((data) => {
        saved = value.trimEnd();
        baseline = data.board?.cards.find((c) => c.key === key)?.notes ?? baseline;
        if (status.isConnected && !timer && !differs()) status.textContent = "Saved";
      })
      .catch((e) => {
        if (!status.isConnected) keepDraft(boardId, key, value, e.message);
        else if (e.status === 412) showConflict();
        else status.textContent = `Not saved: ${e.message} Your text is still here.`;
      });
  };
  // Closing has nowhere to ask about a conflict, so the text is kept for the person to come back to.
  const leave = () => {
    clearTimeout(timer);
    timer = null;
    if (differs() && otherChanged()) keepDraft(boardId, key, notes.value, "they also changed somewhere else.");
    else save();
  };
  notes.addEventListener("input", () => {
    status.textContent = "";
    clearTimeout(timer);
    timer = setTimeout(save, NOTES_DELAY);
  });
  notes.addEventListener("blur", save);
  const ticks = checklistsView(card);
  const shown = viewPart(card);
  const sync = () => {
    const now = cardBy(key);
    if (!now) {
      closeSheet(true);
      if (!$("#toast").classList.contains("error") || $("#toast").hidden) toast("That card was deleted somewhere else.");
    } else if (viewPart(now) !== shown) {
      if (!busy()) openCard(key, { keepPlace: true });
    } else {
      $("#card-column").value = now.column;
      ticks.sync(now);
      if (!busy() && now.notes !== baseline) {
        saved = baseline = now.notes;
        notes.value = saved;
        status.textContent = "";
      }
    }
  };
  showSheet("view", key, cardView(card, notes, status, ticks.nodes), { onClose: leave, unsaved: busy, retry: save, sync });
  if (place) {
    dlg.scrollTop = place.top;
    if (place.focusNotes) {
      notes.focus({ preventScroll: true });
      notes.setSelectionRange(place.start, place.end);
    }
  }
  if (differs()) status.textContent = "These are your notes that weren't saved. They replace what's on the card when you leave this box.";
}

function cardView(card, notes, status, checklists) {
  const b = state.board;
  const g = groupOf(card);
  const column = h("select", { id: "card-column", onchange: (e) => setColumn(card.key, e.target.value) },
    b.columns.map((c) => h("option", { value: c.id, text: c.name })));
  column.value = card.column;
  const fact = (label, ...value) => h("div", { class: "fact" }, h("span", { class: "lbl", text: label }), h("span", { class: "val" }, value));
  const section = (label, text, list) => {
    const lines = text.split("\n").filter((l) => l.trim());
    return h("div", { class: "sec" }, h("h3", { text: label }), list && lines.length > 1
      ? h("ul", {}, lines.map((l) => h("li", {}, rich(l.trim().replace(/^[-*•]\s+/, "")))))
      : h("p", {}, rich(text)));
  };
  const nudge = (label, delta) => h("button", { type: "button", class: "icon", "aria-label": label, title: label, text: delta < 0 ? "↑" : "↓",
    disabled: !!card.archived, onclick: () => { if (shiftCard(card.key, delta)) announce(`${card.key} moved ${delta < 0 ? "up" : "down"}.`); } });
  const frag = document.createDocumentFragment();
  frag.append(...[
    sheetHead(card.title, card.key,
      h("button", { type: "button", text: "Edit", onclick: () => openCardForm(card.key) }),
      h("button", { type: "button", text: "Duplicate", onclick: () => duplicateCard(card.key) }),
      h("button", { type: "button", text: card.archived ? "Restore" : "Archive", onclick: () => setArchived(card.key, !card.archived) })),
    card.archived && h("div", { class: "archived-box" }, `Archived ${when(card.archived)}. It stays off the board until you restore it.`),
    h("div", { class: "facts" },
      h("div", { class: "fact" }, h("label", { class: "lbl", for: "card-column", text: "Column" }),
        h("div", { class: "position" }, column, nudge("Move up", -1), nudge("Move down", 1))),
      g && fact(b.groupLabel, groupLink(g)),
      card.fields.map((f) => fact(f.label, rich(f.value)))),
    card.blocked && h("div", { class: "blocked-box" }, h("strong", { text: "Blocked: " }), rich(card.blocked)),
    card.summary && section("Summary", card.summary, false),
    card.sections.map((s) => section(s.label, s.body, true)),
    !card.summary && !card.sections.length && h("p", { class: "hint", text: "No details yet. Edit the card to add a summary or sections." }),
    checklists,
    h("div", { class: "sec notes" }, h("label", { class: "lbl", for: "card-notes", text: "Notes" }), notes, status),
    h("p", { class: "hint", text: `Created ${when(card.created)}. Updated ${when(card.updated)}.` }),
  ].flat().filter(Boolean));
  return frag;
}

async function setArchived(key, archived) {
  try {
    await send("PATCH", `/cards/${key}`, { archived }, { retry: true });
    sheet.dirty = false;
    if (archived) {
      closeSheet(true);
      toast(`Archived ${key}.`, { action: { label: "Restore", run: () => setArchived(key, false) } });
    } else {
      if (sheet.mode === "view" && sheet.key === key) openCard(key);
      toast(`Restored ${key}. It's at the bottom of its column.`);
      if (sheet.mode === "archive") openArchive();
    }
  } catch (e) {
    toast(e.message, { error: true });
  }
}

async function duplicateCard(key) {
  const card = cardBy(key);
  if (!card) return;
  const after = cardsIn(card.column);
  const next = after[after.indexOf(card) + 1];
  const values = { title: `Copy of ${card.title}`.slice(0, 200), group: card.group, column: card.column, summary: card.summary,
    blocked: card.blocked, fields: card.fields, sections: card.sections,
    rank: next ? (card.rank + next.rank) / 2 : card.rank + 1 };
  try {
    const data = await send("POST", "/cards", values, { retry: true });
    openCard(data.key);
    toast(`Created ${data.key} from ${key}. Its notes and ticks start empty.`);
  } catch (e) {
    toast(e.message, { error: true });
  }
}

// ---- checklists --------------------------------------------------------------------------------

const tickable = (cl) => cl.items.filter((i) => !i.heading);

function progress(card) {
  let done = 0;
  for (const id of card.checks) if (index.checkIds.has(id)) done++;
  return { done, total: index.checkIds.size };
}

// The board's checklists, ticked separately on every card. Each tick saves straight away, and a tick
// is sent as a change to what the server has, so ticks made in two tabs both stay.
function checklistsView(card) {
  let ticked = new Set(card.checks);
  const boxes = new Map();
  const recounts = [];
  const save = (id, on) => send("PATCH", `/cards/${card.key}`, (board) => {
    const current = new Set(board?.cards.find((c) => c.key === card.key)?.checks ?? ticked);
    if (on) current.add(id);
    else current.delete(id);
    return { checks: [...current] };
  }, { retry: true }).catch((e) => {
    toast(`Ticks on ${card.key} weren't saved: ${e.message}`, { error: true });
    sheet.sync?.();  // show what the server has
  });
  const nodes = state.board.checklists.filter((cl) => tickable(cl).length).map((cl) => {
    const tally = h("span", { class: "count" });
    const recount = () => { tally.textContent = `${tickable(cl).filter((i) => ticked.has(i.id)).length}/${tickable(cl).length}`; };
    recounts.push(recount);
    const blocks = [];
    let list = null;
    for (const item of cl.items) {
      if (item.heading) {
        blocks.push(h("h4", {}, rich(item.text)));
        list = null;
        continue;
      }
      if (!list) blocks.push(list = h("ul", { class: "checks" }));
      const box = h("input", {
        type: "checkbox", checked: ticked.has(item.id),
        onchange: () => {
          if (box.checked) ticked.add(item.id);
          else ticked.delete(item.id);
          recount();
          save(item.id, box.checked);
        },
      });
      boxes.set(item.id, box);
      list.append(h("li", {}, h("label", {}, box, h("span", {}, rich(item.text)))));
    }
    recount();
    return h("div", { class: "sec checklist" }, h("h3", {}, cl.name, " ", tally), blocks);
  });
  const sync = (now) => {
    ticked = new Set(now.checks);
    for (const [id, box] of boxes) box.checked = ticked.has(id);
    recounts.forEach((recount) => recount());
  };
  return { nodes, sync };
}

// Lines from the settings box back into checklist items. Lines that didn't change keep their ids,
// and with them the ticks on every card.
function checklistFrom(row) {
  const old = state.board.checklists.find((cl) => cl.id === row.dataset.id);
  const keyOf = (heading, text) => `${heading ? "#" : "-"}${text}`;
  const ids = new Map();
  for (const i of old?.items || []) ids.set(keyOf(i.heading, i.text), [...(ids.get(keyOf(i.heading, i.text)) || []), i.id]);
  const items = $(".k-items", row).value.split("\n").map((l) => l.trim()).filter(Boolean).map((line) => {
    const heading = line.startsWith("#");
    const text = heading ? line.replace(/^#+\s*/, "") : line.replace(/^(?:[-*•]|\[[ xX]?\])\s+/, "");
    return { id: ids.get(keyOf(heading, text))?.shift(), heading, text };
  });
  return { id: row.dataset.id || undefined, name: $(".k-name", row).value, items };
}

// ---- card form --------------------------------------------------------------------------------

function onlyFilteredGroup() {
  return state.groups.size === 1 ? [...state.groups][0] || null : null;
}

// The part of a card the edit form shows, for telling whether someone else has changed it.
const formPart = (card) => (card ? JSON.stringify([card.title, card.group, card.column, card.summary, card.blocked, card.fields, card.sections]) : "");

function openCardForm(key, defaults = {}) {
  const b = state.board;
  const card = key ? cardBy(key) : null;
  let startedFrom = formPart(card);
  const v = card || { title: "", group: onlyFilteredGroup(), column: defaults.column || b.columns[0].id,
    summary: "", blocked: "", fields: [], sections: b.template.map((label) => ({ label, body: "" })) };
  const title = h("input", { maxLength: 200, value: v.title, autofocus: true });
  const group = h("select", {}, h("option", { value: "", text: "None" }), b.groups.map((g) => h("option", { value: g.id, text: g.name })));
  group.value = v.group || "";
  const column = h("select", {}, b.columns.map((c) => h("option", { value: c.id, text: c.name })));
  column.value = v.column;
  const summary = h("textarea", { rows: 3, maxLength: 1000, value: v.summary });
  const blocked = h("input", { maxLength: 500, value: v.blocked });
  const fields = h("textarea", { rows: Math.max(3, v.fields.length + 1), value: pairsText(v.fields), placeholder: "Size: 3 to 4 hours" });
  const rows = h("div", { class: "list-rows" });
  const addSection = (s = { label: "", body: "" }) => {
    const row = h("div", { class: "list-row" });
    row.append(
      h("div", { class: "line" },
        field("Section", h("input", { class: "s-label", maxLength: 60, value: s.label, placeholder: "Acceptance criteria" })),
        h("button", { type: "button", class: "danger", text: "Remove", onclick: () => { row.remove(); sheet.dirty = true; } })),
      h("textarea", { class: "s-body", "aria-label": "Section text", rows: Math.min(12, Math.max(3, s.body.split("\n").length + 1)), value: s.body }));
    rows.append(row);
    return row;
  };
  v.sections.forEach((s) => addSection(s));

  const error = h("div", { class: "form-error", role: "alert" });
  const submit = h("button", { type: "submit", class: "primary", text: card ? "Save" : "Create card" });
  const actions = h("div", { class: "form-actions" }, submit,
    h("button", {
      type: "button", text: "Cancel",
      onclick: () => {
        const back = card ? () => openCard(card.key) : () => closeSheet(true);
        sheet.dirty ? askDiscard(back) : back();
      },
    }),
    h("span", { class: "spacer" }),
    card && h("button", {
      type: "button", class: "danger", text: "Delete card",
      onclick: () => confirmBox(actions, `Delete ${card.key}? This can't be undone. Archive keeps a card out of the way without losing it.`, "Delete", async () => {
        await send("DELETE", `/cards/${card.key}`, undefined, { retry: (fresh) => formPart(fresh.cards.find((c) => c.key === key)) === startedFrom });
        sheet.dirty = false;
        closeSheet(true);
        toast(`Deleted ${card.key}.`);
      }),
    }));

  const onsubmit = async (e) => {
    e.preventDefault();
    error.textContent = "";
    const values = { title: title.value.trim(), group: group.value || null, column: column.value,
      summary: summary.value, blocked: blocked.value };
    try {
      values.fields = parsePairs(fields.value, "Fields");
    } catch (err) {
      error.textContent = err.message;
      fields.focus();
      return;
    }
    values.sections = [...rows.children]
      .map((r) => ({ label: $(".s-label", r).value.trim(), body: $(".s-body", r).value }))
      .filter((s) => s.body.trim());
    if (!values.title) {
      error.textContent = "The card needs a title.";
      title.focus();
      return;
    }
    if (values.sections.some((s) => !s.label)) {
      error.textContent = "Give every section a name, or remove the ones you don't need.";
      return;
    }
    if (card && values.column !== card.column) {
      const ranks = cardsIn(values.column).map((c) => c.rank);
      if (ranks.length) values.rank = ranks[ranks.length - 1] + 1;
    }
    submit.disabled = true;
    try {
      if (card) {
        // Another change to the board is no reason to stop, unless it changed this card too.
        await send("PATCH", `/cards/${card.key}`, values, {
          retry: (fresh) => {
            const now = fresh.cards.find((c) => c.key === card.key);
            if (formPart(now) === startedFrom) return true;
            startedFrom = formPart(now);
            return false;
          },
        });
        sheet.dirty = false;
        openCard(card.key);
      } else {
        const data = await send("POST", "/cards", values, { retry: true });
        sheet.dirty = false;
        openCard(data.key);
        toast(`Created ${data.key}.`);
      }
    } catch (err) {
      error.textContent = err.message;
    } finally {
      submit.disabled = false;
    }
  };

  const form = h("form", { class: "form", novalidate: true, oninput: () => { sheet.dirty = true; }, onsubmit },
    field("Title", title),
    h("div", { class: "row2" }, field(b.groupLabel, group), field("Column", column)),
    field("Summary", summary, "A sentence or two about what this card is for."),
    field("Blocked by", blocked, "Leave empty when nothing blocks it."),
    field("Fields", fields, "One per line, as Label: value. The first one shows on the card."),
    h("div", { class: "field" },
      h("span", { class: "lbl", text: "Sections" }),
      h("span", { class: "hint", text: "Wrap code in `backticks`. A section with several lines shows as a list." }),
      rows,
      h("div", {}, h("button", { type: "button", text: "Add section", onclick: () => { $(".s-label", addSection()).focus(); sheet.dirty = true; } }))),
    error, actions);
  showSheet(card ? "edit" : "new", key, h("div", { class: "form" },
    sheetHead(card ? "Edit card" : "New card", card?.key), form));
}

// ---- board forms ------------------------------------------------------------------------------

function openSettings() {
  const b = state.board;
  const active = activeCards();
  const colCards = countBy(active, (c) => c.column);
  const groupCards = countBy(active, (c) => c.group);
  const dirty = () => { sheet.dirty = true; };
  const name = h("input", { maxLength: 120, value: b.name });
  const desc = h("textarea", { rows: 2, maxLength: 1000, value: b.description });
  const prefix = upper(h("input", { maxLength: 8, value: b.prefix }));
  const groupLabel = h("input", { maxLength: 30, value: b.groupLabel });
  const template = h("textarea", { rows: Math.max(2, b.template.length + 1), value: b.template.join("\n"), placeholder: "Acceptance criteria" });

  const cols = h("div", { class: "list-rows" });
  const addCol = (c = { id: "", name: "", limit: null }) => {
    const n = colCards.get(c.id) || 0;
    const row = h("div", { class: "col-row", "data-id": c.id });
    const moveTo = h("select", { class: "c-move" });
    const removing = h("div", { class: "removing", hidden: true },
      h("span", { text: `Its ${plural(n, "card")} will move to:` }), moveTo,
      h("button", { type: "button", text: "Keep this column", onclick: () => {
        delete row.dataset.removing;
        removing.hidden = true;
        row.classList.remove("removed");
      } }));
    const remove = () => {
      dirty();
      if (!n) {
        row.remove();
        return;
      }
      const targets = [...cols.children].filter((r) => r !== row && r.dataset.id && !r.dataset.removing);
      if (!targets.length) {
        error.textContent = `A column has to stay for the cards in ${c.name} to move to.`;
        return;
      }
      moveTo.replaceChildren(...targets.map((r) => h("option", { value: r.dataset.id, text: $(".c-name", r).value })));
      row.dataset.removing = "1";
      row.classList.add("removed");
      removing.hidden = false;
    };
    row.append(
      field("Column", h("input", { class: "c-name", maxLength: 40, value: c.name })),
      field("Limit", h("input", { class: "c-limit", type: "number", min: 1, max: 999, step: 1, value: c.limit ?? "", placeholder: "None" })),
      h("div", { class: "mini" },
        h("button", { type: "button", class: "icon", title: "Move up", "aria-label": "Move up", text: "↑",
          onclick: () => { row.previousElementSibling?.before(row); dirty(); } }),
        h("button", { type: "button", class: "icon", title: "Move down", "aria-label": "Move down", text: "↓",
          onclick: () => { row.nextElementSibling?.after(row); dirty(); } }),
        h("button", { type: "button", class: "danger", text: "Remove", onclick: remove })),
      removing);
    cols.append(row);
    return row;
  };
  b.columns.forEach((c) => addCol(c));

  const groups = h("div", { class: "list-rows" });
  const addGroup = (g = { id: "", name: "", prefix: "", color: COLORS[groups.children.length % COLORS.length], link: "", details: [] }) => {
    const n = groupCards.get(g.id) || 0;
    const gname = h("input", { class: "g-name", maxLength: 60, value: g.name });
    const gprefix = upper(h("input", { class: "g-prefix", maxLength: 8, value: g.prefix }));
    if (!g.id) gname.addEventListener("input", () => { if (!gprefix.dataset.typed) gprefix.value = autoPrefix(gname.value); });
    gprefix.addEventListener("input", () => { gprefix.dataset.typed = "1"; });
    const color = h("select", { class: "g-color" }, COLORS.map((c) => h("option", { value: c, text: c[0].toUpperCase() + c.slice(1) })));
    color.value = g.color;
    const row = h("div", { class: "list-row", "data-id": g.id },
      h("div", { class: "group-row-top" },
        field("Name", gname), field("Key prefix", gprefix), field("Color", color),
        h("div", { class: "mini" },
          h("button", { type: "button", class: "icon", title: "Move up", "aria-label": "Move up", text: "↑",
            onclick: () => { row.previousElementSibling?.before(row); dirty(); } }),
          h("button", { type: "button", class: "icon", title: "Move down", "aria-label": "Move down", text: "↓",
            onclick: () => { row.nextElementSibling?.after(row); dirty(); } }),
          h("button", { type: "button", class: "danger", text: "Remove", disabled: n > 0,
            title: n ? `${plural(n, "card")} still belong to it` : null, onclick: () => { row.remove(); dirty(); } }))),
      field("Link", h("input", { class: "g-link", type: "url", maxLength: 500, value: g.link, placeholder: "https://" })),
      field("Details", h("textarea", { class: "g-details", rows: Math.max(2, g.details.length + 1), value: pairsText(g.details) }),
        "One per line, as Label: value. They show in the table under the board."));
    groups.append(row);
    return row;
  };
  b.groups.forEach((g) => addGroup(g));

  const lists = h("div", { class: "list-rows" });
  const addList = (cl = { id: "", name: "", items: [] }) => {
    const row = h("div", { class: "list-row", "data-id": cl.id },
      h("div", { class: "line" },
        field("Checklist", h("input", { class: "k-name", maxLength: 60, value: cl.name, placeholder: "Before release" })),
        h("button", { type: "button", class: "danger", text: "Remove", onclick: () => { row.remove(); dirty(); } })),
      h("textarea", { class: "k-items", "aria-label": "Checklist lines", rows: Math.min(16, Math.max(4, cl.items.length + 1)),
        value: cl.items.map((i) => (i.heading ? "# " : "") + i.text).join("\n") }));
    lists.append(row);
    return row;
  };
  b.checklists.forEach((cl) => addList(cl));

  const error = h("div", { class: "form-error", role: "alert" });
  const submit = h("button", { type: "submit", class: "primary", text: "Save" });
  const actions = h("div", { class: "form-actions" }, submit,
    h("button", { type: "button", text: "Cancel", onclick: () => closeSheet() }),
    h("span", { class: "spacer" }),
    h("button", {
      type: "button", class: "danger", text: "Delete board",
      onclick: () => confirmBox(actions, `Delete ${b.name} and its ${plural(b.cards.length, "card")}? A copy is kept in your backups, but it won't be on any board.`,
        "Delete board", async () => {
          await send("DELETE", "");
          sheet.dirty = false;
          closeSheet(true);
          await loadBoards();
          setBoard(null);
          toast(`Deleted ${b.name}.`);
          history.replaceState(null, "", "#");
          await route();
        }),
    }));

  const onsubmit = async (e) => {
    e.preventDefault();
    error.textContent = "";
    let changes;
    try {
      const rows = [...cols.children];
      const staying = rows.filter((r) => !r.dataset.removing);
      changes = {
        name: name.value, description: desc.value, prefix: prefix.value.trim(), groupLabel: groupLabel.value,
        template: template.value.split("\n").map((l) => l.trim()).filter(Boolean),
        columns: staying.map((r) => {
          const limit = $(".c-limit", r).value;
          return { id: r.dataset.id || undefined, name: $(".c-name", r).value, limit: limit === "" ? null : Number(limit) };
        }),
        groups: [...groups.children].map((r) => ({
          id: r.dataset.id || undefined, name: $(".g-name", r).value, prefix: $(".g-prefix", r).value.trim(),
          color: $(".g-color", r).value, link: $(".g-link", r).value.trim(),
          details: parsePairs($(".g-details", r).value, `${$(".g-name", r).value || "Group"} details`),
        })),
        checklists: [...lists.children].map(checklistFrom).filter((cl) => cl.name.trim() || cl.items.length),
      };
      const leaving = rows.filter((r) => r.dataset.removing);
      if (leaving.length) changes.moveCards = Object.fromEntries(leaving.map((r) => [r.dataset.id, $(".c-move", r).value]));
    } catch (err) {
      error.textContent = err.message;
      return;
    }
    submit.disabled = true;
    try {
      await send("PATCH", "", changes);
      sheet.dirty = false;
      closeSheet(true);
      toast("Board settings saved.");
    } catch (err) {
      error.textContent = err.message;
    } finally {
      submit.disabled = false;
    }
  };

  const form = h("form", { class: "form", novalidate: true, oninput: dirty, onsubmit },
    field("Board name", name),
    field("Description", desc, "Shows under the board name."),
    h("div", { class: "row2" },
      field("Key prefix", prefix, "For cards with no group, as in T-12."),
      field("Group label", groupLabel, "What a group is on this board: Repo, Client, Project.")),
    field("New card sections", template, "One name per line. Every new card starts with these sections; any left empty aren't saved."),
    h("div", { class: "field" }, h("span", { class: "lbl", text: "Columns" }),
      h("span", { class: "hint", text: "Top to bottom here is left to right on the board. A limit flags the column when it holds more cards than that." }),
      cols, h("div", {}, h("button", { type: "button", text: "Add column", onclick: () => { $(".c-name", addCol()).focus(); dirty(); } }))),
    h("div", { class: "field" }, h("span", { class: "lbl", text: "Groups" }),
      h("span", { class: "hint", text: "Each group gets a color and its own card keys, like VM-1." }),
      groups, h("div", {}, h("button", { type: "button", text: "Add group", onclick: () => { $(".g-name", addGroup()).focus(); dirty(); } }))),
    h("div", { class: "field" }, h("span", { class: "lbl", text: "Checklists" }),
      h("span", { class: "hint", text: "One line per item. Start a line with # to make it a heading. Every card ticks its own copy, and a line you reword starts unticked." }),
      lists, h("div", {}, h("button", { type: "button", text: "Add checklist", onclick: () => { $(".k-name", addList()).focus(); dirty(); } }))),
    error, actions);
  showSheet("settings", null, h("div", { class: "form" }, sheetHead("Board settings"), form));
}

function openNewBoard() {
  const name = h("input", { maxLength: 120, autofocus: true, placeholder: "Client work" });
  const desc = h("textarea", { rows: 2, maxLength: 1000 });
  const error = h("div", { class: "form-error", role: "alert" });
  const submit = h("button", { type: "submit", class: "primary", text: "Create board" });
  const onsubmit = async (e) => {
    e.preventDefault();
    error.textContent = "";
    submit.disabled = true;
    try {
      const { board } = await api("POST", "/api/boards", { name: name.value, description: desc.value });
      await goToBoard(board.id);
      toast("Board created. Use Board settings to change its columns and add groups.");
    } catch (err) {
      error.textContent = err.message;
    } finally {
      submit.disabled = false;
    }
  };
  const form = h("form", { class: "form", novalidate: true, oninput: () => { sheet.dirty = true; }, onsubmit },
    field("Board name", name),
    field("Description", desc, "Optional. Shows under the board name."),
    h("p", { class: "hint", text: "New boards start with Backlog, Ready, In progress, Review and Done. You can change them later." }),
    error,
    h("div", { class: "form-actions" }, submit, h("button", { type: "button", text: "Cancel", onclick: () => closeSheet() })),
    h("div", { class: "start-actions" }, h("button", { type: "button", text: "Import a file", onclick: chooseImportFile })),
    startingPoints());
  showSheet("new-board", null, h("div", { class: "form" }, sheetHead("New board"), form));
}

async function duplicateBoard() {
  const b = state.board;
  try {
    const data = await api("POST", "/api/import", { ...b, name: `Copy of ${b.name}`.slice(0, 120) });
    await goToBoard(data.boards[0].id);
    toast(`Made a copy of ${b.name}.`);
  } catch (e) {
    toast(e.message, { error: true });
  }
}

// ---- importing --------------------------------------------------------------------------------

function chooseImportFile() {
  const input = $("#import-file");
  input.value = "";
  input.click();
}

// PowerShell's > writes UTF-16, so go by the byte order mark rather than assuming UTF-8.
function decodeFile(buffer) {
  const bytes = new Uint8Array(buffer);
  const label = bytes[0] === 0xff && bytes[1] === 0xfe ? "utf-16le" : bytes[0] === 0xfe && bytes[1] === 0xff ? "utf-16be" : "utf-8";
  return new TextDecoder(label).decode(buffer);
}

async function importFile(file) {
  try {
    if (file.size > MAX_IMPORT_BYTES) throw new Error(`${file.name} is too large to import here. Use the import command instead.`);
    const text = decodeFile(await file.arrayBuffer());
    const data = await api("POST", "/api/import", text);
    await goToBoard(data.boards[0].id);
    const names = data.boards.map((b) => `${b.name} (${plural(b.cards, "card")})`).join(", ");
    toast(`Imported ${names}.`);
  } catch (e) {
    toast(`Couldn't import ${file.name}: ${e.message}`, { error: true });
  }
}

// ---- archived cards ---------------------------------------------------------------------------

function openArchive() {
  const search = h("input", { type: "search", placeholder: "Search archived cards", "aria-label": "Search archived cards" });
  const list = h("div", { class: "archive-list" });
  let limit = ARCHIVE_PAGE;
  const draw = () => {
    const words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
    const found = index.archived.filter((c) => matchesQuery(c, words));
    const row = (card) => h("div", { class: "archive-row" },
      h("div", { class: "archive-text" },
        h("a", { href: `#${state.board.id}/${card.key}`, text: `${card.key}  ${card.title}`, onclick: (e) => { e.preventDefault(); openCard(card.key); } }),
        h("span", { class: "hint", text: `Archived ${when(card.archived)}` })),
      h("button", { type: "button", text: "Restore", onclick: async () => { await setArchived(card.key, false); draw(); } }));
    list.replaceChildren(
      ...(found.length ? found.slice(0, limit).map(row)
        : [h("p", { class: "hint", text: index.archived.length ? "No archived cards match." : "Nothing is archived. The Archive button on a card puts it here." })]),
      ...(found.length > limit ? [h("button", { type: "button", class: "more", text: `Show ${Math.min(ARCHIVE_PAGE, found.length - limit)} more of ${found.length - limit}`,
        onclick: () => { limit += ARCHIVE_PAGE; draw(); } })] : []));
  };
  search.addEventListener("input", () => { limit = ARCHIVE_PAGE; draw(); });
  draw();
  showSheet("archive", null, h("div", { class: "form" },
    sheetHead(`Archived cards (${index.archived.length})`),
    h("p", { class: "hint", text: "Archived cards stay out of the columns and limits, and keep everything they hold." }),
    search, list));
}

// ---- help -------------------------------------------------------------------------------------

function openHelp() {
  const keys = [
    ["n", "New card"], ["/", "Search"], ["?", "This page"],
    ["Arrow keys", "Go from card to card"], ["Enter", "Open the card"],
    ["Shift + Up or Down", "Move the card within its column"], ["Shift + Left or Right", "Move the card to the next column"],
    ["Esc", "Close what's open, or clear the search"],
  ];
  const info = state.health || {};
  showSheet("help", null, h("div", { class: "form" },
    sheetHead("Keyboard shortcuts and help"),
    h("div", { class: "sec" }, h("h3", { text: "Keyboard" }),
      h("table", { class: "keys" }, h("tbody", {}, keys.map(([key, what]) => h("tr", {}, h("td", {}, h("kbd", { text: key })), h("td", { text: what })))))),
    h("div", { class: "sec" }, h("h3", { text: "Writing on cards" }),
      h("p", { text: "Wrap code in `backticks` to get a Copy button. Web addresses become links. A section with several lines shows as a list." })),
    h("div", { class: "sec" }, h("h3", { text: "Your data" }),
      h("p", { text: "Everything is in one file on this computer. Nothing is sent anywhere." }),
      info.data && h("p", {}, "Boards file: ", h("code", { text: info.data })),
      info.backups && h("p", {}, "Daily backups: ", h("code", { text: info.backups })),
      h("p", { text: "To go back to an earlier copy, run the backups command, then the restore command." })),
    h("p", { class: "hint", text: `Laneboard ${info.version || APP_VERSION}` })));
}

// ---- start ------------------------------------------------------------------------------------

// Pick up changes made elsewhere (another tab, a command, the file itself).
let refreshing = false;
async function refresh() {
  if (refreshing || pending || state.drag) return;
  refreshing = true;
  try {
    const hadBoards = state.boards.length || state.board;
    await loadBoards();
    if (!state.board) {
      if (state.boards.length || !hadBoards) await route();
      return;
    }
    if (sheet.mode && sheet.mode !== "view" && sheet.mode !== "archive" && sheet.mode !== "help") return;  // a form holds its own copy until saved
    if (!state.boards.some((b) => b.id === state.board.id)) {
      setBoard(null);
      closeSheet(true);
      await route();
      return;
    }
    const entry = state.boards.find((b) => b.id === state.board.id);
    if (entry.rev !== undefined && entry.rev === state.board.rev) {
      renderPicker();
      return;
    }
    const fresh = (await api("GET", `/api/boards/${state.board.id}`)).board;
    if (fresh.rev === state.board.rev || state.drag || pending) {
      renderPicker();
      return;
    }
    setBoard(fresh);
    syncListEntry();
    render();
    sheet.sync?.();
  } catch {
    // Being stopped or having a broken file shows in the banner; the next round tries again.
  } finally {
    refreshing = false;
  }
}

function focusNeighbour(el, key) {
  const main = $("#board");
  const siblings = (col) => [...col.querySelectorAll(".card")];
  if (!el) {
    main.querySelector('.card[tabindex="0"]')?.focus();
    return;
  }
  const col = el.closest(".col");
  const list = siblings(col);
  const at = list.indexOf(el);
  let target = null;
  if (key === "ArrowUp") target = list[at - 1];
  else if (key === "ArrowDown") target = list[at + 1];
  else {
    const cols = [...main.querySelectorAll(".col")];
    const step = key === "ArrowLeft" ? -1 : 1;
    for (let i = cols.indexOf(col) + step; i >= 0 && i < cols.length && !target; i += step) {
      const other = siblings(cols[i]);
      target = other[Math.min(at, other.length - 1)];
    }
  }
  target?.focus();
}

function bindKeyboard() {
  const search = $("#search");
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $("#menu").open) {
      closeMenu();
      $("#menu summary").focus();
      return;
    }
    if ($("#sheet").open || e.ctrlKey || e.metaKey || e.altKey || e.target.closest?.("input, textarea, select")) return;
    if (e.key === "?") {
      e.preventDefault();
      openHelp();
    } else if (e.key === "/" && state.board) {
      e.preventDefault();
      search.focus();
      search.select();
    } else if (e.key === "n" && state.board) {
      e.preventDefault();
      openCardForm(null, { column: cardBy(e.target.closest?.(".card")?.dataset.key)?.column });
    } else if (e.key.startsWith("Arrow") && state.board) {
      const el = e.target.closest?.(".card");
      if (!el && e.target !== document.body) return;  // arrows keep their usual meaning on buttons and lists
      e.preventDefault();
      if (el && e.shiftKey) {
        const key = el.dataset.key;
        state.focusKey = key;
        state.wantFocus = true;
        if (e.key === "ArrowUp" || e.key === "ArrowDown") {
          const delta = e.key === "ArrowUp" ? -1 : 1;
          if (shiftCard(key, delta)) announce(`${key} moved ${delta < 0 ? "up" : "down"}.`);
        } else {
          shiftColumn(key, e.key === "ArrowLeft" ? -1 : 1);
        }
      } else {
        focusNeighbour(el, e.key);
      }
    }
  });
  $("#board").addEventListener("focusin", (e) => {
    const el = e.target.closest?.(".card");
    if (!el) return;
    $('#board .card[tabindex="0"]')?.setAttribute("tabindex", "-1");
    el.tabIndex = 0;
    state.focusKey = el.dataset.key;
  });
}

function bindPage() {
  $("#new-card").addEventListener("click", () => openCardForm(null));
  $("#board-settings").addEventListener("click", openSettings);
  $("#new-board").addEventListener("click", openNewBoard);
  $("#board-pick").addEventListener("change", (e) => { if (e.target.value) location.hash = e.target.value; });
  $("#import-file").addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (file) importFile(file);
  });
  const search = $("#search");
  let searchTimer = null;
  search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    state.query = search.value;
    if (state.board && state.board.cards.length > LARGE_BOARD) searchTimer = setTimeout(renderColumns, SEARCH_DELAY);
    else renderColumns();
  });
  search.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && search.value) {
      search.value = "";
      state.query = "";
      renderColumns();
    }
  });
  document.addEventListener("click", (e) => {
    if ($("#menu").open && !e.target.closest?.("#menu")) closeMenu();
  });
  window.addEventListener("hashchange", route);
  window.addEventListener("focus", refresh);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  setInterval(() => { if (!document.hidden) refresh(); }, POLL_EVERY);
  window.addEventListener("beforeunload", (e) => {
    if (sheet.dirty || pending || drafts.size || sheet.unsaved?.()) e.preventDefault();
  });
}

async function start() {
  bindPage();
  bindKeyboard();
  bindSheet();
  bindDrag();
  $("#version").textContent = `Laneboard ${APP_VERSION}`;
  checkVersion();
  renderMenu();
  try {
    await loadBoards();
  } catch (e) {
    if (e.status !== 503 && e.status !== 0) toast(e.message, { error: true });
    return;  // the banner says what's wrong, and the next round tries again
  }
  await route();
}

start();
