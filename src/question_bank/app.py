from __future__ import annotations

import contextlib
import sqlite3
from typing import Iterator

from nicegui import app, ui

from . import context, db, exporter, importer, paths, preview
from .moodle_xml import format_category_for_display


# ---------- connection helper ----------


@contextlib.contextmanager
def open_db(class_name: str) -> Iterator[sqlite3.Connection]:
    conn = db.connect(paths.class_db_path(class_name))
    try:
        yield conn
    finally:
        conn.close()


def _inject_css() -> None:
    ui.add_head_html(f"<style>{preview.PREVIEW_CSS}</style>")


# ---------- class picker (home) ----------


@ui.page("/")
def home() -> None:
    _inject_css()
    ui.label("Question Bank").classes("text-2xl font-bold")
    ui.label("Local Moodle question-bank curation").classes("text-sm text-gray-500")

    with ui.card().classes("w-full max-w-xl mt-4"):
        ui.label("Classes").classes("text-lg font-semibold")
        container = ui.column().classes("w-full")
        _render_class_list(container)

        ui.separator().classes("my-3")
        new_name = ui.input(
            label="New class name", placeholder="e.g. ECE212-Spring2026"
        ).classes("w-full")
        preview_label = ui.label("").classes("text-xs text-gray-500 font-mono")

        def _update_preview() -> None:
            raw = (new_name.value or "").strip()
            if not raw:
                preview_label.text = ""
                return
            norm = paths.normalize_class_name(raw)
            if norm == raw:
                preview_label.text = ""
            elif norm:
                preview_label.text = f"→ will be saved as: {norm}"
            else:
                preview_label.text = "(name has no valid characters)"

        new_name.on("update:model-value", lambda _: _update_preview())

        def create_class() -> None:
            raw = new_name.value or ""
            name = paths.normalize_class_name(raw)
            if not name:
                ui.notify("Please enter a valid class name", type="warning")
                return
            if name in paths.list_classes():
                ui.notify(f"Class '{name}' already exists", type="warning")
                return
            paths.ensure_class_dirs(name)
            with open_db(name) as conn:
                pass  # creating the DB initializes schema
            new_name.value = ""
            _render_class_list(container)
            if name != raw.strip():
                ui.notify(f"Created class '{name}' (normalized from '{raw.strip()}')",
                          type="positive")
            else:
                ui.notify(f"Created class '{name}'", type="positive")

        ui.button("Create class", on_click=create_class).props("color=primary")


def _render_class_list(container: ui.column) -> None:
    container.clear()
    classes = paths.list_classes()
    if not classes:
        with container:
            ui.label("No classes yet. Create one below.").classes("text-gray-500 italic")
        return
    for cname in classes:
        with container:
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(cname).classes("font-mono")
                ui.button(
                    "Open",
                    on_click=lambda _, c=cname: ui.navigate.to(f"/class/{c}"),
                ).props("flat dense")


# ---------- class dashboard ----------


@ui.page("/class/{class_name}")
def class_dashboard(class_name: str) -> None:
    _inject_css()
    if class_name not in paths.list_classes():
        ui.label(f"Class '{class_name}' not found").classes("text-red-600")
        ui.button("Back", on_click=lambda: ui.navigate.to("/"))
        return

    with ui.row().classes("items-center gap-4"):
        ui.button("← Classes", on_click=lambda: ui.navigate.to("/")).props("flat")
        ui.label(class_name).classes("text-2xl font-bold")

    with open_db(class_name) as conn:
        counts = db.count_by_status(conn)

    pending = counts.get("pending", 0)
    approved = counts.get("approved", 0)
    rejected = counts.get("rejected", 0)

    with ui.row().classes("gap-4 mt-3"):
        for label, n, color in (
            ("Pending", pending, "warning"),
            ("Approved", approved, "positive"),
            ("Rejected", rejected, "negative"),
        ):
            with ui.card().classes("min-w-40"):
                ui.label(label).classes("text-sm text-gray-600")
                ui.label(str(n)).classes(f"text-3xl font-bold text-{color}")

    with ui.row().classes("gap-2 mt-4"):
        ui.button(
            "Import XML",
            on_click=lambda: ui.navigate.to(f"/class/{class_name}/import"),
        ).props("color=primary")
        ui.button(
            f"Review queue ({pending})",
            on_click=lambda: ui.navigate.to(f"/class/{class_name}/review"),
        ).props("color=secondary").set_enabled(pending > 0)
        ui.button(
            "Assignments",
            on_click=lambda: ui.navigate.to(f"/class/{class_name}/assignments"),
        ).props("color=primary outline").set_enabled(approved > 0)
        ui.button(
            f"Approved bank ({approved})",
            on_click=lambda: ui.navigate.to(f"/class/{class_name}/bank/approved"),
        ).props("flat").set_enabled(approved > 0)
        ui.button(
            f"Rejected bank ({rejected})",
            on_click=lambda: ui.navigate.to(f"/class/{class_name}/bank/rejected"),
        ).props("flat").set_enabled(rejected > 0)


