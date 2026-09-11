#!/usr/bin/env python3
"""Local server for the PAID canvas board.

Serves index.html and a normalized snapshot of Jira board 209 at /api/board.
The Jira token stays server-side; the browser never sees it.
"""
import base64
import calendar
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# The board, the site and the GitLab-for-Jira app are the same for everyone on the squad,
# so they live here rather than in anyone's config. Only the credentials differ.
BASE = "https://pnlfintech.atlassian.net"
BOARD_ID = 209
PORT = int(os.environ.get("PAID_CANVAS_PORT", "8777"))
CACHE_TTL = 180                                     # seconds a snapshot is reused
HERE = os.path.dirname(os.path.abspath(__file__))

BOARD_FIELDS = (
    "summary,status,priority,assignee,reporter,creator,comment,issuetype,issuelinks,"
    "subtasks,parent,labels,created,updated,duedate,resolutiondate,customfield_10022"
)

# One letter per way a person can be involved in a PAID item.
ROLE_ASSIGNEE, ROLE_REPORTER, ROLE_CREATOR = "a", "r", "x"
ROLE_COMMENT, ROLE_LINKED = "c", "k"
# The projects that carry implementation, and the only chips the board draws. Holding a
# ticket in any other project — an idea, a support case — is not work on the story, so it
# does not put you on the card.
CHIP_PROJECTS = ("ANDR", "IOS", "DEV", "BACK", "LOC")

# A column Jira does not have. Everything that reached the last board column in the past two
# days stands in its own column ahead of it, so today's deliveries read at a glance instead of
# sitting among a month of archive. Membership follows the status change rather than the
# resolution date, which is the board's own reading of when a thing arrived.
DONE_COLUMN = "Delivered"
FRESH_COLUMN = "Just delivered"
FRESH_HOURS = 48
LINKED_FIELDS = "summary,status,priority,assignee,issuetype,updated,resolutiondate,parent"

# Outward links that mean "this story spawned that work item".
IMPL_LINKS = {("Defect", "out"), ("Problem/Incident", "out"), ("Blocks", "out"),
              ("Cloners", "out"), ("Work item split", "out"), ("Polaris work item link", "in")}

PRIORITY_ORDER = ["Highest", "High", "Medium", "Low", "Lowest", "None"]

# Jira's development panel, fed by the GitLab for Jira app. The instance key doubles as the
# applicationType the dev-status endpoint expects; "gitlab" silently returns nothing.
DEV_APP = "oAuth-gitlab-jira-connect-gitlab.com"
DEV_CACHE = os.path.join(HERE, ".dev-status.json")
ICON_CACHE = os.path.join(HERE, ".type-icons.json")
DEV_WORKERS = 12
MR_STATE = {"OPEN": "opened", "MERGED": "merged", "DECLINED": "closed"}

# Who counts as the squad. Anyone can end up on a PAID item as the assignee of a linked
# ticket in another project; the people filter wants the people who work on PAID itself.
TEAM_FILE = os.path.join(HERE, "team.json")
# Jira has no field for the repositories an issue touches, so the board reads a person's
# craft instead: who is assigned tells you roughly what kind of work it is.
SPECIALTY_FILE = os.path.join(HERE, "specialties.json")
TEAM_MIN_ITEMS = 3                                  # only used if team.json is missing
NOT_A_PERSON = re.compile(r"\bbot\b|automation|minion|^jira\b", re.I)


def team_override():
    """A hand-kept team.json wins over anything derived. Names or account ids, one list."""
    try:
        with open(TEAM_FILE) as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    if isinstance(d, dict):
        d = d.get("team")
    if isinstance(d, list) and d:
        return {str(x).strip() for x in d if str(x).strip()}
    return None


def type_icons(seen):
    """Jira's own work-type icons, inlined so the page needs nothing from the network."""
    try:
        with open(ICON_CACHE) as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = {}
    missing = {n: u for n, u in seen.items() if n not in cache and u}
    if missing:
        def grab(item):
            name, url = item
            try:
                with urllib.request.urlopen(url, timeout=20) as r:
                    ct = r.headers.get("Content-Type", "image/svg+xml").split(";")[0]
                    blob = base64.b64encode(r.read()).decode()
                return name, "data:" + ct + ";base64," + blob
            except Exception:
                return name, None
        with ThreadPoolExecutor(max_workers=6) as ex:
            for name, data in ex.map(grab, missing.items()):
                if data:
                    cache[name] = data
        try:
            with open(ICON_CACHE, "w") as fh:
                json.dump(cache, fh)
        except OSError:
            pass
    return {n: cache[n] for n in seen if n in cache}


