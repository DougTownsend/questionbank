from __future__ import annotations

import html
import sqlite3
from typing import Iterable

from .moodle_xml import format_category_for_display

PREVIEW_CSS = """
.qb-preview { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
              line-height: 1.5; color: #222; }
.qb-preview .qb-stem { font-size: 1.05rem; margin-bottom: 1rem; }
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
    parts: list[str] = [f'<div class="qb-preview">']

    meta_bits: list[str] = [f"Type: <b>{html.escape(qtype)}</b>"]
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
        parts.append(_render_multichoice(answers))
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
    return "".join(parts)


def _class_for(fraction: float) -> tuple[str, str]:
    if fraction >= 99.999:
        return "qb-correct", "Correct"
    if fraction > 0:
        return "qb-partial", f"Partial ({fraction:g}%)"
    return "qb-wrong", "Incorrect"


def _render_multichoice(answers: list[sqlite3.Row]) -> str:
    items: list[str] = ["<div><b>Answer options:</b></div>"]
    for i, a in enumerate(answers):
        cls, label = _class_for(a["fraction"])
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
