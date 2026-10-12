from __future__ import annotations

import base64
import html
import mimetypes
import re
import sqlite3
from typing import Iterable
from urllib.parse import unquote

from lxml import etree

from .moodle_xml import format_category_for_display

PREVIEW_CSS = """
.qb-preview { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
              line-height: 1.5; color: #222; }
.qb-preview .qb-stem { font-size: 1.05rem; margin-bottom: 1rem; }
.qb-preview img { max-width: 100%; height: auto; }
.qb-preview .qb-meta { color: #666; font-size: 0.85rem; margin-bottom: 0.75rem; }
.qb-preview .qb-answer {
    border: 1px solid #ddd; border-radius: 6px;
    padding: 0.5rem 0.75rem; margin: 0.35rem 0;
    background: #fafafa;
}
.qb-preview .qb-answer.qb-correct { border-color: #2e7d32; background: #e8f5e9; }
.qb-preview .qb-answer.qb-partial { border-color: #f9a825; background: #fffde7; }
.qb-preview .qb-answer.qb-wrong   { border-color: #c62828; background: #ffebee; }
.qb-preview .qb-badge {
    display: inline-block; padding: 0 0.4rem; border-radius: 3px;
    font-size: 0.75rem; font-weight: 600; margin-right: 0.5rem; vertical-align: middle;
}
.qb-preview .qb-badge.qb-correct { background: #2e7d32; color: #fff; }
.qb-preview .qb-badge.qb-partial { background: #f9a825; color: #fff; }
.qb-preview .qb-badge.qb-wrong   { background: #c62828; color: #fff; }
.qb-preview .qb-feedback {
    margin-top: 0.35rem; padding: 0.35rem 0.5rem;
    background: #fff; border-left: 3px solid #90a4ae;
    font-size: 0.9rem; color: #444;
}
.qb-preview .qb-general-feedback {
    margin-top: 1rem; padding: 0.5rem 0.75rem;
    background: #e3f2fd; border-left: 3px solid #1565c0;
    border-radius: 4px;
}
.qb-preview .qb-tolerance { color: #666; font-size: 0.85rem; }
"""


def render_question(
    question: sqlite3.Row, answers: Iterable[sqlite3.Row]
) -> str:
    answers = list(answers)
    qtype = question["moodle_type"]
    multiple = qtype == "multichoice" and question["multichoice_single"] == 0
    parts: list[str] = [f'<div class="qb-preview">']

    type_label = "multichoice (select all that apply)" if multiple else qtype
    meta_bits: list[str] = [f"Type: <b>{html.escape(type_label)}</b>"]
    cat_display = format_category_for_display(question["raw_category"])
    if cat_display:
        meta_bits.append(f"Category: {html.escape(cat_display)}")
    if question["default_grade"] is not None:
        meta_bits.append(f"Grade: {question['default_grade']}")
    if question["name"]:
        meta_bits.append(f"Name: {html.escape(question['name'])}")
    parts.append(f'<div class="qb-meta">{" &middot; ".join(meta_bits)}</div>')

    parts.append(f'<div class="qb-stem">{question["stem_html"] or ""}</div>')

    if qtype == "multichoice":
        parts.append(_render_multichoice(answers, multiple=multiple))
    elif qtype == "truefalse":
        parts.append(_render_truefalse(answers))
    elif qtype == "numerical":
        parts.append(_render_numerical(answers))
    else:
        parts.append(f"<p><em>Unsupported type: {html.escape(qtype)}</em></p>")

    if question["general_feedback_html"]:
        parts.append(
            '<div class="qb-general-feedback">'
            "<b>General feedback (shown after attempt):</b><br>"
            f"{question['general_feedback_html']}"
            "</div>"
        )

    parts.append("</div>")
    return _embed_pluginfiles("".join(parts), question["raw_xml"])


_PLUGINFILE_RE = re.compile(
    r"@@PLUGINFILE@@/(?P<path>[^\"'<>\s?#]+)(?:\?[^\"'<>\s]*)?"
)


def _embed_pluginfiles(rendered_html: str, raw_xml: str) -> str:
    try:
        root = etree.fromstring(raw_xml.encode("utf-8"))
    except (AttributeError, etree.XMLSyntaxError):
        return rendered_html

    files: dict[str, str] = {}
    for file_el in root.findall(".//file"):
        if file_el.get("encoding") != "base64" or not file_el.get("name"):
            continue
        payload = "".join((file_el.text or "").split())
        try:
            base64.b64decode(payload, validate=True)
        except ValueError:
            continue
        path = "/".join(
            part
            for part in (
                (file_el.get("path") or "/").strip("/"),
                file_el.get("name") or "",
            )
            if part
        )
        mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        files[path] = f"data:{mime_type};base64,{payload}"

    def replace(match: re.Match[str]) -> str:
        path = unquote(match.group("path")).strip("/")
        return files.get(path, match.group(0))

    return _PLUGINFILE_RE.sub(replace, rendered_html)


def _class_for(fraction: float, *, multiple: bool = False) -> tuple[str, str]:
    if multiple and fraction > 0:
        return "qb-correct", f"Correct selection ({fraction:g}%)"
    if fraction >= 99.999:
        return "qb-correct", "Correct"
    if fraction > 0:
        return "qb-partial", f"Partial ({fraction:g}%)"
    return "qb-wrong", "Incorrect"


def _render_multichoice(
    answers: list[sqlite3.Row], *, multiple: bool
) -> str:
    instruction = "Select all that apply" if multiple else "Answer options"
    items: list[str] = [f"<div><b>{instruction}:</b></div>"]
    for i, a in enumerate(answers):
        cls, label = _class_for(a["fraction"], multiple=multiple)
        items.append(
            f'<div class="qb-answer {cls}">'
            f'<span class="qb-badge {cls}">{label}</span>'
            f"{a['text_html'] or ''}"
        )
        if a["feedback_html"]:
            items.append(
                f'<div class="qb-feedback"><b>Feedback:</b> {a["feedback_html"]}</div>'
            )
        items.append("</div>")
    return "".join(items)


def _render_truefalse(answers: list[sqlite3.Row]) -> str:
    items: list[str] = ["<div><b>Answer options:</b></div>"]
    for a in answers:
        cls, label = _class_for(a["fraction"])
        items.append(
            f'<div class="qb-answer {cls}">'
            f'<span class="qb-badge {cls}">{label}</span>'
            f"<b>{html.escape((a['text_html'] or '').strip().capitalize())}</b>"
        )
        if a["feedback_html"]:
            items.append(
                f'<div class="qb-feedback"><b>Feedback:</b> {a["feedback_html"]}</div>'
            )
        items.append("</div>")
    return "".join(items)


def _render_numerical(answers: list[sqlite3.Row]) -> str:
    items: list[str] = ["<div><b>Accepted answers:</b></div>"]
    for a in answers:
        cls, label = _class_for(a["fraction"])
        tol = a["tolerance"]
        tol_str = (
            f' <span class="qb-tolerance">(tolerance &plusmn; {tol:g})</span>'
            if tol is not None
            else ""
        )
        items.append(
            f'<div class="qb-answer {cls}">'
            f'<span class="qb-badge {cls}">{label}</span>'
            f"<code>{html.escape(a['text_html'] or '')}</code>{tol_str}"
        )
        if a["feedback_html"]:
            items.append(
                f'<div class="qb-feedback"><b>Feedback:</b> {a["feedback_html"]}</div>'
            )
        items.append("</div>")
    return "".join(items)
