from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid

import pytest
from conftest import ROOT

import cgrid

# ---------------------------------------------------------------- pure helpers


def test_norm_project_folds_worktrees():
    assert cgrid.norm_project("/x/api/.claude/worktrees/brave-fox-1a2b") == "/x/api"
    assert cgrid.norm_project("/x/api/") == "/x/api"
    assert cgrid.norm_project(None) is None


def test_clean_prompt_drops_noise():
    assert cgrid.clean_prompt("/clear") is None
    assert cgrid.clean_prompt("ok") is None
    assert cgrid.clean_prompt("fix   the\nlogin  flow") == "fix the login flow"
    assert cgrid.clean_prompt("[Pasted text #1 +20 lines] explain this crash") == "explain this crash"


def test_parse_ts_accepts_ms_and_iso():
    assert cgrid.parse_ts(1_700_000_000_000) == 1_700_000_000
    assert cgrid.parse_ts("2023-11-14T22:13:20Z") == 1_700_000_000
    assert cgrid.parse_ts("garbage") is None


@pytest.mark.parametrize(
    "n, rows",
    [(1, [1]), (3, [3]), (4, [2, 2]), (5, [3, 2]), (6, [3, 3]), (7, [4, 3]), (9, [3, 3, 3]), (10, [4, 3, 3])],
)
def test_layout(n, rows):
    assert cgrid.layout(n) == rows
    assert sum(rows) == n


def test_ago():
    now = 1_000_000_000
    assert cgrid.ago(0) == ""
    assert cgrid.ago(now - 30, now) == "now"
    assert cgrid.ago(now - 600, now) == "10m"
    assert cgrid.ago(now - 3 * 3600, now) == "3h"
    assert cgrid.ago(now - 3 * 86400, now) == "3d"
    assert cgrid.ago(now - 21 * 86400, now) == "3w"


def test_display_width_handles_wide_glyphs():
    assert cgrid.dwidth("⚡ skip") == 7
    assert cgrid.pad("⚡", 3) == "⚡ "
    assert cgrid.clip("ab⚡cd", 3) == "ab"


def test_claude_command():
    cfg = {"claude": "claude"}
    assert cgrid.claude_command(cfg, "skip", False) == "claude --dangerously-skip-permissions"
    assert cgrid.claude_command(cfg, "plan", True) == "claude --permission-mode plan --continue"
    assert cgrid.claude_command(cfg, "ask", False) == "claude"
    assert cgrid.claude_command({"claude": "/opt/my tools/claude"}, "ask", False) == "'/opt/my tools/claude'"


def item(name, score=0.0, path=None):
    return {"name": name, "path": path or f"/code/{name}", "score": score}


def test_fuzzy_prefers_name_prefix_then_substring_then_subsequence():
    items = [item("acme-billing-api", 5), item("api-gateway", 1), item("maps", 9), item("payments-api", 2)]
    assert [i["name"] for i in cgrid.fuzzy_filter("api", items)][:3] == [
        "api-gateway",
        "acme-billing-api",
        "payments-api",
    ]
    assert cgrid.fuzzy_filter("billapi", items)[0]["name"] == "acme-billing-api"
    assert [i["name"] for i in cgrid.fuzzy_filter("mps", items)] == ["maps"]
    assert [i["name"] for i in cgrid.fuzzy_filter("pmtapi", items)] == ["payments-api"]
    assert cgrid.fuzzy_filter("zzz", items) == []


# ---------------------------------------------------------------- history index


def test_index_ranks_by_recency_and_intensity(home, cfg):
    hot = home.project("code/hot")
    warm = home.project("code/warm")
    cold = home.project("code/cold")
    for _ in range(3):
        home.session(hot, age_days=0.1, prompts=20)
    home.session(warm, age_days=2, prompts=5)
    for _ in range(10):
        home.session(cold, age_days=120, prompts=30)
    names = [p["name"] for p in cgrid.build_index(cfg)["projects"]]
    assert names == ["hot", "warm", "cold"]


