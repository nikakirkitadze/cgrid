#!/usr/bin/env python3
"""cgrid: open a grid of terminal panes for Claude Code sessions.

Each pane starts with a picker that ranks your project folders by how you
actually use Claude (prompt history + session transcripts) and launches
`claude` there with the permission mode you usually use for that folder.

https://github.com/nikakirkitadze/cgrid · MIT License
"""

from __future__ import annotations

import argparse
import curses
import json
import locale
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

__version__ = "0.1.0"

HOME = Path.home()
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", HOME / ".claude"))
XDG_CACHE = Path(os.environ.get("XDG_CACHE_HOME") or HOME / ".cache")
XDG_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
CACHE_DIR = Path(os.environ.get("CGRID_CACHE") or XDG_CACHE / "cgrid")
RUN_DIR = CACHE_DIR / "run"
PICKS_LOG = CACHE_DIR / "picks.jsonl"
CONFIG_PATH = Path(os.environ.get("CGRID_CONFIG") or XDG_CONFIG / "cgrid" / "config.json")
SOCKET = os.environ.get("CGRID_SOCKET") or "cgrid"
SCRIPT = os.path.realpath(__file__)
MAX_PANES = 12
IS_MAC = sys.platform == "darwin"

DEFAULT_CONFIG = {
    "panes": 6,
    "ai": False,
    "ai_model": "haiku",
    "half_life_days": 14,
    "default_mode": None,
    "claude": "claude",
    "terminal": "auto",
    "roots": ["~/Documents", "~/Desktop", "~/Developer", "~/Projects", "~/code", "~/src", "~/dev", "~/work"],
    "exclude": ["~/.claude", "~/.claude-mem", "/tmp", "/private/tmp", "/var/folders", "/private/var/folders"],
}

# key -> (label, claude flags)
MODES = {
    "skip": ("⚡ skip-perms", ["--dangerously-skip-permissions"]),
    "auto": ("auto", ["--permission-mode", "auto"]),
    "accept": ("accept-edits", ["--permission-mode", "acceptEdits"]),
    "plan": ("plan", ["--permission-mode", "plan"]),
    "ask": ("ask", []),
}
MODE_ORDER = list(MODES)
TRANSCRIPT_MODES = {
    "bypassPermissions": "skip",
    "auto": "auto",
    "acceptEdits": "accept",
    "plan": "plan",
    "default": "ask",
    "manual": "ask",
    "dontAsk": "ask",
}


# ---------------------------------------------------------------- helpers


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    except (OSError, ValueError):
        pass
    if os.environ.get("CGRID_CLAUDE"):
        cfg["claude"] = os.environ["CGRID_CLAUDE"]
    return cfg


def short(path: str) -> str:
    home = str(HOME)
    if path == home:
        return "~"
    if path.startswith(home + "/"):
        return "~" + path[len(home) :]
    return path


def display_name(path: str) -> str:
    if path == str(HOME):
        return "~ home"
    return os.path.basename(path.rstrip("/")) or path


def ago(ts: float, now: float | None = None) -> str:
    if not ts:
        return ""
    d = max(0, (now or time.time()) - ts)
    if d < 90:
        return "now"
    if d < 3600:
        return f"{int(d // 60)}m"
    if d < 86400:
        return f"{int(d // 3600)}h"
    if d < 14 * 86400:
        return f"{int(d // 86400)}d"
    if d < 60 * 86400:
        return f"{int(d // (7 * 86400))}w"
    return f"{int(d // (30 * 86400))}mo"


def dwidth(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 0 if unicodedata.combining(c) else 1 for c in text)


def clip(text: str, width: int) -> str:
    out, used = [], 0
    for c in text:
        cw = dwidth(c)
        if used + cw > width:
            break
        out.append(c)
        used += cw
    return "".join(out)


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - dwidth(text))


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def claude_command(cfg: dict, mode: str, cont: bool) -> str:
    flags = MODES[mode][1] + (["--continue"] if cont else [])
    return shlex.join([cfg["claude"], *flags])


