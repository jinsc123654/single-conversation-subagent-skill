"""Regression tests for the scs30-r2 review findings.

Covers: links inside a reviewed scope under the R3 link contract (targets enter
the subject, ACCEPT goes stale, link-only directories are legal), pre-2.7
package create replay, overlapping/duplicate scope file counting, and the
3.0.0 CHANGELOG entry.
"""

import hashlib
import json
import os
import re
from pathlib import Path

import pytest

import scs_mcp.work as work_module
from scs_mcp.errors import ScsError
from scs_mcp.state import State
from scs_mcp.storage import Store
from scs_mcp.work import Work

REVIEW = "RV"
REPO_ROOT = Path(__file__).resolve().parents[2]


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


def task_defs(scope="src", review_scope=None):
    return [
        {"id": "I1", "kind": "implement", "allowed_files": [scope + "/a.py"],
         "acceptance": ["implemented"]},
        {"id": REVIEW, "kind": "review", "allowed_files": review_scope or [scope],
         "acceptance": ["codex report"]},
    ]


def make(work, sid, key, scope="src", package_scope=None, review_scope=None):
    package = work.create(sid, "review package", package_scope or [scope], ["accepted"],
                          task_defs(scope, review_scope), key)
    return work.update(sid, package["id"], package["version"], "IN_PROGRESS")


def review_package(work, sid, key, package_scope, review_scope):
    """Create a package whose review subtask covers ``review_scope``."""
    subtasks = [
        {"id": "I1", "kind": "implement", "allowed_files": ["src/a.py"],
         "acceptance": ["implemented"]},
        {"id": REVIEW, "kind": "review", "allowed_files": review_scope,
         "acceptance": ["codex report"]},
    ]
    package = work.create(sid, "review package", package_scope, ["accepted"],
                          subtasks, key)
    return work.update(sid, package["id"], package["version"], "IN_PROGRESS")


