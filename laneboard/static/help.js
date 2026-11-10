// The keyboard shortcuts and help sheet, with the search language in short (the README's Search section in full).

import { APP_VERSION, state } from "./state.js";
import { h, sheetHead, showSheet } from "./dom.js";
import { openBackups } from "./backups.js";

const KEYS = [
  ["Ctrl + K or Cmd + K", "Command palette: find any action, a board or a card, and run or open it"],
  ["n", "New card"], ["/", "Search"], ["s", "Sort"], ["v", "Saved views"], ["?", "This page"],
  ["Arrow keys", "Go from card to card"], ["Enter", "Open the card"],
  ["Shift + Up or Down", "Move the card within its column (in manual order)"], ["Shift + Left or Right", "Move the card to the next column"],
  ["Esc", "Close what's open, or clear the search"],
];

const IN_SEARCH = [
  ["Enter", "Search now"],
  ["Shift + Enter", "Search every board"],
  ["Esc", "Clear the search"],
];

const IN_CARD = [
  ["Enter", "In a task or a value: save it. In Add a task: add it"],
  ["Alt + Up or Down", "Move the task you're on up or down"],
  ["Esc", "In a changed task or value: put back what was saved"],
  ["Up and Down", "In the label picker: go through the labels and back to the search box"],
  ["Enter", "In the label picker's search box: tick or untick the first match, or make a new label"],
];

const SEARCH = [
  ["login page", "Cards with both words, anywhere on the card"],
  ['"release notes"', "The words together, as typed"],
  ["bug OR crash", "Cards with either word"],
  ["-wontfix or NOT wontfix", "Cards without the word"],
  ["(bug OR crash) column:review", "Parentheses group words"],
  ["column:review, group:web, label:bug", "Cards in that column, group or label, by name or the start of it"],
  ["key:WEB-3, title:login, notes:token", "Words in just that part of a card"],
  ["is:blocked, is:archived, is:over-limit, is:done", "Blocked, archived, in a column over its limit, in a done column (ticked Done in Board settings, or else the last column)"],
  ["has:notes, has:labels, has:tasks, has:points", "Cards with something there"],
  ["tasks:open, tasks:done, progress:>=50", "Tasks left to do, every task done, at least half ticked off"],
  ["points:>=3, points:2..5, priority:high", "A property compared with a value, or between two"],
  ["sort:-points", "Sort as well; a minus sorts the other way"],
];

export function openHelp() {
  const table = (rows, code = "kbd") => h("table", { class: "keys" }, h("tbody", {}, rows.map(([key, what]) => h("tr", {}, h("td", {}, h(code, { text: key })), h("td", { text: what })))));
  const info = state.health || {};
  showSheet("help", null, h("div", { class: "form" },
    sheetHead("Keyboard shortcuts and help"),
    h("div", { class: "sec" }, h("h3", { text: "Keyboard" }), table(KEYS)),
    h("div", { class: "sec" }, h("h3", { text: "In the search box" }), table(IN_SEARCH)),
    h("div", { class: "sec" }, h("h3", { text: "In an open card" }), table(IN_CARD)),
    h("div", { class: "sec" }, h("h3", { text: "Searching" }),
      h("p", { text: "Capitals and accents don't matter. Archived cards are left out unless you ask for them with is:archived." }),
      table(SEARCH, "code"),
      h("p", { text: "A property is named the way the board names it; capitals, spaces and hyphens don't matter. The group, label and Blocked buttons under the box add their part to the search, and take it out again." }),
      h("p", { text: "Sort orders the cards in each column. While the board is sorted, dragging moves a card to another column but not up or down. Views keeps a search and a sort under a name, and one can be what the board opens with." })),
    h("div", { class: "sec" }, h("h3", { text: "Writing on cards" }),
      h("p", { text: "Wrap code in `backticks` to get a Copy button. Web addresses become links. A section with several lines shows as a list." })),
    h("div", { class: "sec" }, h("h3", { text: "Your data" }),
      h("p", { text: "Everything is in one file on this computer. Nothing is sent anywhere." }),
      info.data && h("p", {}, "Boards file: ", h("code", { text: info.data })),
      info.backups && h("p", {}, "Daily backups: ", h("code", { text: info.backups })),
      h("p", {}, "To go back to an earlier copy, open ", h("button", { type: "button", class: "link", text: "Backups", onclick: openBackups }),
        ", or run the backups command, then the restore command.")),
    h("p", { class: "hint", text: `Laneboard ${info.version || APP_VERSION}` })));
}
