"use strict";
// Laneboard front end: shows one board at a time and saves every change through the local JSON API.

const COLORS = ["blue", "teal", "violet", "amber", "rose", "green", "slate"];
const NOTES_DELAY = 900;
const LAST_BOARD = "laneboard:last-board";

const state = {
  boards: [],         // [{id, name, cards}]
  board: null,        // the open board, as the server last sent it
  query: "",
  groups: new Set(),  // group filter; empty shows every card, "" stands for cards with no group
  drag: null,         // key of the card being dragged
};

// What the side sheet is showing. `dirty` guards unsaved form edits; `onClose` flushes card notes.
const sheet = { mode: null, key: null, dirty: false, onClose: null, busy: null, unsaved: null };

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
const cardBy = (key) => state.board.cards.find((c) => c.key === key);
const cardsIn = (column) => state.board.cards.filter((c) => c.column === column).sort(byRank);
const groupOf = (card) => state.board.groups.find((g) => g.id === card.group) || null;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function when(iso) {
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function remember(id) {
  try { localStorage.setItem(LAST_BOARD, id); } catch { /* storage may be blocked; not needed */ }
}

function recall() {
  try { return localStorage.getItem(LAST_BOARD); } catch { return null; }
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
      if (j % 2) out.push(h("a", { href: bit, target: "_blank", rel: "noopener noreferrer", text: bit }));
      else if (bit) out.push(bit);
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

const pairsText = (rows) => rows.map((r) => `${r.label}: ${r.value}`).join("\n");

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
  return raw.replace(/^[0-9]+/, "").slice(0, 8);
}

function field(label, control, hint) {
  return h("label", { class: "field" }, h("span", { class: "lbl", text: label }), control,
    hint && h("span", { class: "hint", text: hint }));
}

function groupTag(g) {
  return h("span", { class: "grp" }, h("span", { class: `dot c-${g.color}` }), g.name);
}

function groupLink(g) {
  return g.link ? h("a", { href: g.link, target: "_blank", rel: "noopener noreferrer" }, groupTag(g)) : groupTag(g);
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
  toastTimer = setTimeout(() => { el.hidden = true; }, error ? 9000 : 4000);
}

// ---- server ----------------------------------------------------------------------------------

async function api(method, path, body, rev) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  if (rev !== undefined && rev !== null) opts.headers["If-Match"] = String(rev);
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw Object.assign(new Error("Can't reach the board server. Start it again with: python -m laneboard"), { status: 0 });
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || `The server answered ${res.status}.`), { status: res.status });
  return data;
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

// Send a change to the open board. On a stale revision the board is reloaded; `retry` then
// re-sends the change (for small edits like notes and moves), otherwise the caller gets the error.
function send(method, path, body, { retry = false } = {}) {
  return enqueue(async () => {
    const id = state.board.id;
    const attempt = () => api(method, `/api/boards/${id}${path}`, body, state.board.rev);
    let data;
    try {
      data = await attempt();
    } catch (e) {
      if (e.status !== 412) throw e;
      await loadBoard(id);
      render();
      if (!retry) {
        throw Object.assign(new Error("The board changed while you were editing (in another tab or in its file). "
          + "It has been reloaded. Save again to keep your version."), { status: 412 });
      }
      data = await attempt();
    }
    if (data.board && state.board && data.board.id === state.board.id) {
      state.board = data.board;
      syncListEntry();
      render();
    }
    return data;
  });
}

async function loadBoards() {
  state.boards = (await api("GET", "/api/boards")).boards;
}

async function loadBoard(id) {
  state.board = (await api("GET", `/api/boards/${encodeURIComponent(id)}`)).board;
  syncListEntry();
  remember(id);
}

function syncListEntry() {
  const entry = state.boards.find((b) => b.id === state.board.id);
  if (entry) Object.assign(entry, { name: state.board.name, cards: state.board.cards.length });
}

// ---- routing: #<board id> or #<board id>/<card key> -----------------------------------------

function parseHash() {
  const [board = "", key = ""] = decodeURIComponent(location.hash.slice(1)).split("/");
  return { board, key };
}

function setHash(id, key) {
  history.replaceState(null, "", `#${id}${key ? "/" + key : ""}`);
}

