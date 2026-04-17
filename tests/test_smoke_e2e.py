"""End-to-end smoke test bypassing the UI: import XML, approve/reject via DB layer,
verify the preview HTML renders without errors for every question type."""
from pathlib import Path

from question_bank import db, importer, preview

FIXTURES = Path(__file__).parent / "fixtures"


def test_full_flow(tmp_path):
    conn = db.connect(tmp_path / "bank.sqlite")
    report = importer.import_file(conn, FIXTURES / "sample_questions.xml")
    assert len(report.inserted) == 3

    for r in db.list_questions(conn, status="pending"):
        answers = db.get_answers(conn, r["id"])
        html = preview.render_question(r, answers)
        assert "<div" in html
        assert r["moodle_type"] in html
        if r["moodle_type"] == "multichoice":
            assert "qb-correct" in html
            assert "Feedback" in html
        if r["moodle_type"] == "numerical":
            assert "tolerance" in html.lower()

    pending = db.list_questions(conn, status="pending")
    db.set_status(conn, pending[0]["id"], "approved")
    db.set_status(conn, pending[1]["id"], "rejected", rejection_note="too easy")
    counts = db.count_by_status(conn)
    assert counts == {"pending": 1, "approved": 1, "rejected": 1}
