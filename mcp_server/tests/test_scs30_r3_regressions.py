"""Regression tests for the scs30-r3 link-contract finding fixed in R1 fix3.

Covers: symlink identity + raw readlink target bytes (hex) in the review
subject (add, remove, retarget, non-UTF-8/literal collision), excluded trees,
scope symlinks, link-only subjects, scope path normalization, special files,
and dependency/generated-tree links.
"""

import hashlib
import json
import os
import re
import shutil

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


def task_defs(package_scope="src"):
    return [
        {"id": "I1", "kind": "implement", "allowed_files": [package_scope + "/a.py"],
         "acceptance": ["implemented"]},
        {"id": REVIEW, "kind": "review", "allowed_files": [package_scope],
         "acceptance": ["codex report"]},
    ]


def make(work, sid, key, package_scope="src"):
    package = work.create(sid, "review package", [package_scope], ["accepted"],
                          task_defs(package_scope), key)
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


def capture_subject(store, work, monkeypatch, package):
    """Return the direct subject digest and the JSON payload used for it."""
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


def set_stored_scopes(store, package, scopes):
    with store.transaction(True) as con:
        row = con.execute("SELECT data FROM work_packages WHERE id=?",
                          (package["id"],)).fetchone()
        data = json.loads(row["data"])
        data["allowed_files"] = scopes
        con.execute("UPDATE work_packages SET data=? WHERE id=?",
                    (json.dumps(data), package["id"]))


def test_subject_hashes_link_identity_and_changes_on_add_remove_retarget(env, monkeypatch):
    """links maps relative path -> hex of raw readlink bytes; changes change it."""
    store, _, sid, work = env
    package = make(work, sid, "link-subject")

    baseline, baseline_payload = capture_subject(store, work, monkeypatch, package)
    assert re.fullmatch(r"[0-9a-f]{64}", baseline)
    assert baseline_payload["subject_version"] == 2
    assert "links" not in baseline_payload
    assert set(baseline_payload["files"]) == {"src/a.py", "src/b.py"}

    link = store.root / "src" / "alias.py"
    os.symlink("a.py", link)
    try:
        added, added_payload = capture_subject(store, work, monkeypatch, package)
        assert added != baseline
        assert added_payload["links"] == {"src/alias.py": b"a.py".hex()}
        assert "src/alias.py" not in added_payload["files"]
        assert added_payload["files"] == baseline_payload["files"]

        link.unlink()
        os.symlink("../outside.txt", link)  # never resolved or read
        retargeted, retargeted_payload = capture_subject(
            store, work, monkeypatch, package)
        assert retargeted != added and retargeted != baseline
        assert retargeted_payload["links"] == {
            "src/alias.py": b"../outside.txt".hex()}
        assert retargeted_payload["files"] == baseline_payload["files"]

        link.unlink()
        removed, removed_payload = capture_subject(store, work, monkeypatch, package)
        assert removed == baseline
        assert "links" not in removed_payload
    finally:
        if os.path.lexists(link):
            link.unlink()


def test_excluded_trees_skip_links_without_changing_the_subject(env, monkeypatch):
    """Links under .git/.venv/... and the evidence tree are skipped, not recorded."""
    store, _, sid, work = env
    package = make(work, sid, "excluded-links")
    baseline, baseline_payload = capture_subject(store, work, monkeypatch, package)
    assert "links" not in baseline_payload

    created = []
    for name in (".git", ".venv", ".venv-mcp", ".scs", ".archify"):
        directory = store.root / "src" / name
        directory.mkdir()
        link = directory / "leak.py"
        os.symlink("../a.py", link)
        created.append(link)
    try:
        after, payload = capture_subject(store, work, monkeypatch, package)
        assert after == baseline
        assert "links" not in payload
        assert payload["files"] == baseline_payload["files"]
    finally:
        for link in created:
            if os.path.lexists(link):
                link.unlink()

    # The docs evidence tree is excluded by prefix even when docs is in scope.
    docs_package = make(work, sid, "excluded-evidence", package_scope="docs")
    docs_baseline, _ = capture_subject(store, work, monkeypatch, docs_package)
    evidence_link = store.root / "docs" / "agent" / "evidence" / "leak.py"
    os.symlink("../../AGENT_PROJECT.md", evidence_link)
    try:
        docs_after, docs_payload = capture_subject(
            store, work, monkeypatch, docs_package)
        assert docs_after == docs_baseline
        assert "links" not in docs_payload
    finally:
        if os.path.lexists(evidence_link):
            evidence_link.unlink()


