# Contributing

Thank you for considering a contribution. This project is maintained by one
person; small, focused changes with tests are the easiest to accept.

## Before you start

- Read [docs/overview.md](docs/overview.md), then
  [docs/project-purpose.md](docs/project-purpose.md): most design decisions
  follow from a rule stated there.
- For anything larger than a fix, open an issue first describing the
  problem and the change you propose.
- Security problems go through [SECURITY.md](SECURITY.md), not an issue.

## Setting up

Follow [docs/environment-setup-windows.md](docs/environment-setup-windows.md)
or [docs/environment-setup-linux.md](docs/environment-setup-linux.md). Every
script in `scripts/` exists as a `.cmd` for Windows and a `.sh` for Linux.

## Making a change

1. Fork the repository and create a topic branch from `main`.
2. Make the change with a test that fails without it.
3. Run every gate and make it pass:

   Windows (cmd):

   ```console
   cd /d %USERPROFILE%\dev\classobjects-ccwebdb
   .\scripts\ccweb_check.cmd
   ```

   Linux (bash):

   ```console
   cd ~/dev/classobjects-ccwebdb
   ./scripts/ccweb_check.sh
   ```

   `ccweb_check fix` applies the formatters first. Every gate is at zero, so
   anything it reports is new.
4. Open a pull request against `main` explaining what changed and why.

## Conventions

The binding conventions are written down; the most important:

- **Code** -- every public function has a docstring and full type
  annotations (ruff `D` and `ANN`); no lint finding is ignored. Comments
  state the constraint a piece of code protects, not the history of how it
  came about. Money is `Decimal`, never a float.
- **Data** -- the database is the system of record; anything that changes
  a table with a history goes through that table's single writer module
  ([docs/architecture.md](docs/architecture.md)).
- **Scripts** -- a change to `scripts/X.cmd` comes with the same change to
  `scripts/X.sh` (same modes, wording and exit codes); no script names a
  port, host or folder of its own -- each comes from
  `backend/app/runtime_settings.py`.
- **Documentation** -- documents describe the current state, not their
  history; designs go in `docs/specs/<topic>-design.md`; command blocks are
  fenced as `console`, paste into their shell as written, and are given for
  both cmd and bash where they differ.
- **Reference data** -- only facts may be shipped; a publisher's catalogue
  numbering or price guide may not ([docs/reference-data.md](docs/reference-data.md)).
- **Test data** -- use `example.com` addresses, fictional names and
  synthetic identifiers; Friedberg numbers in tests are in the 9900s.

[docs/code-quality.md](docs/code-quality.md) explains the gates and their
few exceptions.

## Licence

By contributing you agree that your contribution is licensed under the
Apache License 2.0, as the rest of the project is ([LICENSE](LICENSE)).

## Conduct

Everyone taking part is expected to follow the
[code of conduct](CODE_OF_CONDUCT.md).
