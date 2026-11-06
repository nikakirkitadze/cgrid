# cgrid

**Several Claude Code sessions in one window, without typing `cd` six times.**

`cgrid` opens one terminal window split into a grid of panes. Each pane suggests a project folder,
ranked by how you actually use [Claude Code](https://github.com/anthropics/claude-code). When you
pick one, `claude` starts there with the permission mode you usually use in that folder.

![cgrid demo: six panes, each picking a project and starting Claude Code](https://raw.githubusercontent.com/nikakirkitadze/cgrid/main/docs/demo.gif)

[![PyPI](https://img.shields.io/pypi/v/cgrid)](https://pypi.org/project/cgrid/)
[![CI](https://github.com/nikakirkitadze/cgrid/actions/workflows/ci.yml/badge.svg)](https://github.com/nikakirkitadze/cgrid/actions/workflows/ci.yml)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776ab)
![macOS | Linux](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)
[![MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## Why

Running Claude Code sessions side by side usually starts with the same ritual: open six tabs, `cd`
six times, and type `claude --dangerously-skip-permissions` six times. `cgrid` replaces that with one
command and a few presses of Enter. It works out what to suggest from the history Claude Code
already keeps on your machine.

- **One window, many sessions.** 1–12 panes in a grid (6 by default, as 3×2). tmux runs underneath
  on its own socket and config, so your own tmux setup is left alone.
- **Suggestions from your real usage.** It reads `~/.claude/history.jsonl` and your session
  transcripts. Folders you used recently and heavily rank first. Git worktrees count toward their
  repo.
- **Learns your permission mode per folder.** If you always start `infra` in plan mode and
  everything else with `--dangerously-skip-permissions`, that's what each pane suggests. You can
  change it before launch.
- **No duplicates.** Each pane pre-selects a different project, so pressing ⏎ in every pane opens
  your top N. Panes also see what the others took (`◉2`) and skip past it.
- **Pick anything.** Fuzzy search (`billapi` matches `acme-billing-api`), path browsing (`~/code/…` +
  Tab), and folders you've never opened with Claude.
- **Pick up where you left off.** `cgrid --last -c` reopens yesterday's grid and continues each
  conversation.
- **Optional AI ranking.** `cgrid --ai` asks Claude to choose the grid from your recent prompts, and
  groups related projects such as an API and its app.
- **No dependencies.** One Python file using only the standard library. Your data stays local
  unless you use `--ai`.

## Install

You need macOS or Linux, Python 3.9+ (the macOS system `python3` works), tmux, and Claude Code.

```sh
# with uv
uv tool install cgrid

# or with pipx
pipx install cgrid

# or as a single file in ~/.local/bin
curl -fsSL https://raw.githubusercontent.com/nikakirkitadze/cgrid/main/install.sh | sh
```

To get the latest unreleased code, use `uv tool install git+https://github.com/nikakirkitadze/cgrid`.
Upgrade with `uv tool upgrade cgrid` or `pipx upgrade cgrid`.

Then install tmux if you don't have it (`brew install tmux`, or `sudo apt install tmux`), and check
your setup:

```sh
cgrid doctor
```

## Usage

```sh
cgrid                        # 6 panes, each asks which folder to open
cgrid -n 4                   # 4 panes (2×2)
cgrid --top                  # open your top 6 folders right away
cgrid --last -c              # reopen your previous grid and continue each conversation
cgrid api web                # preset panes 1 and 2 by fuzzy name; the rest ask
cgrid ~/code/app --mode plan # preset by path; every pane in plan mode
cgrid --ai                   # let Claude choose the suggestions
cgrid --here                 # use this terminal window instead of opening a new one

cgrid pick                   # the picker on its own, in this terminal
cgrid top                    # print the ranking with suggested modes and last prompts
cgrid ls                     # running grids: each pane's project, mode, memory, CPU and process count
cgrid attach [grid]          # reattach a grid (they keep running when the window closes)
cgrid kill [grid | --all]    # end a grid and its sessions
cgrid config                 # write and show the config file
cgrid doctor                 # check python, tmux, claude, history and the terminal backend
```

### The picker

| Key | Action |
| --- | --- |
| type | fuzzy filter across your projects and folders you've never opened |
| `~/…` or `/…` | browse any folder |
| ⏎ | open the selected folder |
| ↑ ↓ | move |
| ← → | change the permission mode for this launch |
| `^R` | toggle `--continue` (resume the last conversation in that folder) |
| Tab | dive into the selected folder, e.g. to open a monorepo package |
| Esc | plain shell instead |
| `^D` | close the pane |

When `claude` exits, the pane drops to a shell in that folder. Run `cgrid pick` there to choose
again.

### Inside the grid

The grid is a normal tmux session with a few defaults:

- Click a pane to focus it.
- Drag-select to copy (it goes to your clipboard).
- `^b z` zooms a pane, `^b ←↑↓→` moves between panes, `^b d` detaches.

Each pane's border shows its number, project, mode, and the title Claude Code sets.

**Closing the window only detaches.** Your Claude sessions keep running: `cgrid attach` brings them
back and `cgrid kill` ends them.

## How suggestions work

cgrid builds its index from files Claude Code already writes:

| Source | What it provides |
| --- | --- |
| `~/.claude/history.jsonl` | every prompt you typed, with its folder, session, and time |
| `~/.claude/projects/*/*.jsonl` | session transcripts: the folder, how you started the session (CLI or desktop app), and its permission mode |

Each session adds `0.5^(age / 14 days) × (1 + ln(1 + prompts))` to its folder's score. Recent,
intense work rises; projects you've drifted away from fade over a few weeks. Sessions from the
Agent SDK and tools such as `claude-mem` are ignored, and sessions in deleted folders are dropped.

The suggested **permission mode** is a recency-weighted vote over how you started sessions in that
folder, nudged toward your overall habit:

| Transcript says | cgrid launches | Picker label |
| --- | --- | --- |
| `bypassPermissions` | `claude --dangerously-skip-permissions` | ⚡ skip-perms |
| `auto` | `claude --permission-mode auto` | auto |
| `acceptEdits` | `claude --permission-mode acceptEdits` | accept-edits |
| `plan` | `claude --permission-mode plan` | plan |
| `default` | `claude` | ask |

cgrid only repeats what you already do. It never suggests a mode you haven't used, and a new user
gets plain `claude`. The exact command is shown at the bottom of the picker before anything runs.

### `--ai`

With `--ai`, cgrid runs `claude -p --model haiku` once in the background. It sends your top ~20
folders, their session counts, and your last two prompts in each. The model returns the best N
folders, best first, with a short reason for each pick. The pickers work immediately and
re-rank when the answer arrives, typically after 10–40 seconds. The call goes through your own
Claude Code login, loads no tools or MCP servers, and saves no session. A typical call costs a few
cents of API usage, or uses your plan's usage.

## Performance

**Context doesn't add up across panes.** Each pane runs its own `claude` process with its own
conversation and context window. Nothing is shared between panes, and six panes don't make any one
session's context bigger.

**The grid itself costs almost nothing.** Measured on an M-series MacBook with six live sessions:

| Part | Memory | CPU |
| --- | --- | --- |
| tmux server for the whole grid | ~4 MB | < 0.5% |
| a picker waiting for input | ~10 MB | ~0.01% (redraws only when something changes) |
| cgrid after you pick a folder | none: the picker process is replaced by `claude` | none |
| one Claude Code session | ~100–450 MB, plus whatever it runs | ~0% idle, more while working |
| its stdio MCP servers | ~65 MB for six servers, started separately by **every** session | ~0% idle |

So six panes cost the same as six terminal tabs running `claude`. `cgrid ls` shows the live numbers
for each pane, including MCP servers and the builds or tests Claude starts.

Launching six panes at once (`--top`, `--last`) is fine as it is. In the same test, one session's
MCP servers were ready in about 1 s and six sessions' worth in under 3 s, so starting the panes one
after another would only delay the last one.

What actually limits parallel work:

- **Usage limits are shared.** Six busy sessions use your plan or API quota about six times as fast
  as one.
- **MCP servers start once per session.** Servers you added at user scope (`claude mcp add -s user`)
  start in every pane. Add heavy ones at local or project scope (`-s local`, `-s project`) so they
  only start in the folders that need them.
- **`--continue` resumes the whole previous conversation.** That's what you want for unfinished
  work, but a fresh session starts with a much smaller context. Use `/context` inside a pane to see
  what a session is carrying.

## Where the grid opens

| You run cgrid from | The grid opens in |
| --- | --- |
| Terminal.app | a new, maximized Terminal window |
| iTerm2 | a new iTerm2 window |
| anything else (Ghostty, WezTerm, kitty, VS Code, Linux…) | the current window |

To choose explicitly, set `"terminal": "terminal" | "iterm" | "here"` in the config, or pass
`--here`.

## Configuration

Settings live in `~/.config/cgrid/config.json` (`cgrid config` writes the defaults). Every key is
optional.

| Key | Default | Meaning |
| --- | --- | --- |
| `panes` | `6` | default pane count (max 12) |
| `ai` | `false` | always rank with `--ai` |
| `ai_model` | `"haiku"` | model used by `--ai` |
| `half_life_days` | `14` | how fast old sessions fade |
| `default_mode` | `null` | force one mode everywhere: `skip`, `auto`, `accept`, `plan` or `ask` |
| `claude` | `"claude"` | the command to launch |
| `terminal` | `"auto"` | `auto`, `terminal`, `iterm` or `here` |
| `roots` | `["~/Documents", "~/code", …]` | where folders you've never opened are found when you type |
| `exclude` | `["~/.claude", "/tmp", …]` | folders that are never suggested |

Environment overrides: `CLAUDE_CONFIG_DIR`, `CGRID_CONFIG`, `CGRID_CACHE`, `CGRID_SOCKET`, and
`CGRID_CLAUDE`.

## Privacy

cgrid reads Claude Code's local files and writes only to `~/.cache/cgrid`: a transcript index,
running-grid state, and a log of folders you opened for `--last`. Nothing leaves your machine unless
you use `--ai`, which sends the data described above to Claude through your own `claude` CLI.

## Troubleshooting

- **Start with `cgrid doctor`.** It shows which Python, tmux, and `claude` cgrid found, how many
  sessions it read, and where the grid will open.
- **"tmux is not installed".** Run `brew install tmux` or `sudo apt install tmux`.
- **The picker is empty.** You have no Claude Code history yet. Type a path, or open `claude` in a
  few projects first.
- **macOS asks to let your terminal control Terminal.app.** cgrid opens the new window with
  AppleScript. Allow it once, or set `"terminal": "here"`.
- **Esc feels slow inside Claude.** cgrid sets tmux's `escape-time` to 10 ms. If you start the
  grid's tmux server some other way, set it yourself.

## Development

```sh
git clone https://github.com/nikakirkitadze/cgrid && cd cgrid
python3 -m pytest              # or: uvx --with pytest pytest
uvx ruff check . && uvx ruff format --check .
python3 cgrid.py --here        # run from the checkout
vhs demo/demo.tape             # re-record docs/demo.gif from synthetic data
```

The tests build a fake home folder with synthetic Claude history and drive a real tmux grid with a
stub `claude`, so they never touch your data or start real sessions. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © Nika Kirkitadze

cgrid is an independent project, not affiliated with or endorsed by Anthropic. Claude and Claude
Code are trademarks of Anthropic.
