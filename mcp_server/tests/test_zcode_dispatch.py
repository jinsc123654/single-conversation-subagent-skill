"""Native ZCode dispatch hints are advisory and preserve existing prepare behavior."""

import json

import pytest
from mcp import Client

from scs_mcp.server import create_server
from scs_mcp.storage import Store, encode
from scs_mcp.work import Work


CODE = "ZCODE_DISPATCH_PREFLIGHT_REQUIRED"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SCS_NATIVE_REVIEW", "1")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/sample.txt").write_text("sample\n", encoding="utf-8")
    work = Work(Store(tmp_path))
    sid = work.state.open(str(tmp_path), "zcode", "dispatch", explicit_skip=True)["id"]
    return work, sid


def package(work, sid, key, tasks=(("check", "analyze"),)):
    return work.create(sid, "Dispatch hint", ["src"], ["Checked"], [
        {"id": task_id, "kind": kind, "allowed_files": ["src"], "acceptance": ["Checked"]}
        for task_id, kind in tasks
    ], key)


def ended(work, sid, key, host, kind="analyze"):
    pkg = package(work, sid, key, (("check", kind),))
    prepared = work.prepare_attempt(sid, pkg["id"], pkg["version"], "check", host_agent_id=host)
    work.update_attempt(sid, prepared["attempt"]["id"], pkg["version"], "ENDED", {"status": "NOT_RUN"})
    return prepared["attempt"]


def hint(prepared):
    return next(warning for warning in prepared["warnings"] if warning["code"] == CODE)


@pytest.mark.parametrize("kind,role", [
    ("analyze", "scs-scout"), ("implement", "scs-implementer"),
    ("test", "scs-test-verifier"), ("review", "scs-fixed-reviewer"),
])
def test_single_prepare_returns_advisory_role_hint(env, kind, role):
    work, sid = env
    pkg = package(work, sid, kind, (("check", kind),))
    prepared = work.prepare_attempt(sid, pkg["id"], pkg["version"], "check", user_requested=kind == "review")
    warning = hint(prepared)
    assert prepared["version"] == pkg["version"]
    assert prepared["attempt"]["state"] == "PREPARED"
    assert prepared["attempt"]["host_agent_id"] is None
    assert warning["agent_type"] == prepared["agent_type"] == role
    assert warning["candidates"] == [] and warning["candidates_truncated"] is False
    assert "空列表不证明" in warning["candidate_notice"]
    assert "不证明宿主 completed" in warning["candidate_notice"]
    assert not {"decision", "compatible", "projected_percent"} & warning.keys()


def test_required_checks_use_target_child_capacity_not_parent_or_billing(env):
    work, sid = env
    pkg = package(work, sid, "checks")
    warning = hint(work.prepare_attempt(sid, pkg["id"], pkg["version"], "check"))
    checks = "\n".join(warning["required_checks"])
    assert "parent 仅核验身份归属" in checks
    assert "ListModels（仅提供模型 catalog）" in checks
    assert "未核查不等于 UNKNOWN" in checks
    assert "目标子 Agent" in checks
    assert "input_tokens + output_tokens + reserve" in checks
    assert "该子 Agent 实际 provider/model 的上下文窗口" in checks
    assert "不得使用主对话 token、累计账单或 metadata.totalTokens" in checks
    assert "dispatch_decision" in checks
    assert "新 attempt 和租约" in checks
    assert "不授权" in warning["message"]


def test_candidates_cross_packages_deduplicate_and_use_controller_session(env):
    work, sid = env
    ended(work, sid, "older", "same-agent")
    newest = ended(work, sid, "newer", "same-agent")
    ended(work, sid, "other-role", "test-agent", kind="test")
    for index, host in enumerate((None, "", "   ")):
        ended(work, sid, f"empty-{index}", host)

    other = work.state.open(str(work.store.root), "zcode", "other", explicit_skip=True)["id"]
    foreign = ended(work, other, "foreign", "foreign-agent")
    controlled = ended(work, other, "controlled", "controlled-agent")
    legacy = ended(work, sid, "legacy", "untyped-agent")
    with work.store.transaction(True) as con:
        # Package ownership is not the host/controller identity.
        con.execute("UPDATE work_attempts SET controller_session=? WHERE id=?", (sid, controlled["id"]))
        snapshot = dict(legacy["input_snapshot"])
        snapshot.pop("agent_type")
        con.execute("UPDATE work_attempts SET input_snapshot=? WHERE id=?", (encode(snapshot), legacy["id"]))

    pkg = package(work, sid, "target")
    warning = hint(work.prepare_attempt(sid, pkg["id"], pkg["version"], "check"))
    candidates = {candidate["host_agent_id"]: candidate for candidate in warning["candidates"]}
    assert set(candidates) == {"same-agent", "controlled-agent"}
    assert candidates["same-agent"]["attempt_id"] == newest["id"]
    assert candidates["same-agent"]["package_id"] == newest["package_id"] != pkg["id"]
    assert candidates["controlled-agent"]["package_id"] == controlled["package_id"]
    assert all(candidate["controller_session"] == sid and candidate["state"] == "ENDED"
               for candidate in candidates.values())
    assert foreign["host_agent_id"] not in candidates
    assert all(not {"completed", "compatible", "decision"} & candidate.keys()
               for candidate in candidates.values())


