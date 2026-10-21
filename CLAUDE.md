# CLAUDE.md — classobjects-ccwebdb

Project conventions. **These override any skill's or tool's defaults.** If a
skill specifies a different path, format, or workflow, this file wins.

## Documentation layout

- Specs go in **`docs/specs/<topic>-design.md`** — flat, alongside the other docs.
- Plans go in **`docs/plans/`**, and are **temporary**: a plan is the
  step-by-step build of a feature, and it is deleted once that work is merged.
  What was built is described by its spec, which is kept current.
- **Documentation describes the current state, not its history.** When
  something changes, edit the doc in place and drop what no longer holds;
  no revision logs, amendment lists or "originally" narratives. The history
  is in git.
- **No date in any filename.** `attribution-design.md`, never
  `2026-09-07-attribution-design.md`. The date belongs in the document's own
  header and in git, both of which stay accurate when the file is revised; a
  filename date goes stale on the first edit.
- **No tool-specific subdirectory.** Never `docs/superpowers/`, `docs/claude/`,
  or similar. Project documentation is not filed under the name of whatever
  tool created it.

## Writing files

- **Use the Write tool to create or replace file content. Do not use shell
  heredocs** (`cat > file <<'EOF'`). They have failed repeatedly here: any
  content mixing single quotes, backticks, `%VAR%`, or Windows backslashes
  eventually trips the shell parser, and the failure wastes a whole tool call
  and can truncate the file.
- For a small, surgical change to an existing file, use Edit. For a scripted
  edit across many lines, use a Python script that **asserts the anchor text
  matches exactly once before writing** — a failed assertion is a loud, harmless
  error; a silent mismatch corrupts the file.
- Never `sed` a Windows path: every backslash is a regex escape.

## Scripts and shell

- **cmd on Windows (no PowerShell), bash on Linux.** This covers runnable
  commands given in chat, scripts, and documentation examples. No `.ps1`, and
  no shelling out to `powershell -Command` from inside a `.cmd` — that is
  still PowerShell.
- **Every `scripts/*.cmd` has a `scripts/*.sh` twin** with the same name,
  modes, wording and exit codes (slash options become `--` options). Change
  one, change the other in the same commit. A test fails when either lacks
  its twin.
- **No script writes a port, host or folder of its own.** Each comes from
  `backend/app/runtime_settings.py` through `ccweb_settings.cmd` / `.sh`;
  a new one is a new field there with today's value as its default, and a
  line in `.env.example`. A test refuses the old literals in the scripts.