def test_scope_ancestor_and_scope_symlinks_are_still_rejected(env, tmp_path):
    """A symlinked ancestor or the last scope component stays REVIEW_SUBJECT_INCOMPLETE."""
    store, _, sid, work = env
    (store.root / "src" / "pkg").mkdir()
    (store.root / "src" / "pkg" / "inside.py").write_text("INSIDE = 1\n",
                                                          encoding="utf-8")
    package = make(work, sid, "ancestor-link", package_scope="src/pkg")

    outside = tmp_path.parent / (tmp_path.name + "-outside-ancestor")
    (outside / "pkg").mkdir(parents=True)
    (outside / "pkg" / "inside.py").write_text("OUTSIDE = 2\n", encoding="utf-8")
    real_src = tmp_path / "src-real"
    try:
        os.rename(store.root / "src", real_src)
        os.symlink(outside, store.root / "src", target_is_directory=True)
        with pytest.raises(ScsError) as err:
            work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
        assert err.value.code == "REVIEW_SUBJECT_INCOMPLETE"
        assert work.list_attempts(sid, package["id"])["attempts"] == []
    finally:
        (store.root / "src").unlink()
        os.rename(real_src, store.root / "src")
        shutil.rmtree(outside)

    (store.root / "src" / "last").mkdir()
    (store.root / "src" / "last" / "x.py").write_text("LAST = 3\n", encoding="utf-8")
    last_package = make(work, sid, "last-link", package_scope="src/last")
    outside_last = tmp_path.parent / (tmp_path.name + "-outside-last")
    outside_last.mkdir()
    (outside_last / "x.py").write_text("OUTSIDE = 4\n", encoding="utf-8")
    try:
        os.rename(store.root / "src" / "last", store.root / "src" / "last-real")
        os.symlink(outside_last, store.root / "src" / "last",
                   target_is_directory=True)
        with pytest.raises(ScsError) as err:
            work.prepare_attempt(sid, last_package["id"],
                                 last_package["version"], REVIEW)
        assert err.value.code == "REVIEW_SUBJECT_INCOMPLETE"
        assert work.list_attempts(sid, last_package["id"])["attempts"] == []
    finally:
        (store.root / "src" / "last").unlink()
        os.rename(store.root / "src" / "last-real", store.root / "src" / "last")
        shutil.rmtree(outside_last)


def test_link_only_scope_produces_a_links_subject_and_can_be_accepted(env, monkeypatch):
    """files is empty, links is non-empty, and the subject supports prepare/ACCEPT."""
    store, _, sid, work = env
    scope = store.root / "src" / "links"
    scope.mkdir()
    for name in ("one", "two"):
        target = store.root / "src" / f"{name}.target.py"
        target.write_text(f"TARGET = '{name}'\n", encoding="utf-8")
        os.symlink(target.name, scope / f"{name}.py")

    package = make(work, sid, "links-only-legal", package_scope="src/links")
    digest, payload = capture_subject(store, work, monkeypatch, package)
    assert payload["files"] == {}
    assert payload["links"] == {
        "src/links/one.py": b"one.target.py".hex(),
        "src/links/two.py": b"two.target.py".hex(),
    }
    assert re.fullmatch(r"[0-9a-f]{64}", digest)

    prepared = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    assert prepared["review_subject"] == digest
    ended = end_review(store, work, sid, package, prepared)
    assert ended["task_status"] == "PASS"
    ready = work.update(sid, package["id"], ended["version"], "REVIEW_READY")
    assert ready["state"] == "REVIEW_READY"


def test_non_utf8_link_targets_stay_distinct_and_swaps_stale_accept(env, monkeypatch):
    """0xFF and literal backslash-x-f-f targets stay distinct in the subject.

    The old implementation decoded raw targets with backslashreplace, so the
    single byte 0xFF and the four ASCII bytes backslash x f f both became the
    same string. Retargeting a link between them kept the subject and an
    earlier ACCEPT stayed valid. Raw readlink bytes are hex-encoded now.
    """
    store, _, sid, work = env
    scope = store.root / "src" / "links"
    scope.mkdir()
    raw = scope / "raw.py"          # target: the single byte 0xff
    literal = scope / "literal.py"  # target: four ASCII bytes: \ x f f
    package = make(work, sid, "link-target-collision", package_scope="src/links")

    os.symlink(b"\xff", os.fsencode(raw))
    try:
        raw_digest, raw_payload = capture_subject(store, work, monkeypatch, package)
        assert raw_payload["subject_version"] == 2
        assert raw_payload["files"] == {}
        assert raw_payload["links"] == {"src/links/raw.py": "ff"}

        raw.unlink()
        os.symlink(b"\\xff", os.fsencode(literal))
        literal_digest, literal_payload = capture_subject(
            store, work, monkeypatch, package)
        assert literal_payload["links"] == {"src/links/literal.py": "5c786666"}
        # The two target byte sequences produce different subjects.
        assert literal_digest != raw_digest

        os.symlink(b"\xff", os.fsencode(raw))
        both_digest, both_payload = capture_subject(store, work, monkeypatch, package)
        assert both_payload["files"] == {}
        assert both_payload["links"] == {
            "src/links/literal.py": "5c786666",
            "src/links/raw.py": "ff",
        }
        assert both_digest not in (raw_digest, literal_digest)

        prepared = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
        assert prepared["review_subject"] == both_digest
        ended = end_review(store, work, sid, package, prepared)
        assert ended["task_status"] == "PASS"

        # Swap the two targets: each link's recorded value changes, so the
        # earlier ACCEPT must not be reusable.
        raw.unlink()
        literal.unlink()
        os.symlink(b"\\xff", os.fsencode(raw))
        os.symlink(b"\xff", os.fsencode(literal))
        swapped_digest, swapped_payload = capture_subject(
            store, work, monkeypatch, package)
        assert swapped_payload["links"] == {
            "src/links/literal.py": "ff",
            "src/links/raw.py": "5c786666",
        }
        assert swapped_digest != both_digest
        with pytest.raises(ScsError) as err:
            work.update(sid, package["id"], ended["version"], "REVIEW_READY")
        assert err.value.code == "REVIEW_STALE"
        current = work.get(sid, package["id"])
        assert current["state"] == "IN_PROGRESS"
    finally:
        for link in (raw, literal):
            if os.path.lexists(link):
                link.unlink()


