# CLAUDE.md — classobjects-ccwebdb

Project conventions. **These override any skill's or tool's defaults.** If a
skill specifies a different path, format, or workflow, this file wins.

## Documentation layout

- Specs go in **`docs/specs/<topic>-design.md`** — flat, alongside the other docs.
- Plans go in **`docs/plans/`**.
- **No date in any filename.** `attribution-design.md`, never
  `2026-09-07-attribution-design.md`. The date belongs in the document's own
  header and in git, both of which stay accurate when the file is revised; a
  filename date goes stale on the first edit.
- **No tool-specific subdirectory.** Never `docs/superpowers/`, `docs/claude/`,
  or similar. Project documentation is not filed under the name of whatever
  tool created it.
- Changes to `docs/data-import-plan.md` are **appended as numbered amendments**
  (A, B, C…), never edited in place. Superseded sections get a pointer, not a
  rewrite — the user tracks the original plan against later updates.

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

- **cmd/batch only. No PowerShell.** This covers runnable commands given in
  chat, `.cmd` scripts, and documentation examples. No `.ps1`, and no shelling
  out to `powershell -Command` from inside a `.cmd` — that is still PowerShell.
- Useful pure-cmd equivalents: `curl -s -f -o nul <url>` then `if errorlevel 1`
  for readiness checks; `timeout /t 1 /nobreak >nul` to sleep; `taskkill /PID
  <pid> /T /F` to kill a tree; `setlocal EnableDelayedExpansion` with `!var!`
  inside blocks.
- **Machine trap:** `NoDefaultCurrentDirectoryInExePath=1` is set, so
  `cmd /c script.cmd` fails with "not recognized" even in the current
  directory. Always invoke as `.\script.cmd` or with a full path.

## Git workflow

- **Never commit directly to `main`.** Create a topic branch and commit there,
  then wait — the user says "merge it into main and push" when ready.
- On that request: `git checkout main && git merge --ff-only <branch> &&
  git push origin main`, then `git branch -d <branch>`.
- Before merging, confirm `git merge-base --is-ancestor main <branch>` so a
  fast-forward is guaranteed rather than a silent merge commit. Confirm local
  and remote SHAs match afterwards.
- **The recurring slip:** right after a merge-and-push, the branch is deleted
  and work continues while still sitting on `main`. The next request says
  "add X", not "branch first". So: **after `git branch -d`, the very next code
  change starts by creating a branch.**

## Code quality

- `scripts\ccweb_check.cmd` runs every gate; `ccweb_check.cmd fix` auto-fixes
  first. Non-zero exit if anything fails. Rationale in `docs/code-quality.md`.
- Enforced and clean: `ruff` (Python format + lint), `eslint` + `prettier`
  (frontend), `pytest`. `mypy` is reported only — watch the **count**, not
  individual messages.
- **No ignored lint issues.** Every public class, method and function carries a
  docstring and every function is annotated (ruff `D` and `ANN`). Only three
  documented exceptions exist, each because the rule does not describe the code.
- Line endings are pinned in `.gitattributes`: `* text=auto eol=lf`, but `*.cmd`
  and `*.bat` stay `eol=crlf` — cmd.exe mishandles labels and `goto` inside
  parenthesised blocks in an LF-only file.
- **When a check fails on every file in a category** — generated and vendored
  ones included — suspect the harness, not the content.

## Reference data

Before any retrieved data goes into the repository, seed files, or the
database, establish that it is free to use. This is enforced, not advisory:
the catalogue is sold, so reference data shipped inside the product is
redistributed with it.

The line is **fact vs. arrangement**. Safe: who held an office and when, design
series names and year spans, mint specifications and legislated compositions,
common collector nicknames. Not safe: **Friedberg** numbering, **Pick**
numbering, vendor price-guide values, or any catalogue's mapping of attributes
to its own numbers.

## Working style

- Measure, don't assume — verify through a different channel than the one that
  produced the claim. A check on the same path only proves self-consistency.
- Disagreement backed by evidence is welcome.
