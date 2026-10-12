"""Regression tests for SQLite schema migrations."""
import sqlite3
from pathlib import Path

from question_bank import db


def _simulate_v1_db(path: Path) -> None:
    """Create a bank.sqlite exactly as v1 shipped (no point_value column,
    schema_info.version = 1). Mirrors the old CREATE TABLE for
    assignment_questions plus the minimum bits needed to add questions
    and an assignment."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.executescript(
        """
        CREATE TABLE schema_info (version INTEGER NOT NULL);
        INSERT INTO schema_info VALUES (1);

        CREATE TABLE imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            imported_at TEXT NOT NULL DEFAULT (datetime('now')),
            n_questions INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            moodle_type TEXT NOT NULL,
            name TEXT,
            stem_html TEXT NOT NULL,
            stem_hash TEXT NOT NULL,
            raw_xml TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            raw_category TEXT,
            default_grade REAL,
            penalty REAL,
            general_feedback_html TEXT,
            source_import_id INTEGER REFERENCES imports(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE question_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
            ord INTEGER NOT NULL,
            text_html TEXT NOT NULL,
            fraction REAL NOT NULL,
            feedback_html TEXT,
            tolerance REAL
        );
        CREATE TABLE rejection_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
            rejected_at TEXT NOT NULL DEFAULT (datetime('now')),
            note_text TEXT
        );
        CREATE TABLE assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE assignment_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
            question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
            ord INTEGER NOT NULL,
            UNIQUE(assignment_id, question_id)
        );

        INSERT INTO questions
          (moodle_type, name, stem_html, stem_hash, raw_xml, status, default_grade)
          VALUES ('truefalse', 'Q', '<p>x</p>', 'h',
                  '<question type="truefalse"/>', 'approved', 1.0);
        INSERT INTO assignments (name) VALUES ('A1');
        INSERT INTO assignment_questions (assignment_id, question_id, ord)
          VALUES (1, 1, 0);
        """
    )
    conn.close()


def test_migration_from_v1_adds_point_value_column(tmp_path):
    db_path = tmp_path / "bank.sqlite"
    _simulate_v1_db(db_path)

    # existing row has no point_value yet
    raw_conn = sqlite3.connect(db_path)
    raw_conn.row_factory = sqlite3.Row
    cols_before = {r["name"] for r in raw_conn.execute("PRAGMA table_info(assignment_questions)")}
    assert "point_value" not in cols_before
    raw_conn.close()

    # opening via db.connect triggers the migration
    conn = db.connect(db_path)

    cols_after = {r["name"] for r in conn.execute("PRAGMA table_info(assignment_questions)")}
    assert "point_value" in cols_after

    version = conn.execute("SELECT version FROM schema_info").fetchone()["version"]
    assert version == 5
    question_cols = {
        r["name"] for r in conn.execute("PRAGMA table_info(questions)")
    }
    assert "multichoice_single" in question_cols

    # The untouched assignment migrates into automatic 100-point mode.
    existing = conn.execute(
        "SELECT point_value FROM assignment_questions WHERE question_id = 1"
    ).fetchone()
    assert existing["point_value"] == 100.0
    assert db.get_assignment(conn, 1)["points_manually_adjusted"] == 0

    # New additions rebalance the untouched assignment.
    qid = db.insert_question(
        conn,
        moodle_type="truefalse",
        name="Q2",
        stem_html="<p>y</p>",
        stem_hash="h2",
        raw_xml="<question type='truefalse'/>",
        raw_category=None,
        default_grade=2.5,
        penalty=1.0,
        general_feedback_html=None,
        source_import_id=None,
        answers=[{"text_html": "true", "fraction": 100.0, "feedback_html": None}],
    )
    db.set_status(conn, qid, "approved")
    assert db.add_question_to_assignment(conn, 1, qid) is True
    picked = db.list_assignment_questions(conn, 1)
    assert [p["aq_point_value"] for p in picked] == [50.0, 50.0]


def test_migration_from_v4_recovers_select_all_flag(tmp_path):
    db_path = tmp_path / "bank.sqlite"
    conn = db.connect(db_path)
    qid = db.insert_question(
        conn,
        moodle_type="multichoice",
        multichoice_single=False,
        name="Select all",
        stem_html="Select all that apply.",
        stem_hash="select-all",
        raw_xml=(
            '<question type="multichoice"><single>false</single></question>'
        ),
        raw_category=None,
        default_grade=1.0,
        penalty=0.3333333,
        general_feedback_html=None,
        source_import_id=None,
        answers=[
            {"text_html": "A", "fraction": 50.0, "feedback_html": None},
            {"text_html": "B", "fraction": 50.0, "feedback_html": None},
        ],
    )
    conn.close()

    raw_conn = sqlite3.connect(db_path, isolation_level=None)
    raw_conn.execute("ALTER TABLE questions DROP COLUMN multichoice_single")
    raw_conn.execute("UPDATE schema_info SET version = 4")
    raw_conn.close()

    migrated = db.connect(db_path)

    assert db.get_question(migrated, qid)["multichoice_single"] == 0
    assert migrated.execute("SELECT version FROM schema_info").fetchone()["version"] == 5
