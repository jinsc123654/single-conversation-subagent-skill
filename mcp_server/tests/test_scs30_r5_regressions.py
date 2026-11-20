"""Regression tests for the scs30-r5 review findings fixed in R1 fix5.

Covers: the versioned subject payload (``subject_version`` 2 isolates old
ACCEPTs, including the cross-encoding link collision where a pre-hex target
``b"ff"`` recorded ``"ff"`` and a new target ``b"\\xff"`` hex-encodes to the
same value), and lossless subject encoding for non-UTF-8 file, directory and
link names (surrogateescape characters must not raise ``UnicodeEncodeError``).
"""

import hashlib
import json
import os
import re

import pytest

import scs_mcp.work as work_module
from scs_mcp.errors import ScsError
from scs_mcp.state import State
from scs_mcp.storage import Store
from scs_mcp.work import Work

REVIEW = "RV"
REAL_SUBJECT_JSON = work_module._review_subject_json


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


def make(work, sid, key, scope="src"):
    package = work.create(sid, "review package", [scope], ["accepted"],
                          task_defs(scope), key)
    return work.update(sid, package["id"], package["version"], "IN_PROGRESS")


def write_manifest(store, package, attempt, subject, verdict="ACCEPT"):
    base = (f"{package['docs_root']}/agent/evidence/{package['id']}/"
            f"R{package['revision']}/{REVIEW}/{attempt['attempt']['id']}")
    raw_relative = f"{base}/manifest.final.md"
    (store.root / raw_relative).write_text(f"# Codex review\n\n结论: {verdict}\n",
                                           encoding="utf-8")
    manifest = {
        "verdict": verdict,
        "session_id": "codex-session-1",
        "model": "gpt-test",
        "subject_sha256": subject,
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


def end_review(store, work, sid, package, attempt, subject, verdict="ACCEPT",
               status="PASS"):
    path, sha = write_manifest(store, package, attempt, subject, verdict)
    return work.update_attempt(sid, attempt["attempt"]["id"], package["version"], "ENDED",
        {"status": status, "evidence": [{"path": path, "sha256": sha}]})


def capture_subject(store, work, monkeypatch, package):
    """Return the direct subject digest and the canonical payload dict."""
    captured = []

    def spy(value):
        captured.append(value)
        return REAL_SUBJECT_JSON(value)

    monkeypatch.setattr(work_module, "_review_subject_json", spy)
    with store.transaction() as con:
        row = work._get(con, package["id"])
        digest = work._review_subject(row, json.loads(row["data"]))
    payload = next(item for item in captured
                   if isinstance(item, dict) and "files" in item)
    return digest, payload


def subject_digest(payload, ensure_ascii=False):
    """Digest a payload with the pre-fix5 canonical JSON (no version field)."""
    text = json.dumps(payload, ensure_ascii=ensure_ascii, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def store_old_accept(store, package, attempt, subject):
    """Persist an ACCEPT the way the pre-fix5 implementation would have.

    The package row is rewritten directly: the review subtask is PASS with a
    manifest bound to ``subject`` and the stored review record carries the same
    (unversioned) digest. This simulates a database upgraded with fix5 in place.
    """
    path, sha = write_manifest(store, package, attempt, subject)
    with store.transaction(True) as con:
        row = con.execute("SELECT data FROM work_packages WHERE id=?",
                          (package["id"],)).fetchone()
        data = json.loads(row["data"])
        task = next(item for item in data["subtasks"] if item["id"] == REVIEW)
        task["status"] = "PASS"
        task["evidence"] = [{"path": path, "sha256": sha}]
        task["review"] = {"subject_sha256": subject, "verdict": "ACCEPT",
                          "attempt_id": attempt["attempt"]["id"]}
        con.execute("UPDATE work_packages SET data=? WHERE id=?",
                    (json.dumps(data), package["id"]))


def release_attempt(work, sid, package, attempt):
    """End a prepared attempt with NOT_RUN so the package is quiescent again."""
    return work.update_attempt(sid, attempt["attempt"]["id"], package["version"],
                               "ENDED", {"status": "NOT_RUN"})


def test_unversioned_subject_is_stale_for_ready_and_completed(env, monkeypatch):
    """An ACCEPT bound to a pre-fix5 (unversioned) digest must never be reused."""
    store, _, sid, work = env
    package = make(work, sid, "subject-version")
    assert package["review_required"] is True
    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    current, payload = capture_subject(store, work, monkeypatch, package)
    assert payload["subject_version"] == 2
    assert current == attempt["review_subject"]

    legacy_payload = dict(payload)
    legacy_payload.pop("subject_version")
    legacy = subject_digest(legacy_payload)
    assert legacy != current

    # A PASS manifest carrying the old digest fails at attempt-end time: the
    # frozen prepare-time subject is versioned and no longer matches.
    path, sha = write_manifest(store, package, attempt, legacy)
    with pytest.raises(ScsError) as err:
        work.update_attempt(sid, attempt["attempt"]["id"], package["version"], "ENDED",
            {"status": "PASS", "evidence": [{"path": path, "sha256": sha}]})
    assert err.value.code == "REVIEW_STALE"
    states = [item["state"] for item in work.list_attempts(sid, package["id"])["attempts"]]
    assert states == ["PREPARED"]

    # Simulate an already stored ACCEPT from the old implementation.
    release_attempt(work, sid, package, attempt)
    store_old_accept(store, package, attempt, legacy)

    refreshed = work.get(sid, package["id"])
    with pytest.raises(ScsError) as err:
        work.update(sid, package["id"], refreshed["version"], "REVIEW_READY")
    assert err.value.code == "REVIEW_STALE"
    assert work.get(sid, package["id"])["state"] == "IN_PROGRESS"

    # COMPLETED is gated as well even if the old version had already marked the
    # package REVIEW_READY.
    with store.transaction(True) as con:
        con.execute("UPDATE work_packages SET state='REVIEW_READY' WHERE id=?",
                    (package["id"],))
    with pytest.raises(ScsError) as err:
        work.update(sid, package["id"], refreshed["version"], "COMPLETED",
                    close_reason="legacy accept covers the old subject")
    assert err.value.code == "REVIEW_STALE"
    assert work.get(sid, package["id"])["state"] == "REVIEW_READY"


def test_cross_encoding_link_collision_is_stale(env, monkeypatch):
    """The r5 counterexample: old b"ff" (recorded "ff") vs new b"\\xff" hex "ff"."""
    store, _, sid, work = env
    link = store.root / "src" / "x"
    os.symlink(b"ff", os.fsencode(link))
    try:
        package = make(work, sid, "cross-encoding")
        _, before = capture_subject(store, work, monkeypatch, package)
        assert before["subject_version"] == 2
        assert before["links"] == {"src/x": b"ff".hex()}  # "6666"

        # Old implementation recorded the decoded target text, not hex.
        legacy_payload = dict(before)
        legacy_payload.pop("subject_version")
        legacy_payload["links"] = {"src/x": "ff"}
        old_accept = subject_digest(legacy_payload)

        link.unlink()
        os.symlink(b"\xff", os.fsencode(link))
        new_digest, after = capture_subject(store, work, monkeypatch, package)
        assert after["subject_version"] == 2
        assert after["links"] == {"src/x": "ff"}  # hex of the single 0xFF byte

        # Without subject_version the payloads are byte-identical, which is the
        # collision the r5 review described. The version field breaks it.
        colliding = dict(after)
        colliding.pop("subject_version")
        assert subject_digest(colliding) == old_accept
        assert new_digest != old_accept

        attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
        assert attempt["review_subject"] == new_digest
        release_attempt(work, sid, package, attempt)
        store_old_accept(store, package, attempt, old_accept)

        refreshed = work.get(sid, package["id"])
        with pytest.raises(ScsError) as err:
            work.update(sid, package["id"], refreshed["version"], "REVIEW_READY")
        assert err.value.code == "REVIEW_STALE"
        assert work.get(sid, package["id"])["state"] == "IN_PROGRESS"
    finally:
        if os.path.lexists(link):
            link.unlink()


def test_non_utf8_names_produce_a_deterministic_subject(env, monkeypatch):
    """Byte file, directory and link names digest losslessly; ACCEPT still works."""
    store, _, sid, work = env
    src = os.fsencode(store.root / "src")
    file_name = b"bad\xff.txt"
    dir_name = b"dir\xfe"
    inner_name = b"inner\xff"
    link_name = b"link\xfe"
    link_target = b"t\xff"
    with open(os.path.join(src, file_name), "wb") as handle:
        handle.write(b"BAD = 1\n")
    os.mkdir(os.path.join(src, dir_name))
    with open(os.path.join(src, dir_name, inner_name), "wb") as handle:
        handle.write(b"INNER = 2\n")
    os.symlink(link_target, os.path.join(src, link_name))
    try:
        package = make(work, sid, "non-utf8-names")
        prepared = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
        assert re.fullmatch(r"[0-9a-f]{64}", prepared["review_subject"])

        first, payload = capture_subject(store, work, monkeypatch, package)
        second, _ = capture_subject(store, work, monkeypatch, package)
        assert first == second == prepared["review_subject"]
        assert payload["subject_version"] == 2

        file_key = "src/bad\udcff.txt"
        inner_key = "src/dir\udcfe/inner\udcff"
        link_key = "src/link\udcfe"
        assert set(payload["files"]) == {"src/a.py", "src/b.py", file_key, inner_key}
        assert payload["links"] == {link_key: link_target.hex()}
        encoded = work_module._review_subject_json(payload)
        assert encoded.isascii()
        assert "\\udcff" in encoded and "\\udcfe" in encoded

        ended = end_review(store, work, sid, package, prepared,
                           prepared["review_subject"])
        assert ended["task_status"] == "PASS"
        ready = work.update(sid, package["id"], ended["version"], "REVIEW_READY")
        assert ready["state"] == "REVIEW_READY"
    finally:
        for name in (link_name, file_name):
            path = os.path.join(src, name)
            if os.path.lexists(path):
                os.unlink(path)
        inner = os.path.join(src, dir_name, inner_name)
        if os.path.lexists(inner):
            os.unlink(inner)
        directory = os.path.join(src, dir_name)
        if os.path.lexists(directory):
            os.rmdir(directory)
