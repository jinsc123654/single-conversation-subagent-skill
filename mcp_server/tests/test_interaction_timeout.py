"""Server-side 15s timeout for the MCP form path."""

import sys
from pathlib import Path

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import ElicitResult

SRC = str(Path(__file__).parents[1] / "src")


def server_params(tmp_path):
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "scs_mcp", "--project-root", str(tmp_path)],
        env={"PYTHONPATH": SRC},
    )


async def open_session(client, root):
    result = await client.call_tool(
        "session_open",
        {"project_root": str(root), "client_name": "timeout-test"},
    )
    return result.structured_content["id"]


async def test_timeout_returns_recommendation(tmp_path):
    async def slow(_context, _params):
        await anyio.sleep(5)
        return ElicitResult(action="accept", content={"choice": "standard"})

    async with Client(
        server_params(tmp_path),
        mode="legacy",
        elicitation_callback=slow,
        read_timeout_seconds=10,
    ) as client:
        session_id = await open_session(client, tmp_path)
        result = await client.call_tool(
            "interaction_ask",
            {
                "session_id": session_id,
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
        server_params(tmp_path),
        mode="legacy",
        elicitation_callback=fast,
        read_timeout_seconds=10,
    ) as client:
        session_id = await open_session(client, tmp_path)
        result = await client.call_tool(
            "interaction_ask",
            {
                "session_id": session_id,
                "kind": "mode",
                "recommendation": ["investigate"],
                "timeout_seconds": 2,
            },
        )
        data = result.structured_content
        assert data["status"] == "CONFIRMED"
        assert data["choices"] == ["investigate"]


async def test_form_unsupported_returns_fallback(tmp_path):
    async with Client(server_params(tmp_path), mode="auto", read_timeout_seconds=10) as client:
        session_id = await open_session(client, tmp_path)
        result = await client.call_tool(
            "interaction_ask",
            {
                "session_id": session_id,
                "kind": "mode",
                "recommendation": ["standard"],
                "timeout_seconds": 1,
            },
        )
        data = result.structured_content
        assert data["status"] in {"TEXT_FALLBACK_REQUIRED", "ELICITATION_UNSUPPORTED"}
        assert data["destructive_operations_authorized"] is False


async def test_modern_protocol_reports_no_backchannel(tmp_path):
    async def fast(_context, _params):
        return ElicitResult(action="accept", content={"choice": "standard"})

    async with Client(
        server_params(tmp_path),
        mode="auto",
        elicitation_callback=fast,
        read_timeout_seconds=10,
    ) as client:
        session_id = await open_session(client, tmp_path)
        result = await client.call_tool(
            "interaction_ask",
            {
                "session_id": session_id,
                "kind": "mode",
                "recommendation": ["standard"],
                "timeout_seconds": 1,
            },
        )
        data = result.structured_content
        assert data["status"] == "TEXT_FALLBACK_REQUIRED"
        assert data["fallback_reason"] == "MODERN_NO_BACKCHANNEL"
