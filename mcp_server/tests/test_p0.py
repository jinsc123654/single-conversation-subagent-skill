import asyncio
from pathlib import Path

import pytest
from mcp import Client
from mcp.types import ElicitResult
from mcp.client.stdio import StdioServerParameters

from scs_mcp.server import create_server
from scs_mcp.state import State
from scs_mcp.storage import Store
from scs_mcp.errors import ScsError


@pytest.fixture
def state(tmp_path):
    return State(Store(tmp_path))


def open_session(state, chat="chat", **kw):
    return state.open(str(state.store.root), "test-host", chat, **kw)["id"]


def test_conversations_isolate_and_temp_does_not_reuse(state):
    a = open_session(state)
    assert open_session(state) == a
    assert open_session(state, "second") != a
    assert open_session(state, None) != open_session(state, None)
    assert state.get(a)["question_states"]["mode"] == "NOT_ASKED"
    q = state.request(a, "mode", ["quick"], "text")
    state.submit(a, q["id"], "accept", ["quick"])
    assert state.get(open_session(state, "second"))["mode"] is None
    assert state.request(a, "mode", ["long"], "auto")["id"] == q["id"]
    assert state.request(a, "verification", ["none"], "text")["status"] == "SKIPPED"


@pytest.mark.parametrize("action,target", [("decline", "DECLINED"), ("cancel", "CANCELLED"),
    ("timeout", "TIMED_OUT"), ("unsupported", "UNSUPPORTED"), ("error", "FAILED")])
def test_nonaccept_does_not_authorize(state, action, target):
    sid = open_session(state)
    q = state.request(sid, "mode", ["standard"], "text")
    out = state.submit(sid, q["id"], action, observed_timeout=action == "timeout")
    assert out["status"] == target
    assert state.get(sid)["mode"] is None
    state.reopen(sid, "mode")
    q2 = state.request(sid, "mode", ["quick"], "text")
    assert q2["id"] != q["id"]
    with pytest.raises(ScsError, match="replaced"):
        state.submit(sid, q["id"], "accept", ["standard"])


def test_idempotent_replies_and_conflicts(state):
    sid = open_session(state)
    q = state.request(sid, "mode", ["standard"], "text")
    state.submit(sid, q["id"], "accept", ["long"])
    assert state.submit(sid, q["id"], "accept", ["long"])["idempotent"]
    with pytest.raises(ScsError):
        state.submit(sid, q["id"], "accept", ["quick"])
    with pytest.raises(ScsError):
        state.reopen(sid, "mode")
    state.reopen(sid, "mode", True)
    assert state.get(sid)["mode"] is None


def test_question_order_and_exclusion(state):
    sid = open_session(state)
    with pytest.raises(ScsError):
        state.request(sid, "verification", ["smoke"], "text")
    q = state.request(sid, "mode", ["standard"], "text")
    state.submit(sid, q["id"], "accept", ["standard"])
    with pytest.raises(ScsError):
        state.request(sid, "codex_review", ["no"], "text")
    q = state.request(sid, "verification", ["smoke", "targeted"], "text")
    for choices in (["none", "smoke"], ["none", "method-only"], ["invalid"], []):
        with pytest.raises(ScsError):
            state.submit(sid, q["id"], "accept", choices)
    state.submit(sid, q["id"], "accept", ["method-only"])
    assert state.request(sid, "codex_review", ["no"], "text")["status"] == "SKIPPED"
    open_session(state, review_registration="registered")
    q = state.request(sid, "codex_review", ["no"], "text")
    assert q["kind"] == "codex_review"


