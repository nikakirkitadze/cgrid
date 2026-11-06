# Contributing

Thanks for helping. Bug reports, ideas, and pull requests are all welcome.

## Ground rules

- **Single file, standard library only.** `cgrid.py` must stay installable with one `curl` and run
  on the Python 3.9 that ships with macOS. Please don't add dependencies.
- **Never touch real user data in tests.** Use the `home` fixture in `tests/conftest.py`. It builds
  a fake `$HOME` with synthetic Claude history. For anything that starts a grid, use a unique
  `CGRID_SOCKET` and a stub `claude` (see `test_grid_end_to_end`).
- **Don't widen permissions.** cgrid may suggest only permission modes the user has already used.
  Changes to how modes are inferred need a test.

## Setup

```sh
git clone https://github.com/nikakirkitadze/cgrid && cd cgrid
python3 -m pip install pytest          # or use uvx, as below
python3 -m pytest                      # unit tests plus a tmux end-to-end test (skipped without tmux)
uvx ruff check . && uvx ruff format --check .
```

To try a change against throwaway data instead of your own history:

```sh
bash --rcfile demo/env.sh   # a sandboxed shell: synthetic $HOME, stub claude, separate tmux socket
cgrid                       # runs the checkout's cgrid.py; `exit` leaves the sandbox
```

The GIF in the README comes from `demo/demo.tape` and is recorded with [vhs](https://github.com/charmbracelet/vhs):
`vhs demo/demo.tape`.

## Reporting bugs

Please include the output of `cgrid doctor`, your terminal app, and what you expected to happen.
Claude Code's history formats change between versions, so the `claude --version` line in that
output helps.

## Releasing (maintainers)

1. Update `__version__` in `cgrid.py` and add a section to `CHANGELOG.md`.
2. Commit, then `git tag vX.Y.Z && git push --tags`.
3. `.github/workflows/release.yml` builds the package, creates the GitHub release, and publishes to
   PyPI through trusted publishing. One-time setup: on PyPI, add a trusted publisher for this
   repository with the workflow `release.yml` and the environment `pypi`.
