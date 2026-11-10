// The keyboard shortcuts and help sheet.

import { APP_VERSION, state } from "./state.js";
import { h, sheetHead, showSheet } from "./dom.js";

export function openHelp() {
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
