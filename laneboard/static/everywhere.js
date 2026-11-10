// Searching every board at once: the same search (and sort) run on each board, the cards it finds listed board by
// board. Picking one opens its board with that search in the box and the card open.

import { filterParams, state } from "./state.js";
import { closeSheet, h, sheetHead, showSheet } from "./dom.js";
import { api } from "./api.js";
import { plural } from "./util.js";
import { markedText } from "./search.js";
import { route } from "./nav.js";

const LISTED = 25;         // cards listed per board; the rest are one click away, on the board
const TYPING_DELAY = 200;

export function openEverywhere() {
  const sort = state.sort;
  const box = h("input", { type: "search", id: "everywhere-search", value: state.query, maxLength: 500, autocomplete: "off", spellcheck: false,
    "aria-label": "Search every board", "aria-describedby": "everywhere-status" });
  const status = h("p", { class: "hint", id: "everywhere-status", "aria-live": "polite" });
  const error = h("div", { class: "search-error", hidden: true });
  const list = h("div", { class: "everywhere" });
  const boards = new Map();  // other boards, fetched once while this is open, for their card titles
  let asked = 0;
  let timer = null;
  let controller = null;

  // Open what a link points at: the board, its search and maybe a card. A link opened in a new tab does that too.
  const go = (e) => {
    if (e.button || e.ctrlKey || e.metaKey || e.shiftKey) return;
    e.preventDefault();
    closeSheet(true);
    history.pushState(null, "", e.currentTarget.getAttribute("href"));
    route();
  };

  const draw = (query, results) => {
    const found = results.reduce((n, r) => n + r.keys.length, 0);
    status.textContent = results.length ? `${plural(found, "card")} on ${plural(results.length, "board")}.` : "No card on any board matches.";
    const params = filterParams({ query, sort, viewId: null });
    list.replaceChildren(...results.map((r) => {
      const board = r.board === state.board?.id ? state.board : boards.get(r.board);
      const titles = new Map((board?.cards || []).map((c) => [c.key, c.title]));
      const all = `#${r.board}?${params}`;
      return h("section", { class: "found-board" },
        h("h3", {}, h("a", { href: all, onclick: go, text: r.name }), " ", h("span", { class: "count", text: `${r.keys.length} of ${r.total}` })),
        h("ul", { class: "found-cards" }, r.keys.slice(0, LISTED).map((key) => h("li", {},
          h("a", { href: `#${r.board}/${key}?${params}`, onclick: go }, h("span", { class: "key", text: key }), " ", titles.get(key) || "")))),
        r.keys.length > LISTED && h("a", { href: all, onclick: go, class: "more-link", text: `All ${r.keys.length} on ${r.name}` }));
    }));
  };

  const run = async () => {
    clearTimeout(timer);
    const query = box.value;
    const n = ++asked;
    controller?.abort();
    controller = new AbortController();
    const { signal } = controller;
    error.replaceChildren();
    error.hidden = true;
    if (!query.trim()) {
      list.replaceChildren();
      status.textContent = "Type a search to run it on every board.";
      return;
    }
    status.textContent = "Searching…";
    try {
      const data = await api("GET", `/api/search?${new URLSearchParams({ q: query, sort })}`, undefined, undefined, signal);
      await Promise.all(data.results.filter((r) => r.board !== state.board?.id && !boards.has(r.board)).map(async (r) => {
        try {
          boards.set(r.board, (await api("GET", `/api/boards/${encodeURIComponent(r.board)}`, undefined, undefined, signal)).board);
        } catch (e) {
          if (e.name === "AbortError") throw e;  // a board gone meanwhile just lists keys
        }
      }));
      if (n === asked) draw(query, data.results);
    } catch (e) {
      if (n !== asked || e.name === "AbortError") return;
      list.replaceChildren();
      status.textContent = "";
      const part = e.detail?.part;
      error.hidden = false;
      error.replaceChildren(h("p", { class: "search-message", text: part === "sort" ? `Sort: ${e.message}` : e.message }),
        part ? markedText(part === "sort" ? sort : query, e.detail.position) : "");
    }
  };

  box.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(run, TYPING_DELAY);
  });
  box.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      run();
    }
  });
  showSheet("everywhere", null, h("div", { class: "form" },
    sheetHead("Search every board"),
    box,
    sort.trim() && h("p", { class: "hint", text: "Sorted as this board is. A board without what it sorts by keeps its own order." }),
    status, error, list), { onClose: () => { clearTimeout(timer); controller?.abort(); } });
  box.focus();
  run();
}
