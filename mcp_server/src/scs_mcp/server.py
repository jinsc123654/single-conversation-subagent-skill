"""Official SDK v2 stdio server. No agent creation, process execution, or HTTP."""

import json
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, Literal, Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Context, Elicit, ElicitationResult, Resolve
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from . import __version__
from .errors import ScsError
from .models import SubtaskDefinition, SubtaskResult
from .interaction import MESSAGES, describe, schema_for, supports_form
from .state import State
from .storage import Store


_asked_question = ContextVar("scs_question", default=None)


class SafeServer(MCPServer):
    """Catch resolver failures too: a tool body cannot catch its dependency's RPC."""

    def __init__(self, store: Store):
        self.store = store
        self.state = State(store)
        super().__init__("single-conversation-subagent", version=__version__, log_level="CRITICAL")

    async def run_stdio_async(self) -> None:
        """Serve stdio with the ZCode mixed-era compatibility filter.

        Identified non-ZCode clients keep the SDK's modern era. ZCode and
        anonymous ``server/discover`` probes are served as a normal
        2025-11-25 handshake connection, which both sides already speak.
        """
        from mcp.server.stdio import stdio_server

        from .zcode_compat import zcode_compatible_stream

        async with stdio_server() as (read_stream, write_stream):
            await self._lowlevel_server.run(
                zcode_compatible_stream(read_stream, write_stream),
                write_stream,
                self._lowlevel_server.create_initialization_options(),
            )

    async def call_tool(self, name, arguments, context=None):
        token = _asked_question.set(None)
        try:
            return await self._call_tool(name, arguments, context)
        finally:
            _asked_question.reset(token)

    async def _call_tool(self, name, arguments, context=None):
        try:
            return await super().call_tool(name, arguments, context)
        except ScsError as exc:
            result = exc.result()
        except Exception as caught:
            chain = caught
            while chain is not None:
                if isinstance(chain, ScsError):
                    result = chain.result()
                    return CallToolResult(content=[TextContent(type="text", text=json.dumps(result))], structured_content=result)
                chain = chain.__cause__
            # Never echo errors, validation input, credentials or private URLs.
            result = {"status": "ERROR", "error_code": "TOOL_ERROR", "message": "Tool failed; inspect local diagnostics."}
            if name == "interaction_request":
                sid = arguments.get("session_id")
                try:
                    qid = _asked_question.get()
                    if not qid:
                        raise ScsError("QUESTION_NOT_FOUND", "No question was requested.")
                    q = self.state.question(sid, qid)
                    if q["state"] not in {"CONFIRMED", "CANCELLED", "DECLINED", "TIMED_OUT"}:
                        q = self.state.mark(sid, q["id"], "FAILED", "RPC_ERROR")
                        result = describe(q, "RPC_ERROR")
                except (ScsError, StopIteration, TypeError):
                    pass
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                              structured_content=result)