# ---------- import screen ----------


@ui.page("/class/{class_name}/import")
def import_page(class_name: str) -> None:
    _inject_css()
    if class_name not in paths.list_classes():
        ui.label(f"Class '{class_name}' not found").classes("text-red-600")
        return

    with ui.row().classes("items-center gap-4"):
        ui.button(
            "← Class", on_click=lambda: ui.navigate.to(f"/class/{class_name}")
        ).props("flat")
        ui.label(f"Import XML — {class_name}").classes("text-xl font-bold")

    ui.label(
        "Upload a Moodle XML file. Supported question types: multichoice, truefalse, numerical. "
        "Duplicates (same normalized stem text) are skipped."
    ).classes("text-sm text-gray-600 mt-2")

    report_area = ui.column().classes("w-full mt-4")

    async def handle_upload(e) -> None:
        data: bytes = await e.file.read()
        try:
            with open_db(class_name) as conn:
                report = importer.import_bytes(conn, data, filename=e.file.name)
        except Exception as exc:
            ui.notify(f"Import failed: {exc}", type="negative")
            return
        _render_import_report(report_area, class_name, report)

    ui.upload(
        label="Drop Moodle XML here",
        on_upload=handle_upload,
        multiple=False,
        auto_upload=True,
    ).props('accept=".xml"').classes("w-full")


def _render_import_report(container: ui.column, class_name: str, report) -> None:
    container.clear()
    with container:
        ui.label(f"Imported: {report.filename}").classes("text-lg font-semibold mt-2")
        with ui.row().classes("gap-4"):
            ui.label(f"Inserted: {len(report.inserted)}").classes("text-positive")
            ui.label(f"Skipped (duplicates): {len(report.skipped_duplicates)}").classes(
                "text-warning"
            )
            ui.label(f"Unsupported types: {len(report.unsupported)}").classes(
                "text-gray-600"
            )
        if report.unsupported:
            with ui.expansion("Unsupported questions").classes("w-full mt-2"):
                for qtype, name in report.unsupported:
                    ui.label(f"[{qtype}] {name or '(unnamed)'}").classes(
                        "font-mono text-sm"
                    )
        if report.skipped_duplicates:
            with ui.expansion("Skipped duplicates").classes("w-full mt-2"):
                for q in report.skipped_duplicates:
                    ui.label(f"[{q.moodle_type}] {q.name or '(unnamed)'}").classes(
                        "font-mono text-sm"
                    )
        if report.inserted:
            ui.button(
                f"Go to review queue ({len(report.inserted)} new)",
                on_click=lambda: ui.navigate.to(f"/class/{class_name}/review"),
            ).props("color=primary").classes("mt-2")


# ---------- review queue ----------


@ui.page("/class/{class_name}/review")
def review_page(class_name: str) -> None:
    _inject_css()
    if class_name not in paths.list_classes():
        ui.label(f"Class '{class_name}' not found").classes("text-red-600")
        return

    with ui.row().classes("items-center gap-4"):
        ui.button(
            "← Class", on_click=lambda: ui.navigate.to(f"/class/{class_name}")
        ).props("flat")
        ui.label(f"Review queue — {class_name}").classes("text-xl font-bold")

    body = ui.column().classes("w-full mt-2")
    _render_next_pending(body, class_name)