@pytest.mark.parametrize("state", ["PREPARED", "LAUNCHING", "RUNNING", "DRAINING", "UNKNOWN"])
def test_candidates_exclude_agent_with_any_active_attempt_even_in_other_session(env, state):
    work, sid = env
    ended(work, sid, "history", "busy-agent")
    other = work.state.open(str(work.store.root), "zcode", "active-other", explicit_skip=True)["id"]
    busy = package(work, other, "busy")
    active = work.prepare_attempt(other, busy["id"], busy["version"], "check")
    aid = active["attempt"]["id"]
    work.update_attempt(other, aid, busy["version"], "PREPARED", host_agent_id="busy-agent")
    if state != "PREPARED":
        work.update_attempt(other, aid, busy["version"], "LAUNCHING")
        if state != "LAUNCHING":
            work.update_attempt(other, aid, busy["version"], state)

    target = package(work, sid, "target")
    warning = hint(work.prepare_attempt(sid, target["id"], target["version"], "check"))
    assert warning["candidates"] == []


def test_candidate_list_is_bounded(env):
    work, sid = env
    for index in range(10):
        ended(work, sid, f"history-{index}", f"agent-{index}")
    pkg = package(work, sid, "bounded")
    warning = hint(work.prepare_attempt(sid, pkg["id"], pkg["version"], "check"))
    assert len(warning["candidates"]) == warning["candidate_limit"] == 8
    assert warning["candidates_truncated"] is True
    assert len({candidate["host_agent_id"] for candidate in warning["candidates"]}) == 8


def test_batch_hints_do_not_allocate_same_agent_or_change_version(env):
    work, sid = env
    history = ended(work, sid, "history", "available-agent")
    pkg = package(work, sid, "batch", (("left", "analyze"), ("right", "analyze"), ("test", "test")))
    prepared = work.prepare_attempts(sid, pkg["id"], pkg["version"], ["left", "right", "test"])
    assert prepared["version"] == work.get(sid, pkg["id"])["version"] == pkg["version"]
    warnings = [hint(item) for item in prepared["attempts"]]
    assert [warning["agent_type"] for warning in warnings] == ["scs-scout", "scs-scout", "scs-test-verifier"]
    assert [warning["candidates"][0]["attempt_id"] for warning in warnings[:2]] == [history["id"]] * 2
    assert warnings[2]["candidates"] == []
    assert all(item["attempt"]["host_agent_id"] is None and item["attempt"]["state"] == "PREPARED"
               for item in prepared["attempts"])
    assert all("同批不得把同一 Agent 分给并发任务" in "\n".join(warning["required_checks"])
               for warning in warnings)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("batch", [False, True])
def test_original_warning_is_retained_and_non_native_prepare_unchanged(tmp_path, monkeypatch, native, batch):
    monkeypatch.setenv("SCS_NATIVE_REVIEW", "1" if native else "0")
    work = Work(Store(tmp_path))
    sid = work.state.open(str(tmp_path), "host", "warnings", explicit_skip=True)["id"]
    pkg = package(work, sid, "legacy-read")
    with work.store.transaction(True) as con:
        data = json.loads(con.execute("SELECT data FROM work_packages WHERE id=?", (pkg["id"],)).fetchone()[0])
        data["subtasks"][0]["resources"] = [{"resource": "file:src", "mode": "read"}]
        con.execute("UPDATE work_packages SET data=? WHERE id=?", (encode(data), pkg["id"]))
    if batch:
        prepared = work.prepare_attempts(sid, pkg["id"], pkg["version"], ["check"])["attempts"][0]
    else:
        prepared = work.prepare_attempt(sid, pkg["id"], pkg["version"], "check")
    assert prepared["leases"] == []
    expected = ["READ_LEASE_SKIPPED", CODE] if native else ["READ_LEASE_SKIPPED"]
    assert [warning["code"] for warning in prepared["warnings"]] == expected
    if not native:
        assert "agent_type" not in prepared and "agent_type" not in prepared["input_snapshot"]


@pytest.mark.parametrize("native", [False, True])
async def test_session_hints_cover_advisor_and_closure_without_candidate_reads(tmp_path, monkeypatch, native):
    monkeypatch.setenv("SCS_NATIVE_REVIEW", "1" if native else "0")
    server = create_server(tmp_path, host="zcode" if native else "generic", board_dir=tmp_path / "board")
    async with Client(server, mode="legacy") as client:
        opened = (await client.call_tool("session_open", {"conversation_id": "session-hints", "explicit_skip": True})).structured_content
        with server.store.transaction() as con:
            before = con.execute("SELECT count(*) FROM events").fetchone()[0]
        # Session hints must not query or assemble attempt candidates.
        monkeypatch.setattr(Work, "_dispatch_warning", lambda *args: pytest.fail("session hint queried candidates"))
        fetched = (await client.call_tool("session_get", {"session_id": opened["id"]})).structured_content
        assert opened["native_review"] == fetched["native_review"] == native
        assert opened["id"] == fetched["id"] and fetched["mode"] == "quick"
        if native:
            assert opened["warnings"] == fetched["warnings"]
            warning = hint(fetched)
            assert "顾问" in warning["message"] and "收尾" in warning["message"]
            assert "candidates" not in warning and "agent_type" not in warning
            assert warning["required_checks"]
        else:
            assert "warnings" not in opened and "warnings" not in fetched
        with server.store.transaction() as con:
            assert con.execute("SELECT count(*) FROM events").fetchone()[0] == before
            assert con.execute("SELECT count(*) FROM work_attempts").fetchone()[0] == 0
