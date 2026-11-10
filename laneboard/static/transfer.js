// Files in and out. The formats the server reads and writes, the export links in the More menu, and the Import sheet:
// it sends a file to the server, which says what format it is and what it would add; a Laneboard file goes straight
// in, as it always has, and any other file shows its preview first, with a choice of format if the guess was wrong.
// The server reads every file; nothing here parses one.

import { sheet } from "./state.js";
import { plural } from "./util.js";
import { $, askDiscard, closeSheet, h, sheetHead, showSheet, toast } from "./dom.js";
import { api } from "./api.js";
import { goToBoard } from "./nav.js";

const MAX_IMPORT_BYTES = 64000000;

// ---- formats -----------------------------------------------------------------------------------

export let formats = [];  // [{name, title, extension, mime, export, import}] once the server has said
let loading = null;

// Ask the server for its formats once. Resolves to the list, empty if it couldn't be had (the next call asks again).
export function loadFormats() {
  if (formats.length) return Promise.resolve(formats);
  loading = loading || api("GET", "/api/formats").then((d) => {
    formats = d.formats;
    return formats;
  }).catch(() => []).finally(() => { loading = null; });
  return loading;
}

const titleOf = (name) => formats.find((f) => f.name === name)?.title || name;

// The More menu's export links for the open board, one per format the server writes. The server names the file.
export function exportLinks(board, link) {
  return formats.filter((f) => f.export).map((f) => link(`Export this board as ${f.title}`,
    `/api/boards/${encodeURIComponent(board.id)}/export?format=${encodeURIComponent(f.name)}`, f.extension));
}

// ---- the Import sheet --------------------------------------------------------------------------

// Opening the Import sheet, from the More menu, a new board or an empty page.
export function openImport() {
  if (sheet.dirty) askDiscard(showImport);
  else showImport();
}

let parts = null;  // the open Import sheet's parts, or null
let asked = 0;     // numbers the requests, so a file picked later wins over one still on its way

// Put what the sheet shows about the file in place, leaving out the parts that don't apply.
const fill = (...children) => parts.result.replaceChildren(...children.filter(Boolean));

function showImport() {
  const pick = h("button", { type: "button", class: "primary", text: "Choose a file", onclick: () => {
    const input = $("#import-file");
    input.value = "";
    input.click();
  } });
  const reads = h("p", { class: "hint" });
  const result = h("div", { class: "import-result" });
  parts = { pick, reads, result };
  showSheet("import", null, h("div", { class: "form" },
    sheetHead("Import a file"),
    h("p", { text: "Adds the boards in a file as new boards. Nothing on your other boards changes." }),
    reads,
    h("div", { class: "form-actions" }, pick, h("button", { type: "button", text: "Cancel", onclick: () => closeSheet() })),
    result), { onClose: () => { parts = null; asked++; } });
  loadFormats().then(() => {
    const titles = formats.filter((f) => f.import).map((f) => f.title);
    reads.textContent = titles.length ? `Formats it reads: ${titles.join(", ")}. A Laneboard file goes straight in. Any `
      + "other file shows what it would add first, and you can pick another format if the guess is wrong." : "";
  });
  pick.focus();
}

// A file was picked: find out what it is and what it would add.
async function readFile(file) {
  if (!parts) showImport();
  const n = ++asked;
  parts.pick.textContent = "Choose another file";
  fill(h("p", { class: "hint", text: `Reading ${file.name}…` }));
  if (file.size > MAX_IMPORT_BYTES) {
    refuse(file, "It's too large to import here. Use the import command instead.");
    return;
  }
  try {
    const preview = await send(file, "auto", true);
    if (n !== asked) return;
    if (preview.format === "laneboard" && !preview.warnings.length) {
      await add(file, "laneboard");
      return;
    }
    await loadFormats();
    if (n === asked) showPreview(file, preview);
  } catch (e) {
    if (n === asked) refuse(file, e.message, true);
  }
}

function send(file, format, preview) {
  const params = new URLSearchParams({ format, filename: file.name, preview: preview ? "1" : "0" });
  return api("POST", `/api/import?${params}`, file);
}

