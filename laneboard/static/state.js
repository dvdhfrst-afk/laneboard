// What the page knows: the open board and its lookup tables, the revision per board, unsaved notes, what the side
// sheet shows, and the address. Other modules import these; nothing keeps its own copy.

export const APP_VERSION = "1.1.0";
export const COLORS = ["blue", "teal", "violet", "amber", "rose", "green", "slate"];
export const START_COMMAND = "python -m laneboard";
const LAST_BOARD = "laneboard:last-board";

export const state = {
  boards: [],          // [{id, name, cards, archived, archivedAt, rev}]: every board, archived ones too, in order
  overview: false,     // the boards overview (#/boards) is showing instead of a board
  board: null,         // the open board, as the server last sent it
  query: "",           // the search box, sent to the server as typed; the server reads the search language
  sort: "",            // the sort picker's sort string, like "-points"; empty is the board's own order
  viewId: null,        // the saved view last picked, so it can be updated, or shown as changed
  found: null,         // the server's last good answer: {board, rev, query, sort, keys: Map key -> place, matched, total}
  searchError: null,   // why the server couldn't read the search: {board, rev, query, sort, message, position, part}
  columnNote: null,    // {column, message}: why a card couldn't go into that column, shown at the top of it
  drag: null,          // key of the card being dragged
  shown: new Map(),    // column id -> how many cards to draw
  focusKey: null,      // the card that keyboard focus belongs on
  wantFocus: false,    // put focus on focusKey at the next draw
  offline: false,
  problem: null,       // why the server can't use the boards file, if it can't
  versionNote: null,
};

// Lookup tables for the open board, rebuilt whenever it changes. Search text is filled in as needed.
const emptyIndex = () => ({ byKey: new Map(), byColumn: new Map(), groups: new Map(), labels: new Map(), props: new Map(),
  checkIds: new Set(), done: new Set(), archived: [], text: new Map() });
export let index = emptyIndex();

// The last revision seen per board. A queued change must carry its own board's revision even if
// the person has moved on to another board by the time it runs.
export const revs = new Map();

// The open board as the server last described it. What's on screen can be ahead of this while
// changes are on their way.
export let latest = null;

export function setLatest(board) {
  latest = board;
}

// Notes that couldn't be saved after their card was closed. Nothing typed gets thrown away.
export const drafts = new Map();

// What the side sheet is showing. `dirty` guards unsaved form edits; `onClose` flushes card notes.
export const sheet = { mode: null, key: null, dirty: false, onClose: null, unsaved: null, retry: null, sync: null };

export const byRank = (a, b) => a.rank - b.rank || a.key.localeCompare(b.key, undefined, { numeric: true });
export const cardBy = (key) => index.byKey.get(key);
export const cardsIn = (column) => index.byColumn.get(column) || [];
export const activeCards = () => state.board.cards.filter((c) => !c.archived);
// The columns whose cards count as done: the ones marked done, or the last column when none is. The server
// decides the same way for search (is:done), the summary and the exports.
export function doneColumns(columns) {
  const marked = columns.filter((c) => c.done).map((c) => c.id);
  return new Set(marked.length || !columns.length ? marked : [columns[columns.length - 1].id]);
}
export const isDone = (card) => index.done.has(card.column);
export const groupOf = (card) => index.groups.get(card.group) || null;
export const labelsOf = (ids) => ids.map((id) => index.labels.get(id)).filter(Boolean);

export function setBoard(board) {
  state.board = board;
  latest = board;
  if (board) revs.set(board.id, board.rev);
  reindex();
}

export function reindex() {
  const b = state.board;
  index = emptyIndex();
  if (!b) return;
  for (const col of b.columns) index.byColumn.set(col.id, []);
  index.done = doneColumns(b.columns);
  for (const g of b.groups) index.groups.set(g.id, g);
  for (const label of b.labels) index.labels.set(label.id, label);
  for (const prop of b.properties) index.props.set(prop.id, prop);
  for (const cl of b.checklists) for (const item of cl.items) if (!item.heading) index.checkIds.add(item.id);
  for (const card of b.cards) {
    index.byKey.set(card.key, card);
    if (card.archived) index.archived.push(card);
    else if (index.byColumn.has(card.column)) index.byColumn.get(card.column).push(card);
  }
  for (const list of index.byColumn.values()) list.sort(byRank);
  index.archived.sort((a, x) => String(x.archived).localeCompare(String(a.archived)));
}

export function syncListEntry() {
  const entry = state.boards.find((b) => b.id === state.board.id);
  if (entry) Object.assign(entry, { name: state.board.name, cards: activeCards().length, archived: index.archived.length, rev: state.board.rev });
}

// How the last board was being looked at, forgotten when another board opens.
export function resetView() {
  Object.assign(state, { query: "", sort: "", viewId: null, found: null, searchError: null });
  state.columnNote = null;
  state.shown.clear();
  state.focusKey = null;
}

export function stored(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value === null ? fallback : value;
  } catch { return fallback; }
}

export function store(key, value) {
  try { localStorage.setItem(key, value); } catch { /* storage may be blocked; these are only conveniences */ }
}

export const remember = (id) => store(LAST_BOARD, id);
export const recall = () => stored(LAST_BOARD, null);

// The address: #<board id> or #<board id>/<card key>, then what the board is filtered and sorted by:
// ?q=<search>&sort=<sort>&view=<saved view id>. `filter` is null when the address says nothing about it.
export function parseHash() {
  const raw = location.hash.slice(1);
  const at = raw.indexOf("?");
  let path = at < 0 ? raw : raw.slice(0, at);
  try { path = decodeURIComponent(path); } catch { path = ""; }
  const [board = "", key = ""] = path.split("/");
  let filter = null;
  if (at >= 0) {
    const params = new URLSearchParams(raw.slice(at + 1));
    filter = { query: params.get("q") ?? "", sort: params.get("sort") ?? "", view: params.get("view") || null };
  }
  return { board, key, filter };
}

// The search and sort as the address carries them. A board that opens with a saved view says so when it shows
// everything, so a reload doesn't bring the view back.
export function filterParams({ query, sort, viewId } = state) {
  const params = new URLSearchParams();
  if (viewId) params.set("view", viewId);
  if (query) params.set("q", query);
  if (sort) params.set("sort", sort);
  if (!query && !sort && !viewId && state.board?.settings.defaultView) params.set("q", "");
  return params.toString();
}

export function setHash(id, key) {
  const params = filterParams();
  history.replaceState(null, "", `#${id}${key ? "/" + key : ""}${params ? "?" + params : ""}`);
}

// Keep the address up to date with the search, leaving the open card in it.
export const noteAddress = () => setHash(state.board.id, parseHash().key);