def test_index_folds_worktrees_and_filters_missing_and_excluded(home, cfg):
    api = home.project("code/api")
    home.session(api, age_days=1)
    home.session(api + "/.claude/worktrees/brave-fox", age_days=1)
    home.session(str(home.root / "code" / "deleted"), age_days=1)
    home.session(home.project(".claude-mem/observer"), age_days=1)
    projects = cgrid.build_index(cfg)["projects"]
    assert [p["path"] for p in projects] == [api]
    assert projects[0]["sessions"] == 2


def test_index_keeps_recent_prompts(home, cfg):
    api = home.project("code/api")
    home.session(api, age_days=3, prompts=1, texts=["add rate limiting to the login endpoint"])
    home.session(api, age_days=1, prompts=2, texts=["/clear", "write tests for the refresh token flow"])
    recent = cgrid.build_index(cfg)["projects"][0]["recent"]
    assert recent[-1] == "write tests for the refresh token flow"
    assert "/clear" not in recent


def test_mode_is_learned_per_project_with_global_fallback(home, cfg):
    yolo = home.project("code/yolo")
    careful = home.project("code/careful")
    fresh = home.project("code/fresh")
    for _ in range(4):
        home.session(yolo, age_days=1, mode="bypassPermissions")
    for _ in range(3):
        home.session(careful, age_days=1, mode="plan")
    home.session(fresh, age_days=1, mode=None)
    index = cgrid.build_index(cfg)
    modes = {p["name"]: p["mode"] for p in index["projects"]}
    assert modes == {"yolo": "skip", "careful": "plan", "fresh": "skip"}
    assert index["global_mode"] == "skip"


def test_default_mode_config_overrides_learning(home, cfg):
    home.session(home.project("code/x"), mode="bypassPermissions")
    cfg["default_mode"] = "ask"
    assert cgrid.build_index(cfg)["projects"][0]["mode"] == "ask"


def test_shell_history_is_the_last_fallback_for_mode(home, cfg):
    home.session(home.project("code/x"), mode=None)
    (home.root / ".zsh_history").write_text(": 1700000000:0;claude --dangerously-skip-permissions\n: 1:0;ls\n")
    assert cgrid.build_index(cfg)["global_mode"] == "skip"
    (home.root / ".zsh_history").write_text("claude\nclaude\n")
    assert cgrid.build_index(cfg)["global_mode"] == "ask"


def test_desktop_sessions_count_but_sdk_sessions_do_not(home, cfg):
    desktop = home.project("code/desktop-only")
    bot = home.project("code/automation")
    home.session(desktop, in_history=False, entrypoint="claude-desktop")
    home.session(bot, in_history=False, entrypoint="sdk-ts")
    assert [p["name"] for p in cgrid.build_index(cfg)["projects"]] == ["desktop-only"]


def test_transcript_cache_is_reused(home, cfg):
    home.session(home.project("code/api"))
    cgrid.build_index(cfg)
    cache = json.loads((cgrid.CACHE_DIR / "transcripts.json").read_text())
    assert len(cache) == 1
    assert cgrid.build_index(cfg)["projects"][0]["name"] == "api"


def test_empty_history(home, cfg):
    index = cgrid.build_index(cfg)
    assert index["projects"] == []
    assert index["global_mode"] == "ask"


def test_resolve_project(home, cfg):
    api = home.project("code/acme-api")
    home.session(api)
    items = cgrid.build_index(cfg)["projects"]
    assert cgrid.resolve_project("acmeapi", items, str(home.root)) == api
    assert cgrid.resolve_project("~/code/acme-api", items, "/") == api
    assert cgrid.resolve_project("code/acme-api", items, str(home.root)) == api
    assert cgrid.resolve_project("nope", items, str(home.root)) is None


# ---------------------------------------------------------------- grids and picks


def write_picks(*picks):
    cgrid.PICKS_LOG.parent.mkdir(parents=True, exist_ok=True)
    cgrid.PICKS_LOG.write_text("".join(json.dumps(p) + "\n" for p in picks))


