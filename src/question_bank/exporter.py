from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from lxml import etree

from . import db, paths

_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>\n'


def build_assignment_xml(
    conn: sqlite3.Connection, assignment_id: int
) -> str:
    """Build a Moodle-importable XML for the assignment. All questions are
    placed under a single category named after the assignment so that, after
    importing, picking 'select all from this category' in Moodle's quiz
    builder gets you exactly the questions in this assignment."""
    assignment = db.get_assignment(conn, assignment_id)
    if assignment is None:
        raise ValueError(f"assignment {assignment_id} not found")
    questions = db.list_assignment_questions(conn, assignment_id)
    category_path = f"$course$/{assignment['name']}"
    parts: list[str] = [_XML_DECL, "<quiz>\n", _category_block(category_path)]
    for q in questions:
        prepared = _prepare_export_question(q["raw_xml"], q["aq_point_value"])
        parts.append(_indent_question(prepared))
        parts.append("\n")
    parts.append("</quiz>\n")
    return "".join(parts)


def _prepare_export_question(raw_xml: str, point_value: float | None) -> str:
    """Clean up a stored question for Moodle import:

    1. Wrap every <text> element's content in CDATA — Moodle rejects
       entity-escaped HTML ('string expected (use CDATA?)'), and can't
       handle <text> with mixed content if a stray ']]>' terminated a
       CDATA section in Claude's input.
    2. If the assignment overrides this question's point value, update
       <defaultgrade> in-place (adding it if missing)."""
    parser = etree.XMLParser(strip_cdata=False)
    data = raw_xml.encode("utf-8") if isinstance(raw_xml, str) else raw_xml
    q = etree.fromstring(data, parser=parser)

    for t in q.iter("text"):
        parts: list[str] = [t.text or ""]
        for child in list(t):
            parts.append(etree.tostring(child, encoding="unicode"))
            t.remove(child)
        t.text = etree.CDATA("".join(parts))

    if point_value is not None:
        dg = q.find("defaultgrade")
        if dg is None:
            dg = etree.SubElement(q, "defaultgrade")
        dg.text = _fmt_grade(float(point_value))

    return etree.tostring(q, encoding="unicode", pretty_print=True)


def _fmt_grade(x: float) -> str:
    if x == int(x):
        return f"{int(x)}.0"
    return f"{x:g}"


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
