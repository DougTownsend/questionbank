from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import db, moodle_xml, paths
from .moodle_xml import ParsedQuestion, ParseResult


@dataclass
class ImportReport:
    import_id: int
    filename: str
    inserted: list[int]                    # question ids
    skipped_duplicates: list[ParsedQuestion]  # stem_hash already in DB
    unsupported: list[tuple[str, str]]     # (type, name)
    total_questions: int                   # from the XML, incl. skipped/unsupported


def import_file(
    conn: sqlite3.Connection, path: Path | str
) -> ImportReport:
    path = Path(path)
    parsed = moodle_xml.parse_file(path)
    return _apply_import(conn, filename=path.name, parsed=parsed)


def import_bytes(
    conn: sqlite3.Connection, data: bytes, filename: str = "uploaded.xml"
) -> ImportReport:
    parsed = moodle_xml.parse_bytes(data)
    return _apply_import(conn, filename=filename, parsed=parsed)


def _apply_import(
    conn: sqlite3.Connection, *, filename: str, parsed: ParseResult
) -> ImportReport:
    inserted: list[int] = []
    skipped: list[ParsedQuestion] = []
    # keep only the basename — never trust a path from upload metadata
    filename = Path(filename).name

    conn.execute("BEGIN")
    try:
        import_id = db.create_import(conn, filename=filename, n_questions=0)
        for q in parsed.questions:
            h = q.stem_hash
            if db.stem_hash_exists(conn, h) is not None:
                skipped.append(q)
                continue
            qid = db.insert_question(
                conn,
                moodle_type=q.moodle_type,
                name=q.name,
                stem_html=q.stem_html,
                stem_hash=h,
                raw_xml=q.raw_xml,
                raw_category=q.raw_category,
                default_grade=q.default_grade,
                penalty=q.penalty,
                general_feedback_html=q.general_feedback_html,
                source_import_id=import_id,
                answers=[
                    {
                        "text_html": a.text_html,
                        "fraction": a.fraction,
                        "feedback_html": a.feedback_html,
                        "tolerance": a.tolerance,
                    }
                    for a in q.answers
                ],
            )
            inserted.append(qid)
        conn.execute(
            "UPDATE imports SET n_questions = ? WHERE id = ?",
            (len(inserted), import_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return ImportReport(
        import_id=import_id,
        filename=filename,
        inserted=inserted,
        skipped_duplicates=skipped,
        unsupported=parsed.unsupported,
        total_questions=len(parsed.questions) + len(parsed.unsupported),
    )


def cleanup_completed_import(
    conn: sqlite3.Connection, class_name: str, import_id: int | None
) -> Path | None:
    """If every question from this import has been approved or rejected, delete
    the source XML file from `<class>/imports/` if it still exists there.
    Returns the deleted path, or None if nothing was deleted."""
    if import_id is None:
        return None
    row = conn.execute(
        "SELECT filename FROM imports WHERE id = ?", (import_id,)
    ).fetchone()
    if row is None:
        return None
    pending = conn.execute(
        """SELECT COUNT(*) AS n FROM questions
           WHERE source_import_id = ? AND status = 'pending'""",
        (import_id,),
    ).fetchone()
    if pending["n"] > 0:
        return None
    # pending == 0 can also mean "nothing was ever inserted" (all duplicates).
    # Only clean up if at least one question actually came from this import.
    any_q = conn.execute(
        "SELECT 1 FROM questions WHERE source_import_id = ? LIMIT 1",
        (import_id,),
    ).fetchone()
    if any_q is None:
        return None

    safe_name = Path(row["filename"]).name
    if not safe_name:
        return None
    imports_dir = paths.class_imports_dir(class_name)
    candidate = imports_dir / safe_name
    try:
        resolved = candidate.resolve()
        root = imports_dir.resolve()
        if not resolved.is_relative_to(root):
            return None
        if not resolved.is_file():
            return None
        resolved.unlink()
        return resolved
    except OSError:
        return None