async function route() {
  const want = parseHash();
  const ids = state.boards.map((b) => b.id);
  const id = [want.board, recall()].find((x) => ids.includes(x)) || ids[0];
  if (!id) {
    state.board = null;
    closeSheet(true);
    render();
    return;
  }
  try {
    if (!state.board || state.board.id !== id) {
      closeSheet(true);
      state.groups.clear();
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

// ---- board ------------------------------------------------------------------------------------

function render() {
  const b = state.board;
  document.title = b ? b.name : "Laneboard";
  $("#board-name").textContent = b ? b.name : "Laneboard";
  renderPicker();
  const desc = $("#board-desc");
  desc.textContent = b?.description || "";
  desc.hidden = !b?.description;
  $("#new-card").disabled = !b;
  $("#board-settings").disabled = !b;
  $("#tools").hidden = !b;
  if (!b) {
    $("#groups").hidden = true;
    renderEmpty();
    return;
  }
  renderFilters();
  renderColumns();
  renderGroups();
}

function renderPicker() {
  const pick = $("#board-pick");
  const others = state.boards.filter((b) => b.id !== state.board?.id);
  pick.hidden = !others.length;
  pick.replaceChildren(h("option", { value: "", text: "Switch board" }),
    ...others.map((b) => h("option", { value: b.id, text: `${b.name} (${plural(b.cards, "card")})` })));
  pick.value = "";
}

function renderEmpty() {
  const main = $("#board");
  main.style.setProperty("--cols", 1);
  main.replaceChildren(h("div", { class: "start" },
    h("p", { text: "There are no boards yet." }),
    h("button", { type: "button", class: "primary", text: "Create a board", onclick: openNewBoard })));
}

function renderFilters() {
  const b = state.board;
  const counts = countBy(b.cards, (c) => c.group || "");
  for (const id of [...state.groups]) {
    if (id && !b.groups.some((g) => g.id === id)) state.groups.delete(id);
  }
  const chip = (id, name, color, n) => h("button", {
    type: "button", "aria-pressed": String(state.groups.has(id)),
    onclick: () => { state.groups.has(id) ? state.groups.delete(id) : state.groups.add(id); render(); },
  }, h("span", { class: `dot c-${color}` }), name, h("span", { class: "count", text: String(n) }));
  const chips = b.groups.map((g) => chip(g.id, g.name, g.color, counts.get(g.id) || 0));
  if (counts.get("")) chips.push(chip("", `No ${b.groupLabel.toLowerCase()}`, "slate", counts.get("")));
  if (state.groups.size) {
    chips.push(h("button", { type: "button", class: "link", text: "Show all", onclick: () => { state.groups.clear(); render(); } }));
  }
  const box = $("#filters");
  box.hidden = !b.groups.length;
  box.replaceChildren(...chips);
}

function matches(card) {
  if (state.groups.size && !state.groups.has(card.group || "")) return false;
  const words = state.query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const text = [card.key, card.title, card.summary, card.blocked, card.notes, groupOf(card)?.name,
    ...card.fields.map((f) => `${f.label} ${f.value}`), ...card.sections.map((s) => `${s.label} ${s.body}`)]
    .join("\n").toLowerCase();
  return words.every((w) => text.includes(w));
}

function renderColumns() {
  const b = state.board;
  const main = $("#board");
  const filtering = state.groups.size > 0 || state.query.trim() !== "";
  main.style.setProperty("--cols", b.columns.length);
  main.replaceChildren(...b.columns.map((col) => {
    const all = cardsIn(col.id);
    const shown = all.filter(matches);
    const over = col.limit && all.length > col.limit;
    return h("section", { class: "col", "data-col": col.id, "aria-label": col.name },
      h("div", { class: "col-head" },
        h("h2", { text: col.name }),
        h("div", { class: "right" },
          h("span", { class: over ? "count over" : "count", title: col.limit ? `Limit ${col.limit}` : null,
            text: col.limit ? `${all.length} / ${col.limit}` : String(all.length) }),
          h("button", { type: "button", title: `Add a card to ${col.name}`, "aria-label": `Add a card to ${col.name}`,
            text: "+", onclick: () => openCardForm(null, { column: col.id }) }))),
      shown.length ? shown.map(cardEl)
        : h("div", { class: "empty", text: filtering && all.length ? "No matching cards" : "No cards" }));
  }));
}

function cardEl(card) {
  const g = groupOf(card);
  const first = card.fields.find((f) => f.value);
  const ticks = progress(card);
  return h("a", {
    class: "card", href: `#${state.board.id}/${card.key}`, draggable: true, "data-key": card.key,
    onclick: (e) => {
      if (e.button || e.ctrlKey || e.metaKey || e.shiftKey) return;  // let the browser open a new tab
      e.preventDefault();
      openCard(card.key);
    },
  },
  h("span", { class: "key", text: card.key }),
  h("span", { class: "title", text: card.title }),
  (g || first || card.blocked || ticks.total) && h("span", { class: "meta" },
    g && groupTag(g),
    first && h("span", { text: first.value }),
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
  const counts = countBy(b.cards, (c) => c.group);
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
    }) || null;
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

// Put a card in `column` between two neighbours (either may be missing), keeping ranks sparse.
function moveCard(key, column, prevKey, nextKey) {
  const card = cardBy(key);
  if (!card) return;
  const current = cardsIn(column);
  const others = current.filter((c) => c.key !== key);
  const at = nextKey ? others.findIndex((c) => c.key === nextKey) : others.length;
  if (card.column === column && current.findIndex((c) => c.key === key) === at) return;
  const p = prevKey ? cardBy(prevKey)?.rank : undefined;
  const n = nextKey ? cardBy(nextKey)?.rank : undefined;
  const rank = p !== undefined && n !== undefined ? (p + n) / 2 : p !== undefined ? p + 1 : n !== undefined ? n - 1 : card.rank;
  saveMove(card, column, rank);
}

function setColumn(key, column) {
  const card = cardBy(key);
  if (!card || card.column === column) return;
  const ranks = cardsIn(column).map((c) => c.rank);
  saveMove(card, column, ranks.length ? Math.max(...ranks) + 1 : card.rank);
}

function saveMove(card, column, rank) {
  const before = { column: card.column, rank: card.rank };
  Object.assign(card, { column, rank });
  render();
  send("PATCH", `/cards/${card.key}`, { column, rank }, { retry: true })
    .then(() => {
      const col = state.board.columns.find((c) => c.id === column);
      const n = cardsIn(column).length;
      if (col?.limit && n > col.limit) toast(`${col.name} now holds ${n} cards; its limit is ${col.limit}.`);
    })
    .catch((e) => {
      Object.assign(card, before);
      render();
      toast(e.message, { error: true });
    });
}

// ---- side sheet -------------------------------------------------------------------------------

function showSheet(mode, key, content, { onClose = null, busy = null, unsaved = null } = {}) {
  const dlg = $("#sheet");
  sheet.onClose?.();
  Object.assign(sheet, { mode, key, dirty: false, onClose, busy, unsaved });
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
  sheet.onClose?.();
  Object.assign(sheet, { mode: null, key: null, dirty: false, onClose: null, busy: null, unsaved: null });
  $("#sheet-body").replaceChildren();
  document.body.append($("#toast"));
  if (state.board && parseHash().key) setHash(state.board.id);
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
    h("div", {}, key && h("div", { class: "key", text: key }), h("h2", { id: "sheet-title", text: title })),
    h("div", { class: "actions" }, buttons, h("button", { type: "button", text: "Close", onclick: () => closeSheet() })));
}

// ---- card view --------------------------------------------------------------------------------

function openCard(key) {
  const card = cardBy(key);
  if (!card) {
    toast(`There's no card ${key} on this board.`, { error: true });
    setHash(state.board.id);
    return;
  }
  setHash(state.board.id, key);
  const notes = h("textarea", { id: "card-notes", rows: 6, value: card.notes, placeholder: "Working notes. They save as you type." });
  const status = h("div", { class: "savestate", "aria-live": "polite" });
  let timer = null;
  let saved = card.notes;
  const save = () => {
    clearTimeout(timer);
    timer = null;
    const value = notes.value;
    if (value.trimEnd() === saved) return;
    status.textContent = "Saving…";
    send("PATCH", `/cards/${key}`, { notes: value }, { retry: true })
      .then(() => {
        saved = value.trimEnd();
        if (status.isConnected) status.textContent = "Saved";
      })
      .catch((e) => {
        if (status.isConnected) status.textContent = `Not saved: ${e.message}`;
        else toast(`Notes for ${key} weren't saved: ${e.message}`, { error: true });
      });
  };
  notes.addEventListener("input", () => {
    status.textContent = "";
    clearTimeout(timer);
    timer = setTimeout(save, NOTES_DELAY);
  });
  notes.addEventListener("blur", save);
  showSheet("view", key, cardView(card, notes, status), {
    onClose: save,
    busy: () => timer !== null || document.activeElement === notes,
    unsaved: () => timer !== null,
  });
}

function cardView(card, notes, status) {
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
  const frag = document.createDocumentFragment();
  frag.append(...[
    sheetHead(card.title, card.key, h("button", { type: "button", text: "Edit", onclick: () => openCardForm(card.key) })),
    h("div", { class: "facts" },
      h("div", { class: "fact" }, h("label", { class: "lbl", for: "card-column", text: "Column" }), column),
      g && fact(b.groupLabel, groupLink(g)),
      card.fields.map((f) => fact(f.label, rich(f.value)))),
    card.blocked && h("div", { class: "blocked-box" }, h("strong", { text: "Blocked: " }), rich(card.blocked)),
    card.summary && section("Summary", card.summary, false),
    card.sections.map((s) => section(s.label, s.body, true)),
    !card.summary && !card.sections.length && h("p", { class: "hint", text: "No details yet. Edit the card to add a summary or sections." }),
    checklistsView(card),
    h("div", { class: "sec notes" }, h("label", { class: "lbl", for: "card-notes", text: "Notes" }), notes, status),
    h("p", { class: "hint", text: `Created ${when(card.created)}. Updated ${when(card.updated)}.` }),
  ].flat().filter(Boolean));
  return frag;
}

// ---- checklists --------------------------------------------------------------------------------

const tickable = (cl) => cl.items.filter((i) => !i.heading);

function progress(card) {
  const ids = new Set(state.board.checklists.flatMap(tickable).map((i) => i.id));
  return { done: card.checks.filter((id) => ids.has(id)).length, total: ids.size };
}

// The board's checklists, ticked separately on every card. Each tick saves straight away.
function checklistsView(card) {
  const ticked = new Set(card.checks);
  const save = () => send("PATCH", `/cards/${card.key}`, { checks: [...ticked] }, { retry: true }).catch((e) => {
    toast(`Ticks on ${card.key} weren't saved: ${e.message}`, { error: true });
    if (sheet.mode === "view" && sheet.key === card.key) openCard(card.key);  // show what the server has
  });
  return state.board.checklists.filter((cl) => tickable(cl).length).map((cl) => {
    const tally = h("span", { class: "count" });
    const recount = () => { tally.textContent = `${tickable(cl).filter((i) => ticked.has(i.id)).length}/${tickable(cl).length}`; };
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
          save();
        },
      });
      list.append(h("li", {}, h("label", {}, box, h("span", {}, rich(item.text)))));
    }
    recount();
    return h("div", { class: "sec checklist" }, h("h3", {}, cl.name, " ", tally), blocks);
  });
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