def die(msg: str) -> None:
    print(f"cgrid: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------- history index


def norm_project(path: str | None) -> str | None:
    if not path:
        return None
    p = path.rstrip("/") or "/"
    i = p.find("/.claude/worktrees/")
    return p[:i] if i != -1 else p


def clean_prompt(text: str) -> str | None:
    text = re.sub(r"\[Pasted text #\d+[^\]]*\]", "", text or "")
    text = " ".join(text.split())
    if len(text) < 8 or text.startswith("/") or text.lower() in {"continue", "go ahead", "yes please"}:
        return None
    return text[:160]


def parse_ts(value) -> float | None:
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e11 else value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def scan_history() -> dict:
    """sessionId -> {proj, n, first, last, prompts} from ~/.claude/history.jsonl."""
    sessions: dict = {}
    try:
        fh = open(CLAUDE_DIR / "history.jsonl", encoding="utf-8", errors="replace")
    except OSError:
        return sessions
    with fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            proj, ts = d.get("project"), parse_ts(d.get("timestamp"))
            if not proj or not ts:
                continue
            sid = d.get("sessionId") or f"{proj}@{int(ts // 3600)}"
            s = sessions.setdefault(sid, {"proj": proj, "n": 0, "first": ts, "last": ts, "prompts": []})
            s["n"] += 1
            s["first"], s["last"] = min(s["first"], ts), max(s["last"], ts)
            text = clean_prompt(d.get("display", ""))
            if text:
                s["prompts"] = (s["prompts"] + [(ts, text)])[-3:]
    return sessions


def read_transcript_head(path: Path) -> dict:
    meta = {"sid": path.stem, "cwd": None, "mode": None, "ep": None, "start": None}
    seen = 0
    try:
        with open(path, "rb") as fh:
            for i, line in enumerate(fh):
                seen += len(line)
                if i > 200 or seen > 4_000_000:
                    break
                if b'"cwd"' not in line and b'"permissionMode"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                meta["cwd"] = meta["cwd"] or d.get("cwd")
                meta["ep"] = meta["ep"] or d.get("entrypoint")
                meta["start"] = meta["start"] or parse_ts(d.get("timestamp"))
                meta["mode"] = meta["mode"] or d.get("permissionMode")
                if meta["cwd"] and meta["ep"] and meta["mode"] and meta["start"]:
                    break
    except OSError:
        pass
    return meta


def scan_transcripts() -> list:
    """Head metadata of every session transcript, cached by mtime/size."""
    cache_path = CACHE_DIR / "transcripts.json"
    cache = read_json(cache_path, {})
    out = {}
    for f in (CLAUDE_DIR / "projects").glob("*/*.jsonl"):
        try:
            st = f.stat()
        except OSError:
            continue
        key = str(f)
        hit = cache.get(key)
        if hit and hit.get("m") == st.st_mtime and hit.get("s") == st.st_size:
            out[key] = hit
            continue
        meta = read_transcript_head(f)
        meta.update(m=st.st_mtime, s=st.st_size, end=st.st_mtime)
        out[key] = meta
    if out != cache:
        write_json(cache_path, out)
    return list(out.values())


def shell_history_mode() -> str | None:
    """Fallback when no transcripts carry a permission mode: how you type `claude` in your shell."""
    skip = plain = 0
    for name in (".zsh_history", ".bash_history"):
        try:
            data = (HOME / name).read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        for line in data.splitlines()[-5000:]:
            cmd = re.sub(r"^: \d+:\d+;", "", line).strip()
            if cmd == "claude" or cmd.startswith("claude -"):
                if "--dangerously-skip-permissions" in cmd:
                    skip += 1
                else:
                    plain += 1
    if not skip and not plain:
        return None
    return "skip" if skip > plain else "ask"


def build_index(cfg: dict) -> dict:
    now = time.time()
    half_life = max(1.0, float(cfg["half_life_days"])) * 86400
    history = scan_history()
    transcripts = scan_transcripts()
    by_sid = {t["sid"]: t for t in transcripts}

    sessions = []
    for sid, s in history.items():
        t = by_sid.get(sid)
        mode = t.get("mode") if t and t.get("ep") == "cli" else None
        sessions.append({**s, "mode": mode})
    # Desktop-app and older CLI sessions that never reached history.jsonl.
    for t in transcripts:
        if t["sid"] in history or t.get("ep") not in ("cli", "claude-desktop") or not t.get("cwd"):
            continue
        sessions.append(
            {
                "proj": t["cwd"],
                "n": 3,
                "first": t.get("start") or t["end"],
                "last": t["end"],
                "prompts": [],
                "mode": t.get("mode") if t.get("ep") == "cli" else None,
            }
        )

    excludes = [os.path.expanduser(e).rstrip("/") for e in cfg["exclude"]]
    projects: dict = {}
    global_votes: dict = {}
    for s in sessions:
        path = norm_project(s["proj"])
        if not path or any(path == e or path.startswith(e + "/") for e in excludes):
            continue
        age = max(0.0, now - s["last"])
        weight = 0.5 ** (age / half_life)
        p = projects.setdefault(
            path,
            {
                "path": path,
                "name": display_name(path),
                "score": 0.0,
                "sessions": 0,
                "prompts": 0,
                "last": 0.0,
                "s7": 0,
                "s30": 0,
                "votes": {},
                "recent": [],
            },
        )
        p["score"] += weight * (1 + math.log1p(s["n"]))
        p["sessions"] += 1
        p["prompts"] += s["n"]
        p["last"] = max(p["last"], s["last"])
        p["s7"] += age < 7 * 86400
        p["s30"] += age < 30 * 86400
        p["recent"].extend(s["prompts"])
        if s.get("mode"):
            m = TRANSCRIPT_MODES.get(s["mode"], "ask")
            p["votes"][m] = p["votes"].get(m, 0) + weight + 0.01
            global_votes[m] = global_votes.get(m, 0) + weight + 0.01

    total = sum(global_votes.values())
    prior = {m: v / total for m, v in global_votes.items()} if total else {}
    global_mode = (
        cfg.get("default_mode") or (max(prior, key=prior.get) if prior else None) or shell_history_mode() or "ask"
    )
    items = []
    for p in projects.values():
        if not os.path.isdir(p["path"]):
            continue
        votes = dict(p.pop("votes"))
        for m, share in prior.items():  # your overall habit, worth half a fresh session
            votes[m] = votes.get(m, 0) + 0.5 * share
        p["mode"] = cfg.get("default_mode") or (max(votes, key=votes.get) if votes else global_mode)
        p["recent"] = [t for _, t in sorted(p["recent"])[-3:]]
        p["score"] = round(p["score"], 4)
        items.append(p)
    items.sort(key=lambda p: (-p["score"], -p["last"]))
    return {"built": now, "global_mode": global_mode, "projects": items}


def root_dirs(cfg: dict, known: set, mode: str) -> list:
    """Folders under your usual roots that Claude has never been opened in, newest first."""
    out = []
    for root in cfg["roots"]:
        try:
            entries = list(os.scandir(os.path.expanduser(root)))
        except OSError:
            continue
        for e in entries:
            if e.name.startswith(".") or e.path in known:
                continue
            try:
                if not e.is_dir():
                    continue
                mtime = e.stat().st_mtime
            except OSError:
                continue
            out.append(
                {
                    "path": e.path,
                    "name": e.name,
                    "score": 0,
                    "sessions": 0,
                    "prompts": 0,
                    "last": 0,
                    "mtime": mtime,
                    "recent": [],
                    "mode": mode,
                    "new": True,
                }
            )
    out.sort(key=lambda it: -it["mtime"])
    return out


# ---------------------------------------------------------------- matching


def squash(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", s.lower())


def match_tier(query: str, item: dict) -> int | None:
    q = squash(query)
    if not q:
        return 0
    name = squash(item["name"])
    if name == q:
        return 5
    if name.startswith(q):
        return 4
    if q in name:
        return 3
    if q in squash(item["path"]):
        return 1
    pos = -1
    for ch in q:
        pos = name.find(ch, pos + 1)
        if pos == -1:
            return None
    return 2


def fuzzy_filter(query: str, items: list) -> list:
    scored = []
    for it in items:
        tier = match_tier(query, it)
        if tier is not None:
            scored.append((-tier, -it["score"], len(it["name"]), it))
    scored.sort(key=lambda t: t[:3])
    return [t[3] for t in scored]


def is_pathlike(q: str) -> bool:
    return q.startswith(("/", "~", "./", "../")) or q in (".", "..")


def resolve_project(arg: str, items: list, cwd: str) -> str | None:
    if is_pathlike(arg) or "/" in arg:
        p = os.path.abspath(os.path.join(cwd, os.path.expanduser(arg)))
        return p if os.path.isdir(p) else None
    hits = fuzzy_filter(arg, items)
    return hits[0]["path"] if hits else None


# ---------------------------------------------------------------- picker UI


class Picker:
    def __init__(self, scr, ctx: dict):
        self.scr = scr
        self.ctx = ctx
        self.cfg = ctx["cfg"]
        self.grid_dir: Path | None = ctx.get("grid_dir")
        self.pane = ctx.get("pane")
        data = ctx["index"]
        self.projects = data["projects"]
        self.by_path = {p["path"]: p for p in self.projects}
        self.global_mode = data["global_mode"]
        self.extra = root_dirs(self.cfg, set(self.by_path), self.global_mode)
        self.query = ""
        self.sel = 0
        self.offset = 0
        self.touched = False
        self.mode_override = ctx.get("mode")
        self.cont = ctx.get("cont", False)
        self.msg = ctx.get("message")
        self.claims: dict = {}
        self.ai_state = "pending" if ctx.get("ai") else "off"
        self.ai_picks: list = []
        self.tick = 0
        self.items: list = []
        self.grid_stamp = None
        self.view_key = None

    # -- state

    def poll(self) -> bool:
        """Pick up claims and AI results from other panes; True when the screen needs a redraw."""
        changed = False
        if self.grid_dir:
            try:
                stamp = self.grid_dir.stat().st_mtime_ns  # claims and ai.json land by rename, which bumps this
            except OSError:
                stamp = None
            if stamp != self.grid_stamp:
                self.grid_stamp = stamp
                self.reload_grid_state()
                changed = True
        key = (self.query, tuple(sorted(self.claims.items())), self.ai_state)
        if key != self.view_key:
            self.view_key = key
            self.items = self.visible()
            changed = True
        sel = self.default_sel() if not self.touched else self.sel
        sel = max(0, min(sel, len(self.items) - 1))
        changed = changed or sel != self.sel
        self.sel = sel
        return changed

    def reload_grid_state(self) -> None:
        claims = {}
        try:
            for f in self.grid_dir.glob("claim-*"):
                n = f.name.split("-", 1)[1]
                path = f.read_text().strip()
                if path and str(n) != str(self.pane):
                    claims.setdefault(path, n)
        except OSError:
            pass
        self.claims = claims
        if self.ai_state == "pending":
            ai = read_json(self.grid_dir / "ai.json")
            if ai is not None:
                self.ai_picks = ai.get("picks") or []
                self.ai_state = "ready" if self.ai_picks else "failed"

    def ranked(self) -> list:
        if not self.projects:  # no Claude history yet: offer recently touched folders
            return self.extra
        if not self.ai_picks:
            return self.projects
        out, seen = [], set()
        for pick in self.ai_picks:
            path = pick.get("path")
            if path in seen or not path or not os.path.isdir(path):
                continue
            out.append({**self.item_for(path), "reason": pick.get("reason", "")})
            seen.add(path)
        return out + [p for p in self.projects if p["path"] not in seen]

    def visible(self) -> list:
        q = self.query
        if is_pathlike(q):
            return self.path_items(q)
        if q.strip():
            ranked = self.ranked()
            return fuzzy_filter(q, ranked if ranked is self.extra else ranked + self.extra)
        return self.ranked()

    def path_items(self, q: str) -> list:
        exp = os.path.abspath(os.path.join(self.ctx["cwd"], os.path.expanduser(q)))
        if q.endswith("/") or q in ("~", ".", ".."):
            base, prefix = exp, ""
        else:
            base, prefix = os.path.split(exp)
        out = []
        if os.path.isdir(exp):
            out.append({**self.item_for(exp), "here": True})
        kids = []
        try:
            for e in os.scandir(base):
                if e.name.startswith(".") and not prefix.startswith("."):
                    continue
                if not e.name.lower().startswith(prefix.lower()) or e.path == exp:
                    continue
                try:
                    if e.is_dir():
                        kids.append(self.item_for(e.path))
                except OSError:
                    pass
        except OSError:
            pass
        kids.sort(key=lambda it: (-it["score"], it["name"].lower()))
        return out + kids[:300]

    def item_for(self, path: str) -> dict:
        return self.by_path.get(path) or {
            "path": path,
            "name": display_name(path),
            "score": 0,
            "sessions": 0,
            "prompts": 0,
            "last": 0,
            "recent": [],
            "mode": self.global_mode,
        }

    def default_sel(self) -> int:
        if not self.items:
            return 0
        i = min((self.pane or 1) - 1, len(self.items) - 1)
        for k in range(len(self.items)):
            j = (i + k) % len(self.items)
            if self.items[j]["path"] not in self.claims:
                return j
        return i

    def current(self) -> dict | None:
        return self.items[self.sel] if self.items else None

    def mode_for(self, it: dict | None) -> str:
        return self.mode_override or (it or {}).get("mode") or self.global_mode

    # -- drawing

    def put(self, y: int, x: int, text: str, attr: int = 0) -> int:
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return x
        text = clip(text, max(0, w - x - (1 if y == h - 1 else 0)))
        try:
            self.scr.addstr(y, x, text, attr)
        except curses.error:
            pass
        return x + dwidth(text)

    def draw(self) -> None:
        scr = self.scr
        scr.erase()
        h, w = scr.getmaxyx()
        accent, dim = curses.color_pair(1) | curses.A_BOLD, curses.color_pair(2)
        warn, ai = curses.color_pair(3), curses.color_pair(4)
        roomy = h >= 16

        title = f" ◆ cgrid · pane {self.pane}/{self.ctx['of']}" if self.pane else " ◆ cgrid"
        self.put(0, 0, title, accent)
        badge, battr = "", dim
        if self.ai_state == "pending":
            badge, battr = f"{'◐◓◑◒'[self.tick % 4]} asking Claude… ", ai
        elif self.ai_state == "ready":
            badge, battr = "✦ AI picks ", ai
        elif self.ai_state == "failed":
            badge = "AI unavailable · history "
        elif w > 50:
            badge = "ranked by your sessions " if self.projects else "no history yet · recent folders "
        if len(title) + len(badge) + 2 < w:
            self.put(0, w - len(badge) - 1, badge, battr)

        y = 2 if roomy else 1
        x = self.put(y, 0, " › ", accent)
        if self.query:
            self.put(y, x, self.query, curses.A_BOLD)
        else:
            self.put(y, x, "type to filter · or a path like ~/code/…", dim)
        count = f"{len(self.items)} "
        if self.items and x + dwidth(self.query) + len(count) + 4 < w:
            self.put(y, w - len(count) - 1, count, dim)
        cursor = (y, min(w - 1, x + dwidth(self.query)))

        footer = 4 if roomy else 2
        top = y + (2 if roomy else 1)
        rows = max(1, h - top - footer)
        if self.sel < self.offset:
            self.offset = self.sel
        elif self.sel >= self.offset + rows:
            self.offset = self.sel - rows + 1
        self.offset = max(0, min(self.offset, max(0, len(self.items) - rows)))

        shown = self.items[self.offset : self.offset + rows]
        name_w = min(28, max([dwidth(it["name"]) for it in shown] + [8]))
        for row, it in enumerate(shown):
            self.draw_row(top + row, it, self.offset + row == self.sel, name_w, w)
        if not self.items:
            hint = "no such folder" if is_pathlike(self.query) else "no match · type a path to open any folder"
            self.put(top, 3, hint, dim)

        it = self.current()
        if roomy:
            dy = h - 4
            if self.msg:
                self.put(dy, 1, self.msg, warn | curses.A_BOLD)
            elif it:
                if it.get("reason"):
                    self.put(dy, 1, "✦ " + it["reason"], ai)
                elif it.get("recent"):
                    self.put(dy, 1, "“" + it["recent"][-1] + "”", dim)
                elif it.get("new"):
                    self.put(dy, 1, "never opened with Claude", dim)
                stats = short(it["path"])
                if it.get("sessions"):
                    stats += f" · {it['sessions']} sessions · {it['prompts']} prompts"
                self.put(dy + 1, 1, stats, dim)

        mode = self.mode_for(it)
        label = MODES[mode][0]
        cmd = claude_command(self.cfg, mode, self.cont)
        x = self.put(h - 2, 1, label + "  ", accent)
        x = self.put(h - 2, x, "$ " + cmd, curses.A_BOLD)
        note = "  (override)" if self.mode_override else "  (your usual here)" if it and it.get("sessions") else ""
        if it and x + len(note) < w:
            self.put(h - 2, x, note, dim)
        if self.msg and not roomy:
            self.put(h - 1, 1, self.msg, warn | curses.A_BOLD)
        else:
            self.put(h - 1, 1, "⏎ open  ↑↓ move  ←→ mode  ^R continue  tab dive  esc shell", dim)
        try:
            curses.curs_set(1)
            scr.move(*cursor)
        except curses.error:
            pass
        scr.refresh()

    def draw_row(self, y: int, it: dict, selected: bool, name_w: int, w: int) -> None:
        accent, dim = curses.color_pair(1), curses.color_pair(2)
        warn, ai = curses.color_pair(3), curses.color_pair(4)
        taken = self.claims.get(it["path"])
        if taken:
            tag, tattr = f"◉{taken}", warn
        elif it.get("here"):
            tag, tattr = "⏎", accent
        elif it.get("reason"):
            tag, tattr = "✦", ai
        elif it.get("new"):
            tag, tattr = "new", dim
        else:
            tag, tattr = "", dim
        when = ago(it["last"])
        right = f"{tag:>3} {when:>4} "
        bar = curses.A_REVERSE | accent if selected else 0
        if selected:
            self.put(y, 0, " " * (w - 1), bar)
        self.put(y, 0, " ▸ " if selected else "   ", (bar or accent) | curses.A_BOLD)
        avail = w - 3 - len(right) - 1
        if avail < 4:
            return
        nw = min(name_w, avail)
        name = it["name"] + ("/" if it.get("here") else "")
        if dwidth(name) > nw:
            name = clip(name, max(1, nw - 1)) + "…"
        nattr = bar | curses.A_BOLD if selected else dim if taken else curses.A_BOLD
        x = self.put(y, 3, pad(name, nw), nattr)
        parent_w = avail - nw - 2
        if parent_w > 6 and it["path"] != str(HOME):
            parent = short(os.path.dirname(it["path"]))
            if len(parent) > parent_w:
                parent = "…" + parent[-(parent_w - 1) :]
            self.put(y, x + 2, parent, bar or dim)
        rx = w - len(right) - 1
        self.put(y, rx, f"{tag:>3}", bar or tattr)
        self.put(y, rx + 3, f" {when:>4} ", bar or dim)

    # -- input

    def handle(self, ch):
        if ch == curses.KEY_RESIZE:
            return None
        self.touched = True
        self.msg = None
        n = len(self.items)
        if ch in ("\n", "\r", curses.KEY_ENTER):
            return self.enter()
        if ch in (curses.KEY_UP, "\x10"):
            self.sel = (self.sel - 1) % n if n else 0
        elif ch in (curses.KEY_DOWN, "\x0e"):
            self.sel = (self.sel + 1) % n if n else 0
        elif ch == curses.KEY_PPAGE:
            self.sel = max(0, self.sel - 10)
        elif ch == curses.KEY_NPAGE:
            self.sel = min(max(0, n - 1), self.sel + 10)
        elif ch in (curses.KEY_HOME,):
            self.sel = 0
        elif ch in (curses.KEY_END,):
            self.sel = max(0, n - 1)
        elif ch in (curses.KEY_LEFT, curses.KEY_RIGHT):
            cur = self.mode_for(self.current())
            step = 1 if ch == curses.KEY_RIGHT else -1
            self.mode_override = MODE_ORDER[(MODE_ORDER.index(cur) + step) % len(MODE_ORDER)]
        elif ch == "\x12":  # ^R
            self.cont = not self.cont
        elif ch == "\t":
            it = self.current()
            if it:
                self.query = short(it["path"]).rstrip("/") + "/"
                self.sel = 0
        elif ch in ("\x1b", "\x03"):  # esc, ^C
            if self.query:
                self.query, self.sel = "", 0
            else:
                return ("shell",)
        elif ch == "\x04":  # ^D
            if not self.query:
                return ("quit",)
        elif ch in (curses.KEY_BACKSPACE, "\x7f", "\x08"):
            self.query, self.sel = self.query[:-1], 0
        elif ch == "\x15":  # ^U
            self.query, self.sel = "", 0
        elif ch == "\x17":  # ^W
            q = self.query.rstrip("/ ")
            cut = max(q.rfind("/"), q.rfind(" "))
            self.query, self.sel = (q[: cut + 1] if cut >= 0 else ""), 0
        elif isinstance(ch, str) and ch.isprintable():
            self.query += ch
            self.sel = 0
        return None

    def enter(self):
        it = self.current()
        if not it:
            self.msg = "nothing to open here"
            return None
        if not os.path.isdir(it["path"]):
            self.msg = f"not a folder: {short(it['path'])}"
            return None
        return ("launch", it, self.mode_for(it), self.cont)

    def run(self):
        dirty = True
        while True:
            if self.poll() or dirty:
                self.draw()
                dirty = False
            # idle pickers wake once a second; only the AI spinner needs a faster tick
            self.scr.timeout(250 if self.ai_state == "pending" else 1000)
            try:
                ch = self.scr.get_wch()
            except curses.error:
                if self.ai_state == "pending":
                    self.tick += 1
                    dirty = True
                continue
            except KeyboardInterrupt:
                ch = "\x03"
            result = self.handle(ch)
            if result:
                return result
            dirty = True


def run_picker(ctx: dict):
    os.environ.setdefault("ESCDELAY", "25")
    locale.setlocale(locale.LC_ALL, "")
    try:
        curses.setupterm()
    except curses.error:  # terminfo for $TERM missing (older systems lack tmux-256color)
        os.environ["TERM"] = "xterm-256color"

    def main(scr):
        curses.use_default_colors()
        for i, color in enumerate(
            (
                curses.COLOR_GREEN,
                8 if curses.COLORS > 8 else curses.COLOR_WHITE,
                curses.COLOR_YELLOW,
                curses.COLOR_MAGENTA,
            ),
            start=1,
        ):
            curses.init_pair(i, color, -1)
        scr.keypad(True)
        return Picker(scr, ctx).run()

    return curses.wrapper(main)


# ---------------------------------------------------------------- pane lifecycle


def tmux_path() -> str | None:
    found = shutil.which("tmux")
    if found:
        return found
    for p in ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux"):
        if os.path.exists(p):
            return p
    return None


TMUX_HINT = "tmux is not installed. Install it with: " + (
    "brew install tmux" if IS_MAC else "sudo apt install tmux  (or your distro's package manager)"
)


def tmux(*args: str, check: bool = True) -> str:
    binary = tmux_path()
    if not binary:
        die(TMUX_HINT)
    r = subprocess.run([binary, "-L", SOCKET, *args], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(r.stderr.strip() or f"tmux {' '.join(args)} failed")
    return r.stdout.strip()


def in_cgrid_pane() -> bool:
    return bool(os.environ.get("TMUX_PANE")) and f"/{SOCKET}," in os.environ.get("TMUX", "")


def set_pane(**opts: str) -> None:
    pane = os.environ.get("TMUX_PANE")
    if not pane or not in_cgrid_pane():
        return
    for k, v in opts.items():
        tmux("set-option", "-p", "-t", pane, f"@{k}", v, check=False)


def record_pick(ctx: dict, path: str, mode: str, cont: bool) -> None:
    rec = {
        "ts": time.time(),
        "gid": ctx.get("gid"),
        "pane": ctx.get("pane"),
        "of": ctx.get("of"),
        "path": path,
        "mode": mode,
        "cont": cont,
    }
    try:
        PICKS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(PICKS_LOG, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def exec_claude(ctx: dict, path: str, mode: str, cont: bool) -> None:
    cfg = ctx["cfg"]
    name = display_name(path)
    if ctx.get("grid_dir") and ctx.get("pane"):
        try:  # write then rename, so other panes never read a half-written claim
            tmp = ctx["grid_dir"] / f".claim-{ctx['pane']}.tmp"
            tmp.write_text(path)
            os.replace(tmp, ctx["grid_dir"] / f"claim-{ctx['pane']}")
        except OSError:
            pass
    set_pane(proj=name, mode=MODES[mode][0])
    record_pick(ctx, path, mode, cont)
    cmd = claude_command(cfg, mode, cont)
    # clear our "cgrid" title so the pane border shows the title Claude Code sets
    sys.stdout.write("\033]2;\033\\")
    print(f"\n  \033[1;32m→ {name}\033[0m  \033[2m{short(path)}\033[0m\n  \033[2m$ {cmd}\033[0m\n", flush=True)
    os.chdir(path)
    shell = os.environ.get("SHELL") or "/bin/zsh"
    os.execvp(shell, [shell, "-l", "-i", "-c", f"{cmd}; exec {shlex.quote(shell)} -l -i"])


def exec_shell(ctx: dict) -> None:
    set_pane(proj="shell", mode="")
    try:
        os.chdir(ctx["cwd"])
    except OSError:
        os.chdir(HOME)
    shell = os.environ.get("SHELL") or "/bin/zsh"
    os.execvp(shell, [shell, "-l", "-i"])


def pane_main(ctx: dict, preset: str | None = None) -> None:
    sys.stdout.write("\033]2;cgrid\033\\")
    sys.stdout.flush()
    if ctx.get("pane"):
        set_pane(n=str(ctx["pane"]))
    if preset:
        if os.path.isdir(preset):
            item = ctx["index_by_path"].get(preset, {})
            mode = ctx.get("mode") or item.get("mode") or ctx["index"]["global_mode"]
            exec_claude(ctx, preset, mode, ctx.get("cont", False))
        ctx["message"] = f"preset folder is gone: {short(preset)}"
    result = run_picker(ctx)
    if result[0] == "launch":
        _, item, mode, cont = result
        exec_claude(ctx, item["path"], mode, cont)
    elif result[0] == "shell":
        exec_shell(ctx)
    sys.exit(0)


def make_ctx(cfg: dict, index: dict, **kw) -> dict:
    ctx = {"cfg": cfg, "index": index, "cwd": os.getcwd(), "of": 1, "pane": None}
    ctx.update(kw)
    ctx["index_by_path"] = {p["path"]: p for p in index["projects"]}
    return ctx


# ---------------------------------------------------------------- AI ranking

AI_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["path", "reason"],
            },
        }
    },
    "required": ["picks"],
}


