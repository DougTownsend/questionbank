# Question Bank — development guide

Local Moodle question-bank curation app for a professor. External Claude
generates questions; this app imports, previews, approves/rejects, and
assembles assignments. The app itself never calls Claude — the user pastes
results in and out manually.

This file is for Claude Code assisting with **developing the app** (not for
the external Claude that writes questions — that one reads
`~/question_bank/classes/<class>/CLAUDE.md`, which is auto-generated).

## Stack

- Python 3.10+, pure Python, no frontend build step.
- [NiceGUI](https://nicegui.io/) 3.x for UI (server-rendered Python, runs in
  the user's browser at `localhost:8765`).
- SQLite via stdlib `sqlite3`; one DB file per class.
- `lxml` for Moodle XML parse/serialize.
- `pytest` for tests.
- Virtualenv at `.venv/` (created with `python3 -m venv`). Project installed
  in editable mode (`pip install -e '.[dev]'`).

## Dev commands

```bash
# install / refresh deps
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'

# run tests
.venv/bin/pytest -q

# run the app
.venv/bin/question-bank            # default: serve web UI
.venv/bin/question-bank list       # list class names
.venv/bin/question-bank export-context <class>   # regenerate CLAUDE.md + BANK_CONTEXT.md

# override data location (defaults to ~/question_bank/)
QUESTION_BANK_ROOT=/tmp/test .venv/bin/question-bank
```

A symlink at `~/.local/bin/question-bank` → `.venv/bin/question-bank` makes
the command available globally. Recreate it on a fresh machine:
```bash
ln -sf $(pwd)/.venv/bin/question-bank ~/.local/bin/question-bank
```

## Module map

`src/question_bank/`:

- [`paths.py`](src/question_bank/paths.py) — per-class directory layout
  (`~/question_bank/classes/<name>/{bank.sqlite,materials,imports,exports}`).
  Class names are normalized: whitespace → `_`, filesystem-invalid chars
  stripped.
- [`db.py`](src/question_bank/db.py) — SQLite schema + helpers. Tables:
  `questions`, `question_answers`, `rejection_notes`, `imports`,
  `assignments`, `assignment_questions`. Autocommit mode (`isolation_level=None`)
  so every write is durable immediately. WAL mode enabled.
- [`moodle_xml.py`](src/question_bank/moodle_xml.py) — parse Moodle XML for
  `multichoice`, `truefalse`, `numerical`. Also contains `stem_hash_for()`
  (normalized-text SHA-256 for dedup) and `format_category_for_display()`
  (strips `$course$/`, `$module$/`, `$system$/` prefixes for UI).
- [`importer.py`](src/question_bank/importer.py) — ingest XML into the DB
  with dedup + unsupported-type reporting. `cleanup_completed_import()` auto-
  deletes the source file from `<class>/imports/` once all questions from
  that import are approved or rejected (defense against path traversal
  included).
- [`preview.py`](src/question_bank/preview.py) — HTML renderer for a question
  preview, with green/yellow/red badges for correct/partial/wrong and per-
  answer feedback. CSS is injected once per page via `_inject_css()`.
- [`exporter.py`](src/question_bank/exporter.py) — build Moodle XML for an
  assignment. Emits a `<category>` block only when the category changes
  between consecutive questions. Writes to `<class>/exports/<name>.xml`.
- [`context.py`](src/question_bank/context.py) — generates
  `<class>/CLAUDE.md` (static instructions template) and
  `<class>/BANK_CONTEXT.md` (snapshot of approved + rejected questions with
  rejection notes). Regenerated from scratch every run.
- [`app.py`](src/question_bank/app.py) — the NiceGUI app + CLI entry point.
  Page handlers use `@ui.page("/path")`. `open_db(class_name)` is a
  context manager that opens a fresh connection per action.

Tests live in [`tests/`](tests/) with fixtures at
[`tests/fixtures/sample_questions.xml`](tests/fixtures/sample_questions.xml).

## Key design decisions

These are locked in and shouldn't be reversed without checking with the user:

- **No Claude integration in the app.** The user asks Claude externally. The
  app produces context files and reads Claude's XML output. Don't add HTTP
  calls to Anthropic's API.
- **Local-only, single-user.** No auth, no server, no multi-user state.
- **v1 question types**: `multichoice`, `truefalse`, `numerical`. Others are
  silently dropped on import. Adding new types means updating `SUPPORTED_TYPES`
  in `moodle_xml.py`, extending `preview.py` renderers, and updating the
  CLAUDE.md templates.
- **Rejection reasons are free-text**, not a picklist. Stored verbatim in
  `rejection_notes.note_text` and echoed into `BANK_CONTEXT.md` for Claude
  to learn from.
- **Dedup is exact-match on normalized stem text** (lowercased, whitespace-
  collapsed, HTML stripped, SHA-256). Near-duplicate fuzzy matching is
  deferred to v0.5.
- **Every question keeps its raw XML** (`questions.raw_xml`) so re-export
  is lossless even for types/attributes we don't fully model.
- **Topic model is hybrid**: questions carry a Moodle `<category>` string
  (`raw_category`), plus eventually free-form tags. Tags table is not yet
  implemented (v0.2).
- **`$course$/` etc. is preserved in storage, stripped on display** by
  `format_category_for_display()`.

## Roadmap (from plan file)

Current: **v0.3 done** — import + review + approve/reject + assignment
builder with Moodle XML export.

Upcoming:
- **v0.2**: topic hierarchy + tags + filtering + bank browser with filters.
  (Partially done in assignment detail page already.)
- **v0.4**: built-in `Build Claude context bundle` UI button (currently only
  available via `question-bank export-context` CLI).
- **v0.5**: near-duplicate detection via `rapidfuzz`; edit-in-place for
  approved questions + edit history.
- **v0.6**: export/import a class as `.zip` for sharing between computers.
- **later**: shortanswer/essay/cloze/matching types, MathJax + image
  polish, random-pick-with-constraints, NiceGUI native window mode.

## Conventions followed in this codebase

- **Imports at module top**, `from __future__ import annotations` everywhere.
- **Dataclasses** for parsed results; SQLite rows accessed as dicts via
  `row_factory = sqlite3.Row`.
- **No comments explaining *what* code does** — names should carry that.
  Comments only for *why* something non-obvious is true (e.g., the path-
  traversal guard in `cleanup_completed_import`).
- **No fallback/backwards-compat shims** when a straight edit will do.
  The app has no users other than the professor; breaking changes are
  cheap.
- **Tests use a fresh tmp_path per test**, no shared state. `QUESTION_BANK_ROOT`
  env var is monkeypatched when paths matter.
- **Don't narrate diffs in PR-style summaries** — just state what changed
  and move on.

## Testing checklist before shipping a change

1. `.venv/bin/pytest -q` — all green.
2. `.venv/bin/python -c "from question_bank import app; print('ok')"` —
   app module imports cleanly (catches NiceGUI API regressions).
3. If UI changed: `.venv/bin/question-bank` → open `http://localhost:8765`
   → walk the affected flow manually. Curl can confirm the route returns
   200 but not that the page *works*.

## NiceGUI 3.x gotchas to remember

- Upload events: use `await e.file.read()` and `e.file.name` (not
  `e.content.read()` — that was 2.x).
- Button click handlers that take no arguments but are used with
  `on_click=lambda _: …` need the `_` because NiceGUI passes a click
  event.
- `ui.navigate.reload()` after mutating state is simpler than manually
  re-rendering, but heavier; prefer clearing + re-rendering a column where
  it's ergonomic.