function openCardForm(key, defaults = {}) {
  const b = state.board;
  const card = key ? cardBy(key) : null;
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
      onclick: () => confirmBox(actions, `Delete ${card.key}? This can't be undone.`, "Delete", async () => {
        await send("DELETE", `/cards/${card.key}`);
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
      if (ranks.length) values.rank = Math.max(...ranks) + 1;
    }
    submit.disabled = true;
    try {
      if (card) {
        await send("PATCH", `/cards/${card.key}`, values);
        sheet.dirty = false;
        openCard(card.key);
      } else {
        const data = await send("POST", "/cards", values);
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
  const colCards = countBy(b.cards, (c) => c.column);
  const groupCards = countBy(b.cards, (c) => c.group);
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
    row.append(
      field("Column", h("input", { class: "c-name", maxLength: 40, value: c.name })),
      field("Limit", h("input", { class: "c-limit", type: "number", min: 1, max: 999, step: 1, value: c.limit ?? "", placeholder: "None" })),
      h("div", { class: "mini" },
        h("button", { type: "button", class: "icon", title: "Move up", "aria-label": "Move up", text: "↑",
          onclick: () => { row.previousElementSibling?.before(row); dirty(); } }),
        h("button", { type: "button", class: "icon", title: "Move down", "aria-label": "Move down", text: "↓",
          onclick: () => { row.nextElementSibling?.after(row); dirty(); } }),
        h("button", { type: "button", class: "danger", text: "Remove", disabled: n > 0,
          title: n ? `Move its ${plural(n, "card")} out first` : null, onclick: () => { row.remove(); dirty(); } })));
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
        h("button", { type: "button", class: "danger", text: "Remove", disabled: n > 0,
          title: n ? `${plural(n, "card")} still belong to it` : null, onclick: () => { row.remove(); dirty(); } })),
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
      onclick: () => confirmBox(actions, `Delete ${b.name} and its ${plural(b.cards.length, "card")}? This can't be undone.`,
        "Delete board", async () => {
          await send("DELETE", "");
          sheet.dirty = false;
          closeSheet(true);
          await loadBoards();
          state.board = null;
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
      changes = {
        name: name.value, description: desc.value, prefix: prefix.value.trim(), groupLabel: groupLabel.value,
        template: template.value.split("\n").map((l) => l.trim()).filter(Boolean),
        columns: [...cols.children].map((r) => {
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
      state.boards.push({ id: board.id, name: board.name, cards: 0 });
      state.board = board;
      state.groups.clear();
      remember(board.id);
      sheet.dirty = false;
      closeSheet(true);
      setHash(board.id);
      render();
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
    h("div", { class: "form-actions" }, submit, h("button", { type: "button", text: "Cancel", onclick: () => closeSheet() })));
  showSheet("new-board", null, h("div", { class: "form" }, sheetHead("New board"), form));
}

// ---- start ------------------------------------------------------------------------------------

// Pick up changes made elsewhere (another tab, the file itself) when the window comes back.
async function refresh() {
  if (!state.board || pending || state.drag || (sheet.mode && sheet.mode !== "view")) return;
  try {
    await loadBoards();
    if (!state.boards.some((b) => b.id === state.board.id)) {
      state.board = null;
      await route();
      return;
    }
    const fresh = (await api("GET", `/api/boards/${state.board.id}`)).board;
    if (fresh.rev <= state.board.rev) {
      renderPicker();
      return;
    }
    state.board = fresh;
    render();
    if (sheet.mode === "view" && !sheet.busy?.()) {
      if (cardBy(sheet.key)) openCard(sheet.key);
      else {
        closeSheet(true);
        toast("That card was deleted somewhere else.");
      }
    }
  } catch {
    // The server may be stopped; the next change will say so.
  }
}

function bindPage() {
  $("#new-card").addEventListener("click", () => openCardForm(null));
  $("#board-settings").addEventListener("click", openSettings);
  $("#new-board").addEventListener("click", openNewBoard);
  $("#board-pick").addEventListener("change", (e) => { if (e.target.value) location.hash = e.target.value; });
  const search = $("#search");
  search.addEventListener("input", () => {
    state.query = search.value;
    renderColumns();
  });
  search.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && search.value) {
      search.value = "";
      state.query = "";
      renderColumns();
    }
  });
  document.addEventListener("keydown", (e) => {
    if ($("#sheet").open || e.ctrlKey || e.metaKey || e.altKey || e.target.closest?.("input, textarea, select")) return;
    if (e.key === "/") {
      e.preventDefault();
      search.focus();
    } else if (e.key === "n" && state.board) {
      e.preventDefault();
      openCardForm(null);
    }
  });
  window.addEventListener("hashchange", route);
  window.addEventListener("focus", refresh);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  window.addEventListener("beforeunload", (e) => {
    if (sheet.dirty || pending || sheet.unsaved?.()) e.preventDefault();
  });
}

async function start() {
  bindPage();
  bindSheet();
  bindDrag();
  try {
    await loadBoards();
  } catch (e) {
    toast(e.message, { error: true });
    return;
  }
  await route();
}

start();