def ai_rank(cfg: dict, index: dict, n: int, previous: list) -> dict:
    now = time.time()
    cands = list(index["projects"][:20])
    have = {c["path"] for c in cands}
    by_path = {p["path"]: p for p in index["projects"]}
    for path in previous:
        if path not in have and path in by_path:
            cands.append(by_path[path])
            have.add(path)
    lines = []
    for c in cands:
        recent = " | ".join(f'"{t[:110]}"' for t in c["recent"][-2:]) or "none"
        lines.append(
            f"- {c['path']} (last used {ago(c['last'], now) or 'never'} ago; sessions 7d={c['s7']} "
            f"30d={c['s30']} total={c['sessions']}; recent prompts: {recent})"
        )
    prev = f"\nTheir previous grid had: {', '.join(previous)}\n" if previous else ""
    prompt = (
        f"It is {time.strftime('%A %Y-%m-%d %H:%M')}. A developer is opening {n} terminal panes, "
        f"each running a Claude Code session in one project folder.\n"
        f"Pick the {min(n, len(cands))} folders they most likely want right now, best first. Weigh momentum "
        f"(recent, frequent sessions), unfinished threads in the recent prompts, and projects that belong "
        f"together (an API with its app, for example). Prefer real project folders over broad parents such as "
        f"the home or Documents folder unless they are clearly used as projects. Use exact paths from the "
        f"list. Each reason: at most 7 words, no trailing period.\n{prev}\nCandidates:\n" + "\n".join(lines)
    )
    cmd = [
        cfg["claude"],
        "-p",
        "--model",
        cfg["ai_model"],
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(AI_SCHEMA),
        "--strict-mcp-config",
        "--no-session-persistence",
        "--setting-sources",
        "project",
        "--system-prompt",
        "You rank project folders for a developer. Answer only with the structured output.",
        "--tools",
        "",
    ]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=90, cwd=CACHE_DIR)
    out = json.loads(r.stdout or "{}")
    if r.returncode or out.get("is_error"):
        raise RuntimeError(out.get("result") or r.stderr.strip() or "claude -p failed")
    data = out.get("structured_output") or json.loads(out.get("result") or "{}")
    picks, seen = [], set()
    for p in data.get("picks", []):
        path = norm_project(p.get("path"))
        if path in have and path not in seen:
            picks.append({"path": path, "reason": (p.get("reason") or "").strip().rstrip(".")})
            seen.add(path)
    return {"picks": picks[:n], "cost": out.get("total_cost_usd")}


