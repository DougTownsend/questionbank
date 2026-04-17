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