def _render_next_pending(container: ui.column, class_name: str) -> None:
    container.clear()
    with open_db(class_name) as conn:
        pending = db.list_questions(conn, status="pending")
        remaining = len(pending)
        if not pending:
            with container:
                ui.label("No pending questions. 🎉").classes("text-lg text-positive")
                ui.button(
                    "Back to class",
                    on_click=lambda: ui.navigate.to(f"/class/{class_name}"),
                ).props("flat")
            return
        q = pending[0]
        answers = db.get_answers(conn, q["id"])
        html_preview = preview.render_question(q, answers)

    with container:
        ui.label(f"{remaining} pending").classes("text-sm text-gray-600")
        with ui.card().classes("w-full"):
            ui.html(html_preview)

        note_input = ui.textarea(
            label="Rejection note (optional, only saved if you reject)",
            placeholder="Why are you rejecting this question?",
        ).classes("w-full mt-3")

        def _after_status_change() -> None:
            with open_db(class_name) as conn:
                deleted = importer.cleanup_completed_import(
                    conn, class_name, q["source_import_id"]
                )
            if deleted:
                ui.notify(
                    f"Removed source file: imports/{deleted.name}", type="info"
                )

        def approve() -> None:
            with open_db(class_name) as conn:
                db.set_status(conn, q["id"], "approved")
            ui.notify("Approved", type="positive")
            _after_status_change()
            _render_next_pending(container, class_name)

        def reject() -> None:
            note = (note_input.value or "").strip() or None
            with open_db(class_name) as conn:
                db.set_status(conn, q["id"], "rejected", rejection_note=note)
            ui.notify("Rejected", type="warning")
            _after_status_change()
            _render_next_pending(container, class_name)

        def skip() -> None:
            ui.notify("Skip not implemented yet — question stays pending")

        with ui.row().classes("gap-2 mt-3"):
            ui.button("Approve (A)", on_click=approve).props("color=positive")
            ui.button("Reject (R)", on_click=reject).props("color=negative")
            ui.button("Skip", on_click=skip).props("flat")

        # keyboard shortcuts
        def on_key(e) -> None:
            if e.action.keydown and not e.action.repeat:
                key = (e.key.name or "").lower()
                if key == "a":
                    approve()
                elif key == "r":
                    reject()

        ui.keyboard(on_key=on_key)


# ---------- approved / rejected bank browsers ----------


@ui.page("/class/{class_name}/bank/{status}")
def bank_page(class_name: str, status: str) -> None:
    _inject_css()
    if status not in ("approved", "rejected"):
        ui.label("Unknown bank").classes("text-red-600")
        return
    if class_name not in paths.list_classes():
        ui.label(f"Class '{class_name}' not found").classes("text-red-600")
        return

    with ui.row().classes("items-center gap-4"):
        ui.button(
            "← Class", on_click=lambda: ui.navigate.to(f"/class/{class_name}")
        ).props("flat")
        ui.label(f"{status.capitalize()} bank — {class_name}").classes(
            "text-xl font-bold"
        )

    with open_db(class_name) as conn:
        rows = db.list_questions(conn, status=status)
        data = []
        for r in rows:
            note = (
                db.latest_rejection_note(conn, r["id"])
                if status == "rejected"
                else None
            )
            data.append(
                {
                    "id": r["id"],
                    "type": r["moodle_type"],
                    "name": r["name"] or "",
                    "category": format_category_for_display(r["raw_category"]),
                    "note": note or "",
                }
            )

    if not data:
        ui.label(f"No {status} questions yet.").classes("text-gray-500 italic mt-3")
        return

    columns = [
        {"name": "id", "label": "ID", "field": "id", "align": "left"},
        {"name": "type", "label": "Type", "field": "type", "align": "left"},
        {"name": "name", "label": "Name", "field": "name", "align": "left"},
        {"name": "category", "label": "Category", "field": "category", "align": "left"},
    ]
    if status == "rejected":
        columns.append(
            {"name": "note", "label": "Rejection note", "field": "note", "align": "left"}
        )
    ui.table(columns=columns, rows=data, row_key="id").classes("w-full mt-3")