def ai_worker(grid_dir: Path, n: int) -> None:
    cfg = load_config()
    try:
        index = read_json(grid_dir / "suggestions.json") or build_index(cfg)
        meta = read_json(grid_dir / "meta.json", {})
        result = ai_rank(cfg, index, n, meta.get("previous", []))
    except Exception as e:  # noqa: BLE001 - report any failure to the panes
        result = {"picks": [], "error": str(e)[:300]}
    write_json(grid_dir / "ai.json", result)


# ---------------------------------------------------------------- grids


def tmux_conf() -> Path:
    truecolor = os.environ.get("COLORTERM") in ("truecolor", "24bit")
    term = "tmux-256color"
    if shutil.which("infocmp") and subprocess.run(["infocmp", term], capture_output=True).returncode:
        term = "screen-256color"
    border = (
        "#{?pane_active,#[fg=brightgreen bold],#[fg=green]} #{?#{@n},#{@n},·} "
        "#{?#{@proj},#{@proj},choose a folder…}#[nobold]"
        "#{?#{@mode}, #[fg=brightblack]#{@mode},}"
        "#{?#{@proj}, #[fg=brightblack]#{=/60/…:pane_title},} "
    )
    lines = [
        f'set -g default-terminal "{term}"',
        "set -s escape-time 10",
        "set -g focus-events on",
        "set -g history-limit 100000",
        "set -g mouse on",
        "set -g base-index 1",
        "set -g pane-base-index 1",
        "set -g set-titles on",
        'set -g set-titles-string "cgrid · #S"',
        "set -g allow-passthrough on",
        "set -g pane-border-status top",
        "set -g pane-border-lines single",
        'set -g pane-border-style "fg=green dim"',
        'set -g pane-active-border-style "fg=brightgreen"',
        f"set -g pane-border-format '{border}'",
        'set -g status-style "bg=default fg=green"',
        "set -g status-left-length 40",
        "set -g status-right-length 120",
        "set -g status-left ' ◆ cgrid #S '",
        "set -g status-right '#[fg=brightblack]click a pane to focus · ^b z zoom · ^b ←↑↓→ move · ^b d detach '",
        'set -g window-status-format ""',
        'set -g window-status-current-format ""',
        "set -s set-clipboard on",
    ]
    copy = clipboard_command()
    if copy:
        for table in ("copy-mode", "copy-mode-vi"):
            lines.append(f"bind -T {table} MouseDragEnd1Pane send -X copy-pipe-and-cancel {shlex.quote(copy)}")
    if truecolor:
        lines += ['set -as terminal-features ",xterm-256color:RGB"', "setenv -g COLORTERM truecolor"]
    path = CACHE_DIR / "tmux.conf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path


