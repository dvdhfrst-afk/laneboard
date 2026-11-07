# Security

## What Laneboard is protecting

Your boards, on your computer, from other websites you visit. It has no sign-in, so anything that can
reach its port can read and change your boards. Everything below exists to keep that port private.

## What it does

- **Listens on `127.0.0.1` only.** There is no flag to change that. An option to open it up would be
  an option to give the world your boards with no password.
- **Checks the `Host` header.** Requests must be addressed to `127.0.0.1` or `localhost` on its own
  port. This stops DNS rebinding, where a hostile site points its own name at your machine.
- **Refuses cross-site requests.** Requests to the API with a `Sec-Fetch-Site` other than `same-origin`
  or `none`, or an `Origin` other than its own, are refused, reads included. Changes must be sent as
  `application/json`, which a plain web form can't do. There are no CORS headers.
- **Treats card text as hostile.** The page builds everything with `textContent`, never as HTML. Only
  `http` and `https` addresses become links, and a group link of any other kind is refused on import
  and when the file loads.
- **Content security policy.** `default-src 'none'`, with scripts, styles and data only from itself.
  Even if text did get into the page as HTML, its scripts wouldn't run.
- **No paths from requests.** A static file's name must be a plain name in one folder, matched before
  any decoding, so `../` can't get through. Imports never name files on disk, and board names in download
  names are reduced to safe characters.
- **Limits.** Request bodies are capped (4 MB, or 64 MB for an import) and files are checked in full
  before they are used, including how deeply they nest.

## What it doesn't do

- **Other programs on your computer.** Anything running as you, or as another user on a shared
  machine, can reach `127.0.0.1:8642` without a password. On a shared computer, don't use it for
  anything private.
- **Encryption.** `boards.json` and its backups are plain text, with whatever permissions your folder
  gives them.
- **Other browsers' extensions.** A browser extension you have allowed on all sites can read the page.

Don't put it behind a reverse proxy or forward the port to a network. If you need it elsewhere, use an
SSH tunnel, as the README shows, so the connection stays private to you.

## Testing

`tests/test_security.py` checks the header rules, hostile imports and path traversal.
`tests/test_browser.py` loads hostile boards in a real browser and confirms nothing runs.
[docs/testing.md](docs/testing.md) says what wasn't tried. Nobody outside the project has reviewed this.

## Reporting a problem

Use your code host's private vulnerability reporting for this repository if it's turned on, or open an
issue that says you've found a security problem, without the details, and ask for a private way to
send them. Please include the version (`laneboard --version`) and how to reproduce it.
