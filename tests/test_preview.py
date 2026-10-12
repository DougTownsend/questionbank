from __future__ import annotations

import base64

from question_bank import preview


def _question(stem_html: str, raw_xml: str) -> dict:
    return {
        "moodle_type": "multichoice",
        "multichoice_single": 1,
        "raw_category": "$course$/Test",
        "default_grade": 1.0,
        "name": "Embedded image",
        "stem_html": stem_html,
        "general_feedback_html": None,
        "raw_xml": raw_xml,
    }


def test_render_question_embeds_pluginfile_image() -> None:
    image = b"\x89PNG\r\n\x1a\npreview-test"
    encoded = base64.b64encode(image).decode("ascii")
    stem = '<img src="@@PLUGINFILE@@/maps/kmap%20one.png?forcedownload=1">'
    raw_xml = f"""
    <question type="multichoice">
      <questiontext format="html">
        <text><![CDATA[{stem}]]></text>
        <file name="kmap one.png" path="/maps/" encoding="base64">
          {encoded}
        </file>
      </questiontext>
    </question>
    """

    rendered = preview.render_question(_question(stem, raw_xml), [])

    assert "@@PLUGINFILE@@" not in rendered
    assert f"data:image/png;base64,{encoded}" in rendered


def test_render_question_leaves_unknown_pluginfile_unchanged() -> None:
    stem = '<img src="@@PLUGINFILE@@/missing.png">'
    raw_xml = """
    <question type="multichoice">
      <questiontext format="html"><text>stem</text></questiontext>
    </question>
    """

    rendered = preview.render_question(_question(stem, raw_xml), [])

    assert "@@PLUGINFILE@@/missing.png" in rendered


def test_render_select_all_marks_every_positive_fraction_correct() -> None:
    question = _question("<p>Select all that apply.</p>", "<question/>")
    question["multichoice_single"] = 0
    answers = [
        {"fraction": 50.0, "text_html": "A", "feedback_html": "yes"},
        {"fraction": 50.0, "text_html": "B", "feedback_html": "yes"},
        {"fraction": -50.0, "text_html": "C", "feedback_html": "no"},
    ]

    rendered = preview.render_question(question, answers)

    assert "multichoice (select all that apply)" in rendered
    assert rendered.count("Correct selection (50%)") == 2
    assert rendered.count("qb-correct") >= 2
