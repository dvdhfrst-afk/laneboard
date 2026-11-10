// Talking to the server: requests, the offline and version checks, and the queue that sends changes one at a time.

import { APP_VERSION, START_COMMAND, latest, remember, revs, setBoard, setLatest, sheet, state, syncListEntry } from "./state.js";
import { renderBanner } from "./dom.js";
import { showBoard } from "./board.js";
import { saveDrafts } from "./card.js";

// An error carries the HTTP status (0 when the server can't be reached) and the whole answer as `detail`, which for
// a search the server can't read says where it stopped. `signal` can cancel a request that's no longer wanted; a
// cancelled one rejects with an AbortError and says nothing about the server being gone. A file (a Blob) is sent as
// it is, for the server to read.
export async function api(method, path, body, rev, signal) {
  const opts = { method, headers: {}, signal };
  if (body instanceof Blob) {
    opts.headers["Content-Type"] = "application/octet-stream";
    opts.body = body;
  } else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = typeof body === "string" ? body : JSON.stringify(body);
  }
  if (rev !== undefined && rev !== null) opts.headers["If-Match"] = String(rev);
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    if (e.name === "AbortError") throw e;
    setOffline(true);
    throw Object.assign(new Error(`Can't reach the board server. Start it again with: ${START_COMMAND}`), { status: 0 });
  }
  setOffline(false);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || `The server answered ${res.status}.`), { status: res.status, detail: data });
  return data;
}

function setOffline(offline) {
  if (state.offline === offline) return;
  state.offline = offline;
  renderBanner();
  if (!offline) {
    checkVersion();
    sheet.retry?.();
    saveDrafts();
  }
}

export async function checkVersion() {
  try {
    const health = await api("GET", "/api/health");
    state.health = health;
    state.versionNote = health.version === APP_VERSION ? null
      : `This page is version ${APP_VERSION} and the running Laneboard is version ${health.version}. Stop Laneboard, start it again, then reload this page.`;
    renderBanner();
  } catch { /* the banner already says the server is gone */ }
}

// Changes run one at a time, so each request carries the revision the previous one produced.
let queue = Promise.resolve();
export let pending = 0;  // changes queued or on their way
function enqueue(task) {
  pending++;
  const run = queue.then(task);
  queue = run.then(() => {}, () => {}).then(() => {
    pending--;
    if (!pending && latest && state.board?.id === latest.id && state.board !== latest) {  // the last change failed
      showBoard(latest);
      sheet.sync?.();
    }
  });
  return run;
}

// Send a change to the open board. `body` can be a function of the board (null if another board
// is open by then), so a retry is worked out from what the server has now.
// On a stale revision the board is reloaded; `retry` (true, or a function of the fresh board
// saying whether it's safe) then re-sends the change, otherwise the caller gets the error.
// `other` is the id of a second board the change also needs the revision of (moving a card there):
// its revision is picked up again on a retry, and kept from the answer's `target`.
export function send(method, path, body, { retry = false, other = null } = {}) {
  const id = state.board.id;
  return enqueue(async () => {
    const open = () => state.board?.id === id;
    const attempt = () => api(method, `/api/boards/${id}${path}`, typeof body === "function" ? body(open() ? latest : null) : body, revs.get(id));
    let data;
    try {
      data = await attempt();
    } catch (e) {
      if (e.status !== 412) throw e;
      const fresh = (await api("GET", `/api/boards/${id}`)).board;
      revs.set(id, fresh.rev);
      if (other) revs.set(other, (await api("GET", `/api/boards/${encodeURIComponent(other)}`)).board.rev);
      if (open()) showBoard(fresh);
      if (retry === true || (retry && retry(fresh))) {
        data = await attempt();
      } else {
        sheet.sync?.();
        throw Object.assign(new Error("This changed while you were editing (in another tab or in the file). "
          + "The board has been reloaded. Save again to keep your version, or cancel to keep the other one."), { status: 412 });
      }
    }
    if (other && data.target) revs.set(other, data.target.rev);
    if (data.board) {
      revs.set(id, data.board.rev);
      if (open()) {
        setLatest(data.board);
        // With more on the way the screen is already ahead of this answer, and drawing it would put things back.
        if (pending === 1) {
          showBoard(data.board);
          sheet.sync?.();
        }
      }
    }
    return data;
  });
}

export async function loadBoards() {
  try {
    state.boards = (await api("GET", "/api/boards")).boards;
    state.problem = null;
    renderBanner();
  } catch (e) {
    if (e.status === 503) {
      state.problem = e.message;
      renderBanner();
    }
    throw e;
  }
}

export async function loadBoard(id) {
  setBoard((await api("GET", `/api/boards/${encodeURIComponent(id)}`)).board);
  syncListEntry();
  remember(id);
}