# ---------- assignments: list ----------


@ui.page("/class/{class_name}/assignments")
def assignments_page(class_name: str) -> None:
    _inject_css()
    if class_name not in paths.list_classes():
        ui.label(f"Class '{class_name}' not found").classes("text-red-600")
        return

    with ui.row().classes("items-center gap-4"):
        ui.button(
            "← Class", on_click=lambda: ui.navigate.to(f"/class/{class_name}")
        ).props("flat")
        ui.label(f"Assignments — {class_name}").classes("text-xl font-bold")

    container = ui.column().classes("w-full mt-3")
    _render_assignment_list(container, class_name)


def _render_assignment_list(container: ui.column, class_name: str) -> None:
    container.clear()
    with open_db(class_name) as conn:
        rows = db.list_assignments(conn)
    with container:
        with ui.card().classes("w-full max-w-2xl"):
            ui.label("Create new").classes("text-lg font-semibold")
            name_input = ui.input(
                label="Assignment name", placeholder="e.g. Quiz 3 — Caches"
            ).classes("w-full")

            def create() -> None:
                name = (name_input.value or "").strip()
                if not name:
                    ui.notify("Please enter a name", type="warning")
                    return
                with open_db(class_name) as conn:
                    aid = db.create_assignment(conn, name)
                ui.navigate.to(f"/class/{class_name}/assignments/{aid}")

            ui.button("Create assignment", on_click=create).props("color=primary")

        if not rows:
            ui.label("No assignments yet.").classes("text-gray-500 italic mt-3")
            return

        ui.label("Existing").classes("text-lg font-semibold mt-4")
        for r in rows:
            with ui.card().classes("w-full max-w-2xl"):
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.column().classes("gap-0"):
                        ui.label(r["name"]).classes("font-semibold")
                        ui.label(
                            f"{r['n_questions']} question(s) · updated {r['updated_at']}"
                        ).classes("text-xs text-gray-500")
                    ui.button(
                        "Open",
                        on_click=lambda _, aid=r["id"]: ui.navigate.to(
                            f"/class/{class_name}/assignments/{aid}"
                        ),
                    ).props("flat")


# ---------- assignments: detail (add/remove, rename, export, delete) ----------


@ui.page("/class/{class_name}/assignments/{assignment_id}")
def assignment_detail_page(class_name: str, assignment_id: str) -> None:
    _inject_css()
    if class_name not in paths.list_classes():
        ui.label(f"Class '{class_name}' not found").classes("text-red-600")
        return
    try:
        aid = int(assignment_id)
    except ValueError:
        ui.label("Invalid assignment id").classes("text-red-600")
        return

    with open_db(class_name) as conn:
        a = db.get_assignment(conn, aid)
    if a is None:
        ui.label(f"Assignment {aid} not found").classes("text-red-600")
        ui.button(
            "Back", on_click=lambda: ui.navigate.to(f"/class/{class_name}/assignments")
        )
        return

    with ui.row().classes("items-center gap-4 w-full"):
        ui.button(
            "← Assignments",
            on_click=lambda: ui.navigate.to(f"/class/{class_name}/assignments"),
        ).props("flat")
        name_input = ui.input(value=a["name"]).classes("flex-grow").props("dense")

        def save_name() -> None:
            new_name = (name_input.value or "").strip()
            if not new_name:
                ui.notify("Name cannot be empty", type="warning")
                return
            with open_db(class_name) as conn:
                db.rename_assignment(conn, aid, new_name)
            ui.notify("Renamed", type="positive")

        ui.button("Rename", on_click=save_name).props("flat")

    # filter state
    filter_type: dict[str, str | None] = {"value": None}
    filter_cat: dict[str, str | None] = {"value": None}
    filter_text: dict[str, str] = {"value": ""}

    with ui.row().classes("w-full gap-4 mt-3 items-start"):
        selected_col = ui.column().classes("flex-1")
        pool_col = ui.column().classes("flex-1")

    def render_all() -> None:
        _render_selected(selected_col, class_name, aid)
        _render_pool(pool_col, class_name, aid, filter_type, filter_cat, filter_text, render_all)

    render_all()


