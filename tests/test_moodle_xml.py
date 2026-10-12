from pathlib import Path

import pytest

from question_bank import moodle_xml

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_sample_file() -> None:
    result = moodle_xml.parse_file(FIXTURES / "sample_questions.xml")

    assert len(result.questions) == 3
    types = [q.moodle_type for q in result.questions]
    assert types == ["multichoice", "truefalse", "numerical"]

    assert result.unsupported == [("essay", "Explain cache coherence")]


def test_category_propagates_to_following_questions() -> None:
    result = moodle_xml.parse_file(FIXTURES / "sample_questions.xml")
    for q in result.questions:
        assert q.raw_category == "$course$/ECE212/Chapter 3/Caches"


def test_multichoice_answers_parsed() -> None:
    result = moodle_xml.parse_file(FIXTURES / "sample_questions.xml")
    mc = result.questions[0]
    assert mc.moodle_type == "multichoice"
    assert mc.name == "Cache hit rate definition"
    assert len(mc.answers) == 4
    correct = [a for a in mc.answers if a.fraction == 100.0]
    assert len(correct) == 1
    assert "hits / (hits + misses)" in correct[0].text_html
    assert mc.multichoice_single is True


def test_select_all_parses_and_serializes_with_multiple_correct_answers() -> None:
    xml = b"""
    <quiz><question type="multichoice">
      <name><text>Select all</text></name>
      <questiontext format="html"><text>Select all that apply.</text></questiontext>
      <single>false</single>
      <answer fraction="50"><text>A</text></answer>
      <answer fraction="50"><text>B</text></answer>
      <answer fraction="-50"><text>C</text></answer>
    </question></quiz>
    """

    question = moodle_xml.parse_bytes(xml).questions[0]

    assert question.multichoice_single is False
    assert [a.fraction for a in question.answers] == [50.0, 50.0, -50.0]

    serialized = moodle_xml.serialize_question(
        moodle_type="multichoice",
        multichoice_single=False,
        name=question.name,
        stem_html=question.stem_html,
        general_feedback_html=None,
        default_grade=1.0,
        penalty=0.3333333,
        answers=[
            {
                "text_html": a.text_html,
                "fraction": a.fraction,
                "feedback_html": a.feedback_html,
            }
            for a in question.answers
        ],
    )
    reparsed = moodle_xml.parse_bytes(
        f"<quiz>{serialized}</quiz>".encode("utf-8")
    ).questions[0]
    assert reparsed.multichoice_single is False


def test_select_all_positive_fractions_must_total_100() -> None:
    xml = b"""
    <quiz><question type="multichoice">
      <name><text>Bad scoring</text></name>
      <questiontext format="html"><text>Select all that apply.</text></questiontext>
      <single>false</single>
      <answer fraction="40"><text>A</text></answer>
      <answer fraction="40"><text>B</text></answer>
    </question></quiz>
    """

    with pytest.raises(moodle_xml.ParseError, match="must total 100"):
        moodle_xml.parse_bytes(xml)


def test_truefalse_answers_parsed() -> None:
    result = moodle_xml.parse_file(FIXTURES / "sample_questions.xml")
    tf = result.questions[1]
    assert tf.moodle_type == "truefalse"
    assert [a.text_html for a in tf.answers] == ["true", "false"]
    assert tf.answers[0].fraction == 100.0
    assert tf.answers[1].fraction == 0.0


def test_numerical_answer_has_tolerance() -> None:
    result = moodle_xml.parse_file(FIXTURES / "sample_questions.xml")
    num = result.questions[2]
    assert num.moodle_type == "numerical"
    assert num.answers[0].text_html == "6"
    assert num.answers[0].tolerance == 0.0
    assert num.answers[0].fraction == 100.0


def test_general_feedback_captured() -> None:
    result = moodle_xml.parse_file(FIXTURES / "sample_questions.xml")
    mc = result.questions[0]
    assert mc.general_feedback_html is not None
    assert "hits / total accesses" in mc.general_feedback_html


def test_raw_xml_preserved() -> None:
    result = moodle_xml.parse_file(FIXTURES / "sample_questions.xml")
    mc = result.questions[0]
    assert "<question type=\"multichoice\"" in mc.raw_xml
    assert "<name>" in mc.raw_xml


