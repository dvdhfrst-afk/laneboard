// Importing a boards file from this computer. Exports are download links in the More menu.

import { plural } from "./util.js";
import { $, toast } from "./dom.js";
import { api } from "./api.js";
import { goToBoard } from "./nav.js";

const MAX_IMPORT_BYTES = 64000000;

export function chooseImportFile() {
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

export function bindImport() {
  $("#import-file").addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (file) importFile(file);
  });
}
