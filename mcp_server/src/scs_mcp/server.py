"""Official SDK v2 stdio server. No agent creation, process execution, or HTTP."""

import hashlib
import json
import re
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
from .interaction import MESSAGES, choice_code, describe, native_question, schema_for, supports_form
from .obsidian import ProjectView
from .roots import Roots
from .project_index import publish
from .state import OPTIONS
from .storage import Store
from .work import Work
from .workspace import scan_workspace


_asked_question = ContextVar("scs_question", default=None)
_ask_timeout = ContextVar("scs_ask_timeout", default=None)


class SafeServer(MCPServer):
    """Catch resolver failures too: a tool body cannot catch its dependency's RPC."""

    def __init__(self, roots: Roots):
        self.roots = roots
        super().__init__("single-conversation-subagent", version=__version__, log_level="CRITICAL")

    @property
    def state(self):
        """Default-root state for diagnostics/tests; sessions use roots.for_session."""
        return self.roots.default_state()

    @property
    def store(self):
        return self.state.store

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
            if name in {"interaction_request", "interaction_ask"}:
                sid = arguments.get("session_id")
                try:
                    qid = _asked_question.get()
                    if not qid:
                        raise ScsError("QUESTION_NOT_FOUND", "No question was requested.")
                    state = self.roots.for_session(sid)
                    q = state.question(sid, qid)
                    if q["state"] not in {"CONFIRMED", "CANCELLED", "DECLINED", "TIMED_OUT"}:
                        q = state.mark(sid, q["id"], "FAILED", "RPC_ERROR")
                        result = describe(q, "RPC_ERROR")
                        result["next"] = "Explicitly interaction_reopen before retrying; the question is FAILED."
                except (ScsError, StopIteration, TypeError):
                    pass
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                              structured_content=result)


