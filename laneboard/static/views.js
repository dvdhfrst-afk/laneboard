// Sorting and saved views. The sort picker orders the cards in each column by a part of the card or a property,
// either way round; the board's own order, set by dragging, is the default. A saved view keeps a search and a sort
// under a name on the board, and one view can be what the board opens with when the address names no search.

import { noteAddress, sheet, state } from "./state.js";
import { $, announce, closeSheet, field, h, sheetHead, showSheet, toast } from "./dom.js";
import { api, send } from "./api.js";
import { fold, markedText, redraw, setQuery } from "./search.js";
import { closeMenu } from "./menu.js";

const CHECK_DELAY = 300;  // ms after typing in a view's search before the server checks it

// ---- the sort picker ---------------------------------------------------------------------------

const PARTS = [["title", "Title"], ["key", "Key"], ["created", "Created"], ["updated", "Updated"], ["progress", "Progress"]];

// What the board can be sorted by: [value, label], manual order first.
function sortFields() {
  const b = state.board;
  return [["", "Manual order"], ...PARTS, ...(b.groups.length ? [["group", b.groupLabel]] : []), ...b.properties.map((p) => [p.id, p.name])];
}

// The words for each way round a sort can go, or null when there's only one way (a checkbox: ticked cards first).
function directions(name) {
  const type = state.board.properties.find((p) => p.id === name)?.type ?? name;
  if (type === "checkbox") return null;
  if (type === "created" || type === "updated") return ["Oldest first", "Newest first"];
  if (type === "number" || type === "rating" || type === "progress") return ["Lowest first", "Highest first"];
  if (type === "choice" || type === "multi" || type === "group") return ["In list order", "Reverse order"];
  return ["A to Z", "Z to A"];
}

// A sort string the picker can show: {name, label, descending}. Null for anything it can't, like two names
// separated by a comma, which shows as typed.
function readSort(sort) {
  const text = sort.trim();
  if (!text) return { name: "", label: "Manual order", descending: false };
  const m = text.match(/^([+-]?)\s*([^,]+?)$/);
  if (!m) return null;
  const want = fold(m[2]);
  if (want === "rank") return m[1] === "-" ? null : readSort("");
  const found = sortFields().find(([value, label]) => value && (fold(value) === want || fold(label) === want));
  return found ? { name: found[0], label: found[1], descending: m[1] === "-" } : null;
}

// How a sort reads in a sentence, like "Points, highest first"; empty for manual order.
export function sortName(sort) {
  const read = readSort(sort);
  if (!read) return sort.trim();
  if (!read.name) return "";
  const ways = directions(read.name);
  return ways ? `${read.label}, ${ways[read.descending ? 1 : 0].toLowerCase()}` : read.label;
}

let sortSignature = "";
export function renderSort() {
  const pick = $("#sort-by");
  const dir = $("#sort-dir");
  const read = readSort(state.sort);
  const fields = sortFields();
  const signature = JSON.stringify([fields, read ? null : state.sort]);
  if (signature !== sortSignature) {  // redrawing the list closes it if it's open
    sortSignature = signature;
    pick.replaceChildren(...fields.map(([value, text]) => h("option", { value, text })),
      ...(read ? [] : [h("option", { value: state.sort, text: state.sort.trim() })]));
  }
  pick.value = read ? read.name : state.sort;
  const ways = read?.name ? directions(read.name) : null;
  dir.hidden = !ways;
  if (ways) {
    const now = ways[read.descending ? 1 : 0];
    dir.textContent = now;
    dir.setAttribute("aria-label", `${now}. Press to reverse the order.`);
    dir.title = "Reverse the order";
  }
}

