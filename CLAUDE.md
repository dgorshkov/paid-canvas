# PAID canvas

A local, zoomable board for Jira project PAID. `server.py` reads Jira and serves
`index.html`; there is no build step and no dependency beyond Python 3.8.

## Setting it up for a new user

1. Check Python: `python3 --version` (3.8 or newer).
2. Run `./run check`. If it prints an account name and a board name, credentials already work
   — the user has the Atlassian CLI signed in — and you can skip to step 4.
3. Otherwise run `./run setup`. It is interactive. Point the user at
   https://id.atlassian.com/manage-profile/security/api-tokens, where they click **Create API
   token** (the plain one, not the scoped one), name it, pick an expiry and copy it — Atlassian
   shows it once. `./run setup` then asks for their Atlassian email and that token, verifies
   both against Jira, and writes `config.json` (git-ignored, mode 0600). Have the user paste
   the token into the prompt themselves; never put a token in a command line or in a file you
   write, and never echo it back.
4. `./run` starts the server on 127.0.0.1:8777 and opens the browser.
`team.json` (the squad) and `specialties.json` (who does back, web or mobile work) ship with
the repo; there is nothing to configure in either.

If `./run check` fails with 401 or 403, the token is wrong or the account cannot read board
209. Both are for the user to fix in Jira; there is nothing to change in this repo.

## Layout of the code

- `server.py` — Jira fetch, normalisation, merge requests via Jira's dev-status endpoint, and
  a small HTTP server. Endpoints: `/`, `/api/board`, `/api/progress`, `/api/health`, and
  `POST /api/issue/<KEY>` which sets a priority or runs a transition, then reads the issue
  back and returns what the board needs to move the card.
- `index.html` — the whole client, one file. The board is one `<canvas>` the size of the
  window. `relayout()` places cards in world coordinates, `paint()` draws the ones the
  viewport covers, and `pick()` answers what is under the pointer. Pan and zoom change
  `view` and ask for a frame. The rails, the quick filters, the detail panel and the minimap
  stay DOM.
- `README.md` — what the board shows and every setting.

## Rules

- Never commit `config.json`, `.dev-status.json`, `.type-icons.json` or `.server.log`. They
  are git-ignored; keep them that way. `config.json` holds an email and an API token and
  nothing else.
- The Jira token stays server-side. The browser only ever receives board data.
- `POST /api/issue/<KEY>` writes to real tickets. Never call it to try something out; a status
  change resets that ticket's days-in-status clock, which the board uses to spot stale work.
- Card geometry reserves room for everything a card can show, so zoom never re-lays out the
  board. If you change what is drawn, change the metrics that reserve room for it. Card and
  chip heights come from `wrap()`, which measures the real text, so a metric and its drawing
  have to pass `wrap()` the same width and font or the two disagree.
- Nothing about a card is styled in CSS any more. Its colours are in `C`, its fonts in
  `fnt()`, and its geometry in the constants at the top. `paint()` runs on every frame of a
  gesture, so per-frame work belongs in it only if it is cheap: text is measured once and
  cached, and a run under `TEXT_MIN` on screen is a bar rather than glyphs.