def create_server(project_root: str | Path, question_channel: str = "auto", host: str = "generic", *, allowed_roots=()) -> SafeServer:
    if question_channel not in {"auto", "native", "mcp", "text"}:
        question_channel = "auto"
    raw_host = (host or "generic").strip() or "generic"
    display_host = re.sub(r"[^\w .-]", "_", raw_host)[:32] or "generic"
    roots = Roots(project_root, allowed_roots)
    mcp = SafeServer(roots)

    @mcp.tool(structured_output=True)
    async def session_open(project_root: str | None = None, client_name: str | None = None, conversation_id: str | None = None,
                     review_registration: Literal["registered", "unregistered", "unknown"] = "unknown",
                     explicit_skip: bool = False, register_obsidian: bool = False,
                     resume_session_id: str | None = None) -> dict[str, Any]:
        """Bind a host conversation to its project root and report the host question channel.

        project_root defaults to the server's configured workspace root; pass the
        task's project root (the project must be inside the configured workspace).
        Each project gets its own ``<project>/.scs`` state. The session namespace
        is an unambiguous hash of (configured host, client_name), so two hosts
        with the same conversation ID cannot reuse one session; the stored client
        label is h<sha256 prefix>. review_registration is host registry evidence,
        not a guess. explicit_skip attests that the user explicitly said no
        questions; it authorizes no destructive operation. register_obsidian
        initializes this project's docs vault and publishes it to the desktop
        manager before intake. Supply project_root explicitly (user-named project
        or current conversation working directory) and use this only when the
        project metadata and local index writes are authorized. To recover an
        existing session, pass resume_session_id and its absolute project_root
        with the original client_name (or omit it as before). Omit conversation_id
        and explicit_skip when resuming. A known review_registration updates the
        original session without changing its confirmed choices.
        """
        if (register_obsidian or resume_session_id is not None) and (
                not project_root or not Path(project_root).is_absolute()):
            raise ScsError("PROJECT_ROOT_REQUIRED", "Pass the absolute project or conversation working directory.")
        namespace = hashlib.sha256(
            json.dumps([raw_host, client_name or ""], ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()[:32]
        resolved_client = "h" + namespace
        root, _, result = roots.open(
            project_root, resolved_client, conversation_id, review_registration, explicit_skip,
            resume_session_id=resume_session_id,
        )
        result["question_channel"] = question_channel
        result["host"] = display_host
        result["client_label"] = client_name or display_host
        result["project_root"] = str(root)
        if result["mode"] is None and not result["questions"]:
            result["workspace_scan"] = scan_workspace(root)[:320]
            result["mode_options"] = OPTIONS["mode"]
        if register_obsidian:
            try:
                view = roots.views(result["id"]).refresh(result["id"], register=True)
                result["obsidian"] = publish(root, view["vault_root"], view["overview_path"])
            except ScsError as error:
                result["obsidian"] = error.result()
            except OSError:
                result["obsidian"] = ScsError(
                    "PROJECT_INDEX_FAILED", "The session was saved. Check the local project index and retry registration."
                ).result()
        return result

    @mcp.tool(structured_output=True)
    async def session_get(session_id: str) -> dict[str, Any]:
        """Read confirmed choices and pending questions; does not ask or alter preferences."""
        return roots.for_session(session_id).get(session_id)

    async def prepare(session_id: str, kind: str, recommendation: list[str], channel: str,
                      question_id: str | None, ctx: Context,
                      analysis: str | None = None) -> dict | Elicit:
        state = roots.for_session(session_id)
        analysis = analysis.strip()[:160] if analysis else None
        effective = question_channel if channel == "auto" else channel
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
            if effective == "native":
                return native_question(q, analysis, already_pending=True)
            return describe(q, analysis=analysis)
        if q["state"] in {"UNSUPPORTED", "FAILED"}:
            result = describe(q, "ELICITATION_UNSUPPORTED" if q["state"] == "UNSUPPORTED" else "RPC_ERROR")
            result["next"] = "Explicitly interaction_reopen before retrying; the question ended."
            return result
        if q["state"] == "REQUESTED":
            claimed, q = state.claim(session_id, q["id"])
            if not claimed:
                if q["state"] == "CONFIRMED":
                    return {"status": "ALREADY_CONFIRMED", "question_id": q["id"], "choices": json.loads(q["answer"])}
                result = describe(q, "ELICITATION_UNSUPPORTED" if q["state"] == "UNSUPPORTED" else "RPC_ERROR" if q["state"] == "FAILED" else None)
                if q["state"] in {"UNSUPPORTED", "FAILED"}:
                    result["next"] = "Explicitly interaction_reopen before retrying; the question ended."
                return result
        if effective == "native":
            return native_question(q, analysis)
        if effective == "text":
            return describe(q, "TEXT_REQUESTED", analysis=analysis)
        if not supports_form(ctx.client_capabilities):
            return describe(q, "ELICITATION_UNSUPPORTED", analysis=analysis)
        # SDK handles legacy server callbacks and 2026-07-28 InputRequiredResult.
        # The unique question ID pins the rendered question across multi-round retries.
        _asked_question.set(q["id"])
        timeout = _ask_timeout.get()
        if analysis:
            analysis = f"分析：{analysis}"
        elif q["kind"] == "mode":
            analysis = f"扫描：{scan_workspace(state.store.root)[:150]}"
        message = MESSAGES[q["kind"]] + " question_id=" + q["id"]
        if analysis:
            message += "\n" + analysis
        if timeout and timeout > 0:
            message += f"\n{timeout:g} 秒未选择按推荐项继续。"
        return Elicit(message, schema_for(q, analysis, timeout))

    # Local closure annotations are deliberately evaluated (no future annotations):
    # SDK get_type_hints must see this exact resolver object, not an unresolved name.
    @mcp.tool(structured_output=True)
    async def interaction_request(
        session_id: str, kind: Literal["mode", "verification", "codex_review"],
        recommendation: list[str],
        answer: Annotated[ElicitationResult[BaseModel], Resolve(prepare)],
        channel: Literal["auto", "native", "text"] = "auto",
        question_id: str | None = None,
        analysis: str | None = None,
    ) -> dict[str, Any]:
        """Ask once using the configured host channel; an explicit native/text overrides it.

        Supply one recommended path: mode=[quick|standard|long|investigate],
        verification=[smoke,targeted,...] or [none] or [method-only], codex_review=[no].
        channel=auto follows SCS_QUESTION_CHANNEL; use channel=native/text only
        for an explicit route. analysis is a short rationale shown with the
        question (no secrets). Never open two channels at once. question_id pins
        a known question during a resumed call.
        """
        if answer.action == "accept" and isinstance(answer.data, dict):
            return answer.data
        state = roots.for_session(session_id)
        qid = _asked_question.get()
        q = state.question(session_id, qid)
        if not q["current"]:
            raise ScsError("LATE_REPLY", "A reply to a replaced question cannot affect the new question.")
        if answer.action == "accept":
            # The dynamic answer class is the question identity, not a model-supplied field.
            if type(answer.data).__name__ != "Choice_" + q["id"].replace("-", "_"):
                raise ScsError("LATE_REPLY", "The accepted response belongs to a replaced question.")
            value = answer.data.choice
            choices = choice_code(q["kind"], value)
            if not isinstance(choices, list):
                choices = [choices]
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
        analysis: str | None = None,
    ) -> dict[str, Any]:
        """Ask through the MCP form with a server-side deadline.

        On timeout the question is marked TIMED_OUT and the recommendation is
        returned so the main conversation can continue deterministically. Hosts
        without form support get the normal TEXT_FALLBACK_REQUIRED description.
        analysis is a short rationale shown in the form; the form also carries
        the recommendation marker, deadline, and question_id.
        """
        import anyio
        from mcp_types.version import MODERN_PROTOCOL_VERSIONS

        state = roots.for_session(session_id)
        token = _ask_timeout.set(timeout_seconds)
        try:
            outcome = await prepare(session_id, kind, recommendation, "auto", None, ctx, analysis)
        finally:
            _ask_timeout.reset(token)
        if not isinstance(outcome, Elicit):
            return outcome
        qid = _asked_question.get()
        if not qid:
            raise ScsError("QUESTION_NOT_FOUND", "No question was prepared for the form.")
        if ctx.protocol_version in MODERN_PROTOCOL_VERSIONS:
            q = state.question(session_id, qid)
            return describe(q, "MODERN_NO_BACKCHANNEL", analysis=analysis)
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
            choices = choice_code(kind, value)
            if not isinstance(choices, list):
                choices = [choices]
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
        return roots.for_session(session_id).submit(session_id, question_id, action, choices, source, observed_timeout)

    @mcp.tool(structured_output=True)
    async def interaction_reopen(session_id: str, kind: Literal["mode", "verification", "codex_review"],
                           explicit_user_change: bool = False) -> dict[str, Any]:
        """Explicitly retry an ended question. Replacing an active/confirmed question requires a real user change.

        Creates no UI. The next interaction_request gets a fresh, nonreusable question_id.
        """
        return roots.for_session(session_id).reopen(session_id, kind, explicit_user_change)

    @mcp.tool(structured_output=True)
    async def diagnostics_check(ctx: Context, session_id: str | None = None) -> dict[str, Any]:
        """Check DB, SDK, registered tools and declared capabilities. Connection is not evidence that UI was displayed."""
        from .diagnostics import check
        state = roots.for_session(session_id) if session_id else roots.default_state()
        return check(state.store, ctx.client_capabilities, ctx.protocol_version,
                     [tool.name for tool in await mcp.list_tools()], session_id)

    @mcp.resource("scs://session/{session_id}")
    async def session_resource(session_id: str) -> str:
        """Read-only session snapshot. No inferred conversation identity."""
        return json.dumps(roots.for_session(session_id).get(session_id), ensure_ascii=False)

    @mcp.tool(structured_output=True)
    async def work_package_create(session_id: str, goal: str, allowed_files: list[str],
                                  acceptance: list[str], subtasks: list[SubtaskDefinition], request_key: str) -> dict[str, Any]:
        """Create an optional bounded package after intake. request_key makes retries safe.

        subtasks=[{id,kind:analyze|implement|test,allowed_files,acceptance}].
        Scope is project-relative; no scheduling occurs. Missing Markdown records are
        initialized only; main conversation must explicitly sync reviewed records.
        """
        result = roots.work(session_id).create(session_id, goal, allowed_files, acceptance, [t.model_dump() for t in subtasks], request_key)
        return refresh_enabled_view(session_id, result)

    @mcp.tool(structured_output=True)
    async def work_package_get(session_id: str, package_id: str) -> dict[str, Any]:
        """Read version and revision before an optimistic update."""
        return roots.work(session_id).get(session_id, package_id)

    @mcp.tool(structured_output=True)
    async def work_package_update(session_id: str, package_id: str, expected_version: int,
                                  state: Literal["PENDING", "IN_PROGRESS", "REVIEW_READY", "BLOCKED", "COMPLETED", "ABANDONED"] | None = None,
                                  results: list[SubtaskResult] | None = None, new_revision: bool = False,
                                  close_reason: str | None = None) -> dict[str, Any]:
        """Update with a version precondition. COMPLETED/ABANDONED are terminal and need close_reason.

        results=[{id,status:PASS|FAIL|BLOCKED|NOT_RUN|SUBSTITUTED,
        evidence:[{path,sha256,required_content?}]}]. A PASS report needs reverified
        unchanged evidence. new_revision resets results and allocates fresh evidence
        directories; it preserves every earlier file. COMPLETED requires REVIEW_READY;
        ABANDONED may close a pending, active or blocked package. Closed packages are
        immutable; start a new package for follow-up work.
        """
        result = roots.work(session_id).update(session_id, package_id, expected_version, state,
                             [r.model_dump() for r in results] if results is not None else None, new_revision,
                             close_reason)
        return refresh_enabled_view(session_id, result)

    @mcp.tool(structured_output=True)
    async def work_package_close(project_root: str, package_id: str, expected_version: int,
                                 state: Literal["COMPLETED", "ABANDONED"], close_reason: str) -> dict[str, Any]:
        """Close a stale or duplicate package from the local manager without resuming its conversation.

        The owner session is read from the package; version, close reason and audit
        events are still enforced. COMPLETED requires REVIEW_READY; ABANDONED may
        close a pending, active or blocked package. Prefer work_package_update in the
        owning conversation; this operator path exists for local reconciliation.
        """
        root = roots.resolve(project_root)
        store = Store(root)
        work = Work(store)
        with store.transaction() as con:
            owner = work._get(con, package_id)["session_id"]
        result = work.update(owner, package_id, expected_version, state, close_reason=close_reason)
        try:
            updated = ProjectView(store).refresh(owner, initialize_vault=True)
            result["view_update"] = {"status": "OK", "updated_files": updated["updated_files"]}
        except ScsError as error:
            result["view_update"] = error.result()
            result["view_update"]["work_package_saved"] = True
        return result

    @mcp.tool(structured_output=True)
    async def evidence_verify(session_id: str, package_id: str, revision: int, subtask_id: str,
                              path: str, required_content: list[str] | None = None) -> dict[str, Any]:
        """Verify a bounded UTF-8 file inside this package/revision/subtask evidence directory.

        Test evidence is JSON with command, exit_code=0, and passed>0. VERIFIED is
        file/content validation, not proof of execution or final main-agent acceptance.
        """
        return roots.work(session_id).evidence(session_id, package_id, revision, subtask_id, path, required_content)

    @mcp.tool(structured_output=True)
    async def resource_acquire(session_id: str, resource: str, ttl_seconds: float = 120,
                               lease_token: str | None = None) -> dict[str, Any]:
        """Acquire/renew a token-fenced advisory lease. Supply the returned token to renew.

        resource=file:relative/path or device:/serial:/programmer:logical-id.
        External processes are not blocked. TTL is 1..3600 seconds; expired leases
        get a fresh token so a stale owner cannot release a successor's lease.
        """
        return roots.leases(session_id).acquire(session_id, resource, ttl_seconds, lease_token)

    @mcp.tool(structured_output=True)
    async def resource_release(session_id: str, resource: str, lease_token: str) -> dict[str, Any]:
        """Release only the matching owner and token; repeated release is safe."""
        return roots.leases(session_id).release(session_id, resource, lease_token)

    @mcp.tool(structured_output=True)
    async def handoff_export(session_id: str, package_id: str, expected_version: int) -> dict[str, Any]:
        """Export a digest-bound snapshot into this project's local audit DB. No session preferences are exported."""
        return roots.work(session_id).export(session_id, package_id, expected_version)

    @mcp.tool(structured_output=True)
    async def handoff_resume(session_id: str, handoff_id: str, sha256: str) -> dict[str, Any]:
        """Explicitly attach a current audited handoff to this conversation. Its own first-task choices remain unconfirmed."""
        return roots.work(session_id).resume(session_id, handoff_id, sha256)

    @mcp.resource("scs://work-package/{package_id}")
    async def package_resource(package_id: str) -> str:
        """Read-only project-local work package. A resource grants no mutation or execution permissions."""
        _, _, snapshot = roots.package(package_id)
        return json.dumps(snapshot, ensure_ascii=False)

    def refresh_enabled_view(session_id, result):
        # The work mutation already committed. A projection conflict must not turn
        # it into an ambiguous failed mutation or encourage a duplicate retry.
        try:
            views = roots.views(session_id)
            with views.store.transaction() as con:
                docs, _, path = views._layout(con)
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
        return roots.views(session_id).refresh(session_id, initialize_vault, auto_refresh)

    @mcp.tool(structured_output=True)
    async def project_view_info(session_id: str) -> dict[str, Any]:
        """Read-only vault/view inspection, stale/drift/evidence checks and Obsidian open URI.

        Does not create docs or vault files, launch applications, or claim the vault
        is registered. First use Open folder as vault in Obsidian, selecting vault_root.
        """
        return roots.views(session_id).info(session_id)

    return mcp