def _render_selected(container: ui.column, class_name: str, aid: int) -> None:
    container.clear()
    with open_db(class_name) as conn:
        picked = db.list_assignment_questions(conn, aid)

    with container:
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(f"In this assignment ({len(picked)})").classes(
                "text-lg font-semibold"
            )
            with ui.row().classes("gap-2"):
                ui.button(
                    "Export XML",
                    on_click=lambda: _export_assignment(class_name, aid),
                ).props("color=primary").set_enabled(len(picked) > 0)
                ui.button(
                    "Delete assignment",
                    on_click=lambda: _confirm_delete_assignment(class_name, aid),
                ).props("color=negative flat")

        if not picked:
            ui.label("No questions yet. Add from the right →").classes(
                "text-gray-500 italic"
            )
            return

        for i, q in enumerate(picked):
            with ui.card().classes("w-full"):
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.column().classes("gap-0"):
                        ui.label(
                            f"{i + 1}. [{q['moodle_type']}] {q['name'] or '(unnamed)'}"
                        ).classes("font-semibold")
                        cat_display = format_category_for_display(q["raw_category"])
                        if cat_display:
                            ui.label(cat_display).classes(
                                "text-xs text-gray-500 font-mono"
                            )
                    ui.button(
                        "Remove",
                        on_click=lambda _, qid=q["id"]: _remove_from_assignment(
                            class_name, aid, qid
                        ),
                    ).props("color=negative flat dense")


def _render_pool(
    container: ui.column,
    class_name: str,
    aid: int,
    filter_type: dict,
    filter_cat: dict,
    filter_text: dict,
    refresh: callable,
) -> None:
    container.clear()
    with open_db(class_name) as conn:
        approved = db.list_questions(conn, status="approved")
        picked_ids = set(db.assignment_question_ids(conn, aid))

    all_cats = sorted(
        {format_category_for_display(q["raw_category"]) for q in approved}
        - {""}
    )
    all_types = sorted({q["moodle_type"] for q in approved})

    def matches(q) -> bool:
        if q["id"] in picked_ids:
            return False
        if filter_type["value"] and q["moodle_type"] != filter_type["value"]:
            return False
        if (
            filter_cat["value"]
            and format_category_for_display(q["raw_category"]) != filter_cat["value"]
        ):
            return False
        if filter_text["value"]:
            needle = filter_text["value"].lower()
            hay = " ".join(
                [
                    q["name"] or "",
                    q["stem_html"] or "",
                    format_category_for_display(q["raw_category"]),
                ]
            ).lower()
            if needle not in hay:
                return False
        return True

    available = [q for q in approved if matches(q)]

    with container:
        ui.label(f"Approved bank ({len(available)} available)").classes(
            "text-lg font-semibold"
        )
        with ui.row().classes("w-full gap-2"):
            type_sel = ui.select(
                options=["(any type)"] + all_types,
                value="(any type)" if not filter_type["value"] else filter_type["value"],
            ).classes("min-w-32")
            cat_sel = ui.select(
                options=["(any category)"] + all_cats,
                value="(any category)"
                if not filter_cat["value"]
                else filter_cat["value"],
            ).classes("flex-grow")

            def on_type(e) -> None:
                v = type_sel.value
                filter_type["value"] = None if v == "(any type)" else v
                refresh()

            def on_cat(e) -> None:
                v = cat_sel.value
                filter_cat["value"] = None if v == "(any category)" else v
                refresh()

            type_sel.on("update:model-value", on_type)
            cat_sel.on("update:model-value", on_cat)

        text_in = ui.input(
            placeholder="Search in name/stem/category...",
            value=filter_text["value"],
        ).classes("w-full")

        def on_text(e) -> None:
            filter_text["value"] = text_in.value or ""
            refresh()

        text_in.on("update:model-value", on_text)

        if not available:
            ui.label("Nothing matches (or everything is already added).").classes(
                "text-gray-500 italic mt-2"
            )
            return

        for q in available:
            with ui.card().classes("w-full"):
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.column().classes("gap-0 flex-grow"):
                        ui.label(
                            f"[{q['moodle_type']}] {q['name'] or '(unnamed)'}"
                        ).classes("font-semibold")
                        cat_display = format_category_for_display(q["raw_category"])
                        if cat_display:
                            ui.label(cat_display).classes(
                                "text-xs text-gray-500 font-mono"
                            )
                        ui.html(
                            f"<div style='font-size:0.8rem;color:#555;max-height:3em;overflow:hidden'>{q['stem_html'] or ''}</div>"
                        )
                    ui.button(
                        "Add",
                        on_click=lambda _, qid=q["id"]: _add_to_assignment(
                            class_name, aid, qid
                        ),
                    ).props("color=primary")