export function bindSort() {
  const pick = $("#sort-by");
  pick.addEventListener("change", () => {
    const name = pick.value;
    const custom = !sortFields().some(([value]) => value === name);
    if (custom) return;
    const newestFirst = name === "created" || name === "updated";
    setQuery(state.query, { sort: name ? `${newestFirst ? "-" : ""}${name}` : "" });
    announce(name ? `Sorted by ${sortName(state.sort)}.` : "Cards are in manual order.");
  });
  $("#sort-dir").addEventListener("click", () => {
    const read = readSort(state.sort);
    if (!read?.name) return;
    setQuery(state.query, { sort: `${read.descending ? "" : "-"}${read.name}` });
    announce(`Sorted by ${sortName(state.sort)}.`);
  });
}

// ---- saved views -------------------------------------------------------------------------------

// The view the board is showing: the one last picked, `changed` if the search or sort has moved on since, or else
// any view that matches what's showing.
function currentView() {
  const views = state.board.views;
  const picked = views.find((v) => v.id === state.viewId);
  if (picked) return { view: picked, changed: picked.query !== state.query || picked.sort !== state.sort };
  const same = views.find((v) => v.query === state.query && v.sort === state.sort);
  return same ? { view: same, changed: false } : null;
}

// What a view does, in a few words.
function viewSummary(query, sort) {
  const by = sortName(sort);
  return [query.trim() ? query.trim() : "Every card", by && `sorted by ${by}`].filter(Boolean).join(", ");
}

export function useView(view) {
  closeMenu();
  setQuery(view.query, { sort: view.sort, viewId: view.id });
  announce(`Showing the view ${view.name}.`);
  $("#views-summary").focus();
}

export function renderViews() {
  const b = state.board;
  const now = currentView();
  const summary = $("#views-summary");
  summary.replaceChildren(h("span", { class: "sr", text: "Saved views: " }),
    now ? `${now.view.name}${now.changed ? " (changed)" : ""}` : "Views");
  summary.title = now ? viewSummary(now.view.query, now.view.sort) : "Saved views";
  const item = (text, run, extra = {}) => h("button", { type: "button", ...extra, onclick: () => { closeMenu(); run(); } }, text);
  const items = b.views.map((v) => h("button", {
    type: "button", class: "view-item", "aria-current": now && !now.changed && now.view === v ? "true" : null, onclick: () => useView(v),
  }, h("span", { class: "view-name" }, v.name, b.settings.defaultView === v.id ? h("span", { class: "hint", text: " (opens with this)" }) : ""),
  h("span", { class: "hint view-what", text: viewSummary(v.query, v.sort) })));
  if (!items.length) items.push(h("p", { class: "hint menu-note", text: "No saved views yet. Search or sort the board, then save it as a view." }));
  $("#view-items").replaceChildren(...[...items, h("hr"),
    item("Save view…", openSaveView),
    now?.changed && item(`Update ${now.view.name} to this search`, () => updateView(now.view)),
    b.views.length > 0 && item("Manage views…", () => openViews())].filter(Boolean));
}

async function updateView(view) {
  const query = state.query;
  const sort = state.sort;
  try {
    await send("PATCH", "", (board) => ({ views: (board || state.board).views.map((v) => (v.id === view.id ? { ...v, query, sort } : v)) }), { retry: true });
    state.viewId = view.id;
    noteAddress();
    redraw();
    toast(`Updated the view ${view.name}.`);
  } catch (e) {
    toast(e.message, { error: true });
  }
}

// A search and sort as the server sees them, for a view: null if this board can read them, or {message, position,
// part}. The server is the only reader of the search language, so a view is checked by running it.
async function checkSearch(query, sort) {
  try {
    await api("GET", `/api/boards/${encodeURIComponent(state.board.id)}/search?${new URLSearchParams({ q: query, sort })}`);
    return null;
  } catch (e) {
    if (e.status === 400 && e.detail?.part) return { message: e.message, position: e.detail.position, part: e.detail.part };
    throw e;
  }
}

// What's wrong with a view's search or sort, with the spot marked.
function problem(err, query, sort, intro = "") {
  const text = err.part === "sort" ? sort : query;
  return [h("span", { text: `${intro}${err.part === "sort" ? "Sort: " : ""}${err.message}` }), text.trim() ? markedText(text, err.position) : ""];
}