- **Documentation commands paste into their shell as written.** Fence them
  as ```` ```console ````; one command per line; a stand-in is a CAPITALISED
  word, and only where pasting it unchanged fails harmlessly — otherwise
  prose; a block that depends on its folder starts with `cd /d ...` (cmd) or
  `cd ~/...` (bash); anything that prompts (`set /p`, `read -rs`) stands
  alone in its block. Per-OS documents are `<topic>-windows.md` and
  `<topic>-linux.md`; a shared document shows a block that differs by OS in
  both forms, each labelled. The owner's installation appears only as a
  labelled example.
- Useful pure-cmd equivalents: `curl -s -f -o nul <url>` then `if errorlevel 1`
  for readiness checks; `ping -n 2 127.0.0.1 >nul` to sleep — `timeout` needs a
  real console and fails instantly, with no sleep at all, when stdin is
  redirected, which is exactly how an automated or scheduled caller runs a
  script; `taskkill /PID <pid> /T /F` to kill a tree; `setlocal
  EnableDelayedExpansion` with `!var!` inside blocks.
- **Machine trap:** where `NoDefaultCurrentDirectoryInExePath=1` is set (it
  is on the maintainer's machine), `cmd /c script.cmd` fails with "not
  recognized" even in the current directory. Always invoke as
  `.\script.cmd` or with a full path, which works everywhere.
- In bash: `ccweb_env.sh`, `ccweb_settings.sh`, `ccweb_logdir.sh` and
  `ccweb_testdb.sh use` are sourced and must not `set -u` or end in a
  top-level `return`/`exit`; `shellcheck -x` must report no warning or error.
  WSL from Git Bash: `MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu --cd /mnt/c/...
  --exec bash -c '...'` — `wsl.exe --` re-parses through a shell and eats
  `$variables`.

## Git workflow

The maintainer's own workflow, for an assistant working in the maintainer's
checkout. Outside contributors work from a fork and open a pull request
([CONTRIBUTING.md](CONTRIBUTING.md)).

- **Never commit directly to `main`.** Create a topic branch and commit there,
  then wait — the user says "merge it into main and push" when ready.
- On that request: `git checkout main && git merge --ff-only <branch> &&
  git push origin main`, then `git branch -d <branch>`.
- Before merging, confirm `git merge-base --is-ancestor main <branch>` so a
  fast-forward is guaranteed rather than a silent merge commit. Confirm local
  and remote SHAs match afterwards.
- **A merge is not done at the push: restart Vite and prove what it serves.**
  The checkout and fast-forward can leave Vite serving a module from before
  the branch even when no content changed; `ccweb_status` still says
  RUNNING. After `git branch -d`: kill the dev server on 5173 (`taskkill /PID
  <pid> /T /F`, and the backend on 8000 too when `backend/` changed), run
  `scripts\ccweb_startup.cmd` bare -- never piped or redirected, which hangs
  it -- then fetch a changed module (`curl -s
  http://127.0.0.1:5173/src/<path>.jsx`) and find the new code in it. Tell
  the owner to reload any open page.
- **The recurring slip:** right after a merge-and-push, the branch is deleted
  and work continues while still sitting on `main`. The next request says
  "add X", not "branch first". So: **after `git branch -d`, the very next code
  change starts by creating a branch.**

## Code quality

- `scripts\ccweb_check.cmd` runs every gate; `ccweb_check.cmd fix` auto-fixes
  first. Non-zero exit if anything fails. Rationale in `docs/code-quality.md`.
- Enforced and clean: `ruff` (Python format + lint), `eslint` + `prettier`
  (frontend), `pytest`, `mypy`. All of them are at zero, so any finding is a
  new one — there is no backlog to read past.
- **Two typing traps, both documented in `docs/code-quality.md`.** Do not
  annotate `ReferenceMixin.__tablename__` as `ClassVar[str]`; the
  `TYPE_CHECKING` block in `models/base.py` must keep *matching*
  `DeclarativeBase`'s declarations rather than improving on them. And
  `__mapper_args__` is a `@declared_attr.directive`, not a dict literal,
  because every annotation of the literal fails either ruff or mypy.
- **No ignored lint issues.** Every public class, method and function carries a
  docstring and every function is annotated (ruff `D` and `ANN`). Only three
  documented exceptions exist, each because the rule does not describe the code,
  plus Alembic's generated files.
- Line endings are pinned in `.gitattributes`: `* text=auto eol=lf`, but `*.cmd`
  and `*.bat` stay `eol=crlf` — cmd.exe mishandles labels and `goto` inside
  parenthesised blocks in an LF-only file.
- **When a check fails on every file in a category** — generated and vendored
  ones included — suspect the harness, not the content.

## Reference data

The rule is in **`docs/reference-data.md`**; code and documents cite that
file, never this one. In one line: before any retrieved data goes into the
repository, seed files, or the database, establish that it is free to use --
a fact may be shipped, a publisher's arrangement (**Friedberg** or **Pick**
numbering, price-guide values, a catalog's mapping of attributes to its own
numbers) may not.

## Working style

- Measure, don't assume — verify through a different channel than the one that
  produced the claim. A check on the same path only proves self-consistency.
- Disagreement backed by evidence is welcome.
