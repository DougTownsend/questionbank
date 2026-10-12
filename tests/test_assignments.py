from pathlib import Path

import pytest
from lxml import etree

from question_bank import db, exporter, importer, moodle_xml

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn_with_approved(tmp_path):
    conn = db.connect(tmp_path / "bank.sqlite")
    importer.import_file(conn, FIXTURES / "sample_questions.xml")
    for q in db.list_questions(conn, status="pending"):
        db.set_status(conn, q["id"], "approved")
    return conn


def test_create_list_rename_delete(conn_with_approved):
    conn = conn_with_approved
    aid = db.create_assignment(conn, "Quiz 1")
    assert db.get_assignment(conn, aid)["name"] == "Quiz 1"

    rows = db.list_assignments(conn)
    assert len(rows) == 1
    assert rows[0]["n_questions"] == 0

    db.rename_assignment(conn, aid, "Midterm")
    assert db.get_assignment(conn, aid)["name"] == "Midterm"

    db.delete_assignment(conn, aid)
    assert db.get_assignment(conn, aid) is None


def test_add_and_remove_questions_ordering(conn_with_approved):
    conn = conn_with_approved
    aid = db.create_assignment(conn, "Quiz 1")
    qs = db.list_questions(conn, status="approved")
    assert len(qs) == 3

    for q in qs:
        assert db.add_question_to_assignment(conn, aid, q["id"]) is True
    assert db.add_question_to_assignment(conn, aid, qs[0]["id"]) is False  # dupe

    picked = db.list_assignment_questions(conn, aid)
    assert [p["id"] for p in picked] == [q["id"] for q in qs]

    db.remove_question_from_assignment(conn, aid, qs[1]["id"])
    picked = db.list_assignment_questions(conn, aid)
    assert [p["id"] for p in picked] == [qs[0]["id"], qs[2]["id"]]
    assert [p["aq_ord"] for p in picked] == [0, 1]  # compacted
    assert [p["aq_point_value"] for p in picked] == [50.0, 50.0]


def test_delete_assignment_cascades(conn_with_approved):
    conn = conn_with_approved
    aid = db.create_assignment(conn, "Quiz 1")
    qs = db.list_questions(conn, status="approved")
    db.add_question_to_assignment(conn, aid, qs[0]["id"])
    db.delete_assignment(conn, aid)
    rows = list(conn.execute("SELECT * FROM assignment_questions"))
    assert rows == []


def test_export_xml_is_valid_and_has_all_questions(conn_with_approved, tmp_path):
    conn = conn_with_approved
    aid = db.create_assignment(conn, "Quiz 1")
    for q in db.list_questions(conn, status="approved"):
        db.add_question_to_assignment(conn, aid, q["id"])

    xml = exporter.build_assignment_xml(conn, aid)
    assert xml.startswith("<?xml")

    root = etree.fromstring(xml.encode("utf-8"))
    assert root.tag == "quiz"
    types = [q.get("type") for q in root.findall("question")]
    assert "multichoice" in types
    assert "truefalse" in types
    assert "numerical" in types
    assert "category" in types


def test_export_round_trip_parses_with_own_parser(conn_with_approved, tmp_path):
    conn = conn_with_approved
    aid = db.create_assignment(conn, "Quiz 1")
    for q in db.list_questions(conn, status="approved"):
        db.add_question_to_assignment(conn, aid, q["id"])

    xml = exporter.build_assignment_xml(conn, aid)
    result = moodle_xml.parse_bytes(xml.encode("utf-8"))
    assert len(result.questions) == 3
    assert {q.moodle_type for q in result.questions} == {
        "multichoice",
        "truefalse",
        "numerical",
    }
    # every question ends up under a single category named after the assignment
    for q in result.questions:
        assert q.raw_category == "$course$/Quiz 1"


def test_automatic_points_always_total_100(conn_with_approved):
    conn = conn_with_approved
    aid = db.create_assignment(conn, "Q")
    questions = db.list_questions(conn, status="approved")

    db.add_question_to_assignment(conn, aid, questions[0]["id"])
    assert [q["aq_point_value"] for q in db.list_assignment_questions(conn, aid)] == [100.0]

    db.add_question_to_assignment(conn, aid, questions[1]["id"])
    assert [q["aq_point_value"] for q in db.list_assignment_questions(conn, aid)] == [50.0, 50.0]

    db.add_question_to_assignment(conn, aid, questions[2]["id"])
    points = [
        q["aq_point_value"] for q in db.list_assignment_questions(conn, aid)
    ]
    assert points == [33.34, 33.33, 33.33]
    assert sum(points) == 100.0