export function openSaveView() {
  const query = state.query;
  const sort = state.sort;
  const name = h("input", { maxLength: 60, autofocus: true, placeholder: "Open bugs" });
  const opens = h("input", { type: "checkbox" });
  const error = h("div", { class: "form-error", role: "alert" });
  const submit = h("button", { type: "submit", class: "primary", text: "Save view" });
  const onsubmit = async (e) => {
    e.preventDefault();
    error.replaceChildren();
    submit.disabled = true;
    const wanted = name.value.trim();
    try {
      const data = await send("PATCH", "", (board) => ({
        views: [...(board || state.board).views, { name: name.value, query, sort }],
        ...(opens.checked ? { settings: { defaultView: wanted } } : {}),
      }), { retry: true });
      const view = data.board.views.find((v) => v.name.toLowerCase() === wanted.toLowerCase());
      sheet.dirty = false;
      closeSheet(true);
      if (view && state.query === query && state.sort === sort) state.viewId = view.id;
      noteAddress();
      redraw();
      toast(`Saved the view ${wanted}.`);
    } catch (err) {
      error.replaceChildren(...(err.status === 400 && err.detail?.part ? problem({ ...err.detail, message: err.message }, query, sort) : [err.message]));
    } finally {
      submit.disabled = false;
    }
  };
  const by = sortName(sort);
  const form = h("form", { class: "form", novalidate: true, oninput: () => { sheet.dirty = true; }, onsubmit },
    field("Name", name),
    h("div", { class: "facts" },
      h("div", { class: "fact" }, h("span", { class: "lbl", text: "Search" }), query.trim() ? h("code", { class: "query-text", text: query }) : h("span", { text: "Every card" })),
      h("div", { class: "fact" }, h("span", { class: "lbl", text: "Sort" }), h("span", { text: by || "Manual order" }))),
    h("label", { class: "check-line" }, opens, "Open the board with this view"),
    h("p", { class: "hint", text: "A view keeps the search and sort as they are now. Pick it again from Views by the search box." }),
    error,
    h("div", { class: "form-actions" }, submit, h("button", { type: "button", text: "Cancel", onclick: () => closeSheet() })));
  showSheet("save-view", null, h("div", { class: "form" }, sheetHead("Save view"), form));
}

