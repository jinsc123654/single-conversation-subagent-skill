#!/usr/bin/env python3
"""Manual compatibility probe: run this script with official mcp==1.30.0.

The server uses a separate SDK 2.3 interpreter. All replies are simulated;
this is a real stdio/client compatibility check, not a graphical UI test.
"""

import argparse
import asyncio
import json
import tempfile
from importlib.metadata import version
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client


async def probe(args):
    assert version("mcp") == "1.30.0", "Use a separate environment with mcp==1.30.0"
    results = []
    for supported in (True, False):
        answers = iter(["standard", ["smoke", "targeted"], "no"])
        seen = []

        async def respond(context, params):
            seen.append(params)
            return types.ElicitResult(action="accept", content={"choice": next(answers)})

        with tempfile.TemporaryDirectory(prefix="scs-legacy-") as root:
            params = StdioServerParameters(command=args.server_python,
                args=["-m", "scs_mcp", "--project-root", root],
                env={"PYTHONPATH": args.server_pythonpath} if args.server_pythonpath else None)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write, elicitation_callback=respond if supported else None) as client:
                    init = await client.initialize()

                    async def call(name, **arguments):
                        out = await client.call_tool(name, arguments)
                        assert not out.isError, out
                        assert out.structuredContent is not None, out
                        return out.structuredContent

                    tools = await client.list_tools()
                    sid = (await call("session_open", project_root=root, client_name="official-sdk-1",
                        conversation_id="legacy-probe", review_registration="registered"))["id"]
                    kinds = [("mode", ["standard"]), ("verification", ["smoke", "targeted"]), ("codex_review", ["no"])]
                    for kind, rec in kinds if supported else kinds[:1]:
                        out = await call("interaction_request", session_id=sid, kind=kind, recommendation=rec)
                        if supported:
                            assert out["status"] == "CONFIRMED", out
                        else:
                            assert out["status"] == "TEXT_FALLBACK_REQUIRED", out
                            assert out["fallback_reason"] == "ELICITATION_UNSUPPORTED", out
                            assert (await call("interaction_submit", session_id=sid, question_id=out["question_id"],
                                action="accept", choices=["quick"]))["status"] == "CONFIRMED"
                    snapshot = await call("session_get", session_id=sid)
                    assert snapshot["mode"] == ("standard" if supported else "quick")
                    before = await call("project_view_info", session_id=sid)
                    assert not before["view_initialized"]
                    refreshed = await call("project_view_refresh", session_id=sid, initialize_vault=True)
                    assert refreshed["view_initialized"] and refreshed["auto_refresh_enabled"]
                    assert not refreshed["application_opened"]
                    package = await call("work_package_create", session_id=sid, goal="Legacy view probe",
                        allowed_files=["src"], acceptance=["view updates"], request_key="view",
                        subtasks=[{"id":"A", "kind":"analyze", "allowed_files":["src"], "acceptance":["report"]}])
                    assert package["view_update"]["status"] == "OK"
                    assert not (await call("project_view_info", session_id=sid))["snapshot_stale"]
                    assert (await call("diagnostics_check", session_id=sid))["ui_observation"] == "UI_NOT_OBSERVABLE"
                    assert (await call("session_get", session_id="missing"))["error_code"] == "SESSION_NOT_FOUND"
                    assert len(seen) == (3 if supported else 0)
                    results.append({"client_sdk": version("mcp"), "protocol": init.protocolVersion,
                        "form_supported": supported, "questions_answered": len(seen), "tools": len(tools.tools),
                        "result": "PASS", "project_view": "PASS", "graphical_ui": "NOT_TESTED"})
    if args.output:
        Path(args.output).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-python", required=True)
    parser.add_argument("--server-pythonpath")
    parser.add_argument("--output")
    asyncio.run(probe(parser.parse_args()))
