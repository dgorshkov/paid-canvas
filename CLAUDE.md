# PAID canvas

A local, zoomable board for Jira project PAID. `server.py` reads Jira and serves
`index.html`; there is no build step and no dependency beyond Python 3.8.

## Setting it up for a new user

1. Check Python: `python3 --version` (3.8 or newer).
2. Run `./run check`. If it prints an account name and a board name, credentials already work
   — the user has the Atlassian CLI signed in — and you can skip to step 4.
3. Otherwise run `./run setup`. It is interactive: it asks for an Atlassian email and an API
   token from https://id.atlassian.com/manage-profile/security/api-tokens, verifies them
   against Jira, and writes `config.json` (git-ignored, mode 0600). Ask the user to create the
   token and paste it themselves; never put a token in a command line or a file you write.
4. `./run` starts the server on 127.0.0.1:8777 and opens the browser.
`team.json` ships with the repo and lists the squad; there is nothing to configure there.

If `./run check` fails with 401 or 403, the token is wrong or the account cannot read board
209. Both are for the user to fix in Jira; there is nothing to change in this repo.

## Layout of the code

- `server.py` — Jira fetch, normalisation, merge requests via Jira's dev-status endpoint, and
  a small HTTP server. Endpoints: `/`, `/api/board`, `/api/progress`, `/api/health`.
- `index.html` — the whole client, one file. Canvas layout in world coordinates, pan and zoom
  by CSS transform, two detail levels.
- `README.md` — what the board shows and every setting.

## Rules

- Never commit `config.json`, `.dev-status.json`, `.type-icons.json` or `.server.log`. They
  are git-ignored; keep them that way. `config.json` holds an email and an API token and
  nothing else.
- The Jira token stays server-side. The browser only ever receives board data.
- Card geometry reserves room for everything a card can show, so zoom never re-lays out the
  board. If you change what is drawn, change the metrics that reserve room for it.
