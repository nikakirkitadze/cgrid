# Sourced by demo.tape: a throwaway $HOME with synthetic history and a stub `claude`.
DEMO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$DEMO")"
tmux -L cgrid-demo kill-server 2>/dev/null
rm -rf "$DEMO/.home" && python3 "$DEMO/seed.py" "$DEMO/.home"
export HOME="$DEMO/.home" SHELL=/bin/bash
export CLAUDE_CONFIG_DIR="$HOME/.claude" CGRID_CACHE="$HOME/.cache/cgrid" CGRID_SOCKET=cgrid-demo
export CGRID_CONFIG="$DEMO/config.json" PATH="$DEMO/bin:$PATH"
cgrid() { python3 "$ROOT/cgrid.py" "$@"; }
cd "$HOME" && PS1='\[\e[32m\]~ \$\[\e[0m\] '