def test_sleep_is_not_timeout(state):
    sid = open_session(state)
    q = state.request(sid, "mode", ["quick"], "text")
    with pytest.raises(ScsError):
        state.submit(sid, q["id"], "timeout")
    assert state.get(sid)["mode"] is None


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_sdk_elicitation_both_protocol_eras(tmp_path, mode):
    seen = []
    async def answer(ctx, params):
        seen.append(params)
        return ElicitResult(action="accept", content={"choice": "long"})
    async with Client(create_server(tmp_path), mode=mode, elicitation_callback=answer) as client:
        sid = (await client.call_tool("session_open", {"project_root": str(tmp_path), "client_name": "sdk", "conversation_id": "c"})).structured_content["id"]
        result = await client.call_tool("interaction_request", {"session_id": sid, "kind": "mode", "recommendation": ["standard"]})
        assert result.structured_content["status"] == "CONFIRMED", result
        assert result.structured_content["choices"] == ["long"]
        again = await client.call_tool("interaction_request", {"session_id": sid, "kind": "mode", "recommendation": ["standard"]})
        assert again.structured_content["status"] == "ALREADY_CONFIRMED"
        assert len(seen) == 1
        assert seen[0].requested_schema["properties"]["choice"]["enum"] == [
            "quick — 快速处理",
            "standard — 标准执行（推荐）",
            "long — 长任务",
            "investigate — 只调查",
        ]
        assert seen[0].requested_schema["required"] == ["choice"]
        diagnostics = (await client.call_tool("diagnostics_check", {"session_id": sid})).structured_content
        assert diagnostics["protocol_version"] == ("2025-11-25" if mode == "legacy" else "2026-07-28")


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_unsupported_text_fallback_and_explicit_reply(tmp_path, mode):
    async with Client(create_server(tmp_path), mode=mode) as client:
        sid = (await client.call_tool("session_open", {"project_root": str(tmp_path), "client_name": "sdk"})).structured_content["id"]
        result = (await client.call_tool("interaction_request", {"session_id": sid, "kind": "mode", "recommendation": ["quick"]})).structured_content
        assert result["status"] == "TEXT_FALLBACK_REQUIRED"
        assert result["fallback_reason"] == "ELICITATION_UNSUPPORTED"
        assert len(result["options"]) == 4
        reply = (await client.call_tool("interaction_submit", {"session_id": sid, "question_id": result["question_id"], "action": "accept", "choices": ["quick"]})).structured_content
        assert reply["status"] == "CONFIRMED"
        assert not reply["destructive_operations_authorized"]


async def test_native_route_does_not_elicit(tmp_path):
    async def unexpected(*_):
        pytest.fail("Native and MCP UI must not be opened simultaneously")
    async with Client(create_server(tmp_path), elicitation_callback=unexpected) as client:
        sid = (await client.call_tool("session_open", {"project_root": str(tmp_path), "client_name": "sdk"})).structured_content["id"]
        args = {"session_id": sid, "kind": "mode", "recommendation": ["quick"], "channel": "native"}
        result = (await client.call_tool("interaction_request", args)).structured_content
        assert result["status"] == "NATIVE_QUESTION_REQUIRED"
        again = (await client.call_tool("interaction_request", args)).structured_content
        assert again["question_id"] == result["question_id"]
        assert (await client.call_tool("session_get", {"session_id": sid})).structured_content["mode"] is None


async def test_real_stdio_discovery_and_no_stdout_noise(tmp_path):
    import os
    import sys
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / "src"))
    params = StdioServerParameters(command=sys.executable, args=["-m", "scs_mcp", "--project-root", str(tmp_path)], env=env)
    async with Client(params) as client:
        tools = await client.list_tools()
        assert {"session_open", "session_get", "interaction_request", "interaction_submit", "diagnostics_check"} <= {t.name for t in tools.tools}
        result = await client.call_tool("diagnostics_check", {})
        assert result.structured_content["database"] == "ok"
        invalid = await client.call_tool("session_get", {"session_id": "missing"})
        assert invalid.structured_content["error_code"] == "SESSION_NOT_FOUND"

