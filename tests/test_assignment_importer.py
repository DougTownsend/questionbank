import json
import shutil
from pathlib import Path

import pytest

from question_bank import assignment_importer, db, importer, paths

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("QUESTION_BANK_ROOT", str(tmp_path))
    class_name = "TestClass"
    paths.ensure_class_dirs(class_name)
    conn = db.connect(paths.class_db_path(class_name))
    importer.import_file(conn, FIXTURES / "sample_questions.xml")
    # approve the first two, reject the third
    pending = db.list_questions(conn, status="pending")
    db.set_status(conn, pending[0]["id"], "approved")
    db.set_status(conn, pending[1]["id"], "approved")
    db.set_status(conn, pending[2]["id"], "rejected", rejection_note="no")
    return {
        "class_name": class_name,
        "conn": conn,
        "approved": [pending[0]["id"], pending[1]["id"]],
        "rejected": pending[2]["id"],
    }


def _write_plan(env, data: dict, filename: str = "assignment.json") -> Path:
    path = paths.class_imports_dir(env["class_name"]) / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_import_creates_assignment_and_deletes_source_file(env):
    path = _write_plan(
        env,
        {"name": "Quiz 1", "question_ids": env["approved"]},
        filename="plan.json",
    )
    report = assignment_importer.import_assignment_file(
        env["conn"], env["class_name"], path
    )
    assert report.assignment_id is not None
    assert report.added_question_ids == env["approved"]
    assert report.skipped == []
    assert not path.exists()  # source file removed

    picked = db.list_assignment_questions(env["conn"], report.assignment_id)
    assert [p["id"] for p in picked] == env["approved"]


def test_preserves_order(env):
    reversed_ids = list(reversed(env["approved"]))
    path = _write_plan(env, {"name": "Q", "question_ids": reversed_ids})
    report = assignment_importer.import_assignment_file(
        env["conn"], env["class_name"], path
    )
    picked = db.list_assignment_questions(env["conn"], report.assignment_id)
    assert [p["id"] for p in picked] == reversed_ids


def test_skips_rejected_ids_but_continues(env):
    ids = env["approved"] + [env["rejected"]]
    path = _write_plan(env, {"name": "Q", "question_ids": ids})
    report = assignment_importer.import_assignment_file(
        env["conn"], env["class_name"], path
    )
    assert report.added_question_ids == env["approved"]
    assert any(reason.startswith("status=rejected") for _, reason in report.skipped)


def test_skips_missing_ids(env):
    ids = env["approved"] + [9999]
    path = _write_plan(env, {"name": "Q", "question_ids": ids})
    report = assignment_importer.import_assignment_file(
        env["conn"], env["class_name"], path
    )
    assert 9999 in [qid for qid, _ in report.skipped]


def test_skips_duplicates_in_list(env):
    ids = [env["approved"][0], env["approved"][0], env["approved"][1]]
    path = _write_plan(env, {"name": "Q", "question_ids": ids})
    report = assignment_importer.import_assignment_file(
        env["conn"], env["class_name"], path
    )
    assert len(report.added_question_ids) == 2
    assert any("duplicate" in reason for _, reason in report.skipped)


def test_no_valid_ids_does_not_create_assignment(env):
    path = _write_plan(env, {"name": "Empty", "question_ids": [9998, 9999]})
    report = assignment_importer.import_assignment_file(
        env["conn"], env["class_name"], path
    )
    assert report.assignment_id is None
    assert report.added_question_ids == []
    assert db.list_assignments(env["conn"]) == []
    # source file stays when the import didn't actually create anything
    assert path.exists()


def test_missing_name_raises(env):
    path = _write_plan(env, {"question_ids": env["approved"]})
    with pytest.raises(assignment_importer.AssignmentImportError):
        assignment_importer.import_assignment_file(
            env["conn"], env["class_name"], path
        )


def test_non_integer_id_raises(env):
    path = _write_plan(env, {"name": "Q", "question_ids": [env["approved"][0], "oops"]})
    with pytest.raises(assignment_importer.AssignmentImportError):
        assignment_importer.import_assignment_file(
            env["conn"], env["class_name"], path
        )


def test_empty_question_ids_raises(env):
    path = _write_plan(env, {"name": "Q", "question_ids": []})
    with pytest.raises(assignment_importer.AssignmentImportError):
        assignment_importer.import_assignment_file(
            env["conn"], env["class_name"], path
        )


def test_invalid_json_raises(env):
    path = paths.class_imports_dir(env["class_name"]) / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(assignment_importer.AssignmentImportError):
        assignment_importer.import_assignment_file(
            env["conn"], env["class_name"], path
        )


def test_description_field_preserved_in_report(env):
    path = _write_plan(
        env,
        {
            "name": "Quiz 3",
            "description": "covers caches",
            "question_ids": env["approved"],
        },
    )
    report = assignment_importer.import_assignment_file(
        env["conn"], env["class_name"], path
    )
    assert report.description == "covers caches"


def test_file_outside_imports_dir_is_not_deleted(env, tmp_path):
    # plant the file somewhere that isn't imports/
    foreign = tmp_path / "foreign.json"
    foreign.write_text(
        json.dumps({"name": "Q", "question_ids": env["approved"]}), encoding="utf-8"
    )
    report = assignment_importer.import_assignment_file(
        env["conn"], env["class_name"], foreign
    )
    assert report.assignment_id is not None
    assert foreign.exists()  # not deleted: it wasn't in imports/


def test_import_bytes_does_not_try_to_delete(env):
    data = json.dumps(
        {"name": "Q", "question_ids": env["approved"]}
    ).encode("utf-8")
    report = assignment_importer.import_assignment_bytes(
        env["conn"], env["class_name"], data
    )
    assert report.assignment_id is not None
    assert report.added_question_ids == env["approved"]
