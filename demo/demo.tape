# Record with: vhs demo/demo.tape   (from the repo root; needs vhs and tmux)
Output docs/demo.gif
Require tmux

Set Shell "bash"
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 13
Set Width 1500
Set Height 860
Set Padding 14
Set WindowBar Colorful
Set BorderRadius 10
Set TypingSpeed 70ms
Set Theme { "name": "Phosphor", "background": "#0B110D", "foreground": "#9CEBB0", "cursor": "#9CEBB0", "selection": "#2A4A35", "black": "#1A2A1F", "red": "#E06C75", "green": "#5FD787", "yellow": "#E5C07B", "blue": "#61AFEF", "magenta": "#C678DD", "cyan": "#56B6C2", "white": "#9CEBB0", "brightBlack": "#4D6B57", "brightRed": "#FF7A85", "brightGreen": "#A8FFBF", "brightYellow": "#FFD98A", "brightBlue": "#82C4FF", "brightMagenta": "#D9A0F0", "brightCyan": "#7FD8E0", "brightWhite": "#D6FFE0" }

Hide
Type "source demo/env.sh && clear" Enter
Sleep 1s
Show

Sleep 600ms
Type "cgrid" Sleep 500ms Enter
Sleep 2.5s

# pane 1: take the top suggestion
Enter Sleep 1.2s
# pane 2: its own default (already a different project)
Ctrl+B Right Sleep 500ms Enter Sleep 1.2s
# pane 3: fuzzy search; this folder usually runs in accept-edits mode
Ctrl+B Right Sleep 500ms Type "design" Sleep 700ms Enter Sleep 1.2s
# pane 6: default suggestion
Ctrl+B Down Sleep 500ms Enter Sleep 1.2s
# pane 5: a folder never opened with Claude
Ctrl+B Left Sleep 500ms Type "andr" Sleep 900ms Enter Sleep 1.2s
# pane 4: browse by path; this one remembers you use plan mode here
Ctrl+B Left Sleep 500ms Type "~/code/" Sleep 600ms Type "inf" Sleep 500ms Tab Sleep 1.4s
Right Sleep 600ms Left Sleep 900ms Enter
Sleep 3.5s

Hide
Type "tmux -L cgrid-demo kill-server" Enter