@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("action", ["decline", "cancel"])
async def test_sdk_cancel_decline_do_not_confirm(tmp_path, mode, action):
    async def answer(ctx, params):
        return ElicitResult(action=action)
    async with Client(create_server(tmp_path), mode=mode, elicitation_callback=answer) as client:
        sid = (await client.call_tool("session_open", {"project_root": str(tmp_path), "client_name": "sdk"})).structured_content["id"]
        out = (await client.call_tool("interaction_request", {"session_id": sid, "kind": "mode", "recommendation": ["long"]})).structured_content
        assert out["status"] == {"cancel": "CANCELLED", "decline": "DECLINED"}[action]
        assert (await client.call_tool("session_get", {"session_id": sid})).structured_content["mode"] is None


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("broken", ["rpc", "empty", "invalid"])
async def test_rpc_error_and_malformed_accept_fallback_never_timeout(tmp_path, mode, broken):
    from mcp.shared.exceptions import MCPError
    async def answer(ctx, params):
        if broken == "rpc":
            raise MCPError(code=-32603, message="secret-private-url-do-not-log")
        return ElicitResult(action="accept", content={} if broken == "empty" else {"choice": "made-up"})
    async with Client(create_server(tmp_path), mode=mode, elicitation_callback=answer) as client:
        sid = (await client.call_tool("session_open", {"project_root": str(tmp_path), "client_name": "sdk"})).structured_content["id"]
        args = {"session_id": sid, "kind": "mode", "recommendation": ["long"]}
        if mode == "auto" and broken == "rpc":
            # Modern callbacks run in the client. A client crash before the retry
            # is invisible to the server: the Skill/host must report the failure.
            with pytest.raises(ExceptionGroup):
                await client.call_tool("interaction_request", args)
            before = (await client.call_tool("session_get", {"session_id": sid})).structured_content
            assert before["questions"][0]["state"] == "WAITING_USER"
            await client.call_tool("interaction_submit", {"session_id": sid, "question_id": before["questions"][0]["id"], "action": "error"})
        out = (await client.call_tool("interaction_request", args)).structured_content
        assert out["status"] == "TEXT_FALLBACK_REQUIRED"
        assert out["fallback_reason"] == "RPC_ERROR"
        session = (await client.call_tool("session_get", {"session_id": sid})).structured_content
        assert session["mode"] is None
        assert session["questions"][0]["state"] == "FAILED"
        events = (await client.call_tool("diagnostics_check", {"session_id": sid})).structured_content["events"]
        assert "secret-private" not in str(events)
        assert not any(e["event"] == "TIMED_OUT" for e in events)


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_all_three_questions_multiselect_and_registered_review(tmp_path, mode):
    answers = iter(["standard", ["smoke", "targeted"], "yes"])
    seen = []
    async def answer(ctx, params):
        seen.append(params)
        return ElicitResult(action="accept", content={"choice": next(answers)})
    async with Client(create_server(tmp_path), mode=mode, elicitation_callback=answer) as client:
        sid = (await client.call_tool("session_open", {"project_root": str(tmp_path), "client_name": "sdk", "review_registration": "registered"})).structured_content["id"]
        for kind, rec in [("mode", ["standard"]), ("verification", ["smoke", "targeted"]), ("codex_review", ["no"])]:
            out = (await client.call_tool("interaction_request", {"session_id": sid, "kind": kind, "recommendation": rec})).structured_content
            assert out["status"] == "CONFIRMED", out
        assert seen[1].requested_schema["properties"]["choice"]["type"] == "array"
        assert seen[1].requested_schema["properties"]["choice"]["items"]["enum"]
        session = (await client.call_tool("session_get", {"session_id": sid})).structured_content
        assert session["verification"] == ["smoke", "targeted"] and session["codex_review"] is True