def test_scope_paths_are_normalized_and_unsafe_paths_are_rejected(env, monkeypatch):
    """./src and src//sub normalize; absolute and .. paths are rejected."""
    store, _, sid, work = env
    (store.root / "src" / "sub").mkdir()
    (store.root / "src" / "sub" / "x.py").write_text("X = 1\n", encoding="utf-8")

    assert store.paths.parts("./src") == ("src",)
    assert store.paths.parts("src//sub") == ("src", "sub")
    assert store.paths.parts("src/./sub") == ("src", "sub")

    plain = make(work, sid, "norm-plain")
    dotted = make(work, sid, "norm-dotted", package_scope="./src")
    assert dotted["allowed_files"] == ["src"]
    _, plain_payload = capture_subject(store, work, monkeypatch, plain)
    _, dotted_payload = capture_subject(store, work, monkeypatch, dotted)
    assert dotted_payload["files"] == plain_payload["files"]
    assert dotted_payload["definition"]["allowed_files"] == ["src"]

    double_slash = make(work, sid, "norm-double-slash", package_scope="src//sub")
    assert double_slash["allowed_files"] == ["src/sub"]
    _, slash_payload = capture_subject(store, work, monkeypatch, double_slash)
    assert slash_payload["files"] == {
        "src/sub/x.py": hashlib.sha256(
            (store.root / "src" / "sub" / "x.py").read_bytes()).hexdigest()}

    for bad, key in (("/etc", "bad-absolute"), ("/etc/passwd", "bad-absolute-file"),
                     ("../escape", "bad-parent"), ("src/../../escape", "bad-escape")):
        with pytest.raises(ScsError) as err:
            work.create(sid, "bad scope", [bad], ["accepted"],
                        task_defs("src"), key)
        assert err.value.code == "PATH_INVALID"

    # Even a stored scope that bypasses create validation must not escape.
    for bad_scopes in (["/etc/passwd"], ["../escape"], ["src/../../escape"]):
        set_stored_scopes(store, plain, bad_scopes)
        with pytest.raises(ScsError) as err:
            capture_subject(store, work, monkeypatch, plain)
        assert err.value.code == "REVIEW_SUBJECT_INCOMPLETE"
    set_stored_scopes(store, plain, ["src"])


def test_special_files_make_the_review_subject_incomplete(env):
    """A fifo inside a scope or as the scope itself is rejected."""
    if not hasattr(os, "mkfifo"):
        pytest.skip("os.mkfifo is not available on this platform")
    store, _, sid, work = env
    inside = make(work, sid, "fifo-inside")
    as_scope = make(work, sid, "fifo-scope", package_scope="src/pipe")
    pipe = store.root / "src" / "pipe"
    try:
        os.mkfifo(pipe)
    except OSError as err:  # pragma: no cover - platform/filesystem dependent
        pytest.skip(f"mkfifo is unavailable: {err}")
    try:
        for package in (inside, as_scope):
            with pytest.raises(ScsError) as err:
                work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
            assert err.value.code == "REVIEW_SUBJECT_INCOMPLETE"
            assert work.list_attempts(sid, package["id"])["attempts"] == []
    finally:
        pipe.unlink()


def test_dependency_and_generated_tree_links_do_not_block_prepare(env, monkeypatch):
    """The R3 P2 counterexample: node_modules/build/dist links are recorded, not fatal."""
    store, _, sid, work = env
    node_modules = store.root / "src" / "node_modules"
    (node_modules / ".bin").mkdir(parents=True)
    (store.root / "src" / "dist").mkdir()
    (store.root / "src" / "build").mkdir()
    os.symlink("../../bin/tool", node_modules / ".bin" / "tool")
    os.symlink("../../a.py", node_modules / "pkg.js")
    os.symlink("../a.py", store.root / "src" / "dist" / "bundle.js")
    os.symlink("../missing.js", store.root / "src" / "build" / "out.js")

    package = make(work, sid, "generated-tree-links")
    digest, payload = capture_subject(store, work, monkeypatch, package)
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert payload["links"] == {
        "src/build/out.js": b"../missing.js".hex(),
        "src/dist/bundle.js": b"../a.py".hex(),
        "src/node_modules/.bin/tool": b"../../bin/tool".hex(),
        "src/node_modules/pkg.js": b"../../a.py".hex(),
    }
    assert set(payload["files"]) == {"src/a.py", "src/b.py"}

    prepared = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    assert prepared["review_subject"] == digest
    assert prepared["attempt"]["state"] == "PREPARED"
