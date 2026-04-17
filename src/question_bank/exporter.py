from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from . import db, paths

_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>\n'


def build_assignment_xml(
    conn: sqlite3.Connection, assignment_id: int
) -> str:
    questions = db.list_assignment_questions(conn, assignment_id)
    parts: list[str] = [_XML_DECL, "<quiz>\n"]
    last_category: str | None = None
    for q in questions:
        cat = q["raw_category"]
        if cat and cat != last_category:
            parts.append(_category_block(cat))
            last_category = cat
        parts.append(_indent_question(q["raw_xml"]))
        parts.append("\n")
    parts.append("</quiz>\n")
    return "".join(parts)


def _category_block(category: str) -> str:
    esc = _xml_text_escape(category)
    return (
        "  <question type=\"category\">\n"
        f"    <category><text>{esc}</text></category>\n"
        "  </question>\n"
    )


_INDENT_RE = re.compile(r"^", re.MULTILINE)


def _indent_question(raw_xml: str) -> str:
    stripped = raw_xml.strip()
    return _INDENT_RE.sub("  ", stripped)


def _xml_text_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def export_assignment_to_file(
    conn: sqlite3.Connection,
    class_name: str,
    assignment_id: int,
) -> Path:
    a = db.get_assignment(conn, assignment_id)
    if a is None:
        raise ValueError(f"assignment {assignment_id} not found")
    xml = build_assignment_xml(conn, assignment_id)
    out_dir = paths.class_exports_dir(class_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = _safe_filename(a["name"]) + ".xml"
    path = out_dir / filename
    path.write_text(xml, encoding="utf-8")
    return path


_UNSAFE = re.compile(r'[\\/:*?"<>|\s]+')


def _safe_filename(name: str) -> str:
    out = _UNSAFE.sub("_", name.strip()).strip("._")
    return out or "assignment"
