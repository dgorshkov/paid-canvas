# PAID canvas

A zoomable board for Jira project PAID (board 209, "AD board"). Board columns run across the
top, priority swimlanes run down the page, and every linked work item is nested inside its
story card. Zooming changes what is written on the cards, so the same canvas works as a
whole-pipeline overview and as a detailed read of one story.

Everything runs on your own machine. The server talks to Jira with your account and serves the
page to your browser; nothing is deployed and no data leaves your laptop.

## Setup

Python 3.8 or newer is the only requirement. There are no packages to install and no build step.

```bash
git clone <this repo>
cd paid-canvas
./run setup     # asks for your Atlassian email and API token, once
./run           # starts the server and opens the browser
```

`./run setup` wants an API token from
[id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens).
Create one, paste it in with your Atlassian email, and it checks both against Jira before
writing `config.json` next to `server.py` with owner-only permissions. That file is git-ignored
and never leaves your machine.

Already using the Atlassian CLI? Skip setup. The server reads `acli`'s token from the macOS
keychain and its email from `~/.config/acli/jira_config.yaml`.

Prefer environment variables? `JIRA_EMAIL` and `JIRA_TOKEN` override everything else.

You need read access to the PAID project and its board. Open the page before credentials exist
and it tells you which command to run.

### Commands

```bash
./run           # start (or report it is already up) and open the browser
./run stop      # stop the server
./run check     # confirm your credentials can read the board
./run log       # last lines of the server log
./run setup     # store credentials
```

### If it does not work

| Symptom | Cause |
|---|---|
| the page says it has no credentials | run `./run setup` |
| `./run check` reports 401 or 403 | the token is wrong, or your account cannot see the board |
| `needs Python 3.8 or newer` | install Python, or set `PYTHON=/path/to/python3` |
| the board loads but no merge requests appear | the first dev-status pass takes about a minute in the background; refresh after |
| port 8777 is taken | `PAID_CANVAS_PORT=9000 ./run` |

Every setting is listed at the end of this file, and `config.example.json` shows the shape of
the config file.

## The team roster

The People filter shows the squad rather than everyone who has ever touched a PAID ticket.
Without a `team.json` the server works it out: anyone with a role on at least three PAID items
that is not "assignee of a linked ticket". To set the roster by hand, copy
`team.example.json` to `team.json` and put the display names in it. `team.json` is git-ignored,
so your roster stays local.

## Layout

The eleven board columns are fixed vertical bands, named in a rail that stays pinned below the
quick filters. Swimlanes are horizontal rows, one per priority; a swimlane's label pins to the
left edge once its own header scrolls out of sight. A cell holding many cards wraps into
sub-columns, up to three wide, and each column takes the fewest it can while keeping the grid
close to the shape of the screen. A column narrower than its name shows a short form in the rail.
Delivered reaches back 30 days.

A card carries the story key, title, assignee avatar, type, priority and status, and one chip
per linked work item. Titles are never clipped: the card is laid out around the full text.
Assignees are avatars only, with the name on hover.

Colour is carried by pills alone — type, priority and status — and by the status pill on each
chip. Cards and chips have no coloured edges or backgrounds. Every status pill uses the Jira
status category: grey for to-do, amber for in progress, green for done. Merge-request colours
sit apart from that scale: cyan open, grey draft, purple merged, red closed.

Chips cover the five projects that carry implementation: ANDR, IOS, DEV, BACK and LOC.
Everything else a story links to — support tickets, other PAID items, design, ideas — stays in
the detail panel, which lists every link. The merge-request count on a card covers the chips it
shows.

Chips with a solid border are implementation work — outward `created`, `causes`, `blocks`,
`clones` and `split to` links, plus subtasks. Dashed chips are `relates to`, `duplicates` and
inward links.

## Quick filters

Two rows above the board, one click each. People are avatars, epics are named chips, both
ordered by how many items they touch. Clicking one highlights its cards and dims everything
else; nothing is hidden, so the shape of the board and every card's position stay put.

One filter is active at a time. Clicking a second replaces the first, and clicking the active
one clears it, as does the amber button in the toolbar or `Esc`.

A person matches on more than the assignee field: assignee, author of any comment, or assignee
of a linked work item. Filing a ticket does not count, so reporter and creator are ignored.
Each matching card shows an amber badge naming the roles that matched.

The People row holds the squad only, set by `team.json` or worked out from the board. Anyone
can land on a PAID item as the assignee of a linked ticket in another project — a translator on
a LOC ticket, say — and they get no chip.