def test_stem_hash_stable_across_whitespace_changes() -> None:
    h1 = moodle_xml.stem_hash_for("<p>What  is   X?</p>")
    h2 = moodle_xml.stem_hash_for("<p>What is X?</p>")
    assert h1 == h2


def test_stem_hash_distinguishes_different_stems() -> None:
    h1 = moodle_xml.stem_hash_for("<p>What is X?</p>")
    h2 = moodle_xml.stem_hash_for("<p>What is Y?</p>")
    assert h1 != h2


def test_stem_hash_is_case_insensitive() -> None:
    h1 = moodle_xml.stem_hash_for("<p>What Is X?</p>")
    h2 = moodle_xml.stem_hash_for("<p>what is x?</p>")
    assert h1 == h2


def test_parse_error_on_bad_root() -> None:
    with pytest.raises(moodle_xml.ParseError):
        moodle_xml.parse_bytes(b"<notquiz></notquiz>")


def test_parse_error_on_invalid_xml() -> None:
    with pytest.raises(moodle_xml.ParseError):
        moodle_xml.parse_bytes(b"<quiz><question")


def test_superscript_parses_intact_in_cdata_and_raw_forms() -> None:
    """Whether Claude CDATA-wraps or emits raw <sup>, the parser should
    land the same HTML in text_html so downstream rendering (browser
    preview + LaTeX export) shows the superscript."""
    for stem_markup, expected in [
        (b"<![CDATA[2<sup>31</sup>-1]]>", "2<sup>31</sup>-1"),
        (b"2<sup>31</sup>-1", "2<sup>31</sup>-1"),
    ]:
        xml = (
            b"<quiz><question type='multichoice'>"
            b"<name><text>Q</text></name>"
            b"<questiontext format='html'><text>"
            + stem_markup
            + b"</text></questiontext>"
            b"<answer fraction='100' format='html'>"
            b"<text>" + stem_markup + b"</text>"
            b"<feedback format='html'><text>ok</text></feedback>"
            b"</answer>"
            b"</question></quiz>"
        )
        result = moodle_xml.parse_bytes(xml)
        q = result.questions[0]
        assert q.stem_html == expected
        assert q.answers[0].text_html == expected


def test_text_element_without_cdata_preserves_inline_html() -> None:
    """Claude sometimes writes <text><code>xB4</code></text> without
    CDATA-wrapping, making <code> a real child element. The parser must
    still capture the full inner content, not just the text before the
    first child."""
    xml = (
        b"<quiz>"
        b"<question type='multichoice'>"
        b"<name><text>Q</text></name>"
        b"<questiontext format='html'><text>stem</text></questiontext>"
        b"<answer fraction='100' format='html'>"
        b"<text><code>xB4</code></text>"
        b"<feedback format='html'><text>yes</text></feedback>"
        b"</answer>"
        b"<answer fraction='0' format='html'>"
        b"<text>Four successive <code>.FILL</code> lines</text>"
        b"<feedback format='html'><text>no</text></feedback>"
        b"</answer>"
        b"</question>"
        b"</quiz>"
    )
    result = moodle_xml.parse_bytes(xml)
    assert len(result.questions) == 1
    q = result.questions[0]
    assert q.answers[0].text_html == "<code>xB4</code>"
    assert q.answers[1].text_html == "Four successive <code>.FILL</code> lines"


def test_format_category_strips_course_prefix() -> None:
    assert (
        moodle_xml.format_category_for_display("$course$/ECE212/Error Correction")
        == "ECE212/Error Correction"
    )


def test_format_category_strips_module_prefix() -> None:
    assert (
        moodle_xml.format_category_for_display("$module$/Chapter 3")
        == "Chapter 3"
    )


def test_format_category_leaves_plain_path_alone() -> None:
    assert moodle_xml.format_category_for_display("ECE212/Caches") == "ECE212/Caches"


def test_format_category_handles_none_and_empty() -> None:
    assert moodle_xml.format_category_for_display(None) == ""
    assert moodle_xml.format_category_for_display("") == ""
