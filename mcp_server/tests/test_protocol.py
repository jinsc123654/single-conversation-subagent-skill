"""Real independent stdio processes, including strict stdout framing."""

import json
import os
import select
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import ElicitResult

SRC = str(Path(__file__).parents[1] / "src")


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("supported", [True, False])
async def test_independent_stdio_question_and_session(mode, supported, tmp_path):
    seen = []
    async def answer(ctx, params):
        seen.append(params)
        return ElicitResult(action="accept", content={"choice": "investigate"})
    params = StdioServerParameters(command=sys.executable, args=["-m", "scs_mcp", "--project-root", str(tmp_path)],
                                   env={"PYTHONPATH": SRC})
    async with Client(params, mode=mode, elicitation_callback=answer if supported else None, read_timeout_seconds=5) as client:
        sid = (await client.call_tool("session_open", {"project_root": str(tmp_path), "client_name": "stdio-probe", "conversation_id": "one"})).structured_content["id"]
        out = (await client.call_tool("interaction_request", {"session_id": sid, "kind": "mode", "recommendation": ["standard"]})).structured_content
        assert out["status"] == ("CONFIRMED" if supported else "TEXT_FALLBACK_REQUIRED")
        if not supported:
            await client.call_tool("interaction_submit", {"session_id": sid, "question_id": out["question_id"], "action": "accept", "choices": ["investigate"]})
        out = (await client.call_tool("session_get", {"session_id": sid})).structured_content
        assert out["mode"] == "investigate"
        assert len(seen) == int(supported)
        assert (await client.call_tool("diagnostics_check", {})).structured_content["ui_observation"] == "UI_NOT_OBSERVABLE"


def test_stdout_contains_only_jsonrpc_frames(tmp_path):
    proc = subprocess.Popen([sys.executable, "-m", "scs_mcp", "--project-root", str(tmp_path)],
        env={**os.environ, "PYTHONPATH": SRC}, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1)
    frames = []
    def exchange(message):
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
        assert select.select([proc.stdout], [], [], 5)[0], "No stdout response within five seconds"
        frame = json.loads(proc.stdout.readline())
        frames.append(frame)
        assert frame["jsonrpc"] == "2.0" and frame["id"] == message["id"]
        return frame
    try:
        exchange({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "clientInfo": {"name": "raw-probe", "version": "1"}, "capabilities": {}}})
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()
        tools = exchange({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert "diagnostics_check" in {x["name"] for x in tools["result"]["tools"]}
        exchange({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "session_get", "arguments": {"session_id": "invalid"}}})
        exchange({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "diagnostics_check", "arguments": {}}})
        proc.stdin.close()
        proc.stdin = None
        tail, errors = proc.communicate(timeout=5)
        assert not tail.strip(), "Unexpected stdout noise or extra protocol frames"
        assert proc.returncode == 0, errors
        assert len(frames) == 4
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


async def test_full_reference_client_workflow(tmp_path):
    from scs_mcp.server import create_server
    async with Client(create_server(tmp_path)) as client:
        async def call(name, **args):
            result = await client.call_tool(name, args)
            assert result.structured_content is not None, result
            return result.structured_content
        sid = (await call("session_open", project_root=str(tmp_path), client_name="reference-host", conversation_id="one", explicit_skip=True))["id"]
        package = await call("work_package_create", session_id=sid, goal="Reference workflow", allowed_files=["src"], acceptance=["targeted test"],
            request_key="one", subtasks=[{"id": "T1", "kind": "test", "allowed_files": ["src/file.py"], "acceptance": ["exit 0"]}])
        assert package["status"] == "OK"
        pid = package["id"]
        lease = await call("resource_acquire", session_id=sid, resource="file:src/file.py")
        assert lease["status"] == "ACQUIRED"
        path = f"docs/agent/evidence/{pid}/R1/T1/results.json"
        (tmp_path / path).write_text(json.dumps({"exit_code": 0, "passed": 1, "command": "reference test fixture"}))
        evidence = await call("evidence_verify", session_id=sid, package_id=pid, revision=1, subtask_id="T1", path=path)
        assert evidence["status"] == "VERIFIED"
        await call("work_package_update", session_id=sid, package_id=pid, expected_version=1, state="IN_PROGRESS",
            results=[{"id": "T1", "status": "PASS", "evidence": [{"path": path, "sha256": evidence["sha256"]}]}])
        export = await call("handoff_export", session_id=sid, package_id=pid, expected_version=2)
        assert export["status"] == "EXPORTED"
        other = (await call("session_open", project_root=str(tmp_path), client_name="another-host", conversation_id="two"))["id"]
        assert (await call("handoff_resume", session_id=other, handoff_id=export["handoff_id"], sha256=export["sha256"]))["session_choices_copied"] is False
        assert (await call("session_get", session_id=other))["mode"] is None
        assert (await call("work_package_update", session_id=other, package_id=pid, expected_version=2, state="REVIEW_READY"))["error_code"] == "INTAKE_REQUIRED"
        assert (await call("work_package_get", session_id=sid, package_id=pid))["version"] == 2
        assert (await call("resource_release", session_id=sid, resource="file:src/file.py", lease_token=lease["lease_token"]))["status"] == "RELEASED"
        assert (await client.read_resource("scs://session/" + sid)).contents
        assert (await client.read_resource("scs://work-package/" + pid)).contents


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_independent_stdio_obsidian_view_auto_refresh(mode, tmp_path):
    params = StdioServerParameters(command=sys.executable, args=["-m", "scs_mcp", "--project-root", str(tmp_path)],
                                   env={"PYTHONPATH": SRC})
    async with Client(params, mode=mode, read_timeout_seconds=5) as client:
        async def call(name, **args):
            return (await client.call_tool(name, args)).structured_content
        sid = (await call("session_open", project_root=str(tmp_path), client_name="view-stdio", explicit_skip=True))["id"]
        out = await call("project_view_refresh", session_id=sid, initialize_vault=True)
        assert out["view_initialized"] and out["local_config_present"]
        tools = await client.list_tools()
        assert len(tools.tools) == 16
        p = await call("work_package_create", session_id=sid, goal="View stdio probe", allowed_files=["src"], acceptance=["live view"], request_key="view",
            subtasks=[{"id":"T1", "kind":"analyze", "allowed_files":["src"], "acceptance":["view report"]}])
        assert p["view_update"]["status"] == "OK"
        await call("work_package_update", session_id=sid, package_id=p["id"], expected_version=1, state="IN_PROGRESS")
        assert not (await call("project_view_info", session_id=sid))["snapshot_stale"]
        assert (tmp_path / out["overview_path"]).exists()