def clipboard_command() -> str | None:
    if IS_MAC:
        return "pbcopy"
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        return "wl-copy"
    if shutil.which("xclip"):
        return "xclip -selection clipboard"
    return None  # tmux still sets the clipboard via OSC 52 where the terminal allows it


def grid_sessions() -> list:
    out = tmux("list-sessions", "-F", "#{session_name}", check=False)
    return [s for s in out.splitlines() if s]


def new_grid_name() -> str:
    used = set(grid_sessions())
    k = 1
    while f"grid-{k}" in used:
        k += 1
    return f"grid-{k}"


def clean_run_dirs() -> None:
    alive = set(grid_sessions())
    if not RUN_DIR.exists():
        return
    for d in RUN_DIR.iterdir():
        if d.name not in alive:
            shutil.rmtree(d, ignore_errors=True)


def last_grid() -> dict:
    """pane number -> pick, for the most recent grid you launched."""
    picks = []
    try:
        with open(PICKS_LOG) as fh:
            for line in fh:
                try:
                    picks.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        return {}
    picks = [p for p in picks if p.get("gid") and p.get("pane")]
    if not picks:
        return {}
    gid = max(picks, key=lambda p: p["ts"])["gid"]
    out = {}
    for p in sorted((p for p in picks if p["gid"] == gid), key=lambda p: p["ts"]):
        out[int(p["pane"])] = p
    return out


