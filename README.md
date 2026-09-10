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
git clone git@github.com:dgorshkov/paid-canvas.git
cd paid-canvas
./run setup     # asks for your Atlassian email and API token, once
./run           # starts the server and opens the browser
```

### Getting a Jira API token

1. Open **<https://id.atlassian.com/manage-profile/security/api-tokens>** while signed in to
   your Atlassian account.
2. Click **Create API token**. If the page offers a scoped token as well, take the plain one —
   this tool signs in with your email and the token, and the plain token covers it.
3. Name it something you will recognise later, `paid-canvas` for instance, and pick an expiry.
4. Copy the token. Atlassian shows it once and never again; if you lose it, delete that token
   and make another.
5. Run `./run setup` and paste your Atlassian email and the token when it asks.

The token is your Jira account, so treat it like a password. `./run setup` checks it against
Jira, then writes it to `config.json` next to `server.py` with owner-only permissions. That
file is git-ignored and never leaves your machine. Revoke a token any time on the same page.

Already using the Atlassian CLI? Skip setup. The server reads `acli`'s token from the macOS
keychain and its email from `~/.config/acli/jira_config.yaml`.

Prefer environment variables? `JIRA_EMAIL` and `JIRA_TOKEN` override everything else.
`config.json` holds those two values and nothing else; the board, the Jira site and everything
else are the same for the whole squad and live in `server.py`.

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

## The team roster

`team.json` lists the squad, and the People filter shows those people only. Edit it to add or
remove someone. Delete it and the server works the roster out instead: anyone with a role on at
least three PAID items that is not "assignee of a linked ticket".

## Layout

The eleven board columns are fixed vertical bands, named in a rail that stays pinned below the
quick filters. Swimlanes are horizontal rows, one per priority; a swimlane's label pins to the
left edge once its own header scrolls out of sight. A cell holding many cards wraps into
sub-columns, up to four wide, and each column takes the fewest it can while keeping the grid
close to the shape of the screen. Delivered may go ten wide, so the archive reads as a band
rather than a tower. A column narrower than its name shows a short form in the rail.
Delivered reaches back 30 days.

A card carries the story key, title, assignee avatar, type, priority and status, and one chip
per linked work item. Titles are never clipped: the card is laid out around the full text.
Assignees are avatars only, with the name on hover.

Colour is carried by pills alone — type, priority and status — by the status pill on each
chip, and far out by the bar that stands in for the chips. Cards and chips have no coloured
edges or backgrounds. Every status pill uses the Jira status category: grey for to-do, amber
for in progress, green for done. Merge-request colours
sit apart from that scale: cyan open, grey draft, purple merged, red closed.

Chips cover the five projects that carry implementation: ANDR, IOS, DEV, BACK and LOC.
Everything else a story links to — support tickets, other PAID items, design, ideas — stays in
the detail panel, which lists every link. The merge-request count on a card covers the chips it
shows.

Chips with a solid border are implementation work — outward `created`, `causes`, `blocks`,
`clones` and `split to` links, plus subtasks. Dashed chips are `relates to`, `duplicates` and
inward links.

## Quick filters

Two rows above the board, one click each. People and craft share the first row, epics have the
second. People are avatars in last-name order, so a face keeps its place whatever the board is
doing. Epics are named chips in priority order, and epics of equal priority run by how many items
they touch. Clicking one highlights its cards and dims everything else; nothing is hidden,
so the shape of the board and every card's position stay put.

One filter is active at a time, whichever row it comes from. Clicking a second replaces the
first, and clicking the active one clears it, as does the amber button in the toolbar or `Esc`.

**Craft** stands in for a field Jira does not have. There is no "which repositories does this
touch" on a PAID ticket, so the board reads it from who is assigned: pick `back`, `web` or
`mobile` and it highlights every item where the assignee, or the assignee of one of its linked
tickets, does that kind of work. A PAID story often waits unassigned while its BACK ticket is
already taken, which is why linked tickets count. Each matching card names the person who
matched.

The dashed circle at the end of the avatars highlights everything with no assignee at all —
73 cards, 54 of them in Ready.

`specialties.json` holds the mapping. It ships with the repo:

```json
{
  "back":   ["Andrey Gurev", "Oleg Belovandreev", "Michael Zamaraev", "Toghrul Mirzayev"],
  "web":    ["Aleksandr Opekunov"],
  "mobile": ["Andrey Dovzhenko", "Marina Vasilova"]
}
```

Anyone left out of every group is never matched by a craft filter. Add or move a name and hit
Refresh.

A person matches on more than the assignee field: assignee, author of any comment, or assignee
of a linked work item in one of the five implementation projects. Filing a ticket does not
count, so reporter and creator are ignored, and neither does holding a linked idea or support
ticket — if the board does not draw the chip, its assignee does not put you on the card. Each
matching card shows an amber badge naming the roles that matched.

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
| under 60% | the title, the work-type icon, the assignee, and one bar along the bottom edge with a segment per chip in its status colour |
| 60% and up | title, work-type icon, priority, status, dates, epic, chips with their own titles and status, and every linked merge request inside its chip |

Far out, the title is written across everything the card holds at reading size: its pills, its
dates and the whole chip stack, which collapses into that bar. Every card writes it at one
size, so the board reads as one page of titles rather than a mix of headlines. A card is tall
because its title is long or it carries a lot of linked work, so the room grows with what has
to go into it; a title longer than its card can hold ends in an ellipsis.

Columns are separated by a wide gap, so two columns that both run three cards wide still read
as two columns. They sit in three bands with a wider gap and a divider between them: everything
before Implementation, everything in flight, and Delivered.

Reserving that room makes the board 25,814 world px tall, so **Fit** lands at 5% where the
titles are too small to read. Around 25% they are legible with about a swimlane on screen. The
chips are what drive that height — they carry full titles and up to four merge-request rows each.

## Controls

Drag to pan. `cmd`+scroll or trackpad pinch to zoom; plain scroll pans. `F` fits the board,
`1` returns to reading size, `/` focuses search, `Enter` jumps to the next match, `Esc` clears
the filter and the search. The minimap at bottom right jumps the view.

Click a card for the detail panel, which lists every linked item with its relationship
("created", "causes", "is caused by"). Click a chip or double-click a card to open Jira.

## Changing a ticket

The detail panel has a priority and a status dropdown, and both write straight to Jira. The
workflow on this project is permissive, so every status is reachable from every other one and
the same twelve transitions apply to every ticket. Statuses are listed in board order, left
column to right, with Discarded last because no column shows it. Priorities run Highest to
Lowest.

On success the board re-reads that one issue from Jira and moves the card to its new column and
swimlane. The card stays selected and the camera does not move: an edit reflows the board, and
holding any one card still would shift everything else instead. The days-in-status clock
resets, because Jira reset it. Moving a ticket to Discarded takes it off the board, since no
column maps to that status.

On failure the reason appears under the dropdowns, the dropdown goes back to what it was, and
nothing local changes. Nothing else on a ticket can be edited from here.
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

| Setting | Where | Default |
|---|---|---|
| Atlassian email | `JIRA_EMAIL`, `config.json`, or the `acli` account | none |
| API token | `JIRA_TOKEN`, `config.json`, or the `acli` keychain entry | none |
| port | `PAID_CANVAS_PORT` | 8777 |

Everything else is a constant near the top of `server.py`: the Jira site, board 209, the
180-second cache, and the GitLab-for-Jira app key the dev-status endpoint needs.

`python3 server.py --once out.json` writes a snapshot and exits, without starting a server.
`--check` verifies the credentials, `--setup` stores them.

Three files are written next to `server.py` and all three are git-ignored: `config.json` holds
your credentials, `.dev-status.json` caches merge requests per issue, `.type-icons.json` caches
the work-type SVGs. Deleting a cache costs one slower fetch.