// The server's reason goes in the sheet, and a short message says the file wasn't added. `choose` offers the
// formats, to try reading it another way.
function refuse(file, message, choose = false) {
  toast(`Couldn't import ${file.name}.`, { error: true });
  if (!parts) return;
  fill(h("p", { class: "form-error", role: "alert", text: `${file.name} can't be imported. ${message}` }),
    choose && formatPicker(file, null));
}

// What adding the file would add: each board with its columns and cards, its labels and properties, and what was
// left out on the way in. Picking another format reads the file again as that.
function showPreview(file, preview, chosen = null) {
  const addButton = h("button", { type: "button", class: "primary",
    text: preview.boards.length === 1 ? "Add this board" : `Add ${preview.boards.length} boards`,
    onclick: async () => {
      addButton.disabled = true;
      try {
        await addAs(file, preview.format);
      } finally {
        addButton.disabled = false;
      }
    } });
  fill(
    h("h3", { class: "import-file", text: file.name }),
    formatPicker(file, preview.format, !chosen),
    ...preview.boards.map(boardPreview),
    preview.warnings.length > 0 && h("div", { class: "import-warnings" },
      h("h4", { text: "Left out or changed on the way in" }),
      h("ul", {}, preview.warnings.map((w) => h("li", { text: w })))),
    h("div", { class: "form-actions" }, addButton));
}

function boardPreview(b) {
  const active = b.cards - b.archived;
  return h("section", { class: "import-board" },
    h("h4", { text: b.name }),
    h("p", { class: "hint", text: `${plural(active, "card")}${b.archived ? `, and ${b.archived} archived` : ""}` }),
    h("ul", { class: "import-columns" }, b.columns.map((c) => h("li", {}, h("span", { text: c.name }), h("span", { class: "count", text: String(c.cards) })))),
    b.labels.length > 0 && h("p", { class: "import-line" }, h("span", { class: "lbl", text: "Labels" }), " ", b.labels.join(", ")),
    b.properties.length > 0 && h("p", { class: "import-line" }, h("span", { class: "lbl", text: "Properties" }), " ",
      b.properties.map((p) => p.name).join(", ")));
}

// "Read it as": every format the server reads. `found` is the one the file was read as (null if none worked), and
// `guessed` says the server picked it.
function formatPicker(file, found, guessed = false) {
  const select = h("select", { id: "import-format" },
    !found && h("option", { value: "", text: "Pick a format", disabled: true, selected: true }),
    formats.filter((f) => f.import).map((f) => h("option", { value: f.name, text: f.title, selected: f.name === found })));
  select.addEventListener("change", async () => {
    const n = ++asked;
    const format = select.value;
    select.disabled = true;
    try {
      const preview = await send(file, format, true);
      if (n !== asked) return;
      showPreview(file, preview, format);
      $("#import-format")?.focus();
    } catch (e) {
      if (n !== asked) return;
      fill(
        h("h3", { class: "import-file", text: file.name }),
        formatPicker(file, format),
        h("p", { class: "form-error", role: "alert", text: `Read as ${titleOf(format)}, it can't be imported. ${e.message}` }));
      $("#import-format")?.focus();
    }
  });
  return h("label", { class: "field" }, h("span", { class: "lbl", text: "Read it as" }), select,
    found && guessed && h("span", { class: "hint", text: "Found from the file. Pick another format if that's wrong." }));
}

async function addAs(file, format) {
  try {
    await add(file, format);
  } catch (e) {
    refuse(file, e.message, true);
  }
}

// Add the file's boards for real and open the first.
async function add(file, format) {
  const data = await send(file, format, false);
  await goToBoard(data.boards[0].id);
  const names = data.boards.map((b) => `${b.name} (${plural(b.cards, "card")})`).join(", ");
  toast(format === "laneboard" ? `Imported ${names}.` : `Imported ${names}, read as ${titleOf(data.format)}.`);
}

export function bindImport() {
  $("#import-file").addEventListener("change", (e) => {
    const file = e.target.files[0];
    e.target.value = "";  // so picking the same file again, after changing it, reads it again
    if (file) readFile(file);
  });
}