// Every saved view in one form: rename, change the search or sort, pick the one the board opens with, remove. Each
// search and sort is checked by the server as it's typed, and a view that stopped working says why when this opens.
export function openViews(focusId = null) {
  const b = state.board;
  const dirty = () => { sheet.dirty = true; };
  const rows = h("div", { class: "list-rows" });
  const none = h("input", { type: "radio", name: "default-view", value: "", checked: !b.settings.defaultView });
  const entries = [];
  const add = (v) => {
    const name = h("input", { class: "v-name", maxLength: 60, value: v.name });
    const query = h("input", { class: "v-query", maxLength: 500, value: v.query, spellcheck: false, autocomplete: "off" });
    const sort = h("input", { class: "v-sort", maxLength: 200, value: v.sort, spellcheck: false, autocomplete: "off", placeholder: "Manual order" });
    const opens = h("input", { type: "radio", name: "default-view", value: v.id, checked: b.settings.defaultView === v.id });
    const said = h("div", { class: "form-error", role: "alert" });
    const row = h("div", { class: "list-row view-row", "data-id": v.id });
    const entry = { view: v, row, name, query, sort, opens, said, error: null, checked: null, removed: false };
    let timer = null;
    const check = async () => {
      clearTimeout(timer);
      const at = [query.value, sort.value].join("\u0000");
      if (entry.checked === at) return entry.error;
      try {
        const err = await checkSearch(query.value, sort.value);
        if ([query.value, sort.value].join("\u0000") !== at) return check();  // typed on meanwhile
        entry.checked = at;
        entry.error = err;
        said.replaceChildren(...(err ? problem(err, query.value, sort.value, query.value === v.query && sort.value === v.sort ? "This view doesn't work any more. " : "") : []));
        query.setAttribute("aria-invalid", String(err?.part === "query"));
        sort.setAttribute("aria-invalid", String(err?.part === "sort"));
        return err;
      } catch (e) {
        said.textContent = e.message;
        return null;
      }
    };
    const later = () => {
      clearTimeout(timer);
      timer = setTimeout(check, CHECK_DELAY);
    };
    query.addEventListener("input", later);
    sort.addEventListener("input", later);
    entry.check = check;
    row.append(
      h("div", { class: "row2" }, field("Name", name), h("label", { class: "check-line opens" }, opens, "Open the board with this view")),
      field("Search", query), field("Sort", sort, "Like title or -points: a minus sorts the other way. Commas add more."),
      said,
      h("div", { class: "mini" },
        h("button", { type: "button", text: "Use the search showing now", onclick: () => {
          query.value = state.query;
          sort.value = state.sort;
          dirty();
          check();
        } }),
        h("button", { type: "button", class: "danger", text: "Remove", onclick: () => {
          entry.removed = true;
          row.remove();
          if (opens.checked) none.checked = true;
          dirty();
        } })));
    rows.append(row);
    entries.push(entry);
  };
  b.views.forEach(add);
  const error = h("div", { class: "form-error", role: "alert" });
  const submit = h("button", { type: "submit", class: "primary", text: "Save" });
  const onsubmit = async (e) => {
    e.preventDefault();
    error.replaceChildren();
    const keep = entries.filter((x) => !x.removed);
    submit.disabled = true;
    try {
      const errors = await Promise.all(keep.map((x) => x.check()));
      const bad = keep.find((x, i) => errors[i]);
      if (bad) {
        error.textContent = `${bad.name.value.trim() || "A view"} can't be saved as it is. The problem is marked under it.`;
        (bad.error.part === "sort" ? bad.sort : bad.query).focus();
        return;
      }
      const picked = $('input[name="default-view"]:checked', form)?.value || null;
      const views = keep.map((x) => ({ id: x.view.id, name: x.name.value, query: x.query.value, sort: x.sort.value }));
      await send("PATCH", "", { views, settings: { defaultView: picked } });
      if (!keep.some((x) => x.view.id === state.viewId)) state.viewId = null;
      sheet.dirty = false;
      closeSheet(true);
      noteAddress();
      redraw();
      toast("Saved views saved.");
    } catch (err) {
      error.textContent = err.message;
    } finally {
      submit.disabled = false;
    }
  };
  const form = h("form", { class: "form", novalidate: true, oninput: dirty, onchange: dirty, onsubmit },
    h("p", { class: "hint", text: "A view keeps a search and a sort. Views are listed under Views by the search box, in this order." }),
    rows,
    h("label", { class: "check-line" }, none, "Open the board with every card, in manual order"),
    error,
    h("div", { class: "form-actions" }, submit, h("button", { type: "button", text: "Cancel", onclick: () => closeSheet() })));
  showSheet("views", null, h("div", { class: "form" }, sheetHead("Saved views"), form));
  for (const x of entries) x.check();
  const focus = entries.find((x) => x.view.id === focusId);
  if (focus) {
    focus.query.focus();
    focus.row.scrollIntoView({ block: "nearest" });
  }
}

// ---- opening a board ---------------------------------------------------------------------------

// The search and sort from the address. A view id the board doesn't have is let go; the search still applies.
export function useFilter(filter) {
  const known = state.board.views.some((v) => v.id === filter.view);
  Object.assign(state, { query: filter.query, sort: filter.sort, viewId: known ? filter.view : null });
}

// The board's default view, when it opens with nothing in the address saying otherwise.
export function useDefaultView() {
  const b = state.board;
  const view = b.views.find((v) => v.id === b.settings.defaultView);
  if (view) Object.assign(state, { query: view.query, sort: view.sort, viewId: view.id });
}