Everyone else stays reachable. The detail panel lists every person on an item with their roles,
and clicking a name there — or the epic — filters the board by them.

## Status history

The detail panel lists every status the item has passed through with the days it spent there
and who moved it on. The header says how long it has been in the current status, who put it
there, and how old the item is.

## Merge requests

Jira's development panel already links every merge request to its issue, through the GitLab
for Jira app. A chip whose ticket has merge requests carries a coloured strip along its bottom
edge and an `!N` badge; the card's meta row rolls up every merge request beneath it. Cyan is
open, grey is draft, purple is merged, red is closed. The detail panel lists each one with its
repository, author, reviewers, source and target branch and comment count, linked to GitLab.

The dev-status endpoint takes one request per issue and no batch form, so `server.py` caches
the answer per issue in `.dev-status.json` and only asks again when that issue's `updated`
timestamp moves. The first pass covers about a thousand issues in roughly a minute and runs in
the background, so the board works throughout and simply shows fewer merge requests until it
lands. Later refreshes ask about a handful.

The endpoint needs the integration's own instance key as `applicationType`; the obvious
`gitlab` returns an empty result with no error. `PAID_DEV_APP` holds it.

## Zoom levels

A card's box is the same size at every zoom. It reserves room for everything it can ever show,
so zooming never resizes anything or bumps a neighbour. There are two states, with the boundary
at 60%:

| Zoom | What a card carries |
|---|---|
| under 60% | the key alone, chips as status bars |
| 60% and up | title, work-type icon, priority, status, dates, epic, chips with their own titles and status, and every linked merge request inside its chip |

Columns are separated by a wide gap, so two columns that both run three cards wide still read
as two columns. They sit in three bands with a wider gap and a divider between them: everything
before Implementation, everything in flight, and Delivered.

Reserving that room makes the board 25,814 world px tall, so **Fit** lands at 5% where the keys
are too small to read. Around 30% the keys are legible with about a swimlane on screen. The
chips are what drive that height — they carry full titles and up to four merge-request rows each.

## Controls

Drag to pan. `cmd`+scroll or trackpad pinch to zoom; plain scroll pans. `F` fits the board,
`1` returns to reading size, `/` focuses search, `Enter` jumps to the next match, `Esc` clears
the filter and the search. The minimap at bottom right jumps the
view.

Click a card for the detail panel, which lists every linked item with its relationship
("created", "causes", "is caused by"). Click a chip or double-click a card to open Jira.
Search dims the same way a quick filter does, and the two combine.

The active filter persists in the browser.

## Data

`server.py` reads the board from Jira on request and caches it for 180 seconds; **Refresh**
forces a re-fetch and holds your zoom and position, anchored on the card under the middle of the
screen or on the selected card.

While a fetch is in flight every card is drawn as a shimmering skeleton reading `PAID-000`, in
the layout the board already has, so nothing shifts. A cold start has no layout to keep, so a
board-shaped placeholder stands in until the first fetch returns. A thin bar across the top of
the window tracks the real stages, read from `/api/progress`.

One fetch takes about seven seconds. The board issues come from the agile API with comments,
reporters and the full changelog attached, then every linked key outside the board is fetched in parallel batches to
pick up assignees, which the link payloads omit. A link pointing at another item on the board
reads its assignee from the board fetch, with no extra request.

The token never leaves the server; the browser only ever sees board data.

Every setting takes an environment variable, a `config.json` key, or its default, in that
order.

| Variable | config.json | Default |
|---|---|---|
| `JIRA_EMAIL` | `email` | the `acli` account |
| `JIRA_TOKEN` | `token` | the `acli` keychain entry |
| `PAID_JIRA_BASE` | `base` | https://pnlfintech.atlassian.net |
| `PAID_BOARD_ID` | `boardId` | 209 |
| `PAID_CANVAS_PORT` | `port` | 8777 |
| `PAID_CANVAS_TTL` | `cacheTtl` | 180 |
| `PAID_DEV_APP` | `devApp` | oAuth-gitlab-jira-connect-gitlab.com |
| `PAID_DEV_WORKERS` | `devWorkers` | 12 |
| `PAID_TEAM_MIN_ITEMS` | `teamMinItems` | 3 |

`python3 server.py --once out.json` writes a snapshot and exits, without starting a server.
`--check` verifies the credentials, `--setup` stores them.

Three files are written next to `server.py` and none are shared: `config.json` holds the
credentials, `.dev-status.json` caches merge requests per issue, `.type-icons.json` caches the
work-type SVGs. Deleting any of them costs one slower fetch.