def _add_to_assignment(class_name: str, aid: int, qid: int) -> None:
    with open_db(class_name) as conn:
        added = db.add_question_to_assignment(conn, aid, qid)
    if added:
        ui.notify("Added", type="positive")
    else:
        ui.notify("Already in this assignment", type="warning")
    ui.navigate.reload()


def _remove_from_assignment(class_name: str, aid: int, qid: int) -> None:
    with open_db(class_name) as conn:
        db.remove_question_from_assignment(conn, aid, qid)
    ui.notify("Removed", type="warning")
    ui.navigate.reload()


def _export_assignment(class_name: str, aid: int) -> None:
    with open_db(class_name) as conn:
        path = exporter.export_assignment_to_file(conn, class_name, aid)
    ui.notify(f"Exported: {path}", type="positive", timeout=10000)


def _confirm_delete_assignment(class_name: str, aid: int) -> None:
    with ui.dialog() as dialog, ui.card():
        ui.label("Delete this assignment? (questions stay in the bank.)")
        with ui.row():
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def do_delete() -> None:
                with open_db(class_name) as conn:
                    db.delete_assignment(conn, aid)
                dialog.close()
                ui.navigate.to(f"/class/{class_name}/assignments")

            ui.button("Delete", on_click=do_delete).props("color=negative")
    dialog.open()


# ---------- entry point ----------


def _serve() -> None:
    paths.data_root().mkdir(parents=True, exist_ok=True)
    paths.classes_root().mkdir(parents=True, exist_ok=True)
    ui.run(
        title="Question Bank",
        port=8765,
        reload=False,
        show=True,
        storage_secret="question-bank-local",
    )


def _cmd_export_context(class_name: str) -> int:
    norm = paths.normalize_class_name(class_name)
    if norm not in paths.list_classes():
        print(f"error: class '{norm}' not found", flush=True)
        print(f"available: {', '.join(paths.list_classes()) or '(none)'}")
        return 1
    instructions_path, bank_path = context.export_all(norm)
    print(f"wrote {instructions_path}")
    print(f"wrote {bank_path}")
    return 0


def _cmd_list_classes() -> int:
    classes = paths.list_classes()
    if not classes:
        print("(no classes yet)")
        return 0
    for c in classes:
        print(c)
    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="question-bank")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="Run the web UI (default)")
    sub.add_parser("list", help="List existing classes")
    ec = sub.add_parser(
        "export-context",
        help="Write CLAUDE.md + BANK_CONTEXT.md into a class directory",
    )
    ec.add_argument("class_name", help="Name of the class")

    args = parser.parse_args()
    if args.cmd in (None, "serve"):
        _serve()
        return 0
    if args.cmd == "list":
        return _cmd_list_classes()
    if args.cmd == "export-context":
        return _cmd_export_context(args.class_name)
    parser.print_help()
    return 2


if __name__ in {"__main__", "__mp_main__"}:
    raise SystemExit(main())