def layout(n: int) -> list:
    rows = 1 if n <= 3 else 2 if n <= 8 else 3
    q, r = divmod(n, rows)
    return [q + 1 if i < r else q for i in range(rows)]


def build_grid(grid: str, n: int, pane_cmd, cwd: str) -> None:
    conf = tmux_conf()
    sizes = layout(n)
    starts = [1 + sum(sizes[:i]) for i in range(len(sizes))]
    cols, lines = shutil.get_terminal_size((240, 60))
    first = tmux(
        "-f",
        str(conf),
        "new-session",
        "-d",
        "-s",
        grid,
        "-x",
        str(max(cols, 200)),
        "-y",
        str(max(lines, 50)),
        "-c",
        cwd,
        "-P",
        "-F",
        "#{pane_id}",
        pane_cmd(1),
    )
    tmux("source-file", str(conf), check=False)
    row_ids, cur = [first], first
    for i in range(1, len(sizes)):
        pct = round(100 * (len(sizes) - i) / (len(sizes) - i + 1))
        cur = tmux(
            "split-window", "-v", "-t", cur, "-l", f"{pct}%", "-c", cwd, "-P", "-F", "#{pane_id}", pane_cmd(starts[i])
        )
        row_ids.append(cur)
    for i, rid in enumerate(row_ids):
        cur = rid
        for j in range(1, sizes[i]):
            pct = round(100 * (sizes[i] - j) / (sizes[i] - j + 1))
            cur = tmux(
                "split-window",
                "-h",
                "-t",
                cur,
                "-l",
                f"{pct}%",
                "-c",
                cwd,
                "-P",
                "-F",
                "#{pane_id}",
                pane_cmd(starts[i] + j),
            )
    tmux("select-pane", "-t", first, check=False)


JXA_TERMINAL = r"""
ObjC.import('AppKit');
function run(argv) {
  const app = Application(argv[1]);
  app.activate();
  let win;
  if (argv[1] === 'iTerm') {
    win = app.createWindowWithDefaultProfile({command: argv[0]});
  } else {
    app.doScript(argv[0]);
    delay(0.3);
    win = app.windows[0];
  }
  const vf = $.NSScreen.mainScreen.visibleFrame;
  const primary = $.NSScreen.screens.objectAtIndex(0).frame;
  const top = primary.size.height - (vf.origin.y + vf.size.height);
  try { win.bounds = {x: vf.origin.x, y: top, width: vf.size.width, height: vf.size.height}; } catch (e) {}
}
"""


def window_backend(cfg: dict) -> str:
    """Where a new grid shows up: "terminal" (Terminal.app), "iterm" (iTerm2) or "here"."""
    choice = str(cfg.get("terminal") or "auto").lower()
    if choice in ("terminal", "iterm", "here"):
        return choice
    if not IS_MAC:
        return "here"
    program = os.environ.get("TERM_PROGRAM", "")
    if program == "iTerm.app":
        return "iterm"
    if program == "Apple_Terminal" or not sys.stdout.isatty():
        return "terminal"
    return "here"  # Ghostty, WezTerm, kitty, VS Code…: take over the current window


def open_window(attach_cmd: str, backend: str) -> bool:
    app = "iTerm" if backend == "iterm" else "Terminal"
    r = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", JXA_TERMINAL, attach_cmd, app], capture_output=True, text=True
    )
    if r.returncode:
        print(f"cgrid: could not open a {app} window: {r.stderr.strip()}", file=sys.stderr)
        return False
    return True


def attach(grid: str, backend: str) -> None:
    binary = tmux_path()
    if backend == "here":
        if in_cgrid_pane():
            os.execvp(binary, [binary, "-L", SOCKET, "switch-client", "-t", grid])
        if os.environ.get("TMUX"):
            die(
                f"{grid} is ready, but you are inside another tmux session. "
                f"Run `cgrid attach {grid}` from a plain terminal."
            )
        if not sys.stdin.isatty():
            die(f"{grid} is ready; run `cgrid attach {grid}` in a terminal")
        os.execvp(binary, [binary, "-u", "-L", SOCKET, "attach", "-t", grid])
    cmd = f"clear; exec {shlex.quote(binary)} -u -L {shlex.quote(SOCKET)} attach -t {shlex.quote(grid)}"
    if not open_window(cmd, backend):
        print(f"attach manually with: cgrid attach {grid}")


