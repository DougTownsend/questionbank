from __future__ import annotations

import contextlib
import sqlite3
from typing import Iterator
from urllib.parse import quote_plus

from nicegui import app, ui

from . import (
    assignment_importer,
    context,
    db,
    exporter,
    importer,
    latex_exporter,
    moodle_xml,
    paths,
    preview,
)
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
            context.refresh(name)
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
            "Import",
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
        ui.label(f"Import — {class_name}").classes("text-xl font-bold")

    ui.label(
        "XML files are question banks (multichoice, truefalse, numerical); "
        "duplicates are skipped. JSON files are assignment plans that pick "
        "existing approved questions into a new assignment."
    ).classes("text-sm text-gray-600 mt-2")

    report_area = ui.column().classes("w-full mt-4")
    file_list_area = ui.column().classes("w-full mt-2")

    def refresh_files() -> None:
        _render_imports_dir(
            file_list_area, class_name, import_xml, import_assignment_json
        )

    def import_xml(path) -> None:
        try:
            with open_db(class_name) as conn:
                report = importer.import_file(conn, path)
        except Exception as exc:
            ui.notify(f"Import failed: {exc}", type="negative")
            return
        _render_import_report(report_area, class_name, report)
        refresh_files()

    def import_assignment_json(path) -> None:
        try:
            with open_db(class_name) as conn:
                report = assignment_importer.import_assignment_file(
                    conn, class_name, path
                )
        except assignment_importer.AssignmentImportError as exc:
            ui.notify(f"Assignment import failed: {exc}", type="negative")
            return
        except Exception as exc:
            ui.notify(f"Assignment import failed: {exc}", type="negative")
            return
        _render_assignment_import_report(report_area, class_name, report)
        refresh_files()

    refresh_files()

    ui.separator().classes("my-4")

    ui.label("Or upload a file from elsewhere:").classes(
        "text-sm text-gray-600 mt-2"
    )

    async def handle_upload(e) -> None:
        data: bytes = await e.file.read()
        fname = e.file.name or ""
        is_json = fname.lower().endswith(".json")
        try:
            with open_db(class_name) as conn:
                if is_json:
                    report = assignment_importer.import_assignment_bytes(
                        conn, class_name, data
                    )
                    _render_assignment_import_report(
                        report_area, class_name, report
                    )
                else:
                    report = importer.import_bytes(conn, data, filename=fname)
                    _render_import_report(report_area, class_name, report)
        except assignment_importer.AssignmentImportError as exc:
            ui.notify(f"Assignment import failed: {exc}", type="negative")
            return
        except Exception as exc:
            ui.notify(f"Import failed: {exc}", type="negative")
            return
        refresh_files()

    ui.upload(
        label="Drop Moodle XML or assignment JSON here",
        on_upload=handle_upload,
        multiple=False,
        auto_upload=True,
    ).props('accept=".xml,.json"').classes("w-full")