def create_server(project_root: str | Path) -> SafeServer:
    store = Store(project_root)
    mcp = SafeServer(store)
    state = mcp.state

    @mcp.tool(structured_output=True)
    async def session_open(project_root: str, client_name: str, conversation_id: str | None = None,
                     review_registration: Literal["registered", "unregistered", "unknown"] = "unknown",
                     explicit_skip: bool = False) -> dict[str, Any]:
        """Bind a real host conversation ID to this configured root. Unknown IDs create isolated temporary sessions.

        review_registration is host registry evidence, not a guess. explicit_skip
        attests that the user explicitly said no questions; it authorizes no destructive operation.
        """
        return state.open(project_root, client_name, conversation_id, review_registration, explicit_skip)

    @mcp.tool(structured_output=True)
    async def session_get(session_id: str) -> dict[str, Any]:
        """Read confirmed choices and pending questions; does not ask or alter preferences."""
        return state.get(session_id)

    async def prepare(session_id: str, kind: str, recommendation: list[str], channel: str,
                      question_id: str | None, ctx: Context) -> dict | Elicit:
        if question_id:
            q = state.question(session_id, question_id)
            if q["kind"] != kind or not q["current"]:
                raise ScsError("LATE_REPLY", "Question identity or kind no longer matches.")
        else:
            q = state.request(session_id, kind, recommendation, channel)
        if "id" not in q:
            return q
        if q["state"] == "CONFIRMED":
            return {"status": "ALREADY_CONFIRMED", "question_id": q["id"], "choices": json.loads(q["answer"])}
        if q["state"] in {"DECLINED", "CANCELLED", "TIMED_OUT"}:
            return {"status": q["state"], "question_id": q["id"], "next": "Wait; explicitly interaction_reopen to retry."}
        # A second independent call must not open another UI. The SDK's authenticated
        # retry includes request_state and the same arguments, so it may resolve again.
        if q["state"] == "WAITING_USER" and not ctx.request_state:
            return describe(q)
        if q["state"] in {"UNSUPPORTED", "FAILED"}:
            return describe(q, "ELICITATION_UNSUPPORTED" if q["state"] == "UNSUPPORTED" else "RPC_ERROR")
        if q["state"] == "REQUESTED":
            claimed, q = state.claim(session_id, q["id"])
            if not claimed:
                if q["state"] == "CONFIRMED":
                    return {"status": "ALREADY_CONFIRMED", "question_id": q["id"], "choices": json.loads(q["answer"])}
                return describe(q, "ELICITATION_UNSUPPORTED" if q["state"] == "UNSUPPORTED" else "RPC_ERROR" if q["state"] == "FAILED" else None)
        if q["channel"] == "native":
            result = describe(q)
            result["status"] = "NATIVE_QUESTION_REQUIRED"
            result["next"] = "Use the host native question tool once; submit the explicit reply with this question_id."
            return result
        if q["channel"] == "text":
            return describe(q, "TEXT_REQUESTED")
        if not supports_form(ctx.client_capabilities):
            q = state.mark(session_id, q["id"], "UNSUPPORTED", "ELICITATION_UNSUPPORTED")
            return describe(q, "ELICITATION_UNSUPPORTED")
        # SDK handles legacy server callbacks and 2026-07-28 InputRequiredResult.
        # The unique question ID pins the rendered question across multi-round retries.
        _asked_question.set(q["id"])
        return Elicit(MESSAGES[q["kind"]] + " question_id=" + q["id"], schema_for(q))

    # Local closure annotations are deliberately evaluated (no future annotations):
    # SDK get_type_hints must see this exact resolver object, not an unresolved name.
    @mcp.tool(structured_output=True)
    async def interaction_request(
        session_id: str, kind: Literal["mode", "verification", "codex_review"],
        recommendation: list[str],
        answer: Annotated[ElicitationResult[BaseModel], Resolve(prepare)],
        channel: Literal["auto", "native", "text"] = "auto",
        question_id: str | None = None,
    ) -> dict[str, Any]:
        """Ask once: native route first when available; auto uses declared MCP form support, else TEXT_FALLBACK_REQUIRED.

        Supply one recommended path: mode=[quick|standard|long|investigate],
        verification=[smoke,targeted,...] or [none] or [method-only], codex_review=[no].
        Use channel=native when a native host question tool is available. Never open
        two channels at once. question_id pins a known question during a resumed call.
        """
        if answer.action == "accept" and isinstance(answer.data, dict):
            return answer.data
        qid = _asked_question.get()
        q = state.question(session_id, qid)
        if not q["current"]:
            raise ScsError("LATE_REPLY", "A reply to a replaced question cannot affect the new question.")
        if answer.action == "accept":
            # The dynamic answer class is the question identity, not a model-supplied field.
            if type(answer.data).__name__ != "Choice_" + q["id"].replace("-", "_"):
                raise ScsError("LATE_REPLY", "The accepted response belongs to a replaced question.")
            value = answer.data.choice
            choices = value if isinstance(value, list) else [value]
        else:
            choices = None
        return state.submit(session_id, q["id"], answer.action, choices, source="mcp")

    @mcp.tool(structured_output=True)
    async def interaction_ask(
        ctx: Context,
        session_id: str,
        kind: Literal["mode", "verification", "codex_review"],
        recommendation: list[str],
        timeout_seconds: float = 15.0,
    ) -> dict[str, Any]:
        """Ask through the MCP form with a server-side deadline.

        On timeout the question is marked TIMED_OUT and the recommendation is
        returned so the main conversation can continue deterministically. Hosts
        without form support get the normal TEXT_FALLBACK_REQUIRED description.
        """
        import anyio
        from mcp_types.version import MODERN_PROTOCOL_VERSIONS

        if ctx.protocol_version in MODERN_PROTOCOL_VERSIONS:
            return {
                "status": "TEXT_FALLBACK_REQUIRED",
                "fallback_reason": "MODERN_NO_BACKCHANNEL",
                "kind": kind,
                "recommendation": recommendation,
                "display_text": MESSAGES[kind] + "\n唯一推荐：" + ", ".join(recommendation),
                "next": "Use interaction_request on this protocol or a native question.",
                "destructive_operations_authorized": False,
            }
        outcome = await prepare(session_id, kind, recommendation, "auto", None, ctx)
        if not isinstance(outcome, Elicit):
            return outcome
        qid = _asked_question.get()
        if not qid:
            raise ScsError("QUESTION_NOT_FOUND", "No question was prepared for the form.")
        message = outcome.message
        if timeout_seconds is not None and timeout_seconds > 0:
            message += f"；{timeout_seconds:g} 秒后按推荐项继续。"
            try:
                with anyio.fail_after(timeout_seconds):
                    answer = await ctx.elicit(message, outcome.schema)
            except TimeoutError:
                result = state.submit(session_id, qid, "timeout", None, "mcp", True)
                result["recommendation"] = recommendation
                result["next"] = "Use the recommended choice; the MCP form timed out."
                return result
        else:
            answer = await ctx.elicit(message, outcome.schema)
        if answer.action == "accept":
            data = answer.data
            value = data.choice if hasattr(data, "choice") else (data or {}).get("choice")
            if value is None:
                raise ScsError("CHOICE_INVALID", "The form answer did not contain a choice.")
            choices = value if isinstance(value, list) else [value]
            return state.submit(session_id, qid, "accept", choices, "mcp")
        return state.submit(session_id, qid, answer.action, None, "mcp")

    @mcp.tool(structured_output=True)
    async def interaction_submit(session_id: str, question_id: str,
                           action: Literal["accept", "decline", "cancel", "timeout", "unsupported", "error"],
                           choices: list[str] | None = None, source: Literal["text", "native"] = "text",
                           observed_timeout: bool = False) -> dict[str, Any]:
        """Record an explicit native/text reply for its exact question ID. Defaults and elapsed sleeps are not replies.

        timeout requires observable host UI timeout evidence; it still confirms no
        choice. cancel/decline require an explicit reopen before another choice.
        """
        return state.submit(session_id, question_id, action, choices, source, observed_timeout)

    @mcp.tool(structured_output=True)
    async def interaction_reopen(session_id: str, kind: Literal["mode", "verification", "codex_review"],
                           explicit_user_change: bool = False) -> dict[str, Any]:
        """Explicitly retry an ended question. Replacing an active/confirmed question requires a real user change.

        Creates no UI. The next interaction_request gets a fresh, nonreusable question_id.
        """
        return state.reopen(session_id, kind, explicit_user_change)

    @mcp.tool(structured_output=True)
    async def diagnostics_check(ctx: Context, session_id: str | None = None) -> dict[str, Any]:
        """Check DB, SDK, registered tools and declared capabilities. Connection is not evidence that UI was displayed."""
        from .diagnostics import check
        return check(store, ctx.client_capabilities, ctx.protocol_version,
                     [tool.name for tool in await mcp.list_tools()], session_id)

    @mcp.resource("scs://session/{session_id}")
    async def session_resource(session_id: str) -> str:
        """Read-only session snapshot. No inferred conversation identity."""
        return json.dumps(state.get(session_id), ensure_ascii=False)

    from .work import Work
    from .locks import Leases
    work = Work(store)
    leases = Leases(store)

    @mcp.tool(structured_output=True)
    async def work_package_create(session_id: str, goal: str, allowed_files: list[str],
                                  acceptance: list[str], subtasks: list[SubtaskDefinition], request_key: str) -> dict[str, Any]:
        """Create an optional bounded package after intake. request_key makes retries safe.

        subtasks=[{id,kind:analyze|implement|test,allowed_files,acceptance}].
        Scope is project-relative; no scheduling occurs. Missing Markdown records are
        initialized only; main conversation must explicitly sync reviewed records.
        """
        result = work.create(session_id, goal, allowed_files, acceptance, [t.model_dump() for t in subtasks], request_key)
        return refresh_enabled_view(session_id, result)

    @mcp.tool(structured_output=True)
    async def work_package_get(session_id: str, package_id: str) -> dict[str, Any]:
        """Read version and revision before an optimistic update."""
        return work.get(session_id, package_id)

    @mcp.tool(structured_output=True)
    async def work_package_update(session_id: str, package_id: str, expected_version: int,
                                  state: Literal["PENDING", "IN_PROGRESS", "REVIEW_READY", "BLOCKED"] | None = None,
                                  results: list[SubtaskResult] | None = None, new_revision: bool = False) -> dict[str, Any]:
        """Update with a version precondition. Never makes a final acceptance decision.

        results=[{id,status:PASS|FAIL|BLOCKED|NOT_RUN|SUBSTITUTED,
        evidence:[{path,sha256,required_content?}]}]. A PASS report needs reverified
        unchanged evidence. new_revision resets results and allocates fresh evidence
        directories; it preserves every earlier file.
        """
        result = work.update(session_id, package_id, expected_version, state,
                             [r.model_dump() for r in results] if results is not None else None, new_revision)
        return refresh_enabled_view(session_id, result)

    @mcp.tool(structured_output=True)
    async def evidence_verify(session_id: str, package_id: str, revision: int, subtask_id: str,
                              path: str, required_content: list[str] | None = None) -> dict[str, Any]:
        """Verify a bounded UTF-8 file inside this package/revision/subtask evidence directory.

        Test evidence is JSON with command, exit_code=0, and passed>0. VERIFIED is
        file/content validation, not proof of execution or final main-agent acceptance.
        """
        return work.evidence(session_id, package_id, revision, subtask_id, path, required_content)

    @mcp.tool(structured_output=True)
    async def resource_acquire(session_id: str, resource: str, ttl_seconds: float = 120,
                               lease_token: str | None = None) -> dict[str, Any]:
        """Acquire/renew a token-fenced advisory lease. Supply the returned token to renew.

        resource=file:relative/path or device:/serial:/programmer:logical-id.
        External processes are not blocked. TTL is 1..3600 seconds; expired leases
        get a fresh token so a stale owner cannot release a successor's lease.
        """
        return leases.acquire(session_id, resource, ttl_seconds, lease_token)

    @mcp.tool(structured_output=True)
    async def resource_release(session_id: str, resource: str, lease_token: str) -> dict[str, Any]:
        """Release only the matching owner and token; repeated release is safe."""
        return leases.release(session_id, resource, lease_token)

    @mcp.tool(structured_output=True)
    async def handoff_export(session_id: str, package_id: str, expected_version: int) -> dict[str, Any]:
        """Export a digest-bound snapshot into this project's local audit DB. No session preferences are exported."""
        return work.export(session_id, package_id, expected_version)

    @mcp.tool(structured_output=True)
    async def handoff_resume(session_id: str, handoff_id: str, sha256: str) -> dict[str, Any]:
        """Explicitly attach a current audited handoff to this conversation. Its own first-task choices remain unconfirmed."""
        return work.resume(session_id, handoff_id, sha256)

    @mcp.resource("scs://work-package/{package_id}")
    async def package_resource(package_id: str) -> str:
        """Read-only project-local work package. A resource grants no mutation or execution permissions."""
        with store.transaction() as con:
            return json.dumps(work._snapshot(work._get(con, package_id)), ensure_ascii=False)

    from .obsidian import ProjectView
    views = ProjectView(store)

    def refresh_enabled_view(session_id, result):
        # The work mutation already committed. A projection conflict must not turn
        # it into an ambiguous failed mutation or encourage a duplicate retry.
        try:
            docs, _, path = views._layout()
            manifest, _ = views._manifest(path, docs)
            if manifest and manifest.get("auto_refresh"):
                updated = views.refresh(session_id)
                result["view_update"] = {"status": "OK", "updated_files": updated["updated_files"], "open_uri": updated["open_uri"]}
        except ScsError as error:
            result["view_update"] = error.result()
            result["view_update"]["work_package_saved"] = True
        except Exception:
            result["view_update"] = {"status": "ERROR", "error_code": "VIEW_REFRESH_FAILED", "work_package_saved": True,
                "message": "Work package was saved. Inspect project_view_info and retry the view refresh separately."}
        return result

    @mcp.tool(structured_output=True)
    async def project_view_refresh(session_id: str, initialize_vault: bool = False, auto_refresh: bool = True) -> dict[str, Any]:
        """Generate/refresh a plugin-free Obsidian project view inside docs-root/agent/acceptance.

        One project docs-root is one vault. Preserves manual:begin/end annotations
        and existing .obsidian settings; conflicts stop before overwriting notes.
        initialize_vault creates a missing local .obsidian/app.json and a dedicated
        project-local launch profile, never a global registration. Returns open
        links and desktop profile information; the host/user opens
        Obsidian. PASS reports are shown separately from final main-agent acceptance.
        auto_refresh enables view refresh after MCP work package writes, with
        conflicts reported separately from the saved work. Call at stage boundaries.
        """
        return views.refresh(session_id, initialize_vault, auto_refresh)

    @mcp.tool(structured_output=True)
    async def project_view_info(session_id: str) -> dict[str, Any]:
        """Read-only vault/view inspection, stale/drift/evidence checks and Obsidian open URI.

        Does not create docs or vault files, launch applications, or claim the vault
        is registered. First use Open folder as vault in Obsidian, selecting vault_root.
        """
        return views.info(session_id)

    return mcp
