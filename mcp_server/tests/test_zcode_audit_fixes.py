"""Regression tests for the 9 P0/P1 audit fixes on zcode-native-agents."""

import hashlib
import json
import os

import pytest
from mcp import Client

from scs_mcp.board import Board, BoardError
from scs_mcp.errors import ScsError
from scs_mcp.gate import Gate
from scs_mcp.locks import Leases
from scs_mcp.obsidian import ProjectView
from scs_mcp.server import create_server
from scs_mcp.state import State
from scs_mcp.storage import Store
from scs_mcp.work import Work


def _confirm(state: State, sid: str, kind: str, choices: list[str], channel: str = "text"):
    q = state.request(sid, kind, choices, channel)
    assert q.get("kind") == kind, q
    res = state.submit(sid, q["id"], "accept", choices, source=channel)
    assert res["status"] == "CONFIRMED"
    return q


def _write_file(root, relative: str, content: str) -> str:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = content.encode("utf-8")
    target.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


# ── 1. [P0] VALIDATION_NOT_AUTHORIZED on work_attempt_update / update_many ──


@pytest.mark.parametrize("selection", ["none", "method-only"])
@pytest.mark.parametrize("status", ["PASS", "FAIL", "BLOCKED", "SUBSTITUTED"])
def test_attempt_update_enforces_validation_not_authorized(tmp_path, selection, status):
    store = Store(tmp_path)
    state = State(store)
    sid = state.open(str(tmp_path), "test", f"no-test-{selection}-{status}")["id"]
    _confirm(state, sid, "mode", ["standard"])
    _confirm(state, sid, "verification", [selection])
    work = Work(store)
    pkg = work.create(sid, "no-test package", ["src"], ["done"], [
        {"id": "A1", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["analyzed"]},
        {"id": "T1", "kind": "test", "allowed_files": ["src/a.py"], "acceptance": ["not run"]},
    ], f"k-{selection}-{status}")
    pkg = work.update(sid, pkg["id"], pkg["version"], "IN_PROGRESS")
    a1 = work.prepare_attempt(sid, pkg["id"], pkg["version"], "A1")
    t1 = work.prepare_attempt(sid, pkg["id"], pkg["version"], "T1")

    result = {"status": status}
    if status == "PASS":
        ev_path = f"{t1['evidence_dir']}/test.json"
        sha = _write_file(tmp_path, ev_path, json.dumps({"command": "pytest", "exit_code": 0, "passed": 1}))
        result["evidence"] = [{"path": ev_path, "sha256": sha}]

    with pytest.raises(ScsError) as err:
        work.update_attempt(sid, t1["attempt"]["id"], pkg["version"], "ENDED", result)
    assert err.value.code == "VALIDATION_NOT_AUTHORIZED"
    assert work.get(sid, pkg["id"])["version"] == pkg["version"]

    # Also rejected for PASS even when evidence is omitted.
    if status == "PASS":
        with pytest.raises(ScsError) as err:
            work.update_attempt(sid, t1["attempt"]["id"], pkg["version"], "ENDED", {"status": "PASS"})
        assert err.value.code == "VALIDATION_NOT_AUTHORIZED"

    # Batch update must also reject and roll back atomically.
    with pytest.raises(ScsError) as err:
        work.update_attempts(sid, [
            {"attempt_id": a1["attempt"]["id"], "result": {"status": "NOT_RUN"}},
            {"attempt_id": t1["attempt"]["id"], "result": result},
        ], f"batch-{selection}-{status}")
    assert err.value.code == "VALIDATION_NOT_AUTHORIZED"
    assert work.get(sid, pkg["id"])["version"] == pkg["version"]
    assert all(a["state"] == "PREPARED" for a in work.list_attempts(sid, pkg["id"])["attempts"])
    with store.transaction() as con:
        assert con.execute("SELECT count(*) FROM work_operations").fetchone()[0] == 0

    # Non-test subtask PASS + test subtask NOT_RUN succeeds in batch.
    a1_path = f"{a1['evidence_dir']}/report.txt"
    a1_sha = _write_file(tmp_path, a1_path, "analysis complete\n")
    ended = work.update_attempts(sid, [
        {"attempt_id": a1["attempt"]["id"], "result": {"status": "PASS", "evidence": [{"path": a1_path, "sha256": a1_sha}]}},
        {"attempt_id": t1["attempt"]["id"], "result": {"status": "NOT_RUN"}},
    ], f"ok-{selection}-{status}")
    assert ended["task_statuses"] == {"A1": "PASS", "T1": "NOT_RUN"}


# ── 2. [P0] _review_subject excludes agent/acceptance and agent/view-archives ──


