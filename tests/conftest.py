"""Fixtures that build a fake $HOME with Claude Code history, so tests never touch your real data."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cgrid  # noqa: E402

DAY = 86400


class FakeHome:
    def __init__(self, root: Path):
        self.root = root
        self.claude = root / ".claude"
        (self.claude / "projects").mkdir(parents=True)
        self.now = time.time()

    def project(self, rel: str) -> str:
        path = self.root / rel
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def session(
        self,
        cwd: str,
        *,
        age_days: float = 0,
        prompts: int = 3,
        mode: str | None = "bypassPermissions",
        entrypoint: str = "cli",
        in_history: bool = True,
        texts: list | None = None,
    ) -> str:
        sid = str(uuid.uuid4())
        start = self.now - age_days * DAY
        if in_history:
            with open(self.claude / "history.jsonl", "a") as fh:
                for i in range(prompts):
                    text = (texts or [])[i] if texts and i < len(texts) else f"prompt number {i} for this session"
                    rec = {
                        "display": text,
                        "pastedContents": {},
                        "timestamp": int((start + i * 60) * 1000),
                        "project": cwd,
                        "sessionId": sid,
                    }
                    fh.write(json.dumps(rec) + "\n")
        folder = self.claude / "projects" / cwd.replace("/", "-").replace(".", "-")
        folder.mkdir(exist_ok=True)
        stamp = datetime.fromtimestamp(start, timezone.utc).isoformat().replace("+00:00", "Z")
        line = {"type": "user", "cwd": cwd, "sessionId": sid, "entrypoint": entrypoint, "timestamp": stamp}
        if mode:
            line["permissionMode"] = mode
        transcript = folder / f"{sid}.jsonl"
        transcript.write_text(json.dumps({"type": "file-history-snapshot"}) + "\n" + json.dumps(line) + "\n")
        mtime = start + prompts * 60
        os.utime(transcript, (mtime, mtime))
        return sid


@pytest.fixture
def home(tmp_path, monkeypatch) -> FakeHome:
    root = tmp_path / "home"
    root.mkdir()
    fake = FakeHome(root)
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr(cgrid, "HOME", root)
    monkeypatch.setattr(cgrid, "CLAUDE_DIR", fake.claude)
    cache = tmp_path / "cache"
    monkeypatch.setattr(cgrid, "CACHE_DIR", cache)
    monkeypatch.setattr(cgrid, "RUN_DIR", cache / "run")
    monkeypatch.setattr(cgrid, "PICKS_LOG", cache / "picks.jsonl")
    monkeypatch.setattr(cgrid, "CONFIG_PATH", tmp_path / "config.json")
    return fake


@pytest.fixture
def cfg() -> dict:
    # pytest's tmp dirs live under /private/var/folders on macOS, which the default config excludes
    return {**cgrid.DEFAULT_CONFIG, "exclude": ["~/.claude", "~/.claude-mem"]}