_columns_by_status = {}          # status id -> board column, filled by build()
_progress = {"phase": "idle", "done": 0, "total": 0, "note": ""}


def stage(phase, done=0, total=0, note=""):
    _progress.update(phase=phase, done=done, total=total, note=note)


def specialties(people):
    """Craft -> the account ids of the people who do it, from specialties.json."""
    try:
        with open(SPECIALTY_FILE) as fh:
            groups = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(groups, dict):
        return {}
    by_name = {p["name"]: p["id"] for p in people.values()}
    out = {}
    for craft, names in groups.items():
        ids = [by_name[n] for n in (names or []) if n in by_name]
        if ids:
            out[craft] = ids
    return out


def now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


_TZ = re.compile(r"([+-])(\d{2}):?(\d{2})$")


def epoch(ts):
    """Seconds since the epoch for a Jira timestamp, in the zone the timestamp carries.

    Jira writes 2026-09-11T09:30:00.000+0200. Dropping that offset and reading the rest as
    local time is right only while this machine sits in the same zone, and two hours out
    when it does not, which is a quarter of the window FRESH_HOURS names."""
    try:
        t = time.strptime((ts or "")[:19], "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return None
    m = _TZ.search(ts or "")
    if m:
        off = int(m.group(2))*3600 + int(m.group(3))*60
        return calendar.timegm(t) - (off if m.group(1) == "+" else -off)
    if (ts or "").endswith("Z"):
        return calendar.timegm(t)
    return time.mktime(t)                       # no offset written: this machine's clock


def column_for(col, since_at, now):
    """Which column a card sits in: the board's own, or FRESH_COLUMN if it has just landed."""
    if col != DONE_COLUMN:
        return col
    h = hours_between(since_at, now)
    return FRESH_COLUMN if h is not None and h < FRESH_HOURS else col


def days_between(a, b):
    """Whole days between two Jira timestamps, a earlier than b."""
    ta, tb = epoch(a), epoch(b)
    if ta is None or tb is None:
        return None
    return max(0, int((tb - ta) // 86400))


def hours_between(a, b):
    """Hours between two Jira timestamps, a earlier than b."""
    ta, tb = epoch(a), epoch(b)
    if ta is None or tb is None:
        return None
    return max(0.0, (tb - ta) / 3600.0)


def _file_conf():
    """config.json holds an email and an API token, and nothing else."""
    try:
        with open(CONFIG_FILE) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


class NeedsSetup(Exception):
    """No usable Jira credentials on this machine."""


def keychain_token():
    """The token the Atlassian CLI stores, if this is a Mac and acli is signed in."""
    if sys.platform != "darwin":
        return None
    try:
        raw = subprocess.check_output(
            ["security", "find-generic-password", "-s", "acli", "-w"],
            text=True, stderr=subprocess.DEVNULL, timeout=10).strip()
    except Exception:
        return None
    raw = re.sub(r"^go-keyring-base64:", "", raw)
    try:
        return base64.b64decode(raw).decode().strip()
    except Exception:
        return raw or None


def acli_account():
    """The site and email the Atlassian CLI is signed in with, if it is."""
    for path in (os.path.expanduser("~/.config/acli/jira_config.yaml"),
                 os.path.expanduser("~/.acli/jira_config.yaml")):
        try:
            with open(path) as fh:
                text = fh.read()
        except OSError:
            continue
        email = re.search(r"^\s*email:\s*[\"\']?([^\s\"\']+)", text, re.M)
        site = re.search(r"^\s*site:\s*[\"\']?([^\s\"\']+)", text, re.M)
        if email:
            return email.group(1), (site.group(1) if site else None)
    return None, None


def credentials():
    """Email and API token: environment, then config.json, then the Atlassian CLI."""
    saved = _file_conf()
    email = os.environ.get("JIRA_EMAIL") or saved.get("email")
    token = os.environ.get("JIRA_TOKEN") or saved.get("token") or keychain_token()
    if not email:
        email = acli_account()[0]
    if not email or not token:
        raise NeedsSetup(
            "No Jira credentials. Run  ./run setup  in this folder, or set JIRA_EMAIL and "
            "JIRA_TOKEN. Create a token at "
            "https://id.atlassian.com/manage-profile/security/api-tokens")
    return str(email).strip(), str(token).strip()


def jira_token():
    return credentials()[1]


def setup():
    """Ask once, store in config.json, and check the answer against Jira."""
    print("PAID canvas setup\n")
    print("You need an Atlassian API token:\n")
    print("  1. open https://id.atlassian.com/manage-profile/security/api-tokens")
    print("  2. Create API token, take the plain one if offered a choice")
    print("  3. name it (paid-canvas) and pick an expiry")
    print("  4. copy it now, Atlassian shows it once\n")
    email = input("Atlassian email: ").strip()
    token = input("API token: ").strip()
    if not email or not token:
        print("nothing written", file=sys.stderr)
        return 1
    try:
        who = Jira(token, email).get("/rest/api/3/myself")
        print("\nsigned in as", who.get("displayName"))
    except Exception as e:
        print("\ncould not sign in:", e, file=sys.stderr)
        return 1
    with open(CONFIG_FILE, "w") as fh:
        json.dump({"email": email, "token": token}, fh, indent=2)
    os.chmod(CONFIG_FILE, 0o600)
    print("wrote", CONFIG_FILE)
    return 0


def ordered_transitions(jira, issues, columns):
    """Every status this workflow can reach, in board order rather than alphabetical.

    The workflow here is permissive, so one issue's transition list applies to all. Statuses
    that no column shows, Discarded among them, come last."""
    if not issues:
        return []
    raw = jira.get(f"/rest/api/3/issue/{issues[0]['key']}/transitions").get("transitions") or []
    order = {c["name"]: c["order"] for c in columns}
    where = {}                                  # status name -> the column that shows it
    for i in issues:
        where.setdefault(i["status"], i["column"])
    last = len(columns)

    def rank(t):
        col = where.get(t["to"]["name"])
        return (order.get(col, last), t["to"]["name"])

    return [{"id": t["id"], "to": t["to"]["name"]} for t in sorted(raw, key=rank)]


def status_history(issue, now):
    """Every status move on one issue, oldest first, plus how long each state lasted."""
    cl = issue.get("changelog") or {}
    moves = []
    for h in cl.get("histories") or []:
        for it in h.get("items") or []:
            if it.get("field") == "status":
                moves.append({
                    "at": h.get("created") or "",
                    "by": ((h.get("author") or {}).get("displayName")),
                    "from": it.get("fromString"),
                    "to": it.get("toString"),
                })
    moves.sort(key=lambda m: m["at"])

    created = issue["fields"].get("created") or ""
    spans, prev_at = [], created
    for m in moves:
        spans.append({"status": m["from"], "days": days_between(prev_at, m["at"]),
                      "until": m["at"], "by": m["by"]})
        prev_at = m["at"]
    current = (issue["fields"].get("status") or {}).get("name")
    spans.append({"status": current, "days": days_between(prev_at, now),
                  "until": None, "by": moves[-1]["by"] if moves else None})
    # the first span is the state the issue was created in, which Jira does not name
    if spans and spans[0]["status"] is None:
        spans[0]["status"] = "Created"
    return moves, spans, prev_at


class Jira:
    def __init__(self, token, email=None, base=None):
        self.base = base or BASE
        self.auth = base64.b64encode(
            f"{email or credentials()[0]}:{token}".encode()).decode()

    def send(self, method, path, body):
        """PUT or POST with a JSON body. Jira answers 204 with no content on success."""
        req = urllib.request.Request(
            self.base + path, method=method,
            data=json.dumps(body).encode(),
            headers={"Authorization": "Basic " + self.auth,
                     "Accept": "application/json", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read()
        return json.loads(raw) if raw else {}

    def get(self, path, **params):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url, headers={"Authorization": "Basic " + self.auth, "Accept": "application/json"}
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
            except urllib.error.URLError:
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise


def initials(name):
    parts = [p for p in re.split(r"[\s.]+", name or "") if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def person(a):
    if not a:
        return None
    n = a.get("displayName") or a.get("emailAddress") or "?"
    return {"name": n, "initials": initials(n), "id": a.get("accountId") or n}


def status_of(f):
    s = f.get("status") or {}
    cat = (s.get("statusCategory") or {}).get("key") or "new"
    return s.get("name") or "?", cat


def slim(key, f):
    name, cat = status_of(f)
    return {
        "key": key,
        "project": key.split("-")[0],
        "summary": f.get("summary") or "",
        "type": (f.get("issuetype") or {}).get("name") or "?",
        "status": name,
        "cat": cat,
        "priority": (f.get("priority") or {}).get("name") or "None",
        "assignee": person(f.get("assignee")),
        "updated": (f.get("updated") or "")[:10],
    }


def build(jira):
    cfg = jira.get(f"/rest/agile/1.0/board/{BOARD_ID}/configuration")
    global _columns_by_status
    columns, status_to_col = [], {}
    for c in cfg["columnConfig"]["columns"]:
        columns.append({"name": c["name"], "max": c.get("max")})
        for s in c["statuses"]:
            status_to_col[s["id"]] = c["name"]
    names = [c["name"] for c in columns]
    if DONE_COLUMN in names:
        columns.insert(names.index(DONE_COLUMN), {"name": FRESH_COLUMN, "max": None})
    for order, c in enumerate(columns):
        c["order"] = order

    stage("board", 0, 0, "reading board " + str(BOARD_ID))
    _columns_by_status = dict(status_to_col)
    issues, start = [], 0
    while True:
        d = jira.get(f"/rest/agile/1.0/board/{BOARD_ID}/issue",
                     startAt=start, maxResults=100, fields=BOARD_FIELDS,
                     expand="changelog")
        batch = d.get("issues") or []
        issues.extend(batch)
        start += len(batch)
        stage("board", start, d.get("total", 0), "reading the board")
        if not batch or start >= d.get("total", 0):
            break

    # Collect every linked / subtask key so we can fetch assignees for them.
    wanted = set()
    for i in issues:
        f = i["fields"]
        for l in f.get("issuelinks") or []:
            o = l.get("outwardIssue") or l.get("inwardIssue")
            if o:
                wanted.add(o["key"])
        for st in f.get("subtasks") or []:
            wanted.add(st["key"])
    # A link can point at another item on this board. Those need no extra request, but the
    # embedded link payload carries no assignee, so serve them from the board fetch instead.
    own = {i["key"]: i["fields"] for i in issues}
    wanted -= set(own)

    keys = sorted(wanted)
    chunks = [keys[n:n + 90] for n in range(0, len(keys), 90)]

    def fetch_chunk(ch):
        out = {}
        jql = "key in (%s)" % ",".join(ch)
        token = None
        while True:
            params = {"jql": jql, "fields": LINKED_FIELDS, "maxResults": 100}
            if token:
                params["nextPageToken"] = token
            try:
                d = jira.get("/rest/api/3/search/jql", **params)
            except urllib.error.HTTPError:
                return out
            for i in d.get("issues") or []:
                out[i["key"]] = i["fields"]
            token = d.get("nextPageToken")
            if not token:
                break
        return out

    linked = {}
    if chunks:
        stage("linked", 0, len(keys), "fetching linked tickets")
        with ThreadPoolExecutor(max_workers=6) as ex:
            for part in ex.map(fetch_chunk, chunks):
                linked.update(part)
                stage("linked", len(linked), len(keys), "fetching linked tickets")

    now = now_utc()
    dev, dev_error, dev_running = dev_cache()

    def prs_for(key):
        return (dev.get(key) or {}).get("prs") or []

    icon_urls = {}

    def note_type(t):
        if t and t.get("name") and t.get("iconUrl"):
            icon_urls.setdefault(t["name"], t["iconUrl"])

    dev_wanted = []
    seen_dev = set()

    def want_dev(key, iid, updated):
        if key and iid and key not in seen_dev:
            seen_dev.add(key)
            dev_wanted.append((key, str(iid), (updated or "")[:19]))

    epics, out, people = {}, [], {}

    def note(p):
        if p:
            people[p["id"]] = p

    for i in issues:
        f = i["fields"]
        note_type(f.get("issuetype"))
        for l in f.get("issuelinks") or []:
            o = l.get("outwardIssue") or l.get("inwardIssue")
            if o:
                note_type((own.get(o["key"]) or linked.get(o["key"])
                           or o.get("fields") or {}).get("issuetype"))
        want_dev(i["key"], i["id"], f.get("updated"))
        for l in f.get("issuelinks") or []:
            o = l.get("outwardIssue") or l.get("inwardIssue")
            if o:
                want_dev(o["key"], o.get("id"),
                         (linked.get(o["key"], {}) or {}).get("updated")
                         or (o.get("fields") or {}).get("updated"))
        for st in f.get("subtasks") or []:
            want_dev(st["key"], st.get("id"),
                     (linked.get(st["key"], {}) or {}).get("updated"))
        sid = (f.get("status") or {}).get("id")
        sname, scat = status_of(f)
        par = f.get("parent")
        epic_key = None
        if par:
            pf = par.get("fields") or {}
            if ((pf.get("issuetype") or {}).get("name") or "").lower() == "epic":
                epic_key = par["key"]
                if epic_key not in epics:
                    ps, pc = status_of(pf)
                    epics[epic_key] = {"key": epic_key, "summary": pf.get("summary") or "",
                                       "status": ps, "cat": pc,
                                       "priority": (pf.get("priority") or {}).get("name")
                                       or "None"}

        kids = []
        seen = set()
        for st in f.get("subtasks") or []:
            sf = dict(st.get("fields") or {})
            sf.update(own.get(st["key"]) or linked.get(st["key"]) or {})
            c = slim(st["key"], sf)
            c.update({"rel": "Subtask", "dir": "out", "relLabel": "subtask", "impl": True})
            kids.append(c)
            seen.add(c["key"])
        for l in f.get("issuelinks") or []:
            o = l.get("outwardIssue")
            d = "out"
            if not o:
                o = l.get("inwardIssue")
                d = "in"
            if not o or o["key"] in seen:
                continue
            seen.add(o["key"])
            t = l["type"]
            sf = dict(o.get("fields") or {})
            sf.update(own.get(o["key"]) or linked.get(o["key"]) or {})
            c = slim(o["key"], sf)
            c.update({
                "rel": t["name"],
                "dir": d,
                "relLabel": t["outward"] if d == "out" else t["inward"],
                "impl": (t["name"], d) in IMPL_LINKS,
            })
            kids.append(c)
            note(c["assignee"])

        for c in kids:
            c["mrs"] = prs_for(c["key"])
            c["mr"] = mr_roll_up(c["mrs"])
        kids.sort(key=lambda c: (not c["impl"], c["project"], c["key"]))
        a = person(f.get("assignee"))
        note(a)

        part = {}

        def involve(p, role):
            if not p:
                return
            note(p)
            part[p["id"]] = "".join(sorted(set(part.get(p["id"], "") + role)))

        involve(a, ROLE_ASSIGNEE)
        rep = person(f.get("reporter"))
        involve(rep, ROLE_REPORTER)
        cre = person(f.get("creator"))
        if cre and (not rep or cre["id"] != rep["id"]):
            involve(cre, ROLE_CREATOR)
        for cm in (f.get("comment") or {}).get("comments") or []:
            involve(person(cm.get("author")), ROLE_COMMENT)
        for c in kids:
            if c["project"] in CHIP_PROJECTS:
                involve(c["assignee"], ROLE_LINKED)
        moves, spans, since_at = status_history(i, now)
        own_mrs = prs_for(i["key"])
        seen_mr = set()
        all_mrs = []
        for m in own_mrs + [m for c in kids for m in c["mrs"]]:
            if m["id"] not in seen_mr:
                seen_mr.add(m["id"])
                all_mrs.append(m)

        out.append({
            "key": i["key"],
            "summary": f.get("summary") or "",
            "type": (f.get("issuetype") or {}).get("name") or "?",
            "status": sname,
            "cat": scat,
            "column": column_for(status_to_col.get(sid, sname), since_at, now),
            "priority": (f.get("priority") or {}).get("name") or "None",
            "assignee": a,
            "epic": epic_key,
            "labels": f.get("labels") or [],
            "created": (f.get("created") or "")[:10],
            "updated": (f.get("updated") or "")[:10],
            "resolved": (f.get("resolutiondate") or "")[:10] or None,
            "due": f.get("duedate"),
            "rank": f.get("customfield_10022") or "",
            "part": part,
            "statusSince": since_at[:10],
            "daysInStatus": days_between(since_at, now),
            "age": days_between(f.get("created") or "", now),
            "movedBy": spans[-1]["by"],
            "spans": [sp for sp in spans if sp["days"]],
            "mrs": own_mrs,
            "mr": mr_roll_up(all_mrs),
            "children": kids,
        })

    stage("building", len(out), len(out), "laying out the board")
    global _last_wanted
    _last_wanted = dev_wanted
    refresh_dev_status(dev_wanted)
    out.sort(key=lambda i: i["rank"])

    override = team_override()
    if override:
        team = {p["id"] for p in people.values()
                if p["id"] in override or p["name"] in override}
        team_source = "team.json"
    else:
        # a role on a PAID item other than "assignee of some linked ticket"
        direct = {}
        for i in out:
            for pid, roles in i["part"].items():
                if set(roles) - {ROLE_LINKED}:
                    direct[pid] = direct.get(pid, 0) + 1
        team = {pid for pid, n in direct.items()
                if n >= TEAM_MIN_ITEMS and not NOT_A_PERSON.search(people[pid]["name"])}
        team_source = f"derived, {TEAM_MIN_ITEMS}+ PAID items"
    for p in people.values():
        p["team"] = p["id"] in team
    crafts = specialties(people)
    return {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "board": {"id": BOARD_ID, "name": cfg.get("name"),
                  "projectKey": (cfg.get("location") or {}).get("key"),
                  "url": f"{BASE}/jira/software/projects/PAID/boards/{BOARD_ID}"},
        "baseUrl": BASE,
        "columns": columns,
        "priorities": PRIORITY_ORDER,
        "priorityList": jira.get("/rest/api/3/priority"),
        "transitions": ordered_transitions(jira, out, columns),
        "typeIcons": type_icons(icon_urls),
        "epics": epics,
        "people": sorted(people.values(), key=lambda p: p["name"]),
        "teamSource": team_source,
        "specialties": crafts,
        "issues": out,
        "roles": {"a": "assignee", "r": "reporter", "x": "created it",
                  "c": "commented", "k": "linked work"},
        "chipProjects": list(CHIP_PROJECTS),
        "mrIndex": {"source": "jira dev-status", "error": dev_error, "sweeping": dev_running,
                    "covered": len(dev), "wanted": len(dev_wanted),
                    "mrs": sum(len(v.get("prs") or []) for v in dev.values())},
        "counts": {"issues": len(out), "children": sum(len(i["children"]) for i in out),
                   "linkedFetched": len(linked), "linkedWanted": len(keys),
                   "people": len(people),
                   "team": sum(1 for p in people.values() if p["team"]),
                   "crafts": {k: len(v) for k, v in crafts.items()},
                   "childrenNoAssignee": sum(
                       1 for i in out for c in i["children"] if not c["assignee"]),
                   "withMrs": sum(1 for i in out if i["mr"]["n"]),
                   "openMrs": sum(i["mr"]["opened"] + i["mr"]["draft"] for i in out)},
    }


# ------------------------------------------------------- merge requests via Jira dev-status
# Jira already links every merge request to its issue, so the board asks Jira rather than
# matching text in GitLab. One request per issue, cached until that issue changes.


def slim_pr(p):
    ident = p.get("id") or ""
    repo, _, iid = ident.partition("!")
    title = p.get("name") or ""
    state = MR_STATE.get(p.get("status"), (p.get("status") or "other").lower())
    if state == "opened" and title.lower().startswith("draft:"):
        state = "draft"
    return {
        "id": ident,
        "iid": iid,
        "repo": p.get("repositoryName") or repo,
        "title": title,
        "state": state,
        "url": p.get("url"),
        "author": (p.get("author") or {}).get("name"),
        "updated": (p.get("lastUpdate") or "")[:10],
        "branch": (p.get("source") or {}).get("branch"),
        "target": (p.get("destination") or {}).get("branch"),
        "reviewers": [r.get("name") for r in (p.get("reviewers") or []) if r.get("name")],
        "comments": p.get("commentCount") or 0,
    }


def load_dev_cache():
    try:
        with open(DEV_CACHE) as fh:
            d = json.load(fh)
        if isinstance(d, dict):
            return d
    except (OSError, ValueError):
        pass
    return {}


def fetch_dev_status(jira, wanted, cache):
    """wanted is [(key, numeric id, updated)]. Only re-asks about issues that changed."""
    stale = [w for w in wanted if (cache.get(w[0]) or {}).get("updated") != w[2]]

    def one(w):
        key, iid, updated = w
        try:
            d = jira.get("/rest/dev-status/1.0/issue/detail",
                         issueId=iid, applicationType=DEV_APP, dataType="pullrequest")
        except Exception:
            return key, None
        prs = [slim_pr(p) for block in (d.get("detail") or [])
               for p in (block.get("pullRequests") or [])]
        prs.sort(key=lambda p: (MR_ORDER_RANK.get(p["state"], 9), p["updated"]))
        return key, {"updated": updated, "prs": prs}

    if stale:
        n = 0
        stage("mrs", 0, len(stale), "asking Jira about merge requests")
        with ThreadPoolExecutor(max_workers=DEV_WORKERS) as ex:
            for key, rec in ex.map(one, stale):
                n += 1
                if rec is not None:
                    cache[key] = rec
                if n % 10 == 0 or n == len(stale):
                    stage("mrs", n, len(stale), "asking Jira about merge requests")
    try:
        with open(DEV_CACHE, "w") as fh:
            json.dump(cache, fh)
    except OSError:
        pass
    return len(stale)


MR_ORDER_RANK = {"opened": 0, "draft": 1, "merged": 2, "closed": 3}

_last_wanted = []
_dev = {"cache": None, "running": False, "error": None, "fetched": 0, "done": False}
_dev_lock = threading.Lock()


def dev_cache():
    with _dev_lock:
        if _dev["cache"] is None:
            _dev["cache"] = load_dev_cache()
        return _dev["cache"], _dev["error"], _dev["running"]


def refresh_dev_status(wanted, block=False):
    """Ask Jira about every issue whose merge requests may have moved.

    The first run covers a thousand issues and takes about a minute, so it runs off the
    request path; the board simply shows fewer merge requests until it lands."""
    def run():
        try:
            cache, _, _ = dev_cache()
            token = jira_token()
            n = fetch_dev_status(Jira(token), wanted, cache)
            with _dev_lock:
                _dev.update(cache=cache, error=None, fetched=n, done=True)
        except Exception as e:
            with _dev_lock:
                _dev["error"] = f"{type(e).__name__}: {e}"
        finally:
            with _dev_lock:
                _dev["running"] = False

    with _dev_lock:
        if _dev["running"]:
            return
        _dev["running"] = True
    if block:
        run()
    else:
        threading.Thread(target=run, daemon=True).start()


def mr_roll_up(mrs):
    """One line per card: how many merge requests and how far along they are."""
    c = {"opened": 0, "draft": 0, "merged": 0, "closed": 0, "other": 0}
    for m in mrs:
        c[m["state"] if m["state"] in c else "other"] += 1
    return {"n": len(mrs), **c}


def apply_change(key, priority=None, transition=None):
    """Write one field to Jira, then read the issue back and return what the board needs.

    The card is moved by the caller from these values, so they come from Jira rather than
    from what we hoped we wrote."""
    jira = Jira(jira_token())
    if priority:
        jira.send("PUT", f"/rest/api/3/issue/{key}",
                  {"fields": {"priority": {"name": priority}}})
    if transition:
        jira.send("POST", f"/rest/api/3/issue/{key}/transitions",
                  {"transition": {"id": str(transition)}})

    fresh = jira.get(f"/rest/api/3/issue/{key}",
                     fields="status,priority,updated,resolutiondate,created",
                     expand="changelog")
    f = fresh["fields"]
    now = now_utc()
    _moves, spans, since_at = status_history(fresh, now)
    sname, scat = status_of(f)
    patch = {
        "key": key,
        "status": sname,
        "cat": scat,
        "column": column_for(
            _columns_by_status.get((f.get("status") or {}).get("id"), sname), since_at, now),
        "priority": (f.get("priority") or {}).get("name") or "None",
        "updated": (f.get("updated") or "")[:10],
        "resolved": (f.get("resolutiondate") or "")[:10] or None,
        "statusSince": since_at[:10],
        "daysInStatus": days_between(since_at, now),
        "age": days_between(f.get("created") or "", now),
        "movedBy": spans[-1]["by"] if spans else None,
        "spans": [sp for sp in spans if sp["days"]],
        "onBoard": (f.get("status") or {}).get("id") in _columns_by_status,
    }
    # keep the cached snapshot in step, so a later refresh does not undo what you just saw
    with _lock:
        data = _cache.get("data")
        if data:
            for i in data["issues"]:
                if i["key"] == key:
                    i.update({k: v for k, v in patch.items() if k != "onBoard"})
                    break
    return patch


_cache = {"at": 0, "data": None, "err": None}
_lock = threading.Lock()


def snapshot(force=False):
    with _lock:
        if not force and _cache["data"] and time.time() - _cache["at"] < CACHE_TTL:
            return _cache["data"]
        data = build(Jira(jira_token()))
        _cache.update(at=time.time(), data=data, err=None)
        return data


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % a))

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path)
        m = re.match(r"^/api/issue/([A-Z][A-Z0-9]+-\d+)$", p.path)
        if not m:
            return self._send(404, "not found", "text/plain")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, json.dumps({"error": "bad json"}), "application/json")
        try:
            patch = apply_change(m.group(1), body.get("priority"), body.get("transition"))
            return self._send(200, json.dumps(patch), "application/json")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:400]
            except Exception:
                pass
            return self._send(200, json.dumps(
                {"error": f"Jira said {e.code}. {detail}"}), "application/json")
        except Exception as e:
            return self._send(200, json.dumps(
                {"error": f"{type(e).__name__}: {e}"}), "application/json")

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(p.query)
        if p.path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            except OSError as e:
                return self._send(500, str(e), "text/plain")
        if p.path == "/api/board":
            try:
                data = snapshot(force=q.get("refresh", ["0"])[0] == "1")
                stage("idle", 0, 0, "")
                return self._send(200, json.dumps(data), "application/json")
            except NeedsSetup as e:
                return self._send(200, json.dumps({"error": str(e), "setup": True}),
                                  "application/json")
            except Exception as e:
                return self._send(
                    500, json.dumps({"error": f"{type(e).__name__}: {e}"}), "application/json"
                )
        if p.path == "/api/progress":
            return self._send(200, json.dumps(_progress), "application/json")
        if p.path == "/api/health":
            return self._send(200, json.dumps(
                {"ok": True, "cachedAt": _cache["at"], "ttl": CACHE_TTL}), "application/json")
        self._send(404, "not found", "text/plain")


if __name__ == "__main__":
    if "--setup" in sys.argv:
        sys.exit(setup())
    if "--check" in sys.argv:
        try:
            email, token = credentials()
            me = Jira(token, email).get("/rest/api/3/myself")
            cfg = Jira(token, email).get(f"/rest/agile/1.0/board/{BOARD_ID}/configuration")
            print(f"ok: {me.get('displayName')} <{email}> can read "
                  f"board {BOARD_ID} \"{cfg.get('name')}\" on {BASE}")
            sys.exit(0)
        except Exception as e:
            print(e, file=sys.stderr)
            sys.exit(1)
    if "--once" in sys.argv:
        d = snapshot(force=True)
        with _dev_lock:
            _dev["running"] = False
        refresh_dev_status([(k, i, u) for k, i, u in _last_wanted], block=True)
        d = snapshot(force=True)
        print(json.dumps(d["counts"], indent=2), file=sys.stderr)
        target = sys.argv[sys.argv.index("--once") + 1] if len(sys.argv) > sys.argv.index("--once") + 1 else None
        if target and not target.startswith("-"):
            with open(target, "w") as fh:
                json.dump(d, fh)
            print("wrote " + target, file=sys.stderr)
        else:
            print(json.dumps(d))
        sys.exit(0)
    try:
        credentials()
    except NeedsSetup as e:
        print(str(e), file=sys.stderr)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"PAID canvas on http://127.0.0.1:{PORT}  (board {BOARD_ID} on {BASE}, "
          f"cache {CACHE_TTL}s)", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
