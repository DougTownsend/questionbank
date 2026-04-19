from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import db, paths


@dataclass
class AssignmentImportReport:
    assignment_id: int | None
    assignment_name: str
    description: str | None
    added_question_ids: list[int]
    skipped: list[tuple[int, str]]
    warnings: list[str] = field(default_factory=list)


class AssignmentImportError(Exception):
    pass


def import_assignment_file(
    conn: sqlite3.Connection, class_name: str, path: Path | str
) -> AssignmentImportReport:
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise AssignmentImportError(f"could not read file: {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AssignmentImportError(f"invalid JSON: {e.msg} (line {e.lineno})") from e
    return _apply(conn, class_name, data, source_path=path)


def import_assignment_bytes(
    conn: sqlite3.Connection, class_name: str, data: bytes
) -> AssignmentImportReport:
    try:
        obj = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise AssignmentImportError(f"invalid JSON: {e}") from e
    return _apply(conn, class_name, obj, source_path=None)


def _apply(
    conn: sqlite3.Connection,
    class_name: str,
    data,
    *,
    source_path: Path | None,
) -> AssignmentImportReport:
    if not isinstance(data, dict):
        raise AssignmentImportError(
            "top level must be a JSON object with 'name' and 'question_ids'"
        )

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise AssignmentImportError("missing or empty 'name' string")
    name = name.strip()

    description = data.get("description")
    if description is not None and not isinstance(description, str):
        raise AssignmentImportError("'description' must be a string if provided")

    ids_raw = data.get("question_ids")
    if not isinstance(ids_raw, list):
        raise AssignmentImportError("'question_ids' must be a list of integers")

    question_ids: list[int] = []
    for i, item in enumerate(ids_raw):
        if isinstance(item, bool) or not isinstance(item, int):
            raise AssignmentImportError(
                f"question_ids[{i}] is not an integer: {item!r}"
            )
        question_ids.append(item)

    if not question_ids:
        raise AssignmentImportError("question_ids is empty")

    skipped: list[tuple[int, str]] = []
    valid_ids: list[int] = []
    seen: set[int] = set()
    for qid in question_ids:
        if qid in seen:
            skipped.append((qid, "duplicate in list"))
            continue
        seen.add(qid)
        row = db.get_question(conn, qid)
        if row is None:
            skipped.append((qid, "not found in this class's bank"))
            continue
        if row["status"] != "approved":
            skipped.append((qid, f"status={row['status']} (only approved allowed)"))
            continue
        valid_ids.append(qid)

    if not valid_ids:
        return AssignmentImportReport(
            assignment_id=None,
            assignment_name=name,
            description=description,
            added_question_ids=[],
            skipped=skipped,
            warnings=["No valid approved questions found; assignment not created."],
        )

    conn.execute("BEGIN")
    try:
        aid = db.create_assignment(conn, name)
        for qid in valid_ids:
            db.add_question_to_assignment(conn, aid, qid)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    if source_path is not None:
        _safe_delete_in_imports(class_name, source_path)

    return AssignmentImportReport(
        assignment_id=aid,
        assignment_name=name,
        description=description,
        added_question_ids=valid_ids,
        skipped=skipped,
    )


def _safe_delete_in_imports(class_name: str, path: Path) -> None:
    """Delete the source JSON, but only if it lives inside this class's
    imports/ directory. Defense-in-depth against bad inputs."""
    try:
        imports_dir = paths.class_imports_dir(class_name).resolve()
        resolved = path.resolve()
        if not resolved.is_relative_to(imports_dir):
            return
        if resolved.is_file():
            resolved.unlink()
    except OSError:
        pass