def write_manifest(store, package, attempt, verdict="ACCEPT"):
    base = (f"{package['docs_root']}/agent/evidence/{package['id']}/"
            f"R{package['revision']}/{REVIEW}/{attempt['attempt']['id']}")
    raw_relative = f"{base}/manifest.final.md"
    (store.root / raw_relative).write_text(f"# Codex review\n\n结论: {verdict}\n",
                                           encoding="utf-8")
    manifest = {
        "verdict": verdict,
        "session_id": "codex-session-1",
        "model": "gpt-test",
        "subject_sha256": attempt["review_subject"],
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


def end_review(store, work, sid, package, attempt, verdict="ACCEPT", status="PASS"):
    path, sha = write_manifest(store, package, attempt, verdict)
    return work.update_attempt(sid, attempt["attempt"]["id"], package["version"], "ENDED",
        {"status": status, "evidence": [{"path": path, "sha256": sha}]})


# 1. Links inside a reviewed scope are part of the subject, never followed.

def test_scope_symlink_to_external_file_is_recorded_not_read(env, tmp_path, monkeypatch):
    """R3: a new link inside the scope is recorded as path -> hex target, not read."""
    store, _, sid, work = env
    outside = tmp_path.parent / (tmp_path.name + "-outside-symlink-target.txt")
    outside.write_text("OUTSIDE_SECRET = 7\n", encoding="utf-8")
    link = store.root / "src" / "leak.py"
    os.symlink(outside, link)
    captured = []
    real_subject_json = work_module._review_subject_json

    def spy(value):
        captured.append(value)
        return real_subject_json(value)

    monkeypatch.setattr(work_module, "_review_subject_json", spy)
    try:
        package = make(work, sid, "symlink-new")
        prepared = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
        assert len(prepared["review_subject"]) == 64
        assert len(work.list_attempts(sid, package["id"])["attempts"]) == 1
        payload = next(item for item in captured
                       if isinstance(item, dict) and "files" in item)
        assert payload["subject_version"] == 2
        assert payload["links"] == {"src/leak.py": os.fsencode(outside).hex()}
        assert "src/leak.py" not in payload["files"]
        # Only the two regular files are digested; the outside target content
        # never enters the subject.
        assert set(payload["files"]) == {"src/a.py", "src/b.py"}
        assert outside.read_text(encoding="utf-8") == "OUTSIDE_SECRET = 7\n"
    finally:
        link.unlink()
        outside.unlink()


def test_accept_is_invalidated_when_a_scope_link_appears_or_is_retargeted(env):
    """A link added/retargeted/deleted after ACCEPT must make the subject stale."""
    store, _, sid, work = env
    package = make(work, sid, "symlink-retarget")
    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    ended = end_review(store, work, sid, package, attempt)
    assert ended["task_status"] == "PASS"

    link = store.root / "src" / "alias.py"
    os.symlink("a.py", link)
    try:
        with pytest.raises(ScsError) as err:
            work.update(sid, package["id"], ended["version"], "REVIEW_READY")
        assert err.value.code == "REVIEW_STALE"
        # The failed gate rolled back: the ACCEPT is still stored but unusable.
        current = work.get(sid, package["id"])
        assert current["state"] == "IN_PROGRESS"
        review_task = next(task for task in current["subtasks"] if task["id"] == REVIEW)
        assert review_task["review"]["verdict"] == "ACCEPT"

        link.unlink()
        os.symlink("b.py", link)
        with pytest.raises(ScsError) as err:
            work.update(sid, package["id"], ended["version"], "REVIEW_READY")
        assert err.value.code == "REVIEW_STALE"

        # Removing the link restores the ACCEPTed subject exactly.
        link.unlink()
        ready = work.update(sid, package["id"], ended["version"], "REVIEW_READY")
        assert ready["state"] == "REVIEW_READY"
    finally:
        if os.path.lexists(link):
            link.unlink()

    # A link present at ACCEPT time: deleting it also invalidates the ACCEPT.
    present = store.root / "src" / "present.py"
    os.symlink("a.py", present)
    try:
        link_package = make(work, sid, "symlink-present")
        link_attempt = work.prepare_attempt(sid, link_package["id"],
                                            link_package["version"], REVIEW)
        link_ended = end_review(store, work, sid, link_package, link_attempt)
        assert link_ended["task_status"] == "PASS"
        present.unlink()
        with pytest.raises(ScsError) as err:
            work.update(sid, link_package["id"], link_ended["version"], "REVIEW_READY")
        assert err.value.code == "REVIEW_STALE"
    finally:
        if os.path.lexists(present):
            present.unlink()


def test_directory_of_only_symlinks_produces_a_links_subject(env, monkeypatch):
    """R3: a link-only directory yields a links-only, reviewable subject."""
    store, _, sid, work = env
    links = store.root / "src" / "links"
    links.mkdir()
    for name in ("one", "two"):
        target = store.root / "src" / f"{name}.target.py"
        target.write_text(f"TARGET = '{name}'\n", encoding="utf-8")
        os.symlink(target.name, links / f"{name}.py")

    subtasks = [{"id": REVIEW, "kind": "review", "allowed_files": ["src/links"],
                 "acceptance": ["codex report"]}]
    package = work.create(sid, "links only", ["src/links"], ["reviewed"],
                          subtasks, "links-only")
    package = work.update(sid, package["id"], package["version"], "IN_PROGRESS")

    captured = []
    real_subject_json = work_module._review_subject_json

    def spy(value):
        captured.append(value)
        return real_subject_json(value)

    monkeypatch.setattr(work_module, "_review_subject_json", spy)
    prepared = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    assert len(prepared["review_subject"]) == 64
    payload = next(item for item in captured
                   if isinstance(item, dict) and "files" in item)
    assert payload["subject_version"] == 2
    assert payload["files"] == {}
    assert payload["links"] == {
        "src/links/one.py": b"one.target.py".hex(),
        "src/links/two.py": b"two.target.py".hex(),
    }
    assert work.list_attempts(sid, package["id"])["attempts"][0]["state"] == "PREPARED"


# 2. A pre-2.7 package (no depends_on/required/review/resources) replays create.

def test_pre_27_package_replays_create_and_still_conflicts_on_change(env):
    store, _, sid, work = env
    args = {"goal": "legacy package", "allowed_files": ["src"], "acceptance": ["accepted"],
            "subtasks": task_defs("src"), "request_key": "legacy-replay"}
    package = work.create(sid, **args)
    version = package["version"]

    # Rewrite the stored row into the pre-2.7 shape: no package-level review
    # freeze and no depends_on/required/resources/review on any subtask.
    with store.transaction(True) as con:
        row = con.execute("SELECT data FROM work_packages WHERE id=?",
                          (package["id"],)).fetchone()
        data = json.loads(row["data"])
        data.pop("review_required", None)
        for task in data["subtasks"]:
            for key in ("depends_on", "required", "resources", "review"):
                task.pop(key, None)
        con.execute("UPDATE work_packages SET data=? WHERE id=?",
                    (json.dumps(data, ensure_ascii=False), package["id"]))
    with store.transaction() as con:
        legacy = json.loads(con.execute("SELECT data FROM work_packages WHERE id=?",
                                        (package["id"],)).fetchone()["data"])
    assert "review_required" not in legacy
    for task in legacy["subtasks"]:
        assert not {"depends_on", "required", "resources", "review"} & set(task)

    # The same request_key and the original definition return the original package.
    replay = work.create(sid, **args)
    assert replay["id"] == package["id"]
    assert replay["revision"] == 1
    assert replay["version"] == version

    # A real definition change is still a conflict.
    with pytest.raises(ScsError) as err:
        work.create(sid, **{**args, "goal": "different goal"})
    assert err.value.code == "IDEMPOTENCY_CONFLICT"

    changed = [dict(task) for task in args["subtasks"]]
    changed[0]["acceptance"] = ["different"]
    with pytest.raises(ScsError) as err:
        work.create(sid, **{**args, "subtasks": changed})
    assert err.value.code == "IDEMPOTENCY_CONFLICT"

    changed = [dict(task) for task in args["subtasks"]]
    changed[0]["required"] = False
    with pytest.raises(ScsError) as err:
        work.create(sid, **{**args, "subtasks": changed})
    assert err.value.code == "IDEMPOTENCY_CONFLICT"


# 3. Overlapping and duplicate scopes read, count and digest each file once.

def test_overlapping_scopes_do_not_exceed_the_file_limit(env, monkeypatch):
    """src + src/a.py must not read a.py twice and hit the 500-file cap."""
    store, _, sid, work = env
    monkeypatch.setattr(work_module, "REVIEW_MAX_FILES", 2)
    package = review_package(work, sid, "overlap-files",
                             ["src", "src/a.py"], ["src", "src/a.py"])
    prepared = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    assert len(prepared["review_subject"]) == 64

    duplicate = review_package(work, sid, "duplicate-files",
                               ["src", "src"], ["src", "src"])
    prepared = work.prepare_attempt(sid, duplicate["id"], duplicate["version"], REVIEW)
    assert len(prepared["review_subject"]) == 64


def test_overlapping_scopes_do_not_exceed_the_byte_limit(env, monkeypatch):
    """A 20-byte file set stays at 20 bytes when a file is also declared alone."""
    store, _, sid, work = env
    monkeypatch.setattr(work_module, "REVIEW_MAX_BYTES", 20)
    package = review_package(work, sid, "overlap-bytes",
                             ["src", "src/a.py"], ["src", "src/a.py"])
    prepared = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    assert len(prepared["review_subject"]) == 64

    # A genuinely larger, non-overlapping subject is still rejected.
    (store.root / "src" / "c.py").write_text("EXTRA = 3\n", encoding="utf-8")
    package = review_package(work, sid, "overlap-bytes-too-large",
                             ["src", "src/a.py"], ["src", "src/a.py"])
    with pytest.raises(ScsError) as err:
        work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    assert err.value.code == "REVIEW_SUBJECT_TOO_LARGE"


def test_overlapping_and_plain_scopes_produce_the_same_file_subject(env, monkeypatch):
    """The file part of the subject is identical for src vs src + src/a.py.

    The declared scope text stays in the definition part of the digest (a
    contract fix2 explicitly kept), so only the file map is compared here.
    """
    store, _, sid, work = env
    overlap = review_package(work, sid, "overlap-map",
                             ["src", "src/a.py"], ["src", "src/a.py"])
    plain = review_package(work, sid, "plain-map", ["src"], ["src"])

    captured = []
    real_subject_json = work_module._review_subject_json

    def spy(value):
        captured.append(value)
        return real_subject_json(value)

    monkeypatch.setattr(work_module, "_review_subject_json", spy)
    opened = []
    real_open = os.open

    def counting_open(path, *args, **kwargs):
        if path == "a.py":
            opened.append(path)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(work_module.os, "open", counting_open)
    rows = {}
    with store.transaction() as con:
        for package in (overlap, plain):
            row = work._get(con, package["id"])
            rows[package["id"]] = (row, json.loads(row["data"]))
    d1 = work._review_subject(*rows[overlap["id"]])
    d2 = work._review_subject(*rows[plain["id"]])
    assert len(captured) == 2
    # Once for the overlap package and once for the plain package: the
    # directory/file overlap did not open a.py a second time.
    assert len(opened) == 2
    assert captured[0]["files"] == captured[1]["files"]
    assert captured[0]["files"] == {
        "src/a.py": hashlib.sha256(
            (store.root / "src" / "a.py").read_bytes()).hexdigest(),
        "src/b.py": hashlib.sha256(
            (store.root / "src" / "b.py").read_bytes()).hexdigest(),
    }
    assert re.fullmatch(r"[0-9a-f]{64}", d1)
    assert re.fullmatch(r"[0-9a-f]{64}", d2)


# 4. The changelog current release has no stale 2.9.0/tool-count/test TODOs.

def test_changelog_30_entry_exists_without_stale_release_todos():
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    sections = re.split(r"^## ", text, flags=re.M)
    releases = [section for section in sections if re.match(r"\d+\.\d+\.\d+\b", section)]
    assert releases, "CHANGELOG.md has no release sections"
    assert releases[0].startswith("3.0.0"), "3.0.0 must be the current release entry"

    top = releases[0]
    assert "2.9.0" not in top
    assert "待同步" not in top
    assert "工具数" not in top
    assert any(section.startswith("2.9.0") for section in releases[1:])
