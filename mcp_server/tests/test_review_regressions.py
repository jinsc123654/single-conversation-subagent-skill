"""Regression tests for the scs30-r1 review findings fixed in R1.

Covers: ancestor-symlink escape, unreadable directories, the frozen
prepare-time review subject, new-revision review cleanup, ending a review
attempt after the snapshot broke, create replay after a review result, and
historical run_map versions.
"""

import hashlib
import json
import os
import shutil

import pytest

from scs_mcp.errors import ScsError
from scs_mcp.state import State
from scs_mcp.storage import Store
from scs_mcp.work import REVIEW_MAX_FILES, Work

REVIEW = "RV"


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path)
    state = State(store)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("OTHER = 2\n", encoding="utf-8")
    sid = state.open(str(tmp_path), "test", "long")["id"]
    for kind, choice in (("mode", ["long"]), ("verification", ["smoke"]),
                         ("codex_gate", ["packages"])):
        q = state.request(sid, kind, choice, "text")
        assert state.submit(sid, q["id"], "accept", choice)["status"] == "CONFIRMED"
    return store, state, sid, Work(store)


def task_defs(scope="src"):
    return [
        {"id": "I1", "kind": "implement", "allowed_files": [scope + "/a.py"],
         "acceptance": ["implemented"]},
        {"id": REVIEW, "kind": "review", "allowed_files": [scope],
         "acceptance": ["codex report"]},
    ]


def make(work, sid, key, scope="src", allowed=None):
    package = work.create(sid, "review package", allowed or [scope], ["accepted"],
                          task_defs(scope), key)
    return work.update(sid, package["id"], package["version"], "IN_PROGRESS")


def write_manifest(store, package, attempt, verdict="ACCEPT", subject=None):
    base = (f"{package['docs_root']}/agent/evidence/{package['id']}/"
            f"R{package['revision']}/{REVIEW}/{attempt['attempt']['id']}")
    raw_relative = f"{base}/manifest.final.md"
    (store.root / raw_relative).write_text(f"# Codex review\n\n结论: {verdict}\n",
                                           encoding="utf-8")
    manifest = {
        "verdict": verdict,
        "session_id": "codex-session-1",
        "model": "gpt-test",
        "subject_sha256": subject if subject is not None else attempt["review_subject"],
        "final_message_sha256": hashlib.sha256(
            (store.root / raw_relative).read_bytes()).hexdigest(),
        "command": "codex review --read-only",
        "exit_code": 0,
        "raw_path": "manifest.final.md",
    }
    relative = f"{base}/manifest.json"
    data = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    (store.root / relative).write_bytes(data)
    return relative, hashlib.sha256(data).hexdigest()


def end_review(store, work, sid, package, attempt, verdict="ACCEPT", status="PASS",
               subject=None):
    path, sha = write_manifest(store, package, attempt, verdict, subject)
    return work.update_attempt(sid, attempt["attempt"]["id"], package["version"], "ENDED",
        {"status": status, "evidence": [{"path": path, "sha256": sha}]})


def test_scope_ancestor_symlink_is_rejected(env, tmp_path):
    """P1: replacing an ancestor of the scope with a symlink must fail, not read outside."""
    store, _, sid, work = env
    (tmp_path / "src" / "pkg").mkdir()
    (tmp_path / "src" / "pkg" / "a.py").write_text("INSIDE = 1\n", encoding="utf-8")
    package = make(work, sid, "ancestor-symlink", scope="src/pkg")

    outside = tmp_path.parent / (tmp_path.name + "-outside")
    (outside / "pkg").mkdir(parents=True)
    secret = outside / "pkg" / "a.py"
    secret.write_text("OUTSIDE_SECRET = 42\n", encoding="utf-8")
    real_src = tmp_path / "src-real"
    try:
        os.rename(tmp_path / "src", real_src)
        os.symlink(outside, tmp_path / "src", target_is_directory=True)
        with pytest.raises(ScsError) as err:
            work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
        assert err.value.code == "REVIEW_SUBJECT_INCOMPLETE"
        # The failed prepare rolled back: no attempt row was left behind.
        assert work.list_attempts(sid, package["id"])["attempts"] == []
        assert secret.read_text(encoding="utf-8") == "OUTSIDE_SECRET = 42\n"
    finally:
        (tmp_path / "src").unlink()
        os.rename(real_src, tmp_path / "src")
        shutil.rmtree(outside)