async def test_review_subject_excludes_acceptance_view_and_keeps_user_docs(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/design.md").write_text("design v1\n", encoding="utf-8")
    async with Client(create_server(tmp_path, question_channel="text")) as client:
        async def call(name, **args):
            return (await client.call_tool(name, args)).structured_content

        sid = (await call("session_open", project_root=str(tmp_path), client_name="audit", conversation_id="view"))["id"]
        for kind, choices in (("mode", ["long"]), ("verification", ["smoke"]), ("codex_gate", ["packages"])):
            q = await call("interaction_request", session_id=sid, kind=kind, recommendation=choices, channel="text")
            res = await call("interaction_submit", session_id=sid, question_id=q["question_id"], action="accept", choices=choices)
            assert res["status"] == "CONFIRMED"

        refreshed = await call("project_view_refresh", session_id=sid, initialize_vault=True, auto_refresh=True)
        assert refreshed["auto_refresh_enabled"] is True

        pkg = await call("work_package_create", session_id=sid, goal="Docs review with auto refresh",
                         allowed_files=["docs"], acceptance=["docs reviewed"],
                         subtasks=[{"id": "RV", "kind": "review", "allowed_files": ["docs"], "acceptance": ["accept"]}],
                         request_key="docs-auto-refresh")
        assert pkg["status"] == "OK" and pkg["view_update"]["status"] == "OK"
        pkg = await call("work_package_update", session_id=sid, package_id=pkg["id"],
                         expected_version=pkg["version"], state="IN_PROGRESS")

        # Preparing the review triggers auto_refresh, mutating docs/agent/acceptance/*
        prepared = await call("work_attempt_prepare", session_id=sid, package_id=pkg["id"],
                              expected_version=pkg["version"], subtask_id="RV")
        assert prepared["status"] == "OK"
        assert prepared["view_update"]["status"] == "OK"
        assert prepared["view_update"]["updated_files"] > 0

        # Archives under docs/agent/view-archives must also not stale the review subject.
        _write_file(tmp_path, "docs/agent/view-archives/2026-11-24/old.md", "archived view\n")

        folder = prepared["evidence_dir"]
        raw_path = f"{folder}/review.md"
        raw_sha = _write_file(tmp_path, raw_path, "# Review\n\n结论: ACCEPT\n")
        manifest = {
            "verdict": "ACCEPT",
            "session_id": "codex-1",
            "model": "gpt-test",
            "subject_sha256": prepared["review_subject"],
            "final_message_sha256": raw_sha,
            "command": "codex review",
            "exit_code": 0,
            "raw_path": "review.md",
        }
        manifest_path = f"{folder}/manifest.json"
        manifest_sha = _write_file(tmp_path, manifest_path, json.dumps(manifest))

        ended = await call("work_attempt_update", session_id=sid, attempt_id=prepared["attempt"]["id"],
                           expected_version=pkg["version"], state="ENDED",
                           result={"status": "PASS", "evidence": [{"path": manifest_path, "sha256": manifest_sha}]})
        assert ended["status"] == "OK" and ended["task_status"] == "PASS"

        ready = await call("work_package_update", session_id=sid, package_id=pkg["id"],
                           expected_version=ended["version"], state="REVIEW_READY")
        assert ready["status"] == "OK" and ready["state"] == "REVIEW_READY"

        done = await call("work_package_update", session_id=sid, package_id=pkg["id"],
                          expected_version=ready["version"], state="COMPLETED", close_reason="accepted")
        assert done["status"] == "OK" and done["state"] == "COMPLETED"


@pytest.mark.parametrize("edited_file", ["docs/agent/AGENT_PROJECT.md", "docs/design.md"])
def test_review_subject_still_tracks_agent_project_and_user_docs(tmp_path, edited_file):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/design.md").write_text("design v1\n", encoding="utf-8")
    store = Store(tmp_path)
    state = State(store)
    sid = state.open(str(tmp_path), "test", "docs-track")["id"]
    _confirm(state, sid, "mode", ["long"])
    _confirm(state, sid, "verification", ["smoke"])
    _confirm(state, sid, "codex_gate", ["packages"])
    work = Work(store)
    pkg = work.create(sid, "Track real docs", ["docs"], ["reviewed"], [
        {"id": "RV", "kind": "review", "allowed_files": ["docs"], "acceptance": ["accept"]},
    ], "track-docs")
    pkg = work.update(sid, pkg["id"], pkg["version"], "IN_PROGRESS")
    prepared = work.prepare_attempt(sid, pkg["id"], pkg["version"], "RV")

    # Mutating AGENT_PROJECT.md or a user doc MUST invalidate the frozen subject.
    (tmp_path / edited_file).write_text("modified content\n", encoding="utf-8")
    folder = prepared["evidence_dir"]
    raw_sha = _write_file(tmp_path, f"{folder}/review.md", "结论: ACCEPT\n")
    manifest = {
        "verdict": "ACCEPT",
        "session_id": "codex-1",
        "model": "gpt-test",
        "subject_sha256": prepared["review_subject"],
        "final_message_sha256": raw_sha,
        "command": "codex review",
        "exit_code": 0,
        "raw_path": "review.md",
    }
    m_path = f"{folder}/manifest.json"
    m_sha = _write_file(tmp_path, m_path, json.dumps(manifest))
    with pytest.raises(ScsError) as err:
        work.update_attempt(sid, prepared["attempt"]["id"], pkg["version"], "ENDED",
                            {"status": "PASS", "evidence": [{"path": m_path, "sha256": m_sha}]})
    assert err.value.code == "REVIEW_STALE"


# ── 3. [P1] force_close releases all leases held by the closed package ──


def test_force_close_releases_active_attempt_leases_including_resumed_sessions(tmp_path):
    store = Store(tmp_path)
    state = State(store)
    sid1 = state.open(str(tmp_path), "test", "owner", explicit_skip=True)["id"]
    sid2 = state.open(str(tmp_path), "test", "resumed", explicit_skip=True)["id"]
    work = Work(store)
    leases = Leases(store)

    pkg = work.create(sid1, "stuck pkg", ["src"], ["done"], [
        {"id": "I1", "kind": "implement", "allowed_files": ["src/a.py"], "acceptance": ["a"],
         "resources": [{"resource": "file:src/a.py", "mode": "write"},
                       {"resource": "device:board-1", "mode": "write"}]},
        {"id": "I2", "kind": "implement", "allowed_files": ["src/b.py"], "acceptance": ["b"]},
    ], "stuck")
    pkg = work.update(sid1, pkg["id"], pkg["version"], "IN_PROGRESS")

    # Export and resume in sid2 so I2 is owned by a different session.
    handoff = work.export(sid1, pkg["id"], pkg["version"])
    work.resume(sid2, handoff["handoff_id"], handoff["sha256"])

    work.prepare_attempt(sid1, pkg["id"], pkg["version"], "I1", ttl_seconds=3600)
    work.prepare_attempt(sid2, pkg["id"], pkg["version"], "I2", ttl_seconds=3600)

    # Unrelated package lease and standalone lease must survive force_close.
    other = work.create(sid1, "other pkg", ["other"], ["done"], [
        {"id": "O1", "kind": "implement", "allowed_files": ["other/x.py"], "acceptance": ["x"]},
    ], "other")
    other = work.update(sid1, other["id"], other["version"], "IN_PROGRESS")
    work.prepare_attempt(sid1, other["id"], other["version"], "O1", ttl_seconds=3600)
    standalone = leases.acquire(sid1, "serial:keep-me", ttl_seconds=3600)
    assert standalone["status"] == "ACQUIRED"

    closed = work.force_close(pkg["id"], pkg["version"], "ABANDONED", "orphan stuck")
    assert closed["state"] == "ABANDONED" and closed["forced"] is True

    with store.transaction() as con:
        remaining = {row["resource"] for row in con.execute("SELECT resource FROM leases")}
    assert "file:src/a.py" not in remaining
    assert "file:src/b.py" not in remaining
    assert "device:board-1" not in remaining
    assert remaining == {"file:other/x.py", "serial:keep-me"}

    # Freed resources can be acquired immediately without waiting for TTL.
    assert leases.acquire(sid2, "file:src/a.py")["status"] == "ACQUIRED"
    assert leases.acquire(sid2, "device:board-1")["status"] == "ACQUIRED"


# ── 4. [P1] Non-file read leases do not overwrite active holders ──


@pytest.mark.parametrize("resource", ["device:board-1", "serial:ttyUSB0", "programmer:stlink-v2"])
def test_non_file_read_leases_are_mutually_exclusive_and_not_overwritten(tmp_path, resource):
    store = Store(tmp_path)
    state = State(store)
    sid = state.open(str(tmp_path), "test", "dev-read", explicit_skip=True)["id"]
    work = Work(store)
    leases = Leases(store)

    pkg = work.create(sid, "device read test", ["src"], ["done"], [
        {"id": "A", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["a"],
         "resources": [{"resource": resource, "mode": "read"}]},
        {"id": "B", "kind": "analyze", "allowed_files": ["src/b.py"], "acceptance": ["b"],
         "resources": [{"resource": resource, "mode": "read"}]},
    ], "dev-read-" + resource.split(":")[0])
    pkg = work.update(sid, pkg["id"], pkg["version"], "IN_PROGRESS")

    # Batch preparing both in one transaction must fail and roll back cleanly.
    with pytest.raises(ScsError) as err:
        work.prepare_attempts(sid, pkg["id"], pkg["version"], ["A", "B"])
    assert err.value.code == "RESOURCE_BUSY"
    with store.transaction() as con:
        assert con.execute("SELECT count(*) FROM leases").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM work_attempts").fetchone()[0] == 0

    first = work.prepare_attempt(sid, pkg["id"], pkg["version"], "A")
    first_token = first["leases"][0]["lease_token"]
    first_id = first["attempt"]["id"]

    # Second reader on the same non-file resource must get RESOURCE_BUSY, NOT overwrite.
    with pytest.raises(ScsError) as err:
        work.prepare_attempt(sid, pkg["id"], pkg["version"], "B")
    assert err.value.code == "RESOURCE_BUSY"

    # Original lease row stays intact.
    with store.transaction() as con:
        row = con.execute("SELECT * FROM leases WHERE resource=?", (resource,)).fetchone()
    assert row["attempt_id"] == first_id and row["token"] == first_token and row["mode"] == "read"

    # Standalone acquire cannot hijack or detach an active attempt lease even with its token.
    assert leases.acquire(sid, resource, lease_token=first_token)["status"] == "CONFLICT"
    with store.transaction() as con:
        row = con.execute("SELECT * FROM leases WHERE resource=?", (resource,)).fetchone()
    assert row["attempt_id"] == first_id

    # After A ends, B can acquire the lease.
    ended = work.update_attempt(sid, first_id, pkg["version"], "ENDED", {"status": "NOT_RUN"})
    second = work.prepare_attempt(sid, pkg["id"], ended["version"], "B")
    assert second["leases"][0]["resource"] == resource and second["leases"][0]["mode"] == "read"


# ── 5. [P1] State.submit / reopen native review mode transitions ──


def test_native_reopen_mode_and_reconfirm_long_preserves_gate_and_route(tmp_path, monkeypatch):
    monkeypatch.setenv("SCS_NATIVE_REVIEW", "1")
    state = State(Store(tmp_path))
    sid = state.open(str(tmp_path), "zcode", "reconfirm-long")["id"]
    _confirm(state, sid, "mode", ["long"], "native")
    _confirm(state, sid, "verification", ["targeted"], "native")
    _confirm(state, sid, "codex_gate", ["packages"], "native")
    _confirm(state, sid, "review_route", ["gpt"], "native")
    assert state.get(sid)["intake"]["ready"] is True

    state.reopen(sid, "mode", explicit_user_change=True)
    # While mode is unset, review fields are hidden, intake is not ready, and
    # requesting codex_gate/review_route must enforce QUESTION_ORDER instead of
    # leaking the paused confirmed rows.
    mid = state.get(sid)
    assert mid["mode"] is None and mid["codex_gate"] is None and mid["review_route"] is None
    assert mid["intake"]["reason"] == "MODE_REQUIRED"
    for review_kind, rec in (("codex_gate", ["packages"]), ("review_route", ["gpt"])):
        with pytest.raises(ScsError) as err:
            state.request(sid, review_kind, rec, "native")
        assert err.value.code == "QUESTION_ORDER"

    # Re-confirming long preserves the already confirmed codex_gate and review_route.
    _confirm(state, sid, "mode", ["long"], "native")
    after = state.get(sid)
    assert after["mode"] == "long"
    assert after["codex_gate"] == "packages"
    assert after["review_route"] == "gpt"
    assert after["intake"] == {"ready": True, "policy": "packages", "source": "codex_gate", "reason": None}


@pytest.mark.parametrize("target_mode", ["quick", "standard", "investigate"])
def test_native_switch_long_to_short_retires_gate_and_route(tmp_path, monkeypatch, target_mode):
    monkeypatch.setenv("SCS_NATIVE_REVIEW", "1")
    store = Store(tmp_path)
    state = State(store)
    sid = state.open(str(tmp_path), "zcode", f"long-to-{target_mode}")["id"]
    _confirm(state, sid, "mode", ["long"], "native")
    _confirm(state, sid, "verification", ["targeted"], "native")
    _confirm(state, sid, "codex_gate", ["packages"], "native")
    _confirm(state, sid, "review_route", ["gpt"], "native")

    state.reopen(sid, "mode", explicit_user_change=True)
    _confirm(state, sid, "mode", [target_mode], "native")

    with store.transaction() as con:
        active = con.execute(
            "SELECT kind FROM questions WHERE session_id=? AND current=1 "
            "AND kind IN ('codex_gate','review_route','codex_review')", (sid,)).fetchall()
    assert active == []

    # Switching back to long later must not resurrect the retired gate/route.
    state.reopen(sid, "mode", explicit_user_change=True)
    _confirm(state, sid, "mode", ["long"], "native")
    back = state.get(sid)
    assert back["codex_gate"] is None and back["review_route"] is None
    assert back["intake"]["reason"] == "CODEX_GATE_REQUIRED"


@pytest.mark.parametrize("explicit_skip", [False, True])
def test_native_short_mode_stale_review_retired_directly_on_reopen_to_long(tmp_path, monkeypatch, explicit_skip):
    monkeypatch.setenv("SCS_NATIVE_REVIEW", "1")
    store = Store(tmp_path)
    state = State(store)
    sid = state.open(str(tmp_path), "zcode", f"short-stale-{explicit_skip}", explicit_skip=explicit_skip)["id"]
    if not explicit_skip:
        _confirm(state, sid, "mode", ["standard"], "native")
        _confirm(state, sid, "verification", ["targeted"], "native")
    # Inject stale CONFIRMED rows directly in SQL and reopen WITHOUT calling request() first.
    with store.transaction(True) as con:
        con.execute("INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("stale-gate", sid, "codex_gate", 1, "CONFIRMED", "native", '["packages"]', '["packages"]', "old", "old"))
        con.execute("INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("stale-route", sid, "review_route", 1, "CONFIRMED", "native", '["gpt"]', '["gpt"]', "old", "old"))
        con.execute("INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("stale-legacy", sid, "codex_review", 1, "CONFIRMED", "native", '["yes"]', '["yes"]', "old", "old"))

    state.reopen(sid, "mode", explicit_user_change=True)
    _confirm(state, sid, "mode", ["long"], "native")
    if explicit_skip:
        _confirm(state, sid, "verification", ["targeted"], "native")
    snap = state.get(sid)
    assert snap["codex_gate"] is None and snap["review_route"] is None
    assert snap["intake"]["reason"] == "CODEX_GATE_REQUIRED"
    q_gate = state.request(sid, "codex_gate", ["packages"], "native")
    assert q_gate["created"] and q_gate["id"] != "stale-gate"


# ── 6. [P1] board_task_* allowed_roots boundary and O_NOFOLLOW protection ──


async def test_board_tools_enforce_allowed_roots_and_suppress_foreign_errors(tmp_path):
    allowed = tmp_path / "allowed"
    extra_allowed = tmp_path / "extra-allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    extra_allowed.mkdir()
    outside.mkdir()
    board_dir = tmp_path / "shared-board"
    raw_board = Board(board_dir)

    outside_task = raw_board.add_task(str(outside), "Secret outside task", "private", session_id="s-out")
    allowed_task = raw_board.add_task(str(allowed), "Allowed task", "ok", session_id="s-in")
    extra_task = raw_board.add_task(str(extra_allowed), "Extra allowed task", "ok", session_id="s-extra")

    # Foreign corrupt task (explicit project_root=outside) + unattributable corrupt file + local corrupt task.
    (board_dir / "tasks" / "foreign-bad.md").write_text(
        f'---\nid: "wrong"\nproject_root: {json.dumps(str(outside))}\n---\n', encoding="utf-8")
    (board_dir / "tasks" / "unowned-bad.md").write_text("not frontmatter", encoding="utf-8")
    (board_dir / "tasks" / "local-bad.md").write_text(
        f'---\nid: "wrong"\nproject_root: {json.dumps(str(allowed))}\n---\n', encoding="utf-8")
    # Unbound global task (project_root == "")
    (board_dir / "tasks" / "T-global.md").write_text(
        '---\nid: "T-global"\nproject_root: ""\ntitle: "Global"\npriority: "medium"\n'
        'due_at: ""\nstate: "todo"\nsession_id: ""\ncreated_session: ""\nowner: ""\n'
        'work_package_id: ""\ncreated_by: "gui"\ncreated_at: "2026-11-24T00:00:00+00:00"\n'
        'updated_at: "2026-11-24T00:00:00+00:00"\nversion: 1\n---\n', encoding="utf-8")

    before_md = (board_dir / "tasks" / f"{outside_task['id']}.md").read_bytes()
    before_ev = (board_dir / "events" / f"{outside_task['id']}.jsonl").read_bytes()

    async with Client(create_server(allowed, allowed_roots=[extra_allowed], board_dir=board_dir)) as client:
        async def call(name, **args):
            return (await client.call_tool(name, args)).structured_content

        # All 9 task_id tools must reject outside_task with PROJECT_MISMATCH.
        calls = [
            ("board_task_get", {"task_id": outside_task["id"]}),
            ("board_task_claim", {"task_id": outside_task["id"], "session_id": "s-out"}),
            ("board_task_start", {"task_id": outside_task["id"], "session_id": "s-out"}),
            ("board_task_update", {"task_id": outside_task["id"], "actor": "a", "title": "hack"}),
            ("board_task_comment", {"task_id": outside_task["id"], "actor": "a", "text": "hi"}),
            ("board_task_complete", {"task_id": outside_task["id"], "actor": "a", "summary": "done"}),
            ("board_task_abandon", {"task_id": outside_task["id"], "actor": "a", "reason": "no"}),
            ("board_task_release", {"task_id": outside_task["id"], "actor": "a"}),
            ("board_task_prompt", {"task_id": outside_task["id"]}),
        ]
        for tool_name, tool_args in calls:
            res = await call(tool_name, **tool_args)
            assert res["status"] == "ERROR" and res["error_code"] == "PROJECT_MISMATCH", (tool_name, res)

        # Outside task files remain completely untouched.
        assert (board_dir / "tasks" / f"{outside_task['id']}.md").read_bytes() == before_md
        assert (board_dir / "events" / f"{outside_task['id']}.jsonl").read_bytes() == before_ev

        # Listing outside project is refused.
        refused_list = await call("board_task_list", project_root=str(outside))
        assert refused_list["error_code"] == "PROJECT_MISMATCH"

        # Listing allowed project returns only allowed tasks and local errors, no foreign/unowned leaks.
        listed = await call("board_task_list", project_root=str(allowed))
        assert [t["id"] for t in listed["tasks"]] == [allowed_task["id"]]
        assert any("local-bad.md" in err for err in listed["errors"])
        assert not any("foreign-bad.md" in err for err in listed["errors"])
        assert not any("unowned-bad.md" in err for err in listed["errors"])

        # Task in secondary allowed_roots entry is accessible.
        extra_res = await call("board_task_get", task_id=extra_task["id"])
        assert extra_res["task"]["id"] == extra_task["id"]

        # Unbound global task (project_root == "") remains accessible by ID.
        global_res = await call("board_task_get", task_id="T-global")
        assert global_res["task"]["id"] == "T-global"


async def test_board_rejects_symlinks_on_read_and_write(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    board_dir = tmp_path / "board"
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP_SECRET\n", encoding="utf-8")

    board = Board(board_dir)
    task = board.add_task(str(project), "Normal task", session_id="s1")

    # Symlink task file.
    os.symlink(secret, board_dir / "tasks" / "T-symlink.md")
    with pytest.raises(BoardError) as err:
        board.get_task("T-symlink")
    assert err.value.code == "PATH_UNSAFE"

    # Symlink event file must reject both reads and writes without modifying target or task file.
    event_file = board_dir / "events" / f"{task['id']}.jsonl"
    event_file.unlink()
    os.symlink(secret, event_file)
    task_before = (board_dir / "tasks" / f"{task['id']}.md").read_bytes()

    async with Client(create_server(project, board_dir=board_dir)) as client:
        res_get = (await client.call_tool("board_task_get", {"task_id": task["id"]})).structured_content
        assert res_get["error_code"] == "PATH_UNSAFE"
        res_comment = (await client.call_tool("board_task_comment", {
            "task_id": task["id"], "actor": "tester", "text": "should fail"})).structured_content
        assert res_comment["error_code"] == "PATH_UNSAFE"

    assert secret.read_text(encoding="utf-8") == "TOP_SECRET\n"
    assert (board_dir / "tasks" / f"{task['id']}.md").read_bytes() == task_before

    # Symlink tasks/ directory is rejected.
    sym_board_dir = tmp_path / "sym-board"
    sym_board_dir.mkdir()
    outside_dir = tmp_path / "outside-tasks"
    outside_dir.mkdir()
    os.symlink(outside_dir, sym_board_dir / "tasks")
    with pytest.raises(BoardError) as err:
        Board(sym_board_dir).list_tasks(str(project))
    assert err.value.code == "PATH_UNSAFE"


# ── 7. [P1] work_attempt_update_many optional expected_version ──


async def test_work_attempt_update_many_expected_version_and_idempotency(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("x = 1\n", encoding="utf-8")
    async with Client(create_server(tmp_path)) as client:
        async def call(name, **args):
            return (await client.call_tool(name, args)).structured_content

        sid = (await call("session_open", project_root=str(tmp_path), client_name="opt", explicit_skip=True))["id"]
        pkg = await call("work_package_create", session_id=sid, goal="batch version", allowed_files=["src"],
                         acceptance=["done"], subtasks=[
                             {"id": "A", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["a"]},
                             {"id": "B", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["b"]},
                         ], request_key="batch-ver")
        pkg = await call("work_package_update", session_id=sid, package_id=pkg["id"],
                         expected_version=pkg["version"], state="IN_PROGRESS")
        prep = await call("work_attempt_prepare_many", session_id=sid, package_id=pkg["id"],
                          expected_version=pkg["version"], subtask_ids=["A", "B"])
        ids = [entry["attempt"]["id"] for entry in prep["attempts"]]
        updates = [{"attempt_id": ids[0], "result": {"status": "NOT_RUN"}},
                   {"attempt_id": ids[1], "result": {"status": "NOT_RUN"}}]

        # Stale expected_version fails with VERSION_CONFLICT and mutates nothing.
        stale = await call("work_attempt_update_many", session_id=sid, updates=updates,
                           operation_id="op-stale", expected_version=pkg["version"] + 5)
        assert stale["error_code"] == "VERSION_CONFLICT"
        assert (await call("work_package_get", session_id=sid, package_id=pkg["id"]))["version"] == pkg["version"]

        # Matching expected_version succeeds and bumps version once.
        ok = await call("work_attempt_update_many", session_id=sid, updates=updates,
                        operation_id="op-ok", expected_version=pkg["version"])
        assert ok["status"] == "OK" and ok["version"] == pkg["version"] + 1

        # Replaying with the SAME operation_id and SAME expected_version succeeds idempotently.
        replay = await call("work_attempt_update_many", session_id=sid, updates=updates,
                            operation_id="op-ok", expected_version=pkg["version"])
        assert replay == ok

        # Replaying with the SAME operation_id but a DIFFERENT expected_version raises OPERATION_CONFLICT.
        conflict = await call("work_attempt_update_many", session_id=sid, updates=updates,
                              operation_id="op-ok", expected_version=ok["version"])
        assert conflict["error_code"] == "OPERATION_CONFLICT"


def test_update_attempts_rejects_bool_expected_version(tmp_path):
    store = Store(tmp_path)
    state = State(store)
    sid = state.open(str(tmp_path), "test", "bool-ver", explicit_skip=True)["id"]
    work = Work(store)
    pkg = work.create(sid, "g", ["src"], ["a"], [
        {"id": "A", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["a"]},
    ], "bool-ver")
    pkg = work.update(sid, pkg["id"], pkg["version"], "IN_PROGRESS")
    prep = work.prepare_attempt(sid, pkg["id"], pkg["version"], "A")
    with pytest.raises(ScsError) as err:
        work.update_attempts(sid, [{"attempt_id": prep["attempt"]["id"], "result": {"status": "NOT_RUN"}}],
                             "op-bool", expected_version=True)
    assert err.value.code == "INPUT_INVALID"


# ── 8. [P1] diagnostics_check redacts operation_gate & work_attempt_note payloads ──


async def test_diagnostics_check_redacts_gate_and_note_payloads(tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports/accept.md").write_text("结论: ACCEPT\n", encoding="utf-8")
    (tmp_path / "reports/reject.md").write_text("结论: REJECT\n", encoding="utf-8")

    async with Client(create_server(tmp_path)) as client:
        async def call(name, **args):
            return (await client.call_tool(name, args)).structured_content

        sid = (await call("session_open", project_root=str(tmp_path), client_name="diag", explicit_skip=True))["id"]
        pkg = await call("work_package_create", session_id=sid, goal="diag", allowed_files=["src"],
                         acceptance=["ok"], subtasks=[
                             {"id": "A", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["a"]},
                         ], request_key="diag")
        pkg = await call("work_package_update", session_id=sid, package_id=pkg["id"],
                         expected_version=pkg["version"], state="IN_PROGRESS")
        prep = await call("work_attempt_prepare", session_id=sid, package_id=pkg["id"],
                          expected_version=pkg["version"], subtask_id="A")
        await call("work_attempt_note", session_id=sid, attempt_id=prep["attempt"]["id"],
                   note="PRIVATE_FREE_TEXT_NOTE_TOKEN_999", note_id="n1")

        await call("operation_gate_record", session_id=sid, operation_id="op-acc",
                   command="rm -rf SECRET_CMD_ALPHA", cwd=str(tmp_path), targets=["build"],
                   impact="SECRET_IMPACT_BETA", backup="SECRET_BACKUP_GAMMA",
                   rollback="SECRET_ROLLBACK_DELTA", verdict="ACCEPT",
                   user_confirmed=True, report_path="reports/accept.md")
        await call("operation_gate_record", session_id=sid, operation_id="op-rej",
                   command="dd if=/dev/zero SECRET_REJ", cwd=str(tmp_path), targets=["build"],
                   impact="bad", backup="none", rollback="none", verdict="REJECT",
                   user_confirmed=False, report_path="reports/reject.md")
        await call("operation_gate_record", session_id=sid, operation_id="op-ov",
                   command="rm -rf SECRET_OV", cwd=str(tmp_path), targets=["build"],
                   impact="tmp", backup="tar", rollback="untar", verdict="UNAVAILABLE_OVERRIDE",
                   user_confirmed=True, reason="SECRET_REASON_EPSILON_LONG")

        diag = await call("diagnostics_check", session_id=sid)
        assert diag["status"] == "OK"
        serialized = json.dumps(diag["events"], ensure_ascii=False)
        for secret in ("PRIVATE_FREE_TEXT_NOTE_TOKEN_999", "SECRET_CMD_ALPHA", "SECRET_IMPACT_BETA",
                       "SECRET_BACKUP_GAMMA", "SECRET_ROLLBACK_DELTA", "SECRET_REJ",
                       "SECRET_OV", "SECRET_REASON_EPSILON_LONG"):
            assert secret not in serialized

        gate_events = {e["event"] for e in diag["events"] if e["tool"] == "operation_gate"}
        assert gate_events == {"GATE_ACCEPT", "GATE_REJECT", "GATE_UNAVAILABLE_OVERRIDE"}
        note_events = [e["event"] for e in diag["events"] if e["tool"] == "work_attempt_note"]
        assert note_events == ["NOTE_RECORDED"]

        # Underlying SQLite records remain intact for gate check and run_map.
        assert (await call("operation_gate_check", session_id=sid, operation_id="op-acc"))["status"] == "APPROVED"
        rmap = await call("run_map", session_id=sid, package_id=pkg["id"])
        assert rmap["notes"][prep["attempt"]["id"]][0]["note"] == "PRIVATE_FREE_TEXT_NOTE_TOKEN_999"


# ── 9. [P1] ProjectView.refresh computes _check_report once per package ──


def test_project_view_refresh_checks_reports_only_once_per_package(tmp_path, monkeypatch):
    store = Store(tmp_path)
    state = State(store)
    sid = state.open(str(tmp_path), "test", "view-perf", explicit_skip=True)["id"]
    work = Work(store)
    view = ProjectView(store)

    tamper_target = None
    for idx in range(3):
        p = work.create(sid, f"pkg {idx}", ["src"], ["done"], [
            {"id": "T1", "kind": "test", "allowed_files": ["src/a.py"], "acceptance": ["pass"]},
        ], f"perf-{idx}")
        ev = f"{p['docs_root']}/agent/evidence/{p['id']}/R1/T1/res.json"
        sha = _write_file(tmp_path, ev, json.dumps({"command": "pytest", "exit_code": 0, "passed": 1}))
        work.update(sid, p["id"], p["version"], "IN_PROGRESS", [
            {"id": "T1", "status": "PASS", "evidence": [{"path": ev, "sha256": sha}]}])
        if idx == 2:
            tamper_target = tmp_path / ev

    tamper_target.write_text("tampered", encoding="utf-8")

    calls = []
    original = view._check_report

    def spy(row):
        calls.append(row["id"])
        return original(row)

    monkeypatch.setattr(view, "_check_report", spy)

    first = view.refresh(sid)
    assert first["result"] == "REFRESHED"
    assert len(calls) == 3
    assert len(first["evidence_issues"]) == 1
    assert first["evidence_stale"] is False and first["needs_refresh"] is False

    calls.clear()
    second = view.refresh(sid)
    assert second["result"] == "NO_CHANGES"
    assert len(calls) == 3
    assert second["evidence_issues"] == first["evidence_issues"]
    assert second["evidence_stale"] is False and second["needs_refresh"] is False

    calls.clear()
    info = view.info(sid)
    assert len(calls) == 3
    assert info["evidence_issues"] == first["evidence_issues"]
    assert info["needs_refresh"] is False


# ── R1 Attempt #2 (Codex review fixes): 4 P1 + 2 P2 regressions ──


async def test_board_rejects_ancestor_symlink_on_read_and_write(tmp_path):
    """P1: /base/link/board where link is a symlink must fail even if board is a real dir."""
    project = tmp_path / "proj"
    project.mkdir()
    real_target = tmp_path / "real-target"
    real_target.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    os.symlink(real_target, base / "link")

    # Case A: board does not exist yet under symlinked ancestor — must NOT create it.
    missing_board_dir = base / "link" / "new-board"
    board_missing = Board(missing_board_dir)
    with pytest.raises(BoardError) as err:
        board_missing.add_task(str(project), "should fail")
    assert err.value.code == "PATH_UNSAFE"
    assert not (real_target / "new-board").exists()
    with pytest.raises(BoardError) as err:
        board_missing.list_tasks(str(project))
    assert err.value.code == "PATH_UNSAFE"

    # Case B: board is a real directory inside symlinked ancestor.
    (real_target / "existing-board").mkdir()
    existing_via_link = base / "link" / "existing-board"
    board_existing = Board(existing_via_link)
    for fn in (
        lambda: board_existing.add_task(str(project), "fail"),
        lambda: board_existing.list_tasks(str(project)),
        lambda: board_existing.get_task("T-any"),
    ):
        with pytest.raises(BoardError) as err:
            fn()
        assert err.value.code == "PATH_UNSAFE"

    async with Client(create_server(project, board_dir=existing_via_link)) as client:
        res = (await client.call_tool("board_task_list", {"project_root": str(project)})).structured_content
        assert res["status"] == "ERROR" and res["error_code"] == "PATH_UNSAFE"


def test_board_pins_locked_directory_and_event_fd_against_swaps(tmp_path, monkeypatch):
    """P1: directory swap after flock cannot redirect writes; event swap after open cannot half-write."""
    project = tmp_path / "proj"
    project.mkdir()

    # Subtest A: swap board_dir after _lock acquires flock and pins fds.
    board_a = tmp_path / "board-a"
    board_b = tmp_path / "board-b"
    moved_a = tmp_path / "board-a-moved"
    board = Board(board_a)
    task = board.add_task(str(project), "Pinned task", session_id="s1")
    Board(board_b).add_task(str(project), "Other board task", session_id="s2")

    orig_load = board._load
    swapped = [False]

    def load_with_dir_swap(task_id, *, tasks_fd=None):
        loaded = orig_load(task_id, tasks_fd=tasks_fd)
        if not swapped[0]:
            swapped[0] = True
            os.rename(board_a, moved_a)
            os.rename(board_b, board_a)
        return loaded

    monkeypatch.setattr(board, "_load", load_with_dir_swap)
    updated = board.comment_task(task["id"], "tester", "pinned comment")
    assert updated["version"] == 2
    # Comment landed in the originally locked directory (now at moved_a), NOT in the swapped-in dir.
    moved_task = Board(moved_a).get_task(task["id"])
    assert moved_task["version"] == 2
    assert moved_task["events"][-1]["data"]["text"] == "pinned comment"
    assert not (board_a / "tasks" / f"{task['id']}.md").exists()

    # Subtest B: replacing events/<id>.jsonl with a symlink after event_fd is opened
    # is caught by inode verification before writing event or replacing task file.
    import scs_mcp.board as board_mod

    board2_dir = tmp_path / "board2"
    board2 = Board(board2_dir)
    task2 = board2.add_task(str(project), "Atomic commit", session_id="s1")
    victim = tmp_path / "victim.txt"
    victim.write_text("DO_NOT_TOUCH\n", encoding="utf-8")

    orig_fsync = os.fsync
    swapped_ev = [False]

    def fsync_with_event_swap(fd):
        res = orig_fsync(fd)
        if not swapped_ev[0]:
            # First fsync inside _commit is on the temp task file, right before event inode check.
            swapped_ev[0] = True
            ev_path = board2_dir / "events" / f"{task2['id']}.jsonl"
            ev_path.unlink()
            os.symlink(victim, ev_path)
        return res

    monkeypatch.setattr(os, "fsync", fsync_with_event_swap)
    with pytest.raises(BoardError) as err:
        board2.comment_task(task2["id"], "tester", "must fail before replace")
    assert err.value.code == "PATH_UNSAFE"
    monkeypatch.setattr(os, "fsync", orig_fsync)
    assert victim.read_text(encoding="utf-8") == "DO_NOT_TOUCH\n"
    # Task file was NOT replaced (version stays 1) and no temp files remain.
    assert board2._load(task2["id"])["version"] == 1
    assert not list((board2_dir / "tasks").glob(".tmp-*.md"))

    # Subtest C: if event write fails (e.g. disk full), task file version is NOT modified.
    board3_dir = tmp_path / "board3"
    board3 = Board(board3_dir)
    task3 = board3.add_task(str(project), "Event write error", session_id="s1")

    def failing_write(fd, data):
        raise OSError(errno.ENOSPC if hasattr(errno, "ENOSPC") else 28, "No space left on device")

    import errno
    monkeypatch.setattr(board_mod, "_write_all", failing_write)
    with pytest.raises(OSError):
        board3.comment_task(task3["id"], "tester", "event write fails")
    assert board3.get_task(task3["id"])["version"] == 1
    assert not list((board3_dir / "tasks").glob(".tmp-*.md"))


async def test_native_reopen_review_questions_blocked_while_mode_pending(tmp_path, monkeypatch):
    """P1: interaction_reopen on review questions while mode is unset must not expose/retire old rows."""
    monkeypatch.setenv("SCS_NATIVE_REVIEW", "1")
    async with Client(create_server(tmp_path, question_channel="native")) as client:
        async def call(name, **args):
            return (await client.call_tool(name, args)).structured_content

        sid = (await call("session_open", project_root=str(tmp_path), client_name="zcode", conversation_id="reopen-guard"))["id"]

        async def confirm_native(kind, choice):
            q = await call("interaction_request", session_id=sid, kind=kind, recommendation=[choice])
            assert q["status"] == "NATIVE_QUESTION_REQUIRED", q
            res = await call("interaction_submit", session_id=sid, question_id=q["question_id"],
                             action="accept", choices=[choice], source="native")
            assert res["status"] == "CONFIRMED"

        await confirm_native("mode", "long")
        await confirm_native("verification", "targeted")
        await confirm_native("codex_gate", "packages")
        await confirm_native("review_route", "gpt")

        # Reopen mode -> mode is now None; review choices are paused, not exposed.
        reopened = await call("interaction_reopen", session_id=sid, kind="mode", explicit_user_change=True)
        assert reopened["status"] == "REOPENED"

        for kind in ("codex_gate", "review_route", "codex_review"):
            blocked = await call("interaction_reopen", session_id=sid, kind=kind, explicit_user_change=True)
            assert blocked["status"] == "ERROR" and blocked["error_code"] == "QUESTION_ORDER"
            assert "previous_question_id" not in blocked

        # Re-confirming long restores both codex_gate and review_route intact.
        await confirm_native("mode", "long")
        snap = await call("session_get", session_id=sid)
        assert snap["mode"] == "long"
        assert snap["codex_gate"] == "packages"
        assert snap["review_route"] == "gpt"
        assert snap["intake"]["ready"] is True

    # Short mode with a DB-injected stale review row must also refuse reopen without leaking ID.
    state = State(Store(tmp_path))
    short_sid = state.open(str(tmp_path), "zcode", "short-reopen")["id"]
    _confirm(state, short_sid, "mode", ["standard"], "native")
    with state.store.transaction(True) as con:
        con.execute("INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("secret-stale-id", short_sid, "review_route", 1, "CONFIRMED", "native", '["gpt"]', '["gpt"]', "old", "old"))
    with pytest.raises(ScsError) as err:
        state.reopen(short_sid, "review_route", explicit_user_change=True)
    assert err.value.code == "QUESTION_NOT_FOUND"
    assert "secret-stale-id" not in str(err.value)


async def test_board_list_survives_foreign_and_local_overflow_version_and_bad_types(tmp_path):
    """P1: foreign corrupt files with version=1e999 or invalid field types must not crash board_task_list."""
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    board_dir = tmp_path / "board"
    raw_board = Board(board_dir)
    valid = raw_board.add_task(str(allowed), "Valid local task", session_id="s1")

    # Foreign adversarial files: exact Codex counterexample (version: 1e999) + bad types.
    for stem, version_raw, extra in [
        ("T-foreign-inf", "1e999", ""),
        ("T-foreign-bool", "true", ""),
        ("T-foreign-float", "1.5", ""),
        ("T-foreign-neg", "-1", ""),
        ("T-foreign-huge", "9" * 5000, ""),
        ("T-foreign-state", "1", 'state: ["todo"]\n'),
    ]:
        (board_dir / "tasks" / f"{stem}.md").write_text(
            f'---\nid: "{stem}"\nproject_root: {json.dumps(str(outside))}\n'
            f'title: "Bad"\n{extra}version: {version_raw}\n---\n',
            encoding="utf-8",
        )
    # Duplicate project_root keys: first is allowed, last is outside — must resolve as outside.
    (board_dir / "tasks" / "T-dup-foreign.md").write_text(
        f'---\nid: "T-dup-foreign"\nproject_root: {json.dumps(str(allowed))}\n'
        f'project_root: {json.dumps(str(outside))}\ntitle: "Dup"\nversion: 1e999\n---\n',
        encoding="utf-8",
    )
    # Local corrupt file with version: 1e999 must be reported in errors without crashing.
    (board_dir / "tasks" / "T-local-inf.md").write_text(
        f'---\nid: "T-local-inf"\nproject_root: {json.dumps(str(allowed))}\n'
        'title: "Local inf"\nversion: 1e999\n---\n',
        encoding="utf-8",
    )

    async with Client(create_server(allowed, board_dir=board_dir)) as client:
        listed = (await client.call_tool("board_task_list", {"project_root": str(allowed)})).structured_content
        assert listed.get("status") != "ERROR", listed
        assert [t["id"] for t in listed["tasks"]] == [valid["id"]]
        assert any("T-local-inf.md" in e for e in listed["errors"])
        for stem in ("T-foreign-inf", "T-foreign-bool", "T-foreign-float",
                     "T-foreign-neg", "T-foreign-huge", "T-foreign-state", "T-dup-foreign"):
            assert not any(stem in e for e in listed["errors"])
        dup_get = (await client.call_tool("board_task_get", {"task_id": "T-dup-foreign"})).structured_content
        assert dup_get["status"] == "ERROR" and dup_get["error_code"] == "PROJECT_MISMATCH"


async def test_idempotent_replay_preserves_auto_refresh_response_without_rewriting_view(tmp_path, monkeypatch):
    """P2: replaying operation_id with auto_refresh enabled returns identical response and skips refresh."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("x = 1\n", encoding="utf-8")

    refresh_calls = []
    orig_refresh = ProjectView.refresh

    def spy_refresh(self, *args, **kwargs):
        refresh_calls.append(1)
        return orig_refresh(self, *args, **kwargs)

    monkeypatch.setattr(ProjectView, "refresh", spy_refresh)

    async with Client(create_server(tmp_path)) as client:
        async def call(name, **args):
            return (await client.call_tool(name, args)).structured_content

        sid = (await call("session_open", project_root=str(tmp_path), client_name="replay", conversation_id="r1", explicit_skip=True))["id"]
        await call("project_view_refresh", session_id=sid, initialize_vault=True, auto_refresh=True)
        pkg = await call("work_package_create", session_id=sid, goal="replay view", allowed_files=["src"],
                         acceptance=["done"], subtasks=[
                             {"id": "A", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["a"]},
                             {"id": "B", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["b"]},
                             {"id": "C", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["c"]},
                         ], request_key="replay-view")

        # 1. work_package_update with operation_id
        pkg1 = await call("work_package_update", session_id=sid, package_id=pkg["id"],
                          expected_version=pkg["version"], state="IN_PROGRESS", operation_id="op-pkg")
        assert pkg1["view_update"]["status"] == "OK" and pkg1["view_update"]["updated_files"] > 0
        refresh_calls.clear()
        pkg1_replay = await call("work_package_update", session_id=sid, package_id=pkg["id"],
                                 expected_version=pkg["version"], state="IN_PROGRESS", operation_id="op-pkg")
        assert pkg1_replay == pkg1
        assert refresh_calls == []

        # 2. work_attempt_update_many with operation_id
        prep = await call("work_attempt_prepare_many", session_id=sid, package_id=pkg["id"],
                          expected_version=pkg1["version"], subtask_ids=["A", "B"])
        ids = [entry["attempt"]["id"] for entry in prep["attempts"]]
        updates = [{"attempt_id": ids[0], "result": {"status": "NOT_RUN"}},
                   {"attempt_id": ids[1], "result": {"status": "NOT_RUN"}}]
        batch1 = await call("work_attempt_update_many", session_id=sid, updates=updates,
                            operation_id="op-batch", expected_version=pkg1["version"])
        assert batch1["view_update"]["status"] == "OK" and batch1["view_update"]["updated_files"] > 0
        refresh_calls.clear()
        batch1_replay = await call("work_attempt_update_many", session_id=sid, updates=updates,
                                   operation_id="op-batch", expected_version=pkg1["version"])
        assert batch1_replay == batch1
        assert refresh_calls == []

        # 3. work_attempt_update with operation_id
        prep_c = await call("work_attempt_prepare", session_id=sid, package_id=pkg["id"],
                            expected_version=batch1["version"], subtask_id="C")
        single1 = await call("work_attempt_update", session_id=sid, attempt_id=prep_c["attempt"]["id"],
                             expected_version=batch1["version"], state="ENDED",
                             result={"status": "NOT_RUN"}, operation_id="op-single")
        assert single1["view_update"]["status"] == "OK" and single1["view_update"]["updated_files"] > 0
        refresh_calls.clear()
        single1_replay = await call("work_attempt_update", session_id=sid, attempt_id=prep_c["attempt"]["id"],
                                    expected_version=batch1["version"], state="ENDED",
                                    result={"status": "NOT_RUN"}, operation_id="op-single")
        assert single1_replay == single1
        assert refresh_calls == []

    # 4. Replay survives a fresh server instance (persisted in work_operations).
    async with Client(create_server(tmp_path)) as fresh_client:
        sid_again = (await fresh_client.call_tool("session_open", {
            "project_root": str(tmp_path), "client_name": "replay", "conversation_id": "r1"})).structured_content["id"]
        assert sid_again == sid
        refresh_calls.clear()
        batch_after_restart = (await fresh_client.call_tool("work_attempt_update_many", {
            "session_id": sid, "updates": updates, "operation_id": "op-batch",
            "expected_version": pkg1["version"]})).structured_content
        assert batch_after_restart == batch1
        assert refresh_calls == []


async def test_board_large_event_log_over_10mib_remains_readable_with_bounded_memory(tmp_path):
    """P2: event logs exceeding 10 MiB remain readable via bounded tail streaming instead of TASK_CORRUPT."""
    project = tmp_path / "proj"
    project.mkdir()
    board_dir = tmp_path / "board"
    board = Board(board_dir)

    # Normal small task has events_truncated=False.
    small = board.add_task(str(project), "Small task", session_id="s1")
    assert board.get_task(small["id"])["events_truncated"] is False

    # Build a >10.5 MiB legitimate JSONL event log (~1100 events of ~10 KiB each).
    big = board.add_task(str(project), "Long-lived task", session_id="s1")
    ev_path = board_dir / "events" / f"{big['id']}.jsonl"
    padding = "x" * 10_000
    lines = []
    for idx in range(1100):
        lines.append(json.dumps({
            "at": "2026-11-24T00:00:00+00:00",
            "type": "comment",
            "actor": "tester",
            "session_id": "s1",
            "data": {"text": f"comment-{idx}-{padding}"},
        }, ensure_ascii=False) + "\n")
    lines.append(json.dumps({
        "at": "2026-11-24T00:01:00+00:00",
        "type": "comment",
        "actor": "tester",
        "session_id": "s1",
        "data": {"text": "LATEST_COMMENT_MARKER"},
    }, ensure_ascii=False) + "\n")
    with ev_path.open("a", encoding="utf-8") as handle:
        handle.writelines(lines)
    assert ev_path.stat().st_size > 10 * 1024 * 1024

    async with Client(create_server(project, board_dir=board_dir)) as client:
        got = (await client.call_tool("board_task_get", {"task_id": big["id"]})).structured_content
        assert got.get("status") != "ERROR", got
        task_data = got["task"]
        assert task_data["events_truncated"] is True
        assert 0 < len(task_data["events"]) <= 1000
        assert task_data["events"][-1]["data"]["text"] == "LATEST_COMMENT_MARKER"

        prompt = (await client.call_tool("board_task_prompt", {"task_id": big["id"]})).structured_content
        assert prompt.get("status") != "ERROR", prompt
        assert big["id"] in prompt["prompt"]

    # Oversized single line (>256 KiB) is safely skipped without OOM while keeping valid events.
    oversized = board.add_task(str(project), "Oversized line task", session_id="s1")
    ov_path = board_dir / "events" / f"{oversized['id']}.jsonl"
    with ov_path.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"comment","data":{"text":"' + ("A" * 300_000) + '"}}\n')
        handle.write(json.dumps({"at": "2026-11-24T00:02:00+00:00", "type": "comment",
                                 "actor": "tester", "session_id": "s1",
                                 "data": {"text": "AFTER_OVERSIZED"}}) + "\n")
    ov_got = board.get_task(oversized["id"])
    assert ov_got["events_truncated"] is True
    assert ov_got["events"][0]["type"] == "add"
    assert ov_got["events"][-1]["data"]["text"] == "AFTER_OVERSIZED"


def test_board_read_events_enforces_hard_byte_budget_and_version_cap(tmp_path, monkeypatch):
    """P2: event reading caps total bytes read even under concurrent appends; version never exceeds MAX_VERSION."""
    from scs_mcp import board as board_mod

    project = tmp_path / "proj"
    project.mkdir()
    board_dir = tmp_path / "board"
    board = Board(board_dir)
    task = board.add_task(str(project), "Bounded read", session_id="s1")

    # Simulate a concurrently growing event log that never reaches EOF on os.read.
    line = (json.dumps({"type": "comment", "data": {"text": "grow"}}) + "\n").encode("utf-8")
    chunk_template = (line * ((65536 // len(line)) + 1))[:65536]
    total_read = [0]

    def endless_read(fd, n):
        data = chunk_template[:n]
        total_read[0] += len(data)
        return data

    monkeypatch.setattr(os, "read", endless_read)
    with board._open_subdir("events") as ev_fd:
        events, truncated = board_mod._read_events_fd(ev_fd, f"{task['id']}.jsonl")
    assert truncated is True
    assert total_read[0] == board_mod.MAX_EVENT_TAIL_BYTES
    assert len(events) <= board_mod.MAX_EVENTS_RETURNED

    # Version at MAX_VERSION (1_000_000_000) is readable, but refusing to bump prevents writing 1_000_000_001.
    monkeypatch.undo()
    task_file = board_dir / "tasks" / f"{task['id']}.md"
    task_file.write_text(
        task_file.read_text(encoding="utf-8").replace("version: 1\n", f"version: {board_mod.MAX_VERSION}\n"),
        encoding="utf-8",
    )
    loaded = board.get_task(task["id"])
    assert loaded["version"] == board_mod.MAX_VERSION
    with pytest.raises(BoardError) as err:
        board.comment_task(task["id"], "tester", "over version cap")
    assert err.value.code == "TASK_CORRUPT"
    # Task stays readable at MAX_VERSION and was not overwritten with MAX_VERSION + 1.
    assert board.get_task(task["id"])["version"] == board_mod.MAX_VERSION