def cmd_open(args, cfg: dict) -> None:
    if not tmux_path():
        die(TMUX_HINT)
    cwd = os.getcwd()
    index = build_index(cfg)
    items = index["projects"]
    n = args.n or cfg["panes"]
    presets: dict = {}
    mode, cont = args.mode, args.cont

    if args.last:
        prev = last_grid()
        if not prev:
            die("no previous grid recorded yet")
        n = args.n or max(max(prev), max(p.get("of") or 0 for p in prev.values()))
        for k, p in prev.items():
            presets[k] = (p["path"], mode or p.get("mode"))
    elif args.top:
        for k, p in enumerate(items[:n], start=1):
            presets[k] = (p["path"], mode or p["mode"])
    for k, arg in enumerate(args.projects, start=1):
        path = resolve_project(arg, items + root_dirs(cfg, {p["path"] for p in items}, index["global_mode"]), cwd)
        if not path:
            die(f"no project matches '{arg}'")
        presets[k] = (path, mode)
    n = max(1, min(MAX_PANES, max([n, *presets])))

    clean_run_dirs()
    grid = new_grid_name()
    grid_dir = RUN_DIR / grid
    shutil.rmtree(grid_dir, ignore_errors=True)
    grid_dir.mkdir(parents=True)
    gid = f"{grid}-{int(time.time())}"
    use_ai = (args.ai or cfg["ai"]) and len(presets) < n and not args.no_ai
    previous = [p["path"] for p in last_grid().values()]
    write_json(grid_dir / "suggestions.json", index)
    write_json(grid_dir / "meta.json", {"gid": gid, "n": n, "ai": use_ai, "previous": previous, "cwd": cwd})
    if use_ai:
        subprocess.Popen(
            [sys.executable, SCRIPT, "_ai", str(grid_dir), str(n)],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def pane_cmd(k: int) -> str:
        argv = [sys.executable, SCRIPT, "_pane", "--grid", grid, "--pane", str(k), "--of", str(n)]
        if k in presets:
            path, pmode = presets[k]
            argv += ["--preset", path] + (["--mode", pmode] if pmode else [])
        elif mode:
            argv += ["--mode", mode]
        if cont:
            argv.append("--continue")
        return "exec " + shlex.join(argv)

    build_grid(grid, n, pane_cmd, cwd)
    others = [g for g in grid_sessions() if g != grid]
    print(f"cgrid: opened {grid} with {n} panes" + (f" ({len(presets)} preset)" if presets else ""))
    if others:
        print(f"       also running: {', '.join(others)}  (cgrid ls · cgrid attach · cgrid kill)")
    if args.detach:
        return
    attach(grid, "here" if args.here else window_backend(cfg))


# ---------------------------------------------------------------- other commands


def cmd_pick(args, cfg: dict) -> None:
    index = build_index(cfg)
    ctx = make_ctx(cfg, index, mode=args.mode, cont=args.cont)
    if in_cgrid_pane():
        grid = tmux("display-message", "-p", "-t", os.environ["TMUX_PANE"], "#S", check=False)
        pane = tmux("display-message", "-p", "-t", os.environ["TMUX_PANE"], "#{@n}", check=False)
        grid_dir = RUN_DIR / grid
        if grid_dir.is_dir() and pane.isdigit():
            meta = read_json(grid_dir / "meta.json", {})
            ctx.update(grid_dir=grid_dir, pane=int(pane), of=meta.get("n", 1), gid=meta.get("gid"))
            (grid_dir / f"claim-{pane}").unlink(missing_ok=True)
    pane_main(ctx)


def cmd_pane(args, cfg: dict) -> None:
    grid_dir = RUN_DIR / args.grid
    meta = read_json(grid_dir / "meta.json", {})
    index = read_json(grid_dir / "suggestions.json") or build_index(cfg)
    ctx = make_ctx(
        cfg,
        index,
        grid_dir=grid_dir,
        pane=args.pane,
        of=args.of,
        gid=meta.get("gid"),
        mode=args.mode,
        cont=args.cont,
        ai=meta.get("ai", False),
        cwd=meta.get("cwd") or os.getcwd(),
    )
    pane_main(ctx, args.preset)


def cmd_top(args, cfg: dict) -> None:
    index = build_index(cfg)
    items = index["projects"][: args.limit]
    if args.json:
        print(json.dumps(items, indent=2))
        return
    if not items:
        print(f"No Claude Code sessions found in {CLAUDE_DIR}. Run `claude` in a few projects, then try again.")
        return
    now = time.time()
    width = shutil.get_terminal_size((120, 40)).columns
    name_w = min(26, max([len(p["name"]) for p in items] + [7]))
    print(f"\033[2m  #  {'project'.ljust(name_w)}  last  sess  mode          recent prompt\033[0m")
    for k, p in enumerate(items, start=1):
        recent = p["recent"][-1] if p["recent"] else ""
        head = (
            f"{k:>3}  {p['name'][:name_w].ljust(name_w)}  {ago(p['last'], now):>4}  {p['sessions']:>4}  "
            f"{pad(MODES[p['mode']][0], 12)}  "
        )
        room = max(0, width - len(head) - 2)
        print(f"{head}\033[2m{recent[:room]}\033[0m")
    print(f"\033[2m  usual mode overall: {MODES[index['global_mode']][0]}\033[0m")


def process_table() -> tuple:
    """pid -> (rss KB, cpu %) and ppid -> [child pids], from one `ps` call."""
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss=,pcpu="], capture_output=True, text=True).stdout
    usage, children = {}, {}
    for line in out.splitlines():
        try:
            pid, ppid, rss, cpu = line.split()[:4]
            usage[int(pid)] = (int(rss), float(cpu))
            children.setdefault(int(ppid), []).append(int(pid))
        except ValueError:
            continue
    return usage, children


def tree_usage(pid: int, usage: dict, children: dict) -> tuple:
    """Memory (MB), CPU % and process count of a pane: the shell, claude, and its MCP servers and tools."""
    mb = cpu = 0.0
    count = 0
    stack = [pid]
    while stack:
        p = stack.pop()
        if p in usage:
            mb += usage[p][0] / 1024
            cpu += usage[p][1]
            count += 1
        stack.extend(children.get(p, []))
    return mb, cpu, count


def fmt_mb(mb: float) -> str:
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def cmd_ls(args, cfg: dict) -> None:
    grids = grid_sessions()
    if not grids:
        print("no grids running")
        return
    usage, children = process_table()
    total_mb = total_cpu = 0.0
    sessions = 0
    for g in grids:
        info = tmux("display-message", "-p", "-t", g, "#{session_attached} #{session_created}", check=False).split()
        attached = info and info[0] != "0"
        created = ago(float(info[1])) if len(info) > 1 else ""
        started = "just now" if created == "now" else f"{created} ago"
        rows = []
        panes = tmux("list-panes", "-s", "-t", g, "-F", "#{@n}\t#{@proj}\t#{@mode}\t#{pane_pid}", check=False)
        for line in panes.splitlines():
            num, proj, mode, pid = (line.split("\t") + ["", "", "", ""])[:4]
            mb, cpu, count = tree_usage(int(pid), usage, children) if pid.isdigit() else (0.0, 0.0, 0)
            rows.append((num, proj, mode, mb, cpu, count))
        grid_mb = sum(r[3] for r in rows)
        grid_cpu = sum(r[4] for r in rows)
        claude_panes = sum(1 for r in rows if r[1] and r[1] != "shell")
        total_mb, total_cpu, sessions = total_mb + grid_mb, total_cpu + grid_cpu, sessions + claude_panes
        print(
            f"\033[1m{g}\033[0m  {'attached' if attached else 'detached'} · started {started}"
            f"\033[2m · {fmt_mb(grid_mb)} · {grid_cpu:.0f}% cpu\033[0m"
        )
        for num, proj, mode, mb, cpu, count in rows:
            print(
                f"   {num or '·':>2}  {pad(proj or 'choosing…', 22)} {pad(mode, 14)}"
                f"\033[2m{fmt_mb(mb):>8} {cpu:5.1f}%  {count:>2} proc{'' if count == 1 else 's'}\033[0m"
            )
    if len(grids) > 1 or sessions:
        print(f"\033[2mtotal: {sessions} claude sessions · {fmt_mb(total_mb)} · {total_cpu:.0f}% cpu\033[0m")


def cmd_attach(args, cfg: dict) -> None:
    grids = grid_sessions()
    if not grids:
        die("no grids running")
    grid = args.grid or grids[-1]
    if grid.isdigit():
        grid = f"grid-{grid}"
    if grid not in grids:
        die(f"no grid named {grid} (running: {', '.join(grids)})")
    attach(grid, window_backend(cfg) if args.window else "here")


def cmd_kill(args, cfg: dict) -> None:
    grids = grid_sessions()
    targets = grids if args.all else [args.grid or (grids[-1] if grids else None)]
    for g in targets:
        if not g:
            die("no grids running")
        if g.isdigit():
            g = f"grid-{g}"
        tmux("kill-session", "-t", g, check=False)
        shutil.rmtree(RUN_DIR / g, ignore_errors=True)
        print(f"killed {g}")


def cmd_doctor(args, cfg: dict) -> None:
    def row(good: bool, label: str, detail: str) -> None:
        mark = "\033[32m✓\033[0m" if good else "\033[31m✗\033[0m"
        print(f" {mark} {label:<12} {detail}")

    def version(cmd: list) -> str:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            return (r.stdout or r.stderr).strip().splitlines()[0]
        except (OSError, subprocess.SubprocessError, IndexError):
            return "?"

    print(f"cgrid {__version__} · {sys.platform}")
    row(sys.version_info >= (3, 9), "python", f"{sys.version.split()[0]} ({sys.executable})")
    tm = tmux_path()
    row(bool(tm), "tmux", f"{version([tm, '-V'])} ({tm})" if tm else TMUX_HINT)
    cl = shutil.which(cfg["claude"])
    row(bool(cl), "claude", f"{version([cl, '--version'])} ({cl})" if cl else f"`{cfg['claude']}` not found on PATH")
    hist = CLAUDE_DIR / "history.jsonl"
    sessions = len(scan_history())
    row(sessions > 0, "history", f"{sessions} sessions in {hist}" if hist.exists() else f"{hist} not found")
    count = len(list((CLAUDE_DIR / "projects").glob("*/*.jsonl")))
    row(count > 0, "transcripts", f"{count} in {CLAUDE_DIR / 'projects'}")
    backend = window_backend(cfg)
    names = {"terminal": "new Terminal.app window", "iterm": "new iTerm2 window", "here": "current terminal window"}
    row(
        True,
        "window",
        f"{names[backend]} (terminal={cfg.get('terminal')}, TERM_PROGRAM={os.environ.get('TERM_PROGRAM', '-')})",
    )
    row(True, "config", f"{CONFIG_PATH}" + ("" if CONFIG_PATH.exists() else " (defaults; `cgrid config` writes it)"))


def cmd_config(args, cfg: dict) -> None:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
        print(f"wrote defaults to {CONFIG_PATH}")
    print(CONFIG_PATH)
    print(json.dumps(load_config(), indent=2))


# ---------------------------------------------------------------- CLI

USAGE = """\
cgrid [projects…] [options]     open a grid window of Claude panes
cgrid pick                      pick a folder in this terminal and start claude
cgrid top [-n 20]               show how your folders are ranked
cgrid ls | attach [grid] | kill [grid|--all] | config | doctor"""

EPILOG = """\
examples:
  cgrid                         6 panes, each asks which folder to open
  cgrid --top                   open your top 6 folders right away
  cgrid --last -c               reopen your previous grid, continuing each conversation
  cgrid api web -n 4            two panes preset by fuzzy name, two pickers
  cgrid --ai                    let Claude (haiku) pick the suggestions for this grid
  cgrid --mode plan             start every pane in plan mode

inside the picker:
  type to filter · ~/path to browse · ⏎ open · ↑↓ move · ←→ change mode
  ^R toggle --continue · tab dive into folder · esc plain shell · ^D close pane
"""


def main(argv: list | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    cfg = load_config()
    if argv and argv[0] in ("-V", "--version", "version"):
        print(f"cgrid {__version__}")
        return
    mode_choices = MODE_ORDER

    if argv and argv[0] == "_pane":
        p = argparse.ArgumentParser(prog="cgrid _pane")
        p.add_argument("--grid", required=True)
        p.add_argument("--pane", type=int, required=True)
        p.add_argument("--of", type=int, default=1)
        p.add_argument("--preset")
        p.add_argument("--mode", choices=mode_choices)
        p.add_argument("--continue", dest="cont", action="store_true")
        return cmd_pane(p.parse_args(argv[1:]), cfg)
    if argv and argv[0] == "_ai":
        return ai_worker(Path(argv[1]), int(argv[2]))

    sub = argv[0] if argv else ""
    if sub == "pick":
        p = argparse.ArgumentParser(prog="cgrid pick")
        p.add_argument("--mode", choices=mode_choices)
        p.add_argument("-c", "--continue", dest="cont", action="store_true")
        return cmd_pick(p.parse_args(argv[1:]), cfg)
    if sub in ("top", "suggest"):
        p = argparse.ArgumentParser(prog="cgrid top")
        p.add_argument("-n", "--limit", type=int, default=15)
        p.add_argument("--json", action="store_true")
        return cmd_top(p.parse_args(argv[1:]), cfg)
    if sub == "ls":
        return cmd_ls(None, cfg)
    if sub == "attach":
        p = argparse.ArgumentParser(prog="cgrid attach")
        p.add_argument("grid", nargs="?")
        p.add_argument("-w", "--window", action="store_true", help="open in a new window instead of here")
        return cmd_attach(p.parse_args(argv[1:]), cfg)
    if sub == "kill":
        p = argparse.ArgumentParser(prog="cgrid kill")
        p.add_argument("grid", nargs="?")
        p.add_argument("--all", action="store_true")
        return cmd_kill(p.parse_args(argv[1:]), cfg)
    if sub == "config":
        return cmd_config(None, cfg)
    if sub == "doctor":
        return cmd_doctor(None, cfg)

    p = argparse.ArgumentParser(
        prog="cgrid", usage=USAGE, epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("projects", nargs="*", help="folders to preset, by fuzzy name or path")
    p.add_argument("-n", type=int, help=f"number of panes (default {cfg['panes']}, max {MAX_PANES})")
    p.add_argument("--top", action="store_true", help="launch your top-ranked folders without asking")
    p.add_argument("--last", action="store_true", help="reopen the folders from your previous grid")
    p.add_argument("--ai", action="store_true", help="rank suggestions with Claude for this grid")
    p.add_argument("--no-ai", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--mode", choices=mode_choices, help="permission mode for every pane")
    p.add_argument("-c", "--continue", dest="cont", action="store_true", help="pass --continue to claude")
    p.add_argument("--here", action="store_true", help="use this terminal window instead of a new one")
    p.add_argument("--detach", action="store_true", help="create the grid without attaching")
    cmd_open(p.parse_args(argv), cfg)


if __name__ == "__main__":
    main()