def test_unreadable_directory_fails_the_subject(env):
    """P2: a directory that cannot be enumerated must fail instead of being skipped."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions; unreadable-directory case needs a non-root user")
    store, _, sid, work = env
    locked = store.root / "src" / "locked"
    locked.mkdir()
    (locked / "inner.py").write_text("HIDDEN = 3\n", encoding="utf-8")
    package = make(work, sid, "unreadable")
    locked.chmod(0o000)
    try:
        with pytest.raises(ScsError) as err:
            work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
        assert err.value.code == "REVIEW_SUBJECT_INCOMPLETE"
        assert work.list_attempts(sid, package["id"])["attempts"] == []
    finally:
        locked.chmod(0o755)


def test_prepare_freezes_the_review_subject(env):
    """P2: a PASS must match the subject frozen at prepare time, not just the current one.

    The manifest below is bound to the *changed* file set (what the old code
    recomputed), so only the prepare-time baseline can reject it.
    """
    store, _, sid, work = env
    package = make(work, sid, "frozen-subject")
    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    frozen = attempt["review_subject"]
    persisted = work.get(sid, package["id"])["attempts"][0]["input_snapshot"]
    assert persisted["review_subject"] == frozen

    (store.root / "src" / "a.py").write_text("VALUE = 999\n", encoding="utf-8")
    with store.transaction() as con:
        row = work._get(con, package["id"])
        changed = work._review_subject(row, json.loads(row["data"]))
    assert changed != frozen
    path, sha = write_manifest(store, package, attempt, "ACCEPT", subject=changed)
    with pytest.raises(ScsError) as err:
        work.update_attempt(sid, attempt["attempt"]["id"], package["version"], "ENDED",
            {"status": "PASS", "evidence": [{"path": path, "sha256": sha}]})
    assert err.value.code == "REVIEW_STALE"
    states = [item["state"] for item in work.list_attempts(sid, package["id"])["attempts"]]
    assert states == ["PREPARED"]


def test_manifest_subject_must_match_the_frozen_subject(env):
    """P2: manifest, frozen and recomputed subjects must all agree for PASS."""
    store, _, sid, work = env
    package = make(work, sid, "manifest-subject")
    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    path, sha = write_manifest(store, package, attempt, "ACCEPT", subject="b" * 64)
    with pytest.raises(ScsError) as err:
        work.update_attempt(sid, attempt["attempt"]["id"], package["version"], "ENDED",
            {"status": "PASS", "evidence": [{"path": path, "sha256": sha}]})
    assert err.value.code == "REVIEW_STALE"
    states = [item["state"] for item in work.list_attempts(sid, package["id"])["attempts"]]
    assert states == ["PREPARED"]


def test_new_revision_drops_previous_review_metadata(env):
    """P2: R2 must not inherit R1's review verdict in state, run_map or handoff."""
    store, _, sid, work = env
    package = make(work, sid, "revision-clear")
    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    ended = end_review(store, work, sid, package, attempt, "ACCEPT", "PASS")
    assert ended["task_status"] == "PASS"

    revised = work.update(sid, package["id"], ended["version"], new_revision=True)
    assert revised["revision"] == 2
    review_task = next(task for task in revised["subtasks"] if task["id"] == REVIEW)
    assert review_task["status"] == "NOT_RUN"
    assert "review" not in review_task
    assert review_task["evidence"] == []

    current = work.run_map(sid, package["id"])
    assert current["packages"][0]["revision"] == 2
    current_review = next(item for item in current["review"] if item["package_id"] == package["id"])
    assert current_review["status"] == "REQUIRED"
    assert current_review["reviews"] == [
        {"subtask_id": REVIEW, "status": "NOT_RUN", "verdict": None, "subject_sha256": None}]

    historical = work.run_map(sid, package["id"], revision=1)
    assert historical["packages"][0]["revision"] == 1
    historical_review = next(item for item in historical["review"] if item["package_id"] == package["id"])
    assert historical_review["reviews"][0]["verdict"] == "ACCEPT"

    export = work.export(sid, package["id"], revised["version"])
    exported_review = next(task for task in export["snapshot"]["subtasks"] if task["id"] == REVIEW)
    assert "review" not in exported_review
    assert exported_review["status"] == "NOT_RUN"

    progressed = work.update(sid, package["id"], revised["version"], "IN_PROGRESS")
    with pytest.raises(ScsError) as err:
        work.update(sid, package["id"], progressed["version"], "REVIEW_READY")
    assert err.value.code == "REVIEW_REQUIRED"


