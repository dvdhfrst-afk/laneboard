// The archived cards sheet: searching them and restoring them.

import { index, state } from "./state.js";
import { when } from "./util.js";
import { h, sheetHead, showSheet } from "./dom.js";
import { openCard, setArchived } from "./card.js";
import { matchesQuery } from "./search.js";

const ARCHIVE_PAGE = 100;

export function openArchive() {
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
