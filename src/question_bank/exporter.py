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
        parts.append(_indent_question(_ensure_cdata_in_text(q["raw_xml"])))
        parts.append("\n")
    parts.append("</quiz>\n")
    return "".join(parts)


def _ensure_cdata_in_text(raw_xml: str) -> str:
    """Moodle's XML importer rejects anything other than a plain string
    inside <text> elements (error: 'Invalid XML file - string expected
    (use CDATA?)'). This can happen two ways:

    1. The stored HTML is entity-escaped instead of CDATA-wrapped.
    2. Claude's XML accidentally terminated a CDATA section early
       (e.g. ']]>' appearing mid-HTML) which left real XML subelements
       hanging inside <text>.

    Fix both by collapsing every <text> element's full inner content
    (own text + serialized children + tails) into a single string and
    wrapping it in a CDATA section."""
    parser = etree.XMLParser(strip_cdata=False)
    data = raw_xml.encode("utf-8") if isinstance(raw_xml, str) else raw_xml
    q = etree.fromstring(data, parser=parser)
    for t in q.iter("text"):
        parts: list[str] = [t.text or ""]
        for child in list(t):
            parts.append(etree.tostring(child, encoding="unicode"))
            t.remove(child)
        t.text = etree.CDATA("".join(parts))
    return etree.tostring(q, encoding="unicode", pretty_print=True)


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