@pytest.mark.parametrize("failure", ["unreadable", "oversized"])
@pytest.mark.parametrize("status", ["FAIL", "NOT_RUN"])
def test_broken_snapshot_does_not_trap_an_ending_attempt(env, failure, status):
    """P2: FAIL/NOT_RUN without evidence must end even when the subject cannot be recomputed."""
    if failure == "unreadable" and os.geteuid() == 0:
        pytest.skip("root ignores directory permissions; unreadable-directory case needs a non-root user")
    store, _, sid, work = env
    locked = store.root / "src" / "locked"
    if failure == "unreadable":
        locked.mkdir()
        (locked / "inner.py").write_text("HIDDEN = 4\n", encoding="utf-8")
    package = make(work, sid, "broken-" + failure)
    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    assert len(attempt["review_subject"]) == 64

    if failure == "unreadable":
        locked.chmod(0o000)
    else:
        for index in range(REVIEW_MAX_FILES + 1):
            (store.root / "src" / f"bulk-{index:03d}.py").write_text("BULK = 0\n",
                                                                     encoding="utf-8")
    try:
        ended = work.update_attempt(sid, attempt["attempt"]["id"], package["version"],
                                    "ENDED", {"status": status})
        assert ended["attempt"]["state"] == "ENDED"
        assert ended["attempt"]["result"]["status"] == status
        assert ended["task_status"] == status

        # The package can now start a revision with a narrower scope.
        definition = {
            "goal": "narrow review", "allowed_files": ["src/a.py"],
            "acceptance": ["narrow"], "subtasks": [
                {"id": REVIEW, "kind": "review", "allowed_files": ["src/a.py"],
                 "acceptance": ["review a.py"]}],
        }
        revised = work.update(sid, package["id"], ended["version"], new_revision=True,
                              definition=definition)
        assert revised["revision"] == 2
        prepared = work.prepare_attempt(sid, package["id"], revised["version"], REVIEW)
        assert prepared["attempt"]["state"] == "PREPARED"
        assert len(prepared["review_subject"]) == 64
    finally:
        if failure == "unreadable":
            locked.chmod(0o755)


@pytest.mark.parametrize("verdict,status", [("ACCEPT", "PASS"), ("REJECT", "FAIL")])
def test_create_replay_after_review_result_is_idempotent(env, verdict, status):
    """P2: registering a review result must not break same-request_key create retries."""
    store, _, sid, work = env
    args = {"goal": "review package", "allowed_files": ["src"], "acceptance": ["accepted"],
            "subtasks": task_defs("src"), "request_key": "replay-" + verdict.lower()}
    package = work.create(sid, **args)
    package = work.update(sid, package["id"], package["version"], "IN_PROGRESS")
    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    end_review(store, work, sid, package, attempt, verdict, status)

    replay = work.create(sid, **args)
    assert replay["id"] == package["id"]
    assert replay["revision"] == 1
    replayed_review = next(task for task in replay["subtasks"] if task["id"] == REVIEW)
    assert replayed_review["status"] == status
    assert replayed_review["review"]["verdict"] == verdict


def test_run_map_historical_revision_uses_snapshot_version(env):
    """P3: an old revision reports the version captured with that snapshot."""
    _, _, sid, work = env
    package = make(work, sid, "historical-version")
    first = work.update(sid, package["id"], package["version"], "IN_PROGRESS")
    second = work.update(sid, package["id"], first["version"], new_revision=True)
    assert second["revision"] == 2

    current = work.run_map(sid, package["id"])
    assert current["packages"][0]["revision"] == 2
    assert current["packages"][0]["version"] == second["version"]

    historical = work.run_map(sid, package["id"], revision=1)
    assert historical["packages"][0]["revision"] == 1
    assert historical["packages"][0]["state"] == "IN_PROGRESS"
    assert historical["packages"][0]["version"] == first["version"]
    assert historical["packages"][0]["version"] != current["packages"][0]["version"]
