#!/usr/bin/env python3
"""Create a synthetic $HOME with Claude Code history for the README demo.

    python3 demo/seed.py /tmp/cgrid-demo

Nothing here is real data: project names, prompts and timestamps are made up.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

HOUR, DAY = 3600, 86400

# folder, permission mode, [(hours ago, prompts)], recent prompts
PROJECTS = [
    (
        "code/acme-api",
        "bypassPermissions",
        [(1, 24), (5, 12), (26, 30), (50, 8)],
        ["add idempotency keys to the payments endpoint", "why does the webhook retry twice?"],
    ),
    (
        "code/acme-ios",
        "bypassPermissions",
        [(3, 18), (28, 22), (74, 9)],
        ["wire the new checkout API into CartViewModel", "fix the dark mode contrast on the receipt screen"],
    ),
    ("code/acme-web", "bypassPermissions", [(6, 10), (30, 14)], ["port the pricing page to the new design tokens"]),
    ("code/infra", "plan", [(20, 6), (98, 11)], ["plan the Postgres 17 upgrade with zero downtime"]),
    ("code/design-system", "acceptEdits", [(52, 9), (140, 4)], ["add a Toast component with an undo action"]),
    (
        "code/ml-experiments",
        "bypassPermissions",
        [(80, 15), (300, 20)],
        ["compare the two ranking models on last week's data"],
    ),
    ("code/blog", "default", [(200, 5)], ["proofread the post about migrating to SwiftData"]),
    ("code/dotfiles", "bypassPermissions", [(400, 3)], ["make the zsh prompt show the git branch"]),
]
NEVER_OPENED = ["code/acme-android", "code/scratch", "code/old-landing-page"]


def seed(home: Path) -> None:
    now = time.time()
    claude = home / ".claude"
    (claude / "projects").mkdir(parents=True, exist_ok=True)
    history = []
    for rel, mode, sessions, prompts in PROJECTS:
        cwd = str(home / rel)
        os.makedirs(cwd, exist_ok=True)
        for k, (hours, count) in enumerate(sessions):
            sid = str(uuid.uuid4())
            start = now - hours * HOUR - count * 60
            for i in range(count):
                text = prompts[k] if i == count - 1 and k < len(prompts) else f"step {i} of the task"
                history.append(
                    {
                        "display": text,
                        "pastedContents": {},
                        "timestamp": int((start + i * 60) * 1000),
                        "project": cwd,
                        "sessionId": sid,
                    }
                )
            folder = claude / "projects" / cwd.replace("/", "-")
            folder.mkdir(exist_ok=True)
            stamp = datetime.fromtimestamp(start, timezone.utc).isoformat().replace("+00:00", "Z")
            line = {
                "type": "user",
                "cwd": cwd,
                "sessionId": sid,
                "entrypoint": "cli",
                "permissionMode": mode,
                "timestamp": stamp,
            }
            transcript = folder / f"{sid}.jsonl"
            transcript.write_text(json.dumps(line) + "\n")
            os.utime(transcript, (start + count * 60, start + count * 60))
    history.sort(key=lambda r: r["timestamp"])
    (claude / "history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in history))
    for rel in NEVER_OPENED:
        os.makedirs(home / rel, exist_ok=True)


if __name__ == "__main__":
    seed(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/cgrid-demo").resolve())