def test_bulk_add_preserves_order_and_balances_once(conn_with_approved):
    conn = conn_with_approved
    aid = db.create_assignment(conn, "Q")
    questions = db.list_questions(conn, status="approved")
    ids = [questions[2]["id"], questions[0]["id"], questions[1]["id"]]

    added = db.add_questions_to_assignment(conn, aid, ids + [ids[0]])

    assert added == ids
    picked = db.list_assignment_questions(conn, aid)
    assert [q["id"] for q in picked] == ids
    assert [q["aq_point_value"] for q in picked] == [33.34, 33.33, 33.33]


def test_set_assignment_question_points_updates_value(conn_with_approved):
    conn = conn_with_approved
    aid = db.create_assignment(conn, "Q")
    q = db.list_questions(conn, status="approved")[0]
    db.add_question_to_assignment(conn, aid, q["id"])
    db.set_assignment_question_points(conn, aid, q["id"], 2.5)
    picked = db.list_assignment_questions(conn, aid)
    assert picked[0]["aq_point_value"] == 2.5
    assert db.get_assignment(conn, aid)["points_manually_adjusted"] == 1


def test_manual_points_are_not_rebalanced_on_add_or_remove(conn_with_approved):
    conn = conn_with_approved
    aid = db.create_assignment(conn, "Q")
    questions = db.list_questions(conn, status="approved")
    db.add_questions_to_assignment(conn, aid, [q["id"] for q in questions[:2]])
    db.set_assignment_question_points(conn, aid, questions[0]["id"], 60.0)

    db.add_question_to_assignment(conn, aid, questions[2]["id"])
    picked = db.list_assignment_questions(conn, aid)
    assert [q["aq_point_value"] for q in picked] == [60.0, 50.0, 1.0]

    db.remove_question_from_assignment(conn, aid, questions[2]["id"])
    picked = db.list_assignment_questions(conn, aid)
    assert [q["aq_point_value"] for q in picked] == [60.0, 50.0]


def test_deleting_question_rebalances_automatic_assignment(conn_with_approved):
    conn = conn_with_approved
    aid = db.create_assignment(conn, "Q")
    questions = db.list_questions(conn, status="approved")
    db.add_questions_to_assignment(conn, aid, [q["id"] for q in questions])

    db.delete_question(conn, questions[1]["id"])

    picked = db.list_assignment_questions(conn, aid)
    assert [q["aq_point_value"] for q in picked] == [50.0, 50.0]


def test_export_uses_overridden_point_value(conn_with_approved):
    conn = conn_with_approved
    aid = db.create_assignment(conn, "Q")
    qs = db.list_questions(conn, status="approved")
    db.add_question_to_assignment(conn, aid, qs[0]["id"])
    db.add_question_to_assignment(conn, aid, qs[1]["id"])
    db.set_assignment_question_points(conn, aid, qs[0]["id"], 3.0)
    # The second question retains its last automatic value after manual mode starts.

    xml = exporter.build_assignment_xml(conn, aid)
    root = etree.fromstring(xml.encode("utf-8"))
    non_cat = [q for q in root.findall("question") if q.get("type") != "category"]
    grades = [float(q.find("defaultgrade").text) for q in non_cat]
    assert grades == [3.0, 50.0]


def test_export_uses_assignment_name_as_sole_category(conn_with_approved):
    conn = conn_with_approved
    aid = db.create_assignment(conn, "Quiz 3 — Caches")
    for q in db.list_questions(conn, status="approved"):
        db.add_question_to_assignment(conn, aid, q["id"])
    xml = exporter.build_assignment_xml(conn, aid)

    root = etree.fromstring(xml.encode("utf-8"))
    category_questions = [
        q for q in root.findall("question") if q.get("type") == "category"
    ]
    assert len(category_questions) == 1
    cat_text = category_questions[0].find("category/text").text
    assert cat_text == "$course$/Quiz 3 — Caches"


def test_export_wraps_text_nodes_in_cdata(conn_with_approved):
    """Moodle rejects entity-escaped HTML in <text>; export must emit CDATA."""
    conn = conn_with_approved
    aid = db.create_assignment(conn, "Q")
    for q in db.list_questions(conn, status="approved"):
        db.add_question_to_assignment(conn, aid, q["id"])
    xml = exporter.build_assignment_xml(conn, aid)
    assert "<![CDATA[" in xml
    # ensure HTML bits are inside CDATA rather than entity-escaped
    assert "&lt;p&gt;" not in xml