def _render_imports_dir(container, class_name: str, on_xml, on_json) -> None:
    container.clear()
    imports_dir = paths.class_imports_dir(class_name)
    files = sorted(
        (
            p
            for p in imports_dir.iterdir()
            if p.is_file() and p.suffix.lower() in (".xml", ".json")
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    with container:
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(f"Files in imports/  ({len(files)})").classes(
                "text-lg font-semibold"
            )
            ui.button("Refresh", on_click=lambda: _render_imports_dir(
                container, class_name, on_xml, on_json
            )).props("flat dense")
        ui.label(f"~/question_bank/classes/{class_name}/imports/").classes(
            "text-xs text-gray-500 font-mono"
        )
        if not files:
            ui.label(
                "No XML or JSON files in imports/ yet. When Claude writes one "
                "here, refresh to see it."
            ).classes("text-gray-500 italic mt-1")
            return
        from datetime import datetime

        for p in files:
            size_kb = p.stat().st_size / 1024
            mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime(
                "%Y-%m-%d %H:%M"
            )
            is_json = p.suffix.lower() == ".json"
            kind_label = "assignment plan" if is_json else "question bank"
            handler = on_json if is_json else on_xml
            with ui.card().classes("w-full"):
                with ui.row().classes("w-full items-center no-wrap gap-3"):
                    ui.button(
                        "Import",
                        on_click=lambda _, path=p, h=handler: h(path),
                    ).props("color=primary")
                    with ui.column().classes("gap-0 flex-grow"):
                        ui.label(p.name).classes("font-semibold")
                        ui.label(
                            f"{kind_label} · {size_kb:.1f} KB · modified {mtime}"
                        ).classes("text-xs text-gray-500")


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


def _render_assignment_import_report(
    container: ui.column, class_name: str, report
) -> None:
    container.clear()
    with container:
        ui.label(f"Assignment: {report.assignment_name}").classes(
            "text-lg font-semibold mt-2"
        )
        if report.description:
            ui.label(report.description).classes("text-sm text-gray-600 italic")
        with ui.row().classes("gap-4"):
            ui.label(
                f"Questions added: {len(report.added_question_ids)}"
            ).classes("text-positive")
            if report.skipped:
                ui.label(f"Skipped: {len(report.skipped)}").classes("text-warning")
        if report.skipped:
            with ui.expansion("Skipped entries").classes("w-full mt-2"):
                for qid, reason in report.skipped:
                    ui.label(f"[id={qid}] {reason}").classes("font-mono text-sm")
        for w in report.warnings:
            ui.label(w).classes("text-warning")
        if report.assignment_id is not None:
            ui.button(
                "Open assignment",
                on_click=lambda: ui.navigate.to(
                    f"/class/{class_name}/assignments/{report.assignment_id}"
                ),
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

        def discard() -> None:
            source_import_id = q["source_import_id"]
            with open_db(class_name) as conn:
                db.delete_question(conn, q["id"])
                deleted = importer.cleanup_completed_import(
                    conn, class_name, source_import_id
                )
            ui.notify("Discarded", type="info")
            if deleted:
                ui.notify(
                    f"Removed source file: imports/{deleted.name}", type="info"
                )
            _render_next_pending(container, class_name)

        def edit() -> None:
            return_to = quote_plus(f"/class/{class_name}/review")
            ui.navigate.to(
                f"/class/{class_name}/questions/{q['id']}/edit?return_to={return_to}"
            )

        def discard_all() -> None:
            with open_db(class_name) as conn:
                pending_list = db.list_questions(conn, status="pending")
            n = len(pending_list)
            if n == 0:
                return

            with ui.dialog() as dialog, ui.card():
                ui.label(f"Discard all {n} pending question(s)?").classes(
                    "text-lg font-semibold"
                )
                ui.label(
                    "They will be deleted entirely — not moved to the rejected "
                    "bank. This cannot be undone."
                ).classes("text-sm text-gray-700")
                with ui.row():
                    ui.button("Cancel", on_click=dialog.close).props("flat")

                    def do_discard_all() -> None:
                        deleted_files: list = []
                        with open_db(class_name) as conn:
                            current = db.list_questions(conn, status="pending")
                            source_imports = {
                                q["source_import_id"]
                                for q in current
                                if q["source_import_id"] is not None
                            }
                            for q in current:
                                db.delete_question(conn, q["id"])
                            for iid in source_imports:
                                d = importer.cleanup_completed_import(
                                    conn, class_name, iid
                                )
                                if d:
                                    deleted_files.append(d)
                        dialog.close()
                        ui.notify(
                            f"Discarded {len(current)} question(s)", type="warning"
                        )
                        for d in deleted_files:
                            ui.notify(
                                f"Removed source file: imports/{d.name}",
                                type="info",
                            )
                        _render_next_pending(container, class_name)

                    ui.button(
                        f"Discard all {n}", on_click=do_discard_all
                    ).props("color=negative")
            dialog.open()

        with ui.row().classes("gap-2 mt-3 flex-wrap"):
            ui.button("Approve (A)", on_click=approve).props("color=positive")
            ui.button("Reject (R)", on_click=reject).props("color=negative")
            ui.button("Edit (E)", on_click=edit).props("flat")
            ui.button("Discard", on_click=discard).props("color=negative flat")
            ui.button(
                f"Discard all ({remaining})", on_click=discard_all
            ).props("color=negative flat")

        # keyboard shortcuts
        def on_key(e) -> None:
            if e.action.keydown and not e.action.repeat:
                key = (e.key.name or "").lower()
                if key == "a":
                    approve()
                elif key == "r":
                    reject()
                elif key == "e":
                    edit()

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
        items: list[dict] = []
        for r in rows:
            answers = db.get_answers(conn, r["id"])
            rendered = preview.render_question(r, answers)
            note = (
                db.latest_rejection_note(conn, r["id"])
                if status == "rejected"
                else None
            )
            cat = format_category_for_display(r["raw_category"])
            header_parts = [f"[{r['moodle_type']}] {r['name'] or '(unnamed)'}"]
            if cat:
                header_parts.append(cat)
            items.append(
                {
                    "id": r["id"],
                    "header": "  ·  ".join(header_parts),
                    "note": note,
                    "rendered": rendered,
                }
            )

    if not items:
        ui.label(f"No {status} questions yet.").classes("text-gray-500 italic mt-3")
        return

    ui.label(
        f"{len(items)} {status} question(s). Click any row to expand."
    ).classes("text-sm text-gray-600 mt-3")

    selection: dict[int, "ui.checkbox"] = {}

    def selected_ids() -> list[int]:
        return [qid for qid, cb in selection.items() if cb.value]

    def refresh_toolbar() -> None:
        n = len(selected_ids())
        remove_selected_btn.text = f"Remove selected ({n})"
        remove_selected_btn.set_enabled(n > 0)

    def select_all() -> None:
        for cb in selection.values():
            cb.value = True
        refresh_toolbar()

    def clear_selection() -> None:
        for cb in selection.values():
            cb.value = False
        refresh_toolbar()

    def remove_selected() -> None:
        ids = selected_ids()
        if not ids:
            return
        _confirm_remove_questions(class_name, ids, status)

    with ui.row().classes("gap-2 items-center mt-2"):
        remove_selected_btn = ui.button(
            "Remove selected (0)", on_click=remove_selected
        ).props("color=negative flat")
        remove_selected_btn.set_enabled(False)
        ui.button("Select all", on_click=select_all).props("flat")
        ui.button("Clear", on_click=clear_selection).props("flat")

    for it in items:
        caption = (
            (it["note"][:120] + "…")
            if it["note"] and len(it["note"]) > 120
            else it["note"]
        )
        with ui.row().classes("w-full items-start no-wrap gap-2"):
            cb = ui.checkbox(on_change=lambda _: refresh_toolbar()).classes("mt-2")
            selection[it["id"]] = cb
            ui.button(
                "Remove",
                on_click=lambda _, qid=it["id"]: _confirm_remove_question(
                    class_name, qid, status
                ),
            ).props("color=negative flat dense").classes("mt-2")
            with ui.expansion(it["header"], caption=caption).classes(
                "flex-grow"
            ).props("header-class=bg-gray-50"):
                if it["note"]:
                    with ui.card().classes(
                        "w-full bg-red-50 border-l-4 border-red-400 mb-2"
                    ):
                        ui.label("Rejection note").classes(
                            "font-bold text-red-700 text-sm"
                        )
                        ui.label(it["note"]).classes(
                            "text-red-900 whitespace-pre-wrap"
                        )
                ui.html(it["rendered"])
                if status == "approved":
                    with ui.row().classes("gap-2 mt-3"):
                        ui.button(
                            "Edit",
                            on_click=lambda _, qid=it["id"]: ui.navigate.to(
                                f"/class/{class_name}/questions/{qid}/edit"
                                f"?return_to={quote_plus(f'/class/{class_name}/bank/approved')}"
                            ),
                        ).props("flat")


# ---------- shared: remove question + edit page ----------


def _confirm_remove_questions(
    class_name: str, qids: list[int], status: str
) -> None:
    with open_db(class_name) as conn:
        affected_assignments: dict[str, int] = {}
        for qid in qids:
            for a in db.assignments_containing_question(conn, qid):
                affected_assignments[a["name"]] = affected_assignments.get(
                    a["name"], 0
                ) + 1

    with ui.dialog() as dialog, ui.card():
        ui.label(f"Delete {len(qids)} question(s) from the bank?").classes(
            "text-lg font-semibold"
        )
        ui.label("This cannot be undone.").classes("text-sm text-gray-700")
        if affected_assignments:
            with ui.card().classes("w-full bg-yellow-50 border-l-4 border-yellow-400"):
                ui.label("Selected questions are used in these assignments:").classes(
                    "font-bold text-yellow-800 text-sm"
                )
                for aname, n in sorted(affected_assignments.items()):
                    ui.label(f"• {aname} ({n} question{'s' if n > 1 else ''})").classes(
                        "text-yellow-900 text-sm"
                    )
                ui.label(
                    "They will be removed from those assignments too."
                ).classes("text-yellow-800 text-xs")
        with ui.row():
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def do_remove() -> None:
                with open_db(class_name) as conn:
                    for qid in qids:
                        db.delete_question(conn, qid)
                dialog.close()
                ui.notify(f"Removed {len(qids)} question(s)", type="warning")
                ui.navigate.to(f"/class/{class_name}/bank/{status}")

            ui.button(f"Delete {len(qids)}", on_click=do_remove).props(
                "color=negative"
            )
    dialog.open()


def _confirm_remove_question(class_name: str, qid: int, status: str) -> None:
    with open_db(class_name) as conn:
        in_assignments = db.assignments_containing_question(conn, qid)

    with ui.dialog() as dialog, ui.card():
        ui.label("Delete this question from the bank?").classes("text-lg font-semibold")
        ui.label(
            "The question will be removed completely. This cannot be undone."
        ).classes("text-sm text-gray-700")
        if in_assignments:
            names = ", ".join(a["name"] for a in in_assignments)
            with ui.card().classes("w-full bg-yellow-50 border-l-4 border-yellow-400"):
                ui.label("Also in these assignments:").classes(
                    "font-bold text-yellow-800 text-sm"
                )
                ui.label(names).classes("text-yellow-900 text-sm")
                ui.label(
                    "The question will be removed from those assignments too."
                ).classes("text-yellow-800 text-xs")
        with ui.row():
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def do_remove() -> None:
                with open_db(class_name) as conn:
                    db.delete_question(conn, qid)
                dialog.close()
                ui.notify("Removed from bank", type="warning")
                ui.navigate.to(f"/class/{class_name}/bank/{status}")

            ui.button("Delete", on_click=do_remove).props("color=negative")
    dialog.open()


@ui.page("/class/{class_name}/questions/{question_id}/edit")
def edit_question_page(
    class_name: str, question_id: str, return_to: str = ""
) -> None:
    _inject_css()
    if class_name not in paths.list_classes():
        ui.label(f"Class '{class_name}' not found").classes("text-red-600")
        return
    try:
        qid = int(question_id)
    except ValueError:
        ui.label("Invalid question id").classes("text-red-600")
        return

    back_url = return_to or f"/class/{class_name}"

    with open_db(class_name) as conn:
        q = db.get_question(conn, qid)
        if q is None:
            ui.label(f"Question {qid} not found").classes("text-red-600")
            return
        answers_rows = db.get_answers(conn, qid)

    qtype = q["moodle_type"]
    state: dict = {
        "name": q["name"] or "",
        "stem_html": q["stem_html"] or "",
        "raw_category": q["raw_category"] or "",
        "general_feedback_html": q["general_feedback_html"] or "",
        "answers": [
            {
                "text_html": a["text_html"] or "",
                "fraction": float(a["fraction"]),
                "feedback_html": a["feedback_html"] or "",
                "tolerance": a["tolerance"],
            }
            for a in answers_rows
        ],
    }

    with ui.row().classes("items-center gap-4"):
        ui.button("← Cancel", on_click=lambda: ui.navigate.to(back_url)).props("flat")
        ui.label(f"Edit question · type={qtype}").classes("text-xl font-bold")

    with ui.column().classes("w-full max-w-3xl gap-3 mt-3"):
        name_input = ui.input(label="Name", value=state["name"]).classes("w-full")
        cat_input = ui.input(
            label="Category (e.g. $course$/ECE212/Caches)",
            value=state["raw_category"],
        ).classes("w-full")
        stem_input = (
            ui.textarea(label="Question stem (HTML allowed)", value=state["stem_html"])
            .classes("w-full")
            .props("rows=5 autogrow")
        )
        gf_input = (
            ui.textarea(
                label="General feedback (HTML, shown after attempt)",
                value=state["general_feedback_html"],
            )
            .classes("w-full")
            .props("rows=3 autogrow")
        )

        ui.separator().classes("my-2")
        ui.label("Answers").classes("text-lg font-semibold")

        answers_col = ui.column().classes("w-full gap-3")
        answer_rows: list[dict] = []

        def collect_state() -> None:
            state["name"] = name_input.value or ""
            state["stem_html"] = stem_input.value or ""
            state["raw_category"] = cat_input.value or ""
            state["general_feedback_html"] = gf_input.value or ""
            for i, row in enumerate(answer_rows):
                if i >= len(state["answers"]):
                    continue
                state["answers"][i]["text_html"] = row["text"].value or ""
                try:
                    state["answers"][i]["fraction"] = float(row["fraction"].value or 0)
                except (TypeError, ValueError):
                    state["answers"][i]["fraction"] = 0.0
                state["answers"][i]["feedback_html"] = row["feedback"].value or ""
                if "tolerance" in row:
                    val = row["tolerance"].value
                    try:
                        state["answers"][i]["tolerance"] = (
                            float(val) if val is not None and val != "" else None
                        )
                    except (TypeError, ValueError):
                        state["answers"][i]["tolerance"] = None

        def render_answers() -> None:
            answers_col.clear()
            answer_rows.clear()
            with answers_col:
                for i, a in enumerate(state["answers"]):
                    with ui.card().classes("w-full"):
                        row: dict = {}
                        with ui.row().classes("w-full items-end gap-2"):
                            row["fraction"] = (
                                ui.number(
                                    label="Score % (100=correct, 0=wrong)",
                                    value=a["fraction"],
                                    step=1,
                                )
                                .classes("w-56")
                            )
                            if qtype == "numerical":
                                row["tolerance"] = (
                                    ui.number(
                                        label="Tolerance ±",
                                        value=a.get("tolerance"),
                                        step=0.01,
                                    )
                                    .classes("w-40")
                                )
                            if qtype == "multichoice" and len(state["answers"]) > 1:
                                def remove(_, idx=i) -> None:
                                    collect_state()
                                    state["answers"].pop(idx)
                                    render_answers()

                                ui.button("Remove", on_click=remove).props(
                                    "color=negative flat dense"
                                ).classes("ml-auto")
                        row["text"] = (
                            ui.textarea(
                                label="Answer text (HTML allowed)",
                                value=a["text_html"],
                            )
                            .classes("w-full")
                            .props("rows=2 autogrow")
                        )
                        row["feedback"] = (
                            ui.textarea(
                                label="Feedback shown when this answer is chosen",
                                value=a["feedback_html"],
                            )
                            .classes("w-full")
                            .props("rows=2 autogrow")
                        )
                        answer_rows.append(row)

                if qtype == "multichoice":
                    def add_answer() -> None:
                        collect_state()
                        state["answers"].append(
                            {
                                "text_html": "",
                                "fraction": 0.0,
                                "feedback_html": "",
                                "tolerance": None,
                            }
                        )
                        render_answers()

                    ui.button("+ Add answer", on_click=add_answer).props("flat")

        render_answers()

        def save() -> None:
            collect_state()
            if not state["stem_html"].strip():
                ui.notify("Stem cannot be empty", type="negative")
                return
            if not state["answers"]:
                ui.notify("At least one answer is required", type="negative")
                return

            raw_xml = moodle_xml.serialize_question(
                moodle_type=qtype,
                name=state["name"] or None,
                stem_html=state["stem_html"],
                general_feedback_html=state["general_feedback_html"] or None,
                default_grade=q["default_grade"],
                penalty=q["penalty"],
                answers=state["answers"],
            )
            stem_hash = moodle_xml.stem_hash_for(state["stem_html"])
            with open_db(class_name) as conn:
                db.update_question(
                    conn,
                    qid,
                    name=state["name"] or None,
                    stem_html=state["stem_html"],
                    stem_hash=stem_hash,
                    raw_xml=raw_xml,
                    raw_category=state["raw_category"] or None,
                    general_feedback_html=state["general_feedback_html"] or None,
                    answers=state["answers"],
                )
            ui.notify("Saved", type="positive")
            ui.navigate.to(back_url)

        with ui.row().classes("gap-2 mt-4"):
            ui.button("Save", on_click=save).props("color=primary")
            ui.button("Cancel", on_click=lambda: ui.navigate.to(back_url)).props("flat")


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
            with ui.row().classes("gap-2 flex-wrap"):
                ui.button(
                    "Export Moodle XML",
                    on_click=lambda: _export_assignment(class_name, aid),
                ).props("color=primary").set_enabled(len(picked) > 0)
                ui.button(
                    "LaTeX (student)",
                    on_click=lambda: _export_assignment_latex(
                        class_name, aid, include_answer_key=False
                    ),
                ).props("color=primary outline").set_enabled(len(picked) > 0)
                ui.button(
                    "LaTeX (with answer key)",
                    on_click=lambda: _export_assignment_latex(
                        class_name, aid, include_answer_key=True
                    ),
                ).props("color=primary outline").set_enabled(len(picked) > 0)
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
                with ui.row().classes(
                    "w-full items-center no-wrap gap-2"
                ):
                    with ui.column().classes("gap-0 flex-grow"):
                        ui.label(
                            f"{i + 1}. [{q['moodle_type']}] {q['name'] or '(unnamed)'}"
                        ).classes("font-semibold")
                        cat_display = format_category_for_display(q["raw_category"])
                        if cat_display:
                            ui.label(cat_display).classes(
                                "text-xs text-gray-500 font-mono"
                            )
                    ui.number(
                        label="Pts",
                        value=q["aq_point_value"],
                        step=0.5,
                        min=0,
                        on_change=lambda e, qid=q["id"]: _save_points(
                            class_name, aid, qid, e.value
                        ),
                    ).classes("w-24").props("dense")
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
                with ui.row().classes("w-full items-center no-wrap gap-3"):
                    ui.button(
                        "Add",
                        on_click=lambda _, qid=q["id"]: _add_to_assignment(
                            class_name, aid, qid
                        ),
                    ).props("color=primary")
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


def _save_points(class_name: str, aid: int, qid: int, raw_value) -> None:
    try:
        points = None if raw_value in (None, "") else float(raw_value)
    except (TypeError, ValueError):
        ui.notify("Points must be a number", type="warning")
        return
    with open_db(class_name) as conn:
        db.set_assignment_question_points(conn, aid, qid, points)


def _export_assignment(class_name: str, aid: int) -> None:
    with open_db(class_name) as conn:
        path = exporter.export_assignment_to_file(conn, class_name, aid)
    ui.notify(f"Exported: {path}", type="positive", timeout=10000)


def _export_assignment_latex(
    class_name: str, aid: int, *, include_answer_key: bool
) -> None:
    with open_db(class_name) as conn:
        path = latex_exporter.export_assignment_to_latex_file(
            conn, class_name, aid, include_answer_key=include_answer_key
        )
    kind = "with answer key" if include_answer_key else "student copy"
    ui.notify(f"Exported LaTeX ({kind}): {path}", type="positive", timeout=10000)


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


def _serve(show_browser: bool = False) -> None:
    paths.data_root().mkdir(parents=True, exist_ok=True)
    paths.classes_root().mkdir(parents=True, exist_ok=True)
    for cname in paths.list_classes():
        context.refresh(cname)
    ui.run(
        title="Question Bank",
        port=8765,
        reload=False,
        show=show_browser,
        storage_secret="question-bank-local",
    )


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
    p_serve = sub.add_parser("serve", help="Run the web UI (default)")
    for p in (parser, p_serve):
        p.add_argument(
            "-b",
            "--browser",
            action="store_true",
            help="auto-open the UI in your default browser on startup",
        )
    sub.add_parser("list", help="List existing classes")

    # Argparse clobbers the top-level flag when the subparser redefines it.
    # Sniff argv directly so --browser works anywhere on the command line.
    import sys as _sys
    show_browser = any(a in ("-b", "--browser") for a in _sys.argv[1:])

    args = parser.parse_args()
    if args.cmd in (None, "serve"):
        _serve(show_browser=show_browser)
        return 0
    if args.cmd == "list":
        return _cmd_list_classes()
    parser.print_help()
    return 2


if __name__ in {"__main__", "__mp_main__"}:
    raise SystemExit(main())
