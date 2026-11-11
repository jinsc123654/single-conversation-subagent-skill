"""Host question channels and the server-side form deadline."""

import json
import sys
from pathlib import Path

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import ElicitResult

SRC = str(Path(__file__).parents[1] / "src")


def server_params(tmp_path, channel="mcp", host="zcode"):
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "scs_mcp", "--project-root", str(tmp_path)],
        env={"PYTHONPATH": SRC, "SCS_QUESTION_CHANNEL": channel, "SCS_HOST": host},
    )


async def open_session(client):
    result = await client.call_tool("session_open", {"client_name": "timeout-test"})
    return result.structured_content


async def submit_mode(client, session_id, question_id, choice="standard"):
    return (await client.call_tool(
        "interaction_submit",
        {"session_id": session_id, "question_id": question_id, "action": "accept", "choices": [choice]},
    )).structured_content


async def test_timeout_returns_recommendation(tmp_path):
    async def slow(_context, _params):
        await anyio.sleep(5)
        return ElicitResult(action="accept", content={"choice": "standard"})

    async with Client(
        server_params(tmp_path, "mcp", "zcode"),
        mode="legacy",
        elicitation_callback=slow,
        read_timeout_seconds=10,
    ) as client:
        opened = await open_session(client)
        assert opened["question_channel"] == "mcp"
        assert opened["host"] == "zcode"
        result = await client.call_tool(
            "interaction_ask",
            {
                "session_id": opened["id"],
                "kind": "mode",
                "recommendation": ["standard"],
                "timeout_seconds": 0.2,
            },
        )
        data = result.structured_content
        assert data["status"] == "TIMED_OUT"
        assert data["error_code"] == "HOST_OBSERVED_TIMEOUT"
        assert data["recommendation"] == ["standard"]
        assert data["destructive_operations_authorized"] is False


async def test_answer_still_confirms_before_deadline(tmp_path):
    async def fast(_context, _params):
        return ElicitResult(action="accept", content={"choice": "investigate"})

    async with Client(
        server_params(tmp_path, "mcp", "zcode"),
        mode="legacy",
        elicitation_callback=fast,
        read_timeout_seconds=10,
    ) as client:
        opened = await open_session(client)
        result = await client.call_tool(
            "interaction_ask",
            {
                "session_id": opened["id"],
                "kind": "mode",
                "recommendation": ["investigate"],
                "timeout_seconds": 2,
            },
        )
        data = result.structured_content
        assert data["status"] == "CONFIRMED"
        assert data["choices"] == ["investigate"]


async def test_modern_fallback_keeps_question_for_submit(tmp_path):
    async def fast(_context, _params):
        return ElicitResult(action="accept", content={"choice": "standard"})

    async with Client(
        server_params(tmp_path, "mcp", "codex"),
        mode="auto",
        elicitation_callback=fast,
        read_timeout_seconds=10,
    ) as client:
        opened = await open_session(client)
        result = await client.call_tool(
            "interaction_ask",
            {
                "session_id": opened["id"],
                "kind": "mode",
                "recommendation": ["standard"],
                "timeout_seconds": 1,
            },
        )
        data = result.structured_content
        assert data["status"] == "TEXT_FALLBACK_REQUIRED"
        assert data["fallback_reason"] == "MODERN_NO_BACKCHANNEL"
        assert data["question_id"]
        submitted = await submit_mode(client, opened["id"], data["question_id"])
        assert submitted["status"] == "CONFIRMED"
        assert submitted["choices"] == ["standard"]


async def test_legacy_no_form_keeps_question_for_submit(tmp_path):
    async with Client(server_params(tmp_path, "mcp", "codex"), mode="legacy", read_timeout_seconds=10) as client:
        opened = await open_session(client)
        result = await client.call_tool(
            "interaction_ask",
            {
                "session_id": opened["id"],
                "kind": "mode",
                "recommendation": ["standard"],
                "timeout_seconds": 1,
            },
        )
        data = result.structured_content
        assert data["status"] == "TEXT_FALLBACK_REQUIRED"
        assert data["fallback_reason"] == "ELICITATION_UNSUPPORTED"
        assert data["question_id"]
        submitted = await submit_mode(client, opened["id"], data["question_id"])
        assert submitted["status"] == "CONFIRMED"
        assert submitted["choices"] == ["standard"]


async def test_native_channel_keeps_question_for_submit(tmp_path):
    async with Client(server_params(tmp_path, "native", "codex"), mode="legacy", read_timeout_seconds=10) as client:
        opened = await open_session(client)
        assert opened["question_channel"] == "native"
        result = await client.call_tool(
            "interaction_ask",
            {
                "session_id": opened["id"],
                "kind": "mode",
                "recommendation": ["standard"],
                "timeout_seconds": 15,
            },
        )
        data = result.structured_content
        assert data["status"] == "NATIVE_QUESTION_REQUIRED"
        assert data["question_id"]
        submitted = await submit_mode(client, opened["id"], data["question_id"])
        assert submitted["status"] == "CONFIRMED"