def test_last_grid_returns_latest_pick_per_pane(home):
    write_picks(
        {"ts": 1, "gid": "old", "pane": 1, "path": "/a"},
        {"ts": 5, "gid": "new", "pane": 1, "path": "/b"},
        {"ts": 6, "gid": "new", "pane": 2, "path": "/c"},
        {"ts": 7, "gid": "new", "pane": 1, "path": "/d"},
        {"ts": 9, "gid": None, "pane": None, "path": "/single-terminal-pick"},
    )
    assert {k: v["path"] for k, v in cgrid.last_grid().items()} == {1: "/d", 2: "/c"}


def test_tmux_conf(home, monkeypatch):
    monkeypatch.setenv("COLORTERM", "truecolor")
    conf = cgrid.tmux_conf().read_text()
    assert "pane-border-status top" in conf
    assert "escape-time 10" in conf
    assert "RGB" in conf


def test_window_backend(monkeypatch):
    assert cgrid.window_backend({"terminal": "iterm"}) == "iterm"
    monkeypatch.setattr(cgrid, "IS_MAC", False)
    assert cgrid.window_backend({"terminal": "auto"}) == "here"


# ---------------------------------------------------------------- picker state (no curses needed)


def make_picker(home, cfg, **kw):
    index = cgrid.build_index(cfg)
    ctx = cgrid.make_ctx(cfg, index, cwd=str(home.root), **kw)
    return cgrid.Picker(None, ctx)


def test_each_pane_defaults_to_a_different_project_and_skips_claimed(home, cfg, tmp_path):
    for i, name in enumerate(["a", "b", "c", "d"]):
        home.session(home.project(f"code/{name}"), age_days=i, prompts=5)
    grid_dir = tmp_path / "grid"
    grid_dir.mkdir()
    picker = make_picker(home, cfg, grid_dir=grid_dir, pane=2, of=4)
    picker.poll()
    assert picker.current()["name"] == "b"
    (grid_dir / "claim-1").write_text(str(home.root / "code" / "b"))
    picker.poll()
    assert picker.current()["name"] == "c"
    assert picker.claims == {str(home.root / "code" / "b"): "1"}


def test_typing_filters_and_tab_dives_into_folders(home, cfg):
    api = home.project("code/acme-api")
    home.project("code/acme-api/services/billing")
    home.project("code/acme-web")
    home.session(api)
    picker = make_picker(home, cfg)
    for ch in "acmeapi":
        picker.handle(ch)
    picker.poll()
    assert picker.current()["path"] == api
    picker.handle("\t")
    picker.poll()
    assert picker.query == "~/code/acme-api/"
    assert picker.items[0].get("here") and picker.items[0]["path"] == api
    assert [i["name"] for i in picker.items[1:]] == ["services"]


def test_never_opened_folders_are_offered_when_there_is_no_history(home, cfg):
    home.project("Documents/side-project")
    picker = make_picker(home, cfg)
    picker.poll()
    assert [i["name"] for i in picker.items] == ["side-project"]


def test_mode_cycles_and_enter_launches(home, cfg):
    home.session(home.project("code/api"))
    picker = make_picker(home, cfg)
    picker.poll()
    import curses

    picker.handle(curses.KEY_RIGHT)
    picker.handle("\x12")  # ^R
    action, it, mode, cont = picker.handle("\n")
    assert (action, it["name"], mode, cont) == ("launch", "api", "auto", True)
    assert picker.handle("\x1b") == ("shell",)


def test_ai_picks_reorder_the_list(home, cfg, tmp_path):
    for name in ["a", "b", "c"]:
        home.session(home.project(f"code/{name}"))
    grid_dir = tmp_path / "grid"
    grid_dir.mkdir()
    picker = make_picker(home, cfg, grid_dir=grid_dir, pane=1, of=3, ai=True)
    picker.poll()
    assert picker.ai_state == "pending"
    c = str(home.root / "code" / "c")
    (grid_dir / "ai.json").write_text(json.dumps({"picks": [{"path": c, "reason": "unfinished refactor"}]}))
    picker.poll()
    assert picker.ai_state == "ready"
    assert picker.items[0]["path"] == c and picker.items[0]["reason"] == "unfinished refactor"