async def test_late_native_or_legacy_reply_cannot_cancel_new_question(tmp_path):
    server = create_server(tmp_path)
    ready = asyncio.Event()
    release = asyncio.Event()
    async def answer(ctx, params):
        ready.set()
        await release.wait()
        return ElicitResult(action="cancel")
    async with Client(server, mode="legacy", elicitation_callback=answer) as client:
        sid = (await client.call_tool("session_open", {"project_root": str(tmp_path), "client_name": "sdk"})).structured_content["id"]
        task = asyncio.create_task(client.call_tool("interaction_request", {"session_id": sid, "kind": "mode", "recommendation": ["standard"]}))
        await asyncio.wait_for(ready.wait(), 3)
        server.state.reopen(sid, "mode", True)
        new = server.state.request(sid, "mode", ["quick"], "text")
        release.set()
        out = (await task).structured_content
        assert out["error_code"] == "LATE_REPLY"
        assert server.state.question(sid, new["id"])["state"] == "REQUESTED"


async def test_p0_bad_inputs_are_structured_and_do_not_leak(tmp_path):
    async with Client(create_server(tmp_path)) as client:
        assert (await client.call_tool("session_open", {"project_root": str(tmp_path.parent), "client_name": "sdk"})).structured_content["error_code"] == "PROJECT_MISMATCH"
        for name, args in [("session_get", {"session_id": "missing"}),
                           ("diagnostics_check", {"session_id": "missing"}),
                           ("interaction_request", {"session_id": "missing", "kind": "mode", "recommendation": ["quick"]}),
                           ("interaction_submit", {"session_id": "missing", "question_id": "missing", "action": "cancel"})]:
            out = (await client.call_tool(name, args)).structured_content
            assert out["status"] == "ERROR"
        sid = (await client.call_tool("session_open", {"project_root": str(tmp_path), "client_name": "sdk", "explicit_skip": True})).structured_content["id"]
        assert (await client.call_tool("session_get", {"session_id": sid})).structured_content["mode"] == "quick"
        out = (await client.call_tool("interaction_request", {"session_id": sid, "kind": "mode", "recommendation": ["bad"]})).structured_content
        assert out["error_code"] == "CHOICE_INVALID"


def test_reopen_mode_supersedes_unfinished_followup(state):
    sid = open_session(state)
    q = state.request(sid, "mode", ["long"], "text")
    state.submit(sid, q["id"], "accept", ["long"])
    verification = state.request(sid, "verification", ["smoke"], "text")
    state.reopen(sid, "mode", True)
    q = state.request(sid, "mode", ["quick"], "text")
    state.submit(sid, q["id"], "accept", ["quick"])
    with pytest.raises(ScsError) as err:
        state.submit(sid, verification["id"], "accept", ["smoke"])
    assert err.value.code == "LATE_REPLY"


@pytest.mark.parametrize("declared", [None, "empty", "form", "url"])
def test_capabilities_are_actual_not_client_name(declared):
    from mcp.types import ClientCapabilities, ElicitationCapability, FormElicitationCapability, UrlElicitationCapability
    from scs_mcp.interaction import supports_form
    cap = {None: None, "empty": ElicitationCapability(), "form": ElicitationCapability(form=FormElicitationCapability()),
           "url": ElicitationCapability(url=UrlElicitationCapability())}[declared]
    assert supports_form(ClientCapabilities(elicitation=cap)) == (declared in {"empty", "form"})


def test_concurrent_question_claim_only_one_ui_request(state):
    from concurrent.futures import ThreadPoolExecutor
    sid = open_session(state)
    q = state.request(sid, "mode", ["standard"], "auto")
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = list(pool.map(lambda _: state.claim(sid, q["id"])[0], range(8)))
    assert claimed.count(True) == 1
    assert state.question(sid, q["id"])["state"] == "WAITING_USER"


def test_explicit_skip_can_later_enter_standard_in_same_conversation(state):
    sid = open_session(state, explicit_skip=True)
    assert state.get(sid)["mode"] == "quick"
    with pytest.raises(ScsError):
        state.reopen(sid, "mode")
    state.reopen(sid, "mode", explicit_user_change=True)
    q = state.request(sid, "mode", ["standard"], "text")
    state.submit(sid, q["id"], "accept", ["standard"])
    verification = state.request(sid, "verification", ["smoke"], "text")
    assert verification["kind"] == "verification"
    assert open_session(state) == sid