async def test_text_channel_keeps_question_for_submit(tmp_path):
    async with Client(server_params(tmp_path, "text", "generic"), mode="legacy", read_timeout_seconds=10) as client:
        opened = await open_session(client)
        assert opened["question_channel"] == "text"
        result = await client.call_tool(
            "interaction_ask",
            {
                "session_id": opened["id"],
                "kind": "mode",
                "recommendation": ["standard"],
                "timeout_seconds": 15,
            },
        )
        data = result.structured_content
        assert data["status"] == "TEXT_FALLBACK_REQUIRED"
        assert data["fallback_reason"] == "TEXT_REQUESTED"
        assert data["question_id"]
        submitted = await submit_mode(client, opened["id"], data["question_id"])
        assert submitted["status"] == "CONFIRMED"


async def test_host_namespaces_do_not_collide(tmp_path):
    async with (
        Client(server_params(tmp_path, "native", "codex"), mode="legacy", read_timeout_seconds=10) as codex,
        Client(server_params(tmp_path, "native", "zcode"), mode="legacy", read_timeout_seconds=10) as zcode,
    ):
        first = (await codex.call_tool("session_open", {"conversation_id": "same"})).structured_content
        first_again = (await codex.call_tool("session_open", {"conversation_id": "same"})).structured_content
        second = (await zcode.call_tool("session_open", {"conversation_id": "same"})).structured_content
        assert first["id"] == first_again["id"]
        assert first["id"] != second["id"]
        assert first["host"] == "codex"
        assert second["host"] == "zcode"


async def test_host_client_pair_hash_is_unambiguous(tmp_path):
    async with (
        Client(server_params(tmp_path, "native", "codex"), mode="legacy", read_timeout_seconds=10) as first_client,
        Client(server_params(tmp_path, "native", "codex-dev"), mode="legacy", read_timeout_seconds=10) as second_client,
    ):
        first = (await first_client.call_tool(
            "session_open", {"client_name": "dev-ui", "conversation_id": "same"}
        )).structured_content
        second = (await second_client.call_tool(
            "session_open", {"client_name": "ui", "conversation_id": "same"}
        )).structured_content
        assert first["id"] != second["id"]


async def test_rpc_error_marks_question_failed(tmp_path):
    async def broken(_context, _params):
        raise RuntimeError("broken form")

    async with Client(
        server_params(tmp_path, "mcp", "zcode"),
        mode="legacy",
        elicitation_callback=broken,
        read_timeout_seconds=10,
    ) as client:
        opened = await open_session(client)
        result = await client.call_tool(
            "interaction_ask",
            {
                "session_id": opened["id"],
                "kind": "mode",
                "recommendation": ["standard"],
                "timeout_seconds": 2,
            },
        )
        data = result.structured_content
        assert data["status"] == "TEXT_FALLBACK_REQUIRED"
        assert data["fallback_reason"] == "RPC_ERROR"
        assert data["question_id"]
        snapshot = (await client.call_tool("session_get", {"session_id": opened["id"]})).structured_content
        failed = [q for q in snapshot["questions"] if q["id"] == data["question_id"]]
        assert failed and failed[0]["state"] == "FAILED"


async def test_analysis_and_title_reach_the_form(tmp_path):
    captured = {}

    async def capture(_context, params):
        captured["params"] = params
        return ElicitResult(action="accept", content={"choice": "standard"})

    async with Client(
        server_params(tmp_path, "mcp", "zcode"),
        mode="legacy",
        elicitation_callback=capture,
        read_timeout_seconds=10,
    ) as client:
        opened = await open_session(client)
        result = await client.call_tool(
            "interaction_ask",
            {
                "session_id": opened["id"],
                "kind": "mode",
                "recommendation": ["standard"],
                "timeout_seconds": 15,
                "analysis": "推荐 standard：范围小，先做冒烟。",
            },
        )
        assert result.structured_content["status"] == "CONFIRMED"
        params = captured["params"]
        if isinstance(params, dict):
            schema = params.get("requested_schema") or params.get("requestedSchema")
        else:
            schema = getattr(params, "requested_schema", None) or getattr(params, "requestedSchema", None)
        if hasattr(schema, "model_dump"):
            schema = schema.model_dump()
        text = json.dumps(schema, ensure_ascii=False)
        assert "请选择本对话执行模式" in text
        assert "分析：推荐 standard" in text
        assert "standard=标准执行（推荐）" in text
        assert "15 秒未选择按推荐项继续" in text
        assert "question_id=" in text