# ---------------------------------------------------------------- AI ranking with a stub claude


def test_ai_rank_validates_paths(home, cfg, tmp_path):
    api = home.project("code/api")
    web = home.project("code/web")
    home.session(api)
    home.session(web)
    reply = {
        "structured_output": {
            "picks": [
                {"path": web, "reason": "Pairs with the API."},
                {"path": "/etc", "reason": "not a candidate"},
                {"path": web, "reason": "duplicate"},
                {"path": api, "reason": "momentum"},
            ]
        },
        "total_cost_usd": 0.01,
    }
    stub = tmp_path / "claude-stub"
    stub.write_text(f"#!/bin/sh\ncat > /dev/null\necho '{json.dumps(reply)}'\n")
    stub.chmod(0o755)
    cfg["claude"] = str(stub)
    result = cgrid.ai_rank(cfg, cgrid.build_index(cfg), 3, [])
    assert result["picks"] == [{"path": web, "reason": "Pairs with the API"}, {"path": api, "reason": "momentum"}]


# ---------------------------------------------------------------- end to end through tmux


def wait_for(predicate, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.2)
    return predicate()


@pytest.mark.skipif(not shutil.which("tmux"), reason="tmux not installed")
def test_grid_end_to_end(home, tmp_path):
    api = home.project("code/acme-api")
    web = home.project("code/acme-web")
    home.session(api, age_days=0.1, prompts=9)
    home.session(web, age_days=1)
    stub = tmp_path / "claude"
    stub.write_text('#!/bin/sh\necho "STUB-CLAUDE cwd=$PWD args=$*"\n')
    stub.chmod(0o755)
    socket = f"cgrid-test-{uuid.uuid4().hex[:8]}"
    (tmp_path / "config.json").write_text(json.dumps({"exclude": ["~/.claude"]}))
    env = {
        **os.environ,
        "HOME": str(home.root),
        "CLAUDE_CONFIG_DIR": str(home.claude),
        "CGRID_CACHE": str(tmp_path / "cache"),
        "CGRID_CONFIG": str(tmp_path / "config.json"),
        "CGRID_SOCKET": socket,
        "CGRID_CLAUDE": str(stub),
        "SHELL": "/bin/sh",
        "TERM": "xterm-256color",
    }
    env.pop("TMUX", None)

    def tmux(*args):
        return subprocess.run(["tmux", "-L", socket, *args], capture_output=True, text=True).stdout

    try:
        out = subprocess.run(
            [sys.executable, str(ROOT / "cgrid.py"), "-n", "2", "--detach"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert out.returncode == 0, out.stderr
        assert len(tmux("list-panes", "-s", "-t", "grid-1", "-F", "#{pane_id}").split()) == 2
        assert wait_for(lambda: "pane 2/2" in tmux("capture-pane", "-p", "-t", "grid-1.2"))
        tmux("send-keys", "-t", "grid-1.2", "web")
        assert wait_for(lambda: "acme-web" in tmux("capture-pane", "-p", "-t", "grid-1.2"))
        tmux("send-keys", "-t", "grid-1.2", "Enter")
        line = wait_for(
            lambda: next(
                (ln for ln in tmux("capture-pane", "-p", "-J", "-t", "grid-1.2").splitlines() if "STUB-CLAUDE" in ln),
                None,
            )
        )
        assert line and f"cwd={web}" in line and "--dangerously-skip-permissions" in line
        assert (tmp_path / "cache" / "run" / "grid-1" / "claim-2").read_text() == web
        assert wait_for(lambda: "acme-web" in tmux("list-panes", "-s", "-t", "grid-1", "-F", "#{@proj}"))
    finally:
        subprocess.run(["tmux", "-L", socket, "kill-server"], capture_output=True)
