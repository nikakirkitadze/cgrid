#!/bin/sh
# Install cgrid as a single file:
#   curl -fsSL https://raw.githubusercontent.com/nikakirkitadze/cgrid/main/install.sh | sh
# Options (environment): CGRID_BIN_DIR (default ~/.local/bin), CGRID_REF (git ref, default main)
set -eu

REF="${CGRID_REF:-main}"
BIN_DIR="${CGRID_BIN_DIR:-$HOME/.local/bin}"
URL="${CGRID_URL:-https://raw.githubusercontent.com/nikakirkitadze/cgrid/$REF/cgrid.py}"

say() { printf '%s\n' "$*"; }
fail() { printf 'cgrid install: %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required (3.9 or newer)"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || fail "cgrid needs Python 3.9 or newer"
python3 -c 'import curses' 2>/dev/null || fail "your python3 has no curses module"

mkdir -p "$BIN_DIR"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
curl -fsSL "$URL" -o "$tmp" || fail "download failed: $URL"
head -n 1 "$tmp" | grep -q python3 || fail "unexpected content at $URL"
chmod +x "$tmp"
mv "$tmp" "$BIN_DIR/cgrid"
say "installed $("$BIN_DIR/cgrid" --version) to $BIN_DIR/cgrid"

if ! command -v tmux >/dev/null 2>&1; then
  case "$(uname -s)" in
    Darwin) say "next: brew install tmux" ;;
    *) say "next: install tmux, e.g. sudo apt install tmux" ;;
  esac
fi
command -v claude >/dev/null 2>&1 || say "note: Claude Code (claude) is not on your PATH yet"
case ":$PATH:" in
  *":$BIN_DIR:"*) say "run: cgrid" ;;
  *) say "add $BIN_DIR to your PATH, then run: cgrid" ;;
esac
