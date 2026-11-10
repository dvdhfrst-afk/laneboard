// What the page knows: the open board and its lookup tables, the revision per board, unsaved notes, what the side
// sheet shows, and the address. Other modules import these; nothing keeps its own copy.

export const APP_VERSION = "1.0.0";
export const COLORS = ["blue", "teal", "violet", "amber", "rose", "green", "slate"];
export const START_COMMAND = "python -m laneboard";
const LAST_BOARD = "laneboard:last-board";

export const state = {
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
export let index = { byKey: new Map(), byColumn: new Map(), groups: new Map(), checkIds: new Set(), archived: [], text: new Map() };

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

const byRank = (a, b) => a.rank - b.rank || a.key.localeCompare(b.key, undefined, { numeric: true });
export const cardBy = (key) => index.byKey.get(key);
export const cardsIn = (column) => index.byColumn.get(column) || [];
export const activeCards = () => state.board.cards.filter((c) => !c.archived);
export const groupOf = (card) => index.groups.get(card.group) || null;

export function setBoard(board) {
  state.board = board;
  latest = board;
  if (board) revs.set(board.id, board.rev);
  reindex();
}

export function reindex() {
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

export function syncListEntry() {
  const entry = state.boards.find((b) => b.id === state.board.id);
  if (entry) Object.assign(entry, { name: state.board.name, cards: activeCards().length, archived: index.archived.length, rev: state.board.rev });
}

// How the last board was being looked at, forgotten when another board opens.
export function resetView() {
  state.groups.clear();
  state.blockedOnly = false;
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

// The address: #<board id> or #<board id>/<card key>.
export function parseHash() {
  let raw = location.hash.slice(1);
  try { raw = decodeURIComponent(raw); } catch { raw = ""; }
  const [board = "", key = ""] = raw.split("/");
  return { board, key };
}

export function setHash(id, key) {
  history.replaceState(null, "", `#${id}${key ? "/" + key : ""}`);
}