def test_export_flattens_mixed_content_in_text(tmp_path):
    """If Claude's XML accidentally terminated a CDATA early (']]>' mid-HTML),
    lxml parses real subelements inside <text>. Export must flatten these
    back into a CDATA string so Moodle accepts the file."""
    conn = db.connect(tmp_path / "bank.sqlite")
    # raw_xml where CDATA terminates early and <i>...</i> becomes a real child
    # of <text>; subsequent text is the <i>'s tail.
    busted_raw = (
        '<question type="multichoice">'
        "<name><text>Q</text></name>"
        "<questiontext format=\"html\"><text>stem</text></questiontext>"
        "<defaultgrade>1.0</defaultgrade>"
        "<penalty>0.333</penalty>"
        "<hidden>0</hidden>"
        "<single>true</single>"
        "<shuffleanswers>true</shuffleanswers>"
        "<answernumbering>abc</answernumbering>"
        "<answer fraction=\"100\" format=\"html\">"
        "<text>right</text>"
        "<feedback format=\"html\"><text>ok</text></feedback>"
        "</answer>"
        "<answer fraction=\"0\" format=\"html\">"
        "<text>wrong</text>"
        "<feedback format=\"html\">"
        "<text>refers to the <i>topology</i> of the circuit</text>"
        "</feedback>"
        "</answer>"
        "</question>"
    )
    qid = db.insert_question(
        conn,
        moodle_type="multichoice",
        name="Q",
        stem_html="stem",
        stem_hash="h",
        raw_xml=busted_raw,
        raw_category=None,
        default_grade=1.0,
        penalty=0.333,
        general_feedback_html=None,
        source_import_id=None,
        answers=[
            {"text_html": "right", "fraction": 100.0, "feedback_html": "ok"},
            {"text_html": "wrong", "fraction": 0.0, "feedback_html": "..."},
        ],
    )
    db.set_status(conn, qid, "approved")
    aid = db.create_assignment(conn, "Mixed")
    db.add_question_to_assignment(conn, aid, qid)
    xml = exporter.build_assignment_xml(conn, aid)

    root = etree.fromstring(xml.encode("utf-8"))
    # no <text> element should have child elements in the exported XML
    for t in root.iter("text"):
        assert len(list(t)) == 0, (
            f"<text> still has child elements: "
            f"{etree.tostring(t, encoding='unicode')}"
        )
    # the italic content survives as text inside the CDATA
    assert "<i>topology</i>" in xml
    assert "<![CDATA[" in xml


def test_export_handles_entity_escaped_raw_xml(tmp_path):
    """A question whose raw_xml has entity-escaped HTML (e.g. from a pre-fix
    DB row) should still export with proper CDATA."""
    conn = db.connect(tmp_path / "bank.sqlite")
    legacy_raw = (
        '<question type="truefalse">'
        "<name><text>Q</text></name>"
        "<questiontext format=\"html\">"
        "<text>&lt;p&gt;Is this true?&lt;/p&gt;</text>"
        "</questiontext>"
        "<answer fraction=\"100\" format=\"moodle_auto_format\">"
        "<text>true</text>"
        "<feedback format=\"html\"><text>yes</text></feedback>"
        "</answer>"
        "<answer fraction=\"0\" format=\"moodle_auto_format\">"
        "<text>false</text>"
        "<feedback format=\"html\"><text>no</text></feedback>"
        "</answer>"
        "</question>"
    )
    qid = db.insert_question(
        conn,
        moodle_type="truefalse",
        name="Q",
        stem_html="<p>Is this true?</p>",
        stem_hash="h",
        raw_xml=legacy_raw,
        raw_category=None,
        default_grade=1.0,
        penalty=1.0,
        general_feedback_html=None,
        source_import_id=None,
        answers=[
            {"text_html": "true", "fraction": 100.0, "feedback_html": "yes"},
            {"text_html": "false", "fraction": 0.0, "feedback_html": "no"},
        ],
    )
    db.set_status(conn, qid, "approved")
    aid = db.create_assignment(conn, "Legacy")
    db.add_question_to_assignment(conn, aid, qid)
    xml = exporter.build_assignment_xml(conn, aid)
    assert "<![CDATA[" in xml
    assert "Is this true?" in xml
    assert "&lt;p&gt;" not in xml
    # sanity: still parses as valid XML
    etree.fromstring(xml.encode("utf-8"))


def test_export_to_file_writes_under_class_exports(
    conn_with_approved, tmp_path, monkeypatch
):
    monkeypatch.setenv("QUESTION_BANK_ROOT", str(tmp_path))
    from question_bank import paths

    class_name = "TestClass"
    paths.ensure_class_dirs(class_name)
    # re-connect via the class path so the DB lives where exporter expects
    class_db = db.connect(paths.class_db_path(class_name))
    importer.import_file(class_db, FIXTURES / "sample_questions.xml")
    for q in db.list_questions(class_db, status="pending"):
        db.set_status(class_db, q["id"], "approved")
    aid = db.create_assignment(class_db, "Quiz 3 — Caches")
    for q in db.list_questions(class_db, status="approved"):
        db.add_question_to_assignment(class_db, aid, q["id"])

    out_path = exporter.export_assignment_to_file(class_db, class_name, aid)
    assert out_path.exists()
    assert out_path.parent == paths.class_exports_dir(class_name)
    assert out_path.suffix == ".xml"
    # filename should have no spaces
    assert " " not in out_path.name
