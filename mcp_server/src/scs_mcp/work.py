"""Versioned work packages and auditable handoffs, without agent scheduling."""

import hashlib
import json
import os
import re
import stat

from .errors import ScsError
from .evidence import FIXED_REVIEWER, FIXED_REVIEWER_IDENTITIES, verify_file
from .locks import Leases
from .state import State, identifier
from .storage import Store, encode, now

STATES = {"PENDING": {"IN_PROGRESS", "BLOCKED", "SUSPENDED", "ABANDONED"},
          "IN_PROGRESS": {"REVIEW_READY", "BLOCKED", "SUSPENDED", "ABANDONED"},
          "BLOCKED": {"IN_PROGRESS", "SUSPENDED", "ABANDONED"},
          "REVIEW_READY": {"IN_PROGRESS", "BLOCKED", "SUSPENDED", "COMPLETED", "ABANDONED"},
          "SUSPENDED": {"IN_PROGRESS", "ABANDONED"},
          "COMPLETED": set(), "ABANDONED": set()}
RESULTS = {"PASS", "FAIL", "BLOCKED", "NOT_RUN", "SUBSTITUTED"}
CLOSED_STATES = {"COMPLETED", "ABANDONED"}


def _journey_pass_invalid(check: dict, status: str, profile: str | None = None) -> bool:
    """A PASS for a journey/device attempt must carry satisfied assertions."""
    if status != "PASS":
        return False
    is_journey = check.get("kind") == "journey"
    if profile == "journey" and not is_journey:
        return True
    if is_journey and (check.get("journey_passed") is not True or check.get("journey_exit_code") != 0):
        return True
    return False
REVIEW_EXCLUDED = {".git", ".venv", ".venv-mcp", ".scs", ".archify"}
REVIEW_MAX_FILES = 500
REVIEW_MAX_BYTES = 100 * 1024 * 1024
ZCODE_AGENTS = {"analyze": "scs-scout", "implement": "scs-implementer", "test": "scs-test-verifier",
                "journey": "scs-device-verifier", "review": FIXED_REVIEWER}
ZCODE_DISPATCH_CANDIDATE_LIMIT = 8
ZCODE_DISPATCH_REQUIRED_CHECKS = (
    "由主对话查找同一宿主父会话内已完成的同角色实例，核对目标、基线、本轮授权和审阅独立性；"
    "SCS session 相同不证明宿主父会话相同，parent 仅核验身份归属。",
    "有相容候选时先在当前对话实际调用 ListModels（仅提供模型 catalog），再运行 Skill 的只读上下文探针；"
    "未核查不等于 UNKNOWN，工具或脚本不可用须记录实际失败或缺项。",
    "70% 只检查目标子 Agent：最近一次已完成请求的 input_tokens + output_tokens + reserve，"
    "除以该子 Agent 实际 provider/model 的上下文窗口；不得使用主对话 token、累计账单或 metadata.totalTokens。",
    "相容且探针 REUSE、预计低于 70% 时优先 SendMessage；ROTATE 或实际 UNKNOWN 时保守新开；"
    "首次、无相容候选、不同工作线或明确要求全新盲审时无需无候选容量检查，选择仍由主对话作出。",
    "旧执行和资源静止后才续跑或重派；需要 prepare 的工作使用新 attempt 和租约，转交本轮范围、授权和证据目录；"
    "同批不得把同一 Agent 分给并发任务。",
    "在现有协调记录或工具简报保留 dispatch_decision：角色、候选或无候选、相容性、实际探针结果或具体缺项、"
    "选择和原因；不得把未调用或未读用法记成 UNKNOWN。",
)


def zcode_dispatch_warning() -> dict:
    """Advisory only; no host lookup, capacity verdict, authorization or scheduling."""
    return {"code": "ZCODE_DISPATCH_PREFLIGHT_REQUIRED",
            "message": "每次 Agent 或对已完成实例的 SendMessage 前执行 Skill 派发前检查，顾问、包级复核、收尾和操作预审也适用；"
                       "本提示不授权、不分配实例、不拦截宿主工具。",
            "required_checks": list(ZCODE_DISPATCH_REQUIRED_CHECKS)}


# Bumped whenever the subject payload shape or canonical encoding changes.
# The field is part of the digested payload, so ACCEPTs recorded by an older
# version fail freshness checks (REVIEW_STALE) instead of being reused under a
# new encoding: the safe direction after an upgrade.
REVIEW_SUBJECT_VERSION = 2

# Immutable parts of a subtask definition. Runtime fields (status, evidence,
# review) must not participate in create idempotency comparisons.
TASK_DEFINITION_FIELDS = ("id", "kind", "allowed_files", "acceptance",
                          "depends_on", "resources", "required", "execution_profile")
ATTEMPT_STATES = {"PREPARED", "LAUNCHING", "RUNNING", "DRAINING", "ENDED", "UNKNOWN"}
ATTEMPT_TRANSITIONS = {
    "PREPARED": {"LAUNCHING", "UNKNOWN", "ENDED"},
    "LAUNCHING": {"RUNNING", "DRAINING", "UNKNOWN", "ENDED"},
    "RUNNING": {"DRAINING", "UNKNOWN", "ENDED"},
    "DRAINING": {"ENDED", "UNKNOWN"},
    "UNKNOWN": {"RUNNING", "DRAINING", "ENDED"},
    "ENDED": set(),
}


def _review_subject_json(value) -> str:
    """Canonical JSON for the review subject digest.

    ``ensure_ascii=True`` escapes every non-ASCII character, including lone
    surrogates that ``surrogateescape`` produces for non-UTF-8 file, link and
    directory names, as ``\\uXXXX``. The result is plain ASCII, so encoding it
    as UTF-8 never raises ``UnicodeEncodeError``. The escape is lossless and
    injective: an actual surrogate and the literal text ``\\udcff`` remain
    different JSON strings, and no ``surrogatepass`` (which would emit invalid
    UTF-8) is involved.
    """
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class Work:
    def __init__(self, store: Store):
        self.store = store
        self.state = State(store)
        self.leases = Leases(store)

    def _get(self, con, package_id: str):
        row = con.execute("SELECT * FROM work_packages WHERE id=?", (package_id,)).fetchone()
        if row is None:
            raise ScsError("PACKAGE_NOT_FOUND", "Work package does not exist in this project.")
        return row

    def _access(self, con, session_id: str, row):
        self.state._session(con, session_id)
        if row["session_id"] != session_id and not con.execute(
                "SELECT 1 FROM resumes r JOIN handoffs h ON r.handoff_id=h.id WHERE r.session_id=? AND h.package_id=?",
                (session_id, row["id"])).fetchone():
            raise ScsError("HANDOFF_REQUIRED", "Another conversation's package requires an explicit handoff resume.")

    def _records(self, docs: str):
        # No state projection overwrites user edits. Main agent owns Markdown sync.
        self.store.paths.create_once(docs + "/agent/AGENT_PROJECT.md",
            "# Agent project\n\nMain conversation owns goals, decisions, withdrawn plans and final acceptance.\nSQLite work state must be explicitly reconciled with this reviewed record.\n")
        self.store.paths.create_once(docs + "/agent/AGENT_COORDINATION.md",
            "# Agent coordination\n\nMain conversation owns stage summaries, subtask IDs, evidence and handoff.\nPure Skill edits require explicit reconciliation before MCP resumes.\n")

    def _scope(self, scopes: list[str]) -> list[str]:
        if not isinstance(scopes, list) or not scopes or not all(isinstance(p, str) for p in scopes):
            raise ScsError("SCOPE_EMPTY", "Declare explicit project-relative files or directories.")
        return [self.store.paths.validate(p) for p in scopes]

    def _default_resources(self, kind: str, scopes: list[str]) -> list[dict]:
        # Only implementation takes an exclusive write lease per scope. analyze,
        # test and review take no default lease: this version has no shared read
        # leases, so two readers would otherwise overwrite one lease row.
        if kind != "implement":
            return []
        return [{"resource": "file:" + scope, "mode": "write"} for scope in scopes]

    def _resources(self, task: dict, scopes: list[str]) -> list[dict]:
        raw = task.get("resources", [])
        if not isinstance(raw, list) or len(raw) > 100:
            raise ScsError("RESOURCE_INVALID", "Declare at most 100 resources per subtask.")
        if not raw:
            return self._default_resources(task["kind"], scopes)
        merged = {}
        for item in raw:
            if not isinstance(item, dict) or set(item) - {"resource", "mode"}:
                raise ScsError("RESOURCE_INVALID", "Resource entries contain resource and mode only.")
            resource = self.leases.resource(item.get("resource", ""))
            mode = self.leases.mode(item.get("mode", "write"))
            if resource.startswith("file:") and mode == "read":
                raise ScsError("RESOURCE_READ_UNSUPPORTED",
                               "Shared file read leases are not provided: a later reader would overwrite the "
                               "earlier lease. Omit the resource or use an exclusive write lease.")
            if resource.startswith("file:"):
                path = resource[5:]
                if not any(path == parent or path.startswith(parent + "/") for parent in scopes):
                    raise ScsError("SCOPE_EXPANDED", "Resource file must be inside the subtask scope.")
            if resource not in merged or mode == "write":
                merged[resource] = mode
        return [{"resource": key, "mode": merged[key]} for key in sorted(merged)]

    def _stored_resources(self, task: dict) -> tuple[list[dict], list[dict]]:
        """Normalize already-stored resources; drop legacy file read leases."""
        raw = task.get("resources") or []
        if not isinstance(raw, list):
            raise ScsError("RESOURCE_INVALID", "Stored subtask resources are invalid.")
        kept, warnings = [], []
        for item in raw:
            if not isinstance(item, dict) or set(item) - {"resource", "mode"}:
                raise ScsError("RESOURCE_INVALID", "Resource entries contain resource and mode only.")
            resource = self.leases.resource(item.get("resource", ""))
            mode = self.leases.mode(item.get("mode", "write"))
            if resource.startswith("file:") and mode == "read":
                warnings.append({"code": "READ_LEASE_SKIPPED", "resource": resource,
                                 "message": "Shared file read leases are not provided in this version; "
                                            "the stored read resource was skipped."})
                continue
            kept.append({"resource": resource, "mode": mode})
        return kept, warnings

    def _normalize_task(self, task: dict) -> dict:
        task = dict(task)
        task.setdefault("depends_on", [])
        task.setdefault("required", True)
        task.setdefault("execution_profile", None)
        if "resources" in task:
            # Idempotent create retries compare against the immutable original
            # definition. Legacy file read leases no longer exist, so ignore
            # them on both sides instead of reporting a false conflict.
            raw = task["resources"]
            resources = ([item for item in raw
                          if not (isinstance(item, dict)
                                  and str(item.get("resource", "")).startswith("file:")
                                  and item.get("mode") == "read")]
                         if isinstance(raw, list) else raw)
            task["resources"] = resources if resources else self._default_resources(task["kind"], task["allowed_files"])
        else:
            task["resources"] = self._default_resources(task["kind"], task["allowed_files"])
        return task

    @staticmethod
    def _definition_task(task: dict) -> dict:
        """Only the requested definition; ignore status/evidence/review runtime fields.

        Legacy packages (before 2.7) have no depends_on/required keys. Project
        only keys that exist so _normalize_task can apply its []/True defaults:
        a None placeholder would defeat setdefault and make a legitimate create
        replay look like a conflicting definition.
        """
        defined = {key: task[key] for key in TASK_DEFINITION_FIELDS if key in task}
        if not isinstance(defined.get("allowed_files"), list):
            defined["allowed_files"] = []
        return defined

    def _tasks(self, subtasks: list[dict], allowed: list[str]) -> list[dict]:
        if not subtasks or len(subtasks) > 100:
            raise ScsError("SUBTASK_INVALID", "Declare between one and 100 bounded subtasks.")
        out = []
        for task in subtasks:
            if set(task) - set(TASK_DEFINITION_FIELDS) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(task.get("id", ""))):
                raise ScsError("SUBTASK_INVALID", "Use unique simple subtask identifiers and known fields.")
            criteria = task.get("acceptance")
            if (task.get("kind") not in {"analyze", "implement", "test", "review"} or not isinstance(criteria, list)
                    or not criteria or not all(isinstance(x, str) and x.strip() for x in criteria)):
                raise ScsError("SUBTASK_INVALID", "Every subtask needs a kind and acceptance criteria.")
            profile = task.get("execution_profile")
            if profile not in (None, "standard", "journey"):
                raise ScsError("SUBTASK_INVALID", "execution_profile must be standard or journey.")
            if profile == "journey" and task["kind"] != "test":
                raise ScsError("SUBTASK_INVALID", "execution_profile=journey is only valid for test subtasks.")
            deps = task.get("depends_on", [])
            if (not isinstance(deps, list) or len(deps) > 100
                    or not all(isinstance(dep, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", dep) for dep in deps)
                    or len(set(deps)) != len(deps)):
                raise ScsError("SUBTASK_INVALID", "depends_on must contain unique simple subtask IDs.")
            required = task.get("required", True)
            if not isinstance(required, bool):
                raise ScsError("SUBTASK_INVALID", "required must be a boolean.")
            scopes = self._scope(task.get("allowed_files", []))
            if any(not any(p == parent or p.startswith(parent + "/") for parent in allowed) for p in scopes):
                raise ScsError("SCOPE_EXPANDED", "Subtask scope must be inside the work package's scope.")
            out.append({"id": task["id"], "kind": task["kind"], "allowed_files": scopes,
                        "acceptance": criteria, "depends_on": deps,
                        "resources": self._resources(task, scopes), "required": required,
                        "execution_profile": profile,
                        "status": "NOT_RUN", "evidence": []})
        ids = {t["id"] for t in out}
        if len(ids) != len(out):
            raise ScsError("SUBTASK_INVALID", "Subtask IDs must be unique.")
        for task in out:
            for dep in task["depends_on"]:
                if dep not in ids:
                    raise ScsError("SUBTASK_INVALID", "Dependency must name a subtask in this package.")
                if dep == task["id"]:
                    raise ScsError("SUBTASK_INVALID", "A subtask cannot depend on itself.")
        indegree = {task["id"]: len(task["depends_on"]) for task in out}
        children = {}
        for task in out:
            for dep in task["depends_on"]:
                children.setdefault(dep, []).append(task["id"])
        queue = [task_id for task_id, degree in indegree.items() if degree == 0]
        seen = 0
        while queue:
            current = queue.pop()
            seen += 1
            for child in children.get(current, []):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if seen != len(out):
            raise ScsError("DEPENDENCY_CYCLE", "Subtask dependencies must form a DAG.")
        return out

    def create(self, session_id: str, goal: str, allowed_files: list[str], acceptance: list[str],
               subtasks: list[dict], request_key: str) -> dict:
        if not goal.strip() or len(goal) > 4000 or not acceptance or not all(isinstance(a, str) and a.strip() for a in acceptance):
            raise ScsError("INPUT_INVALID", "Supply a bounded goal and nonempty acceptance criteria.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_key):
            raise ScsError("INPUT_INVALID", "A simple unique request_key is required for safe create retries.")
        allowed = self._scope(allowed_files)
        tasks = self._tasks(subtasks, allowed)
        # Deterministic idempotency key, scoped to this conversation.
        pid = "WP-" + hashlib.sha256(encode([session_id, request_key]).encode()).hexdigest()[:24]
        definition = {"goal": goal, "allowed_files": allowed, "acceptance": acceptance,
                      "subtasks": tasks, "request_key": request_key}
        with self.store.transaction(True) as con:
            session = self.state._session(con, session_id)
            intake = self.state.intake_status(con, session_id)
            if not intake["ready"]:
                raise ScsError("INTAKE_REQUIRED", "Confirm this conversation's intake before creating a work package.",
                               {"reason": intake["reason"], "source": intake["source"], "policy": intake["policy"]})
            old = con.execute("SELECT * FROM work_packages WHERE id=?", (pid,)).fetchone()
            if old:
                # Compare the immutable original definition, not later result updates.
                # review_required is a policy freeze derived from intake, not part
                # of the requested definition, so old rows without it still match.
                original = json.loads(old["data"])
                original = {k: original.get(k) for k in definition}
                original["subtasks"] = [
                    {**self._normalize_task(self._definition_task(task)),
                     "status": "NOT_RUN", "evidence": []}
                    for task in original["subtasks"] if not task.get("manual_review_slot")]
                if original != definition or old["revision"] != 1:
                    raise ScsError("IDEMPOTENCY_CONFLICT", "request_key was already used for another definition or revision.")
                return self._snapshot(old)
            docs = self.store.docs_root(con)
            self._records(docs)
            for task in tasks:
                self.store.paths.mkdir(f"{docs}/agent/evidence/{pid}/R1/{task['id']}")
            data = {**definition,
                    "review_required": session["mode"] == "long" and intake["policy"] == "packages"}
            con.execute("INSERT INTO work_packages VALUES(?,?,?,?,?,?,?,?)", (pid, session_id, 1, 1, "PENDING", encode(data), docs, now()))
            self.store.event(con, session_id, None, "work_package_create", "PACKAGE_CREATED")
            row = self._get(con, pid)
            self.store.revision_snapshot(con, row)
            return self._snapshot(row)

    def _requires_review(self, data: dict) -> bool:
        return not self.store.native_review and bool(data.get("review_required"))

    def _snapshot(self, row) -> dict:
        data = json.loads(row["data"])
        data["review_required"] = self._requires_review(data)
        return {"status": "OK", "id": row["id"], "owner_session": row["session_id"], "revision": row["revision"],
                "version": row["version"], "state": row["state"], **data,
                "docs_root": row["docs_root"], "records": [row["docs_root"] + "/agent/AGENT_PROJECT.md", row["docs_root"] + "/agent/AGENT_COORDINATION.md"],
                "record_sync_required": True, "record_sync_owner": "MAIN_CONVERSATION", "final_acceptance": False}

    def get(self, session_id: str, package_id: str) -> dict:
        with self.store.transaction() as con:
            row = self._get(con, package_id)
            self._access(con, session_id, row)
            result = self._snapshot(row)
            result["attempts"] = self._attempts(con, package_id)
            return result

    def _get_attempt(self, con, attempt_id: str):
        row = con.execute("SELECT * FROM work_attempts WHERE id=?", (attempt_id,)).fetchone()
        if row is None:
            raise ScsError("ATTEMPT_NOT_FOUND", "Execution attempt does not exist in this project.")
        return row

    def _attempt_snapshot(self, row) -> dict:
        return {"id": row["id"], "attempt_id": row["id"], "package_id": row["package_id"],
                "revision": row["revision"], "subtask_id": row["subtask_id"],
                "attempt_no": row["attempt_no"], "state": row["state"],
                "host_agent_id": row["host_agent_id"], "controller_session": row["controller_session"],
                "input_snapshot": json.loads(row["input_snapshot"]),
                "resources": json.loads(row["resources"]),
                "result": json.loads(row["result_data"]) if row["result_data"] else None,
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "last_observed_at": row["last_observed_at"]}

    def _attempts(self, con, package_id: str, revision: int | None = None) -> list[dict]:
        if revision is None:
            rows = con.execute("SELECT * FROM work_attempts WHERE package_id=? ORDER BY created_at,attempt_no", (package_id,))
        else:
            rows = con.execute("SELECT * FROM work_attempts WHERE package_id=? AND revision=? ORDER BY created_at,attempt_no", (package_id, revision))
        return [self._attempt_snapshot(row) for row in rows]

    def _active_attempts(self, con, package_id: str, revision: int | None = None) -> list[dict]:
        if revision is None:
            rows = con.execute("SELECT * FROM work_attempts WHERE package_id=? AND state!='ENDED' ORDER BY created_at", (package_id,))
        else:
            rows = con.execute("SELECT * FROM work_attempts WHERE package_id=? AND revision=? AND state!='ENDED' ORDER BY created_at", (package_id, revision))
        return [self._attempt_snapshot(row) for row in rows]

    def _assert_quiescent(self, con, package_id: str, revision: int | None, action: str):
        active = self._active_attempts(con, package_id, revision)
        if active:
            raise ScsError("ATTEMPTS_ACTIVE", "Finish or abandon active attempts before " + action + ".",
                           {"attempts": [attempt["id"] for attempt in active]})

    def _operation(self, con, operation_id: str, tool: str, session_id: str, payload: str):
        row = con.execute("SELECT * FROM work_operations WHERE id=?", (operation_id,)).fetchone()
        if row is None:
            return None
        if row["tool"] != tool or row["session_id"] != session_id or row["payload"] != payload:
            raise ScsError("OPERATION_CONFLICT", "operation_id was already used with another request.")
        return json.loads(row["result"])

    def _store_operation(self, con, operation_id: str, tool: str, session_id: str,
                         payload: str, result: dict):
        con.execute("INSERT INTO work_operations VALUES(?,?,?,?,?,?)",
                    (operation_id, tool, session_id, payload, encode(result), now()))

    @staticmethod
    def _operation_id(value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            raise ScsError("INPUT_INVALID", "operation_id must be a simple identifier.")
        return value

    def list_attempts(self, session_id: str, package_id: str, revision: int | None = None) -> dict:
        with self.store.transaction() as con:
            row = self._get(con, package_id)
            self._access(con, session_id, row)
            return {"status": "OK", "package_id": package_id, "revision": row["revision"],
                    "attempts": self._attempts(con, package_id, revision)}

    @staticmethod
    def _validate_host(host_agent_id: str | None):
        if host_agent_id is not None and (not isinstance(host_agent_id, str) or len(host_agent_id) > 256):
            raise ScsError("INPUT_INVALID", "host_agent_id must be a bounded host identifier.")

    def _assert_review_window(self, con, row, data, task):
        """A review runs alone; other work waits until that review attempt ends."""
        package_id, revision = row["id"], row["revision"]
        if task["kind"] == "review":
            rows = con.execute(
                "SELECT id,subtask_id FROM work_attempts WHERE package_id=? AND revision=? AND state!='ENDED'",
                (package_id, revision)).fetchall()
            others = [item["id"] for item in rows if item["subtask_id"] != task["id"]]
            if others:
                raise ScsError("REVIEW_WINDOW_ACTIVE",
                               "A review attempt requires the package to be quiescent; finish other active attempts first.",
                               {"attempts": others})
            return
        review_ids = sorted(item["id"] for item in data["subtasks"] if item["kind"] == "review")
        if not review_ids:
            return
        marks = ",".join("?" for _ in review_ids)
        rows = con.execute(
            f"SELECT id FROM work_attempts WHERE package_id=? AND revision=? AND state!='ENDED' "
            f"AND subtask_id IN ({marks})", (package_id, revision, *review_ids)).fetchall()
        if rows:
            raise ScsError("REVIEW_WINDOW_ACTIVE",
                           "A review attempt is active; start other work only after it ends.",
                           {"attempts": [item["id"] for item in rows]})

    def _assert_host_idle(self, con, host_agent_id: str | None) -> None:
        if self.store.native_review and host_agent_id:
            active = con.execute("SELECT id FROM work_attempts WHERE host_agent_id=? AND state!='ENDED' LIMIT 1",
                                 (host_agent_id,)).fetchone()
            if active:
                raise ScsError("HOST_AGENT_ACTIVE", "This ZCode Agent already has an active attempt.")

    def _dispatch_warning(self, con, session_id: str, agent_type: str) -> dict:
        # Select the newest ENDED record per Agent across packages. ENDED is an
        # SCS lifecycle fact, not proof of host completion or compatibility.
        rows = con.execute("""
            WITH candidates AS (
                SELECT id AS attempt_id, package_id, revision, subtask_id, host_agent_id,
                       controller_session, state, updated_at,
                       ROW_NUMBER() OVER (PARTITION BY host_agent_id ORDER BY updated_at DESC, id DESC) AS rank
                FROM work_attempts AS ended
                WHERE controller_session=? AND state='ENDED'
                  AND host_agent_id IS NOT NULL AND TRIM(host_agent_id)!=''
                  AND json_extract(input_snapshot, '$.agent_type')=?
                  AND NOT EXISTS (SELECT 1 FROM work_attempts AS active
                                  WHERE active.host_agent_id=ended.host_agent_id AND active.state!='ENDED')
            )
            SELECT attempt_id, package_id, revision, subtask_id, host_agent_id,
                   controller_session, state, updated_at
            FROM candidates WHERE rank=1 ORDER BY updated_at DESC, attempt_id DESC LIMIT ?
        """, (session_id, agent_type, ZCODE_DISPATCH_CANDIDATE_LIMIT + 1)).fetchall()
        warning = zcode_dispatch_warning()
        warning.update(agent_type=agent_type,
                       candidates=[dict(row) for row in rows[:ZCODE_DISPATCH_CANDIDATE_LIMIT]],
                       candidate_limit=ZCODE_DISPATCH_CANDIDATE_LIMIT,
                       candidates_truncated=len(rows) > ZCODE_DISPATCH_CANDIDATE_LIMIT,
                       candidate_scope="SCS controller_session only; host parent must be verified",
                       candidate_notice="候选仅为同 controller_session、同角色且无活动 attempt 的 ENDED 登记；"
                                        "不证明宿主 completed、可达、已相容或可续跑。空列表不证明没有宿主候选；"
                                        "顾问和独立收尾可能未登记 attempt，同批候选不代表自动分配。")
        return warning

    def _prepare_in_tx(self, con, session_id: str, row, data: dict, task: dict,
                       host_agent_id: str | None, ttl_seconds: float, user_requested: bool = False,
                       execution_profile: str | None = None) -> dict:
        """Create one attempt inside the caller's write transaction."""
        package_id, revision = row["id"], row["revision"]
        if row["state"] == "SUSPENDED":
            raise ScsError("PACKAGE_SUSPENDED", "Resume the work package before preparing another attempt.")
        if execution_profile not in (None, "standard", "journey"):
            raise ScsError("INPUT_INVALID", "execution_profile must be standard or journey.")
        effective_profile = self._execution_profile(con, row, revision, task)
        if effective_profile == "journey" and not (
                task.get("execution_profile") == "journey" or task.get("result_attempt_id")
                or self._has_journey_attempt(con, row, revision, task)):
            raise ScsError("JOURNEY_ATTEMPT_REQUIRED",
                           "Start a new revision to retry a legacy journey without an attempt.")
        if execution_profile is not None and execution_profile != effective_profile:
            raise ScsError("INPUT_INVALID", "execution_profile must match the subtask's recorded profile. "
                           "Declare execution_profile=journey in the subtask definition first.")
        resources, warnings = self._stored_resources(task)
        if effective_profile == "journey":
            intake = self.state.intake_status(con, session_id)
            if not intake["ready"]:
                raise ScsError("INTAKE_REQUIRED", "Confirm this conversation's intake before preparing a journey.",
                               {"reason": intake["reason"], "source": intake["source"], "policy": intake["policy"]})
            session = self.state._session(con, session_id)
            validation = json.loads(session["verification"]) if session["verification"] else []
            if "journey" not in validation:
                raise ScsError("VALIDATION_NOT_AUTHORIZED",
                               "Choose the journey verification scope before preparing a device attempt.")
            resources = self.leases._normalize_set(resources)
            devices = [item["resource"].split(":", 1)[1] for item in resources
                       if item["resource"].startswith("device:")]
            if len(devices) != 1:
                raise ScsError("JOURNEY_RESOURCE_REQUIRED",
                               "A journey attempt needs exactly one device:<id> write lease.")
            for item in resources:
                kind, resource_id = item["resource"].split(":", 1)
                if kind == "file":
                    continue
                if item["mode"] != "write":
                    raise ScsError("JOURNEY_RESOURCE_REQUIRED",
                                   "Journey device/serial/programmer resources need exclusive write leases.")
                if resource_id != devices[0]:
                    raise ScsError("JOURNEY_RESOURCE_REQUIRED",
                                   "Serial/programmer leases must use the same physical device id.")
        self._assert_review_window(con, row, data, task)
        if task["kind"] == "review" and self.store.native_review:
            if not user_requested:
                raise ScsError("REVIEW_MANUAL_REQUIRED", "Only an explicit user request for this package can start review.")
        if task["kind"] == "review" and self._requires_review(data) and not user_requested:
            used = con.execute(
                "SELECT COUNT(*) FROM work_attempts WHERE package_id=? AND revision=? AND subtask_id=?",
                (package_id, revision, task["id"])).fetchone()[0]
            if used >= 2:
                raise ScsError("REVIEW_BUDGET_EXHAUSTED",
                               "This revision has used its two automatic review attempts; ask the user before starting another.",
                               {"attempts_used": used})
        if task["kind"] == "review" and data.get("native_review_required") and not self.store.native_review:
            raise ScsError("NATIVE_REVIEW_DISABLED", "This package uses the fixed ZCode reviewer.")
        self._assert_host_idle(con, host_agent_id)
        active = con.execute("SELECT id FROM work_attempts WHERE package_id=? AND revision=? AND subtask_id=? AND state!='ENDED'",
                             (package_id, revision, task["id"])).fetchone()
        if active:
            raise ScsError("ATTEMPT_ACTIVE", "This subtask already has an active attempt.",
                           {"attempt_id": active["id"]})
        dependencies = []
        for dep_id in task.get("depends_on", []):
            dep_task = next((t for t in data["subtasks"] if t["id"] == dep_id), None)
            if dep_task is None:
                raise ScsError("SUBTASK_INVALID", "Dependency does not exist in this revision.")
            dep_active = con.execute("SELECT id FROM work_attempts WHERE package_id=? AND revision=? AND subtask_id=? AND state!='ENDED'",
                                     (package_id, revision, dep_id)).fetchone()
            if dep_active:
                raise ScsError("DEPENDENCY_NOT_READY", "Dependency still has an active attempt.",
                               {"subtask_id": dep_id, "attempt_id": dep_active["id"]})
            dep_attempt = con.execute("SELECT * FROM work_attempts WHERE package_id=? AND revision=? AND subtask_id=? AND state='ENDED' ORDER BY attempt_no DESC LIMIT 1",
                                      (package_id, revision, dep_id)).fetchone()
            if dep_attempt is not None and dep_attempt["result_state"] in {"PASS", "SUBSTITUTED"}:
                dependencies.append({"subtask_id": dep_id, "attempt_id": dep_attempt["id"],
                    "result_state": dep_attempt["result_state"],
                    "result": json.loads(dep_attempt["result_data"]) if dep_attempt["result_data"] else None})
            elif dep_task["status"] in {"PASS", "SUBSTITUTED"}:
                dependencies.append({"subtask_id": dep_id, "attempt_id": None,
                    "result_state": dep_task["status"],
                    "result": {"status": dep_task["status"], "evidence": dep_task["evidence"]}})
            else:
                raise ScsError("DEPENDENCY_NOT_READY", "Dependency has no verified result yet.",
                               {"subtask_id": dep_id})
        attempt_no = con.execute("SELECT COALESCE(MAX(attempt_no),0)+1 FROM work_attempts WHERE package_id=? AND revision=? AND subtask_id=?",
                                 (package_id, revision, task["id"])).fetchone()[0]
        attempt_id = identifier("A-")
        leases = self.leases.acquire_many(con, session_id, attempt_id, package_id, resources, ttl_seconds) if resources else []
        state = "LAUNCHING" if host_agent_id else "PREPARED"
        stamp = now()
        evidence_dir = f"{row['docs_root']}/agent/evidence/{package_id}/R{revision}/{task['id']}/{attempt_id}"
        input_snapshot = {"revision": revision, "subtask_id": task["id"], "dependencies": dependencies}
        if effective_profile != "standard":
            input_snapshot["execution_profile"] = effective_profile
        if self.store.native_review:
            effective = "journey" if effective_profile == "journey" else task["kind"]
            input_snapshot["agent_type"] = ZCODE_AGENTS[effective]
        review_subject = None
        if task["kind"] == "review":
            # Freeze the prepare-time subject inside the attempt: an ACCEPT
            # manifest must match this baseline and the current recomputation.
            review_subject = self._review_subject(row, data)
            input_snapshot["review_subject"] = review_subject
            if self.store.native_review:
                input_snapshot["user_requested"] = True
        self.store.paths.mkdir(evidence_dir)
        con.execute("INSERT INTO work_attempts(id,package_id,revision,subtask_id,attempt_no,state,host_agent_id,controller_session,input_snapshot,resources,result_state,result_data,created_at,updated_at,last_observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,NULL,NULL,?,?,?)",
                    (attempt_id, package_id, revision, task["id"], attempt_no, state,
                     host_agent_id, session_id, encode(input_snapshot), encode(resources), stamp, stamp, stamp))
        self.store.event(con, session_id, None, "work_attempt_prepare", "ATTEMPT_PREPARED")
        attempt = self._get_attempt(con, attempt_id)
        entry = {"subtask_id": task["id"], "attempt": self._attempt_snapshot(attempt),
                 "leases": leases, "evidence_dir": evidence_dir, "input_snapshot": input_snapshot,
                 "warnings": warnings}
        if review_subject is not None:
            entry["review_subject"] = review_subject
            entry["review_required"] = self._requires_review(data)
        if self.store.native_review:
            entry["agent_type"] = input_snapshot["agent_type"]
            warnings.append(self._dispatch_warning(con, session_id, entry["agent_type"]))
        return entry

    def prepare_attempt(self, session_id: str, package_id: str, expected_version: int,
                        subtask_id: str, host_agent_id: str | None = None,
                        ttl_seconds: float = 120, *, user_requested: bool = False,
                        execution_profile: str | None = None) -> dict:
        self._validate_host(host_agent_id)
        with self.store.transaction(True) as con:
            row = self._get(con, package_id)
            self._access(con, session_id, row)
            if row["state"] in CLOSED_STATES:
                raise ScsError("PACKAGE_CLOSED", "Closed work packages cannot start new attempts.")
            if expected_version != row["version"]:
                raise ScsError("VERSION_CONFLICT", "Refresh the work package before preparing an attempt.")
            data = json.loads(row["data"])
            task = next((t for t in data["subtasks"] if t["id"] == subtask_id), None)
            added = False
            if self.store.native_review and user_requested and subtask_id == "review":
                task = next((t for t in data["subtasks"] if t["kind"] == "review"), None)
                if task is None:
                    ids = {t["id"] for t in data["subtasks"]}
                    review_id = "review"
                    if review_id in ids:
                        review_id = next(f"review-{i}" for i in range(1, len(ids) + 1)
                                         if f"review-{i}" not in ids)
                    task = {"id": review_id, "kind": "review", "allowed_files": [],
                            "acceptance": ["Independent review of the current package"],
                            "depends_on": [], "resources": [], "required": False,
                            "status": "NOT_RUN", "evidence": [], "manual_review_slot": True}
                    data["subtasks"].append(task)
                    added = True
            if task is None:
                raise ScsError("SUBTASK_INVALID", "Subtask does not exist in this revision.")
            prepared = self._prepare_in_tx(con, session_id, row, data, task, host_agent_id, ttl_seconds,
                                           user_requested=user_requested, execution_profile=execution_profile)
            if added:
                con.execute("UPDATE work_packages SET data=?, version=version+1 WHERE id=?",
                            (encode(data), package_id))
                row = self._get(con, package_id)
                self.store.revision_snapshot(con, row)
            return {"status": "OK", "package_id": package_id, "revision": row["revision"],
                    "version": row["version"], **prepared}

    def prepare_attempts(self, session_id: str, package_id: str, expected_version: int,
                         subtask_ids: list[str], host_agent_ids: list[str | None] | None = None,
                         ttl_seconds: float = 120) -> dict:
        """Prepare a homogeneous batch atomically; the package version does not change."""
        if (not isinstance(subtask_ids, list) or not 1 <= len(subtask_ids) <= 100
                or not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", item) for item in subtask_ids)):
            raise ScsError("INPUT_INVALID", "Supply between one and 100 simple subtask IDs.")
        if len(set(subtask_ids)) != len(subtask_ids):
            raise ScsError("INPUT_INVALID", "subtask_ids must be unique in one batch.")
        if host_agent_ids is not None:
            if not isinstance(host_agent_ids, list) or len(host_agent_ids) != len(subtask_ids):
                raise ScsError("INPUT_INVALID", "host_agent_ids must match subtask_ids one to one.")
            for host in host_agent_ids:
                self._validate_host(host)
        with self.store.transaction(True) as con:
            row = self._get(con, package_id)
            self._access(con, session_id, row)
            if row["state"] in CLOSED_STATES:
                raise ScsError("PACKAGE_CLOSED", "Closed work packages cannot start new attempts.")
            if expected_version != row["version"]:
                raise ScsError("VERSION_CONFLICT", "Refresh the work package before preparing attempts.")
            data = json.loads(row["data"])
            by_id = {t["id"]: t for t in data["subtasks"]}
            selected = []
            for subtask_id in subtask_ids:
                task = by_id.get(subtask_id)
                if task is None:
                    raise ScsError("SUBTASK_INVALID", "Subtask does not exist in this revision.",
                                   {"subtask_id": subtask_id})
                selected.append(task)
            if any(self._execution_profile(con, row, row["revision"], task) == "journey" for task in selected):
                raise ScsError("JOURNEY_SINGLE_REQUIRED", "Prepare journey subtasks individually with work_attempt_prepare.")
            if len(selected) > 1 and any(task["kind"] == "review" for task in selected):
                raise ScsError("REVIEW_WINDOW_ACTIVE",
                               "Prepare a review attempt alone; it requires the package to be quiescent.")
            hosts = host_agent_ids or [None] * len(selected)
            entries = [self._prepare_in_tx(con, session_id, row, data, task, host, ttl_seconds)
                       for task, host in zip(selected, hosts)]
            response = {"status": "OK", "package_id": package_id, "revision": row["revision"],
                        "version": row["version"], "attempts": entries}
            subjects = [entry["review_subject"] for entry in entries if "review_subject" in entry]
            if subjects:
                response["review_subject"] = subjects[0]
            return response

    def _apply_result(self, con, row, data: dict, attempt, result: dict,
                      host_agent_id: str | None = None) -> dict:
        """Validate one ENDED result and apply it to the in-memory revision data."""
        if not isinstance(result, dict) or set(result) - {"status", "evidence", "note"}:
            raise ScsError("INPUT_INVALID", "An ending attempt needs a status, evidence and optional note.")
        status = result.get("status")
        if status not in RESULTS:
            raise ScsError("SUBTASK_INVALID", "Result status is not allowed.")
        evidence = result.get("evidence", [])
        if not isinstance(evidence, list):
            raise ScsError("EVIDENCE_INVALID", "Evidence must be a list.")
        if status == "PASS" and not evidence:
            raise ScsError("EVIDENCE_REQUIRED", "A PASS attempt needs verifiable evidence.")
        task = next((t for t in data["subtasks"] if t["id"] == attempt["subtask_id"]), None)
        if task is None:
            raise ScsError("SUBTASK_INVALID", "Attempt subtask no longer exists.")
        prefix = f"{row['docs_root']}/agent/evidence/{row['id']}/R{row['revision']}/{task['id']}/{attempt['id']}"
        if task["kind"] == "review":
            snapshot = json.loads(attempt["input_snapshot"]) if attempt["input_snapshot"] else {}
            agent_type = snapshot.get("agent_type")
            native = agent_type is not None
            host = host_agent_id if host_agent_id is not None else attempt["host_agent_id"]
            frozen = snapshot.get("review_subject")
            if not isinstance(frozen, str) or not re.fullmatch(r"[0-9a-f]{64}", frozen):
                # Attempts prepared before the subject was frozen keep the old
                # contract: the manifest is bound to the current recomputation.
                frozen = None
            if evidence and (self.store.native_review or data.get("native_review_required")) and not native:
                raise ScsError("REVIEW_ATTEMPT_REQUIRED", "Prepare a new attempt for the fixed ZCode reviewer.")
            if native and evidence:
                if agent_type not in FIXED_REVIEWER_IDENTITIES:
                    raise ScsError("REVIEW_AGENT_MISMATCH", "The prepared role must be the fixed ZCode reviewer.")
                if not frozen or not host:
                    raise ScsError("REVIEW_AGENT_REQUIRED", "Record the actual ZCode agent ID and prepared subject.")
                if status not in {"PASS", "FAIL"} or len(evidence) != 1:
                    raise ScsError("REVIEW_INVALID", "A native verdict needs exactly one manifest and a PASS/FAIL result.")
            if status == "PASS" and len(evidence) != 1:
                raise ScsError("REVIEW_INVALID", "A PASS review needs exactly one JSON manifest.")
            if status in {"FAIL", "BLOCKED", "NOT_RUN"} and len(evidence) > 1:
                raise ScsError("REVIEW_INVALID", "A review attempt records at most one JSON manifest.")
            if status == "PASS":
                # PASS/ACCEPT needs the manifest, the prepare-time baseline and
                # the current subject to agree; a failure without evidence never
                # recomputes, so a broken file set cannot trap an open attempt.
                current = self._review_subject(row, data)
                if frozen is not None and frozen != current:
                    raise ScsError("REVIEW_STALE", "The reviewed subject changed after preparation; "
                                   + ("end this attempt without a current verdict; do not retrigger package review."
                                      if self.store.native_review else "run a new review attempt."))
                expected = frozen if frozen is not None else current
            elif evidence:
                expected = frozen if frozen is not None else self._review_subject(row, data)
            else:
                expected = frozen
            manifests = []
            for entry in evidence:
                if (not isinstance(entry, dict) or set(entry) - {"path", "sha256", "required_content"}
                        or not isinstance(entry.get("path"), str)):
                    raise ScsError("EVIDENCE_INVALID", "Supply evidence path, sha256 and optional required_content.")
                check = verify_file(self.store, entry["path"], prefix, "review",
                                    entry.get("required_content"), subject_sha256=expected,
                                    native_review=native, host_agent_id=host,
                                    expected_agent_type=agent_type if native else FIXED_REVIEWER)
                if check["status"] != "VERIFIED" or entry.get("sha256") != check["sha256"]:
                    code = check.get("error_code") or "EVIDENCE_INVALID"
                    if code in {"REVIEW_STALE", "REVIEW_SUBJECT_TOO_LARGE", "REVIEW_SUBJECT_INCOMPLETE",
                                "REVIEW_AGENT_MISMATCH"}:
                        raise ScsError(code, check.get("message") or "The review subject is stale or unavailable.")
                    raise ScsError("EVIDENCE_INVALID", "Review evidence is invalid, insufficient or changed.")
                manifests.append(check["review"])
            if status == "PASS":
                if manifests[0]["verdict"] != "ACCEPT":
                    raise ScsError("REVIEW_VERDICT_INVALID", "A PASS review requires an ACCEPT manifest.")
                task["review"] = {"subject_sha256": expected, "verdict": "ACCEPT", "attempt_id": attempt["id"]}
            elif status == "FAIL" and manifests:
                if manifests[0]["verdict"] != "REJECT":
                    raise ScsError("REVIEW_VERDICT_INVALID", "A FAIL review with evidence must record REJECT.")
                task["review"] = {"subject_sha256": expected, "verdict": "REJECT", "attempt_id": attempt["id"]}
            else:
                task.pop("review", None)
            if native and manifests:
                task["review"].update(agent_type=agent_type, agent_id=host)
        else:
            profile = json.loads(attempt["input_snapshot"]).get("execution_profile")
            if profile != "journey" and self._execution_profile(con, row, row["revision"], task) == "journey":
                # A standard attempt prepared before the upgrade may need to
                # release its leases without replacing the stored journey result.
                if status in {"BLOCKED", "NOT_RUN"} and not evidence:
                    return {"status": status, "evidence": [], "note": result.get("note")}
                raise ScsError("JOURNEY_ATTEMPT_REQUIRED", "End a prepared journey attempt for this subtask.")
            for entry in evidence:
                if not isinstance(entry, dict) or set(entry) - {"path", "sha256", "required_content"} or not isinstance(entry.get("path"), str):
                    raise ScsError("EVIDENCE_INVALID", "Supply evidence path, sha256 and optional required_content.")
                check = self._verify_task_evidence(con, row, row["revision"], task, entry["path"],
                                                   entry.get("required_content"), attempt["id"])
                if check.get("error_code") == "JOURNEY_ATTEMPT_REQUIRED":
                    raise ScsError("JOURNEY_ATTEMPT_REQUIRED", "Journey evidence must belong to this journey attempt.")
                if check["status"] != "VERIFIED" or entry.get("sha256") != check["sha256"]:
                    raise ScsError("EVIDENCE_INVALID", "Attempt evidence is invalid, insufficient or changed.")
                if _journey_pass_invalid(check, status, profile):
                    raise ScsError("JOURNEY_NOT_PASSED",
                                   "A journey PASS needs journey evidence with satisfied assertions and exit_code 0.")
            if profile == "journey":
                task["result_attempt_id"] = attempt["id"]
        task.update(status=status, evidence=evidence)
        data["updated_at"] = now()
        return {"status": status, "evidence": evidence, "note": result.get("note")}

    def update_attempt(self, session_id: str, attempt_id: str, expected_version: int,
                       state: str | None = None, result: dict | None = None,
                       host_agent_id: str | None = None, operation_id: str | None = None) -> dict:
        operation_id = self._operation_id(operation_id)
        if state is None and result is None and host_agent_id is None:
            raise ScsError("INPUT_INVALID", "Specify a state, result, or host agent binding.")
        if result is not None and state != "ENDED":
            raise ScsError("INPUT_INVALID", "A result can only be recorded when ending the attempt.")
        if state is not None and state not in ATTEMPT_STATES:
            raise ScsError("ATTEMPT_STATE_INVALID", "Unknown attempt state.")
        if host_agent_id is not None and (not isinstance(host_agent_id, str) or len(host_agent_id) > 256):
            raise ScsError("INPUT_INVALID", "host_agent_id must be a bounded host identifier.")
        payload = encode({"attempt_id": attempt_id, "expected_version": expected_version,
                          "state": state, "result": result, "host_agent_id": host_agent_id})
        with self.store.transaction(True) as con:
            attempt = self._get_attempt(con, attempt_id)
            row = self._get(con, attempt["package_id"])
            self._access(con, session_id, row)
            if operation_id:
                stored = self._operation(con, operation_id, "work_attempt_update", session_id, payload)
                if stored is not None:
                    return stored
            if attempt["state"] == "ENDED":
                raise ScsError("ATTEMPT_CLOSED", "This attempt has already ended.")
            if expected_version != row["version"]:
                raise ScsError("VERSION_CONFLICT", "Refresh the work package before updating the attempt.")
            current = attempt["state"]
            target = state or current
            if target != current and target not in ATTEMPT_TRANSITIONS[current]:
                raise ScsError("ATTEMPT_STATE_INVALID", "Invalid attempt lifecycle transition.")
            data = json.loads(row["data"])
            task = next((t for t in data["subtasks"] if t["id"] == attempt["subtask_id"]), None)
            if task is None:
                raise ScsError("SUBTASK_INVALID", "Attempt subtask no longer exists.")
            host = host_agent_id if host_agent_id is not None else attempt["host_agent_id"]
            if host != attempt["host_agent_id"]:
                snapshot = json.loads(attempt["input_snapshot"])
                if attempt["host_agent_id"] and (self.store.native_review or snapshot.get("agent_type")):
                    raise ScsError("HOST_AGENT_MISMATCH", "An attempt is bound to one ZCode Agent.")
                self._assert_host_idle(con, host)
            result_data = None
            if target == "ENDED":
                if attempt["revision"] != row["revision"]:
                    raise ScsError("REVISION_CONFLICT", "This attempt belongs to another revision.")
                result_data = self._apply_result(con, row, data, attempt, result, host)
            stamp = now()
            con.execute("UPDATE work_attempts SET state=?,host_agent_id=?,result_state=?,result_data=?,updated_at=?,last_observed_at=? WHERE id=?",
                        (target, host,
                         result_data["status"] if result_data else attempt["result_state"],
                         encode(result_data) if result_data else attempt["result_data"],
                         stamp, stamp, attempt_id))
            if target == "ENDED":
                # _apply_result verifies this attempt's evidence. Historical
                # reports are rechecked at acceptance and handoff, not release.
                con.execute("UPDATE work_packages SET version=version+1,data=? WHERE id=? AND version=?",
                            (encode(data), row["id"], expected_version))
                self.store.revision_snapshot(con, self._get(con, row["id"]))
                self.leases.release_attempt(con, attempt_id, session_id)
            self.store.event(con, session_id, None, "work_attempt_update",
                             "ATTEMPT_ENDED" if target == "ENDED" else "ATTEMPT_" + target)
            updated = self._get_attempt(con, attempt_id)
            package = self._get(con, row["id"])
            response = {"status": "OK", "package_id": row["id"], "revision": package["revision"],
                        "version": package["version"], "attempt": self._attempt_snapshot(updated),
                        "task_status": next(t["status"] for t in json.loads(package["data"])["subtasks"]
                                            if t["id"] == attempt["subtask_id"])}
            if operation_id:
                self._store_operation(con, operation_id, "work_attempt_update", session_id, payload, response)
            return response

    def update_attempts(self, session_id: str, updates: list[dict], operation_id: str) -> dict:
        """End same-package attempts atomically: one version increment, all or nothing."""
        if not isinstance(updates, list) or not 1 <= len(updates) <= 100:
            raise ScsError("INPUT_INVALID", "Supply between one and 100 attempt updates.")
        seen = set()
        for item in updates:
            if not isinstance(item, dict) or set(item) != {"attempt_id", "result"}:
                raise ScsError("INPUT_INVALID", "Each update needs exactly attempt_id and result.")
            attempt_id = item["attempt_id"]
            if not isinstance(attempt_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", attempt_id):
                raise ScsError("INPUT_INVALID", "attempt_id must be a simple identifier.")
            if attempt_id in seen:
                raise ScsError("INPUT_INVALID", "attempt_id values must be unique in one batch.")
            seen.add(attempt_id)
        if not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", operation_id):
            raise ScsError("INPUT_INVALID", "operation_id must be a simple identifier.")
        payload = encode({"updates": updates})
        with self.store.transaction(True) as con:
            self.state._session(con, session_id)
            first = self._get_attempt(con, updates[0]["attempt_id"])
            row = self._get(con, first["package_id"])
            self._access(con, session_id, row)
            # A replay returns the original receipt even after the attempts ended.
            stored = self._operation(con, operation_id, "work_attempt_update_many", session_id, payload)
            if stored is not None:
                return stored
            if row["state"] in CLOSED_STATES:
                raise ScsError("PACKAGE_CLOSED", "Closed work packages are immutable.")
            attempts = []
            for item in updates:
                attempt = self._get_attempt(con, item["attempt_id"])
                if attempt["package_id"] != row["id"]:
                    raise ScsError("BATCH_PACKAGE_MISMATCH", "All attempts must belong to one work package.")
                if attempt["revision"] != row["revision"]:
                    raise ScsError("BATCH_REVISION_MISMATCH", "All attempts must belong to the current revision.")
                if attempt["state"] == "ENDED":
                    raise ScsError("ATTEMPT_CLOSED", "This attempt has already ended.",
                                   {"attempt_id": attempt["id"]})
                attempts.append(attempt)
            data = json.loads(row["data"])
            by_id = {t["id"]: t for t in data["subtasks"]}
            kinds = [by_id.get(attempt["subtask_id"], {}).get("kind") for attempt in attempts]
            if any(kind is None for kind in kinds):
                raise ScsError("SUBTASK_INVALID", "Attempt subtask no longer exists.")
            if sum(kind == "review" for kind in kinds) > 1 or (any(kind == "review" for kind in kinds) and len(attempts) > 1):
                raise ScsError("REVIEW_WINDOW_ACTIVE", "End a review attempt alone; do not mix it with other results.")
            expected_version = row["version"]
            stamp = now()
            for attempt, item in zip(attempts, updates):
                result_data = self._apply_result(con, row, data, attempt, item["result"])
                con.execute("UPDATE work_attempts SET state='ENDED',result_state=?,result_data=?,updated_at=?,last_observed_at=? WHERE id=?",
                            (result_data["status"], encode(result_data), stamp, stamp, attempt["id"]))
                self.leases.release_attempt(con, attempt["id"], session_id)
            con.execute("UPDATE work_packages SET version=version+1,data=? WHERE id=? AND version=?",
                        (encode(data), row["id"], expected_version))
            package = self._get(con, row["id"])
            self.store.revision_snapshot(con, package)
            self.store.event(con, session_id, None, "work_attempt_update_many", "ATTEMPTS_ENDED")
            response = {"status": "OK", "package_id": row["id"], "revision": package["revision"],
                        "version": package["version"],
                        "attempts": [self._attempt_snapshot(self._get_attempt(con, attempt["id"]))
                                     for attempt in attempts],
                        "task_statuses": {task["id"]: task["status"] for task in json.loads(package["data"])["subtasks"]}}
            self._store_operation(con, operation_id, "work_attempt_update_many", session_id, payload, response)
            return response

    def note(self, session_id: str, attempt_id: str, note: str, note_id: str) -> dict:
        """Append an audited note. No package version change, no lease renewal, no liveness claim."""
        if not isinstance(note, str) or not note.strip() or len(note) > 2000:
            raise ScsError("INPUT_INVALID", "Note must be 1..2000 characters.")
        note_id = self._operation_id(note_id)
        payload = encode({"attempt_id": attempt_id, "note": note, "note_id": note_id})
        with self.store.transaction(True) as con:
            self.state._session(con, session_id)
            attempt = self._get_attempt(con, attempt_id)
            row = self._get(con, attempt["package_id"])
            self._access(con, session_id, row)
            stored = self._operation(con, note_id, "work_attempt_note", session_id, payload)
            if stored is not None:
                return stored
            at = now()
            self.store.event(con, session_id, None, "work_attempt_note",
                             encode({"attempt_id": attempt_id, "note": note, "note_id": note_id, "at": at}))
            response = {"status": "OK", "recorded": True, "note_id": note_id, "attempt_id": attempt_id,
                        "package_id": row["id"], "at": at, "note": note,
                        "liveness_proven": False, "lease_renewed": False, "version_changed": False}
            self._store_operation(con, note_id, "work_attempt_note", session_id, payload, response)
            return response

    @staticmethod
    def _mermaid_id(prefix: str, value: str) -> str:
        return prefix + re.sub(r"[^A-Za-z0-9_]", "_", str(value))[:40]

    @staticmethod
    def _mermaid_label(value: str) -> str:
        # Truncate the raw text before entity escaping so a label never ends
        # inside an entity such as ``#quot;``.
        text = " ".join(str(value).split())[:120]
        text = text.replace("\\", "/").replace("#", "#35;").replace("&", "#38;")
        for source, target in (('"', "#quot;"), ("[", "#91;"), ("]", "#93;"),
                               ("{", "#123;"), ("}", "#125;"), ("(", "#40;"), (")", "#41;"),
                               ("<", "#60;"), (">", "#62;"), ("|", "#124;"), ("`", "#96;")):
            text = text.replace(source, target)
        return text

    def run_map(self, session_id: str, package_id: str | None = None,
                revision: int | None = None, limit: int = 100) -> dict:
        """Bounded read-only run map: JSON plus Mermaid, no file writes.

        Packages <= 64, attempts <= 200, notes <= 3 per attempt. Review subject
        freshness is enforced at REVIEW_READY/COMPLETED/export, not recomputed
        here, so this call stays cheap and never mutates state.
        """
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ScsError("INPUT_INVALID", "limit must be a positive integer.")
        if revision is not None and package_id is None:
            raise ScsError("INPUT_INVALID", "revision requires package_id.")
        captured = now()
        package_cap = min(limit, 64)
        attempt_cap = 200
        subtask_cap = 500
        truncated = False
        with self.store.transaction() as con:
            self.state._session(con, session_id)
            if package_id is not None:
                row = self._get(con, package_id)
                self._access(con, session_id, row)
                rows = [row]
            else:
                rows = con.execute(
                    "SELECT * FROM work_packages WHERE session_id=? ORDER BY created_at DESC,id LIMIT ?",
                    (session_id, package_cap + 1)).fetchall()
                if len(rows) > package_cap:
                    truncated = True
                    rows = rows[:package_cap]
            packages, subtasks, attempts_out, blockers, review_out = [], [], [], [], []
            attempts_by_task: dict[str, list[dict]] = {}
            attempt_ids: set[str] = set()
            attempt_budget = attempt_cap
            subtask_budget = subtask_cap
            for row in rows:
                current_revision, current_state, current_data = row["revision"], row["state"], row["data"]
                map_version = row["version"]
                if revision is not None:
                    if revision == row["revision"]:
                        map_revision, map_state = revision, row["state"]
                    else:
                        snapshot = con.execute(
                            "SELECT state,data,version FROM work_package_revisions WHERE package_id=? AND revision=?",
                            (row["id"], revision)).fetchone()
                        if snapshot is None:
                            raise ScsError("REVISION_NOT_FOUND", "No stored snapshot for this revision.",
                                           {"package_id": row["id"], "revision": revision})
                        map_revision, map_state, current_data = revision, snapshot["state"], snapshot["data"]
                        # A historical map reports the version captured for that
                        # revision, not the package row's current version.
                        map_version = snapshot["version"]
                else:
                    map_revision, map_state = current_revision, current_state
                data = json.loads(current_data)
                review_required = self._requires_review(data)
                package_entry = {"id": row["id"], "revision": map_revision, "version": map_version,
                                 "state": map_state, "goal": data.get("goal", ""),
                                 "review_required": review_required,
                                 "force_closed": bool(data.get("force_closed"))}
                packages.append(package_entry)
                reviews = []
                for task in data.get("subtasks", []):
                    if subtask_budget <= 0:
                        truncated = True
                        break
                    subtask_budget -= 1
                    subtasks.append({"package_id": row["id"], "id": task["id"], "kind": task.get("kind"),
                                     "status": task.get("status"), "required": task.get("required", True),
                                     "depends_on": task.get("depends_on", [])})
                    if task.get("kind") == "review":
                        reviews.append({"subtask_id": task["id"], "status": task.get("status"),
                                        "verdict": (task.get("review") or {}).get("verdict"),
                                        "subject_sha256": (task.get("review") or {}).get("subject_sha256")})
                    if self.store.native_review and task.get("kind") == "review":
                        continue
                    if task.get("status") == "FAIL":
                        blockers.append({"package_id": row["id"], "code": "SUBTASK_FAIL",
                                         "subtask_id": task["id"]})
                    elif task.get("status") == "BLOCKED":
                        blockers.append({"package_id": row["id"], "code": "SUBTASK_BLOCKED",
                                         "subtask_id": task["id"]})
                if attempt_budget > 0:
                    attempt_rows = con.execute(
                        "SELECT * FROM work_attempts WHERE package_id=? AND revision=? "
                        "ORDER BY created_at,attempt_no LIMIT ?",
                        (row["id"], map_revision, attempt_budget + 1)).fetchall()
                else:
                    attempt_rows = []
                    if con.execute("SELECT 1 FROM work_attempts WHERE package_id=? AND revision=? LIMIT 1",
                                   (row["id"], map_revision)).fetchone():
                        truncated = True
                if len(attempt_rows) > attempt_budget:
                    truncated = True
                    attempt_rows = attempt_rows[:attempt_budget]
                active = 0
                for attempt_row in attempt_rows:
                    attempt_budget -= 1
                    attempt = self._attempt_snapshot(attempt_row)
                    attempt_ids.add(attempt["id"])
                    attempts_by_task.setdefault(f"{row['id']}|{attempt['subtask_id']}", []).append(attempt)
                    attempts_out.append({"id": attempt["id"], "package_id": row["id"],
                                         "subtask_id": attempt["subtask_id"],
                                         "attempt_no": attempt["attempt_no"], "state": attempt["state"],
                                         "host_agent_id": attempt["host_agent_id"],
                                         "result_state": attempt_row["result_state"],
                                         "active": attempt["state"] != "ENDED"})
                    if attempt["state"] != "ENDED":
                        active += 1
                if active:
                    blockers.append({"package_id": row["id"], "code": "ACTIVE_ATTEMPTS", "count": active})
                if review_required or reviews:
                    accepted = any(review["status"] == "PASS" and review["verdict"] == "ACCEPT"
                                   for review in reviews)
                    rejected = any(review["verdict"] == "REJECT" for review in reviews)
                    review_out.append({"package_id": row["id"], "review_required": review_required,
                                       "status": ("ACCEPTED" if accepted else "REJECTED" if rejected
                                                  else "REQUIRED" if review_required else "PENDING"),
                                       "reviews": reviews})
                    if review_required and not accepted:
                        blockers.append({"package_id": row["id"],
                                         "code": "REVIEW_REJECTED" if rejected else "REVIEW_REQUIRED"})
            notes: dict[str, list[dict]] = {}
            note_rows = con.execute(
                "SELECT at,event FROM events WHERE tool='work_attempt_note' ORDER BY seq DESC LIMIT 2001").fetchall()
            if len(note_rows) > 2000:
                truncated = True
                note_rows = note_rows[:2000]
            for note_row in note_rows:
                try:
                    payload = json.loads(note_row["event"])
                except (ValueError, TypeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                attempt_id = payload.get("attempt_id")
                if attempt_id not in attempt_ids or len(notes.get(attempt_id, [])) >= 3:
                    continue
                notes.setdefault(attempt_id, []).append({
                    "at": payload.get("at") or note_row["at"],
                    "note_id": payload.get("note_id"),
                    "note": payload.get("note", "")})
            # Newest first for display; Mermaid stays bounded to the shown nodes.
            lines = ["graph TD"]
            for package_index, package in enumerate(packages):
                package_node = self._mermaid_id(f"p{package_index}_", package["id"])
                package_label = f'{package["id"]} · {str(package["goal"])[:40]} · {package["state"]}'
                lines.append(f'{package_node}["{self._mermaid_label(package_label)}"]')
                for task_index, task in enumerate(
                        [item for item in subtasks if item["package_id"] == package["id"]]):
                    task_node = self._mermaid_id(f"s{package_index}_{task_index}_", task["id"])
                    lines.append(f'{package_node} --> {task_node}["{self._mermaid_label(task["id"] + " · " + str(task["kind"]) + " · " + str(task["status"]))}"]')
                    for attempt_index, attempt in enumerate(attempts_by_task.get(f'{package["id"]}|{task["id"]}', [])):
                        attempt_node = self._mermaid_id(
                            f"a{package_index}_{task_index}_{attempt_index}_", attempt["id"])
                        result = str((attempt["result"] or {}).get("status", "-")) if isinstance(attempt["result"], dict) else "-"
                        count = len(notes.get(attempt["id"], []))
                        label = f'{attempt["id"]} · {attempt["state"]} · {result}' + (f" · note×{count}" if count else "")
                        lines.append(f'{task_node} --> {attempt_node}["{self._mermaid_label(label)}"]')
        return {"status": "OK", "session_id": session_id, "captured_at": captured,
                "packages": packages, "subtasks": subtasks, "attempts": attempts_out,
                "notes": notes, "blockers": blockers, "review": review_out,
                "mermaid": "\n".join(lines), "truncated": truncated,
                "limits": {"packages": package_cap, "attempts": attempt_cap,
                           "notes_per_attempt": 3}}

    @staticmethod
    def _has_journey_attempt(con, row, revision: int, task: dict) -> bool:
        # Older revisions recorded the profile only on the attempt. Check the
        # entire revision history, not just the latest attempt or task result.
        return con.execute(
            "SELECT 1 FROM work_attempts WHERE package_id=? AND revision=? AND subtask_id=? "
            "AND json_extract(input_snapshot, '$.execution_profile')='journey' LIMIT 1",
            (row["id"], revision, task["id"])).fetchone() is not None

    def _execution_profile(self, con, row, revision: int, task: dict) -> str:
        if task["kind"] != "test":
            return task.get("execution_profile") or "standard"
        if (task.get("execution_profile") == "journey" or task.get("result_attempt_id")
                or self._has_journey_attempt(con, row, revision, task)):
            return "journey"
        # A pre-attempt journey could also be registered directly. Do not let
        # a replacement report erase that identity; require a new revision.
        for entry in task.get("evidence", []):
            check = self._verify_task_evidence(con, row, revision, task, entry["path"])
            if check.get("kind") == "journey":
                return "journey"
        return task.get("execution_profile") or "standard"

    @staticmethod
    def _journey_attempt(con, package_id: str, revision: int, subtask_id: str, attempt_id: str | None):
        if attempt_id is None:
            return None
        attempt = con.execute(
            "SELECT * FROM work_attempts WHERE id=? AND package_id=? AND revision=? AND subtask_id=?",
            (attempt_id, package_id, revision, subtask_id)).fetchone()
        if attempt and json.loads(attempt["input_snapshot"]).get("execution_profile") == "journey":
            return attempt
        return None

    def _verify_task_evidence(self, con, row, revision: int, task: dict, path: str,
                              required_content: list[str] | None = None, attempt_id: str | None = None) -> dict:
        prefix = f"{row['docs_root']}/agent/evidence/{row['id']}/R{revision}/{task['id']}"

        def journey_scope(clean: str) -> str | None:
            parsed_id, slash, _ = clean[len(prefix) + 1:].partition("/")
            if slash and self._journey_attempt(con, row["id"], revision, task["id"], parsed_id):
                return f"{prefix}/{parsed_id}"
            return None

        scope = f"{prefix}/{attempt_id}" if attempt_id else prefix
        check = verify_file(self.store, path, scope, task["kind"], required_content, journey_scope=journey_scope)
        if (check.get("error_code") == "EVIDENCE_SCOPE"
                and (task.get("execution_profile") == "journey" or task.get("result_attempt_id"))):
            return {"status": "INVALID", "error_code": "JOURNEY_ATTEMPT_REQUIRED",
                    "message": "Journey evidence must stay inside the matching attempt."}
        return check

    def evidence(self, session_id: str, package_id: str, revision: int, subtask_id: str,
                 path: str, required_content: list[str] | None = None) -> dict:
        with self.store.transaction() as con:
            row = self._get(con, package_id)
            self._access(con, session_id, row)
            if revision != row["revision"]:
                raise ScsError("REVISION_CONFLICT", "Evidence is for another revision.")
            data = json.loads(row["data"])
            task = next((t for t in data["subtasks"] if t["id"] == subtask_id), None)
            if not task:
                raise ScsError("SUBTASK_INVALID", "Subtask does not exist in this revision.")
            prefix = f"{row['docs_root']}/agent/evidence/{package_id}/R{revision}/{subtask_id}"
            if task["kind"] == "review":
                try:
                    clean = self.store.paths.validate(path)
                except ScsError as exc:
                    return {"status": "INVALID", "error_code": exc.code, "message": str(exc)}
                if not clean.startswith(prefix + "/"):
                    return verify_file(self.store, path, prefix, task["kind"], required_content)
                attempt_id = clean[len(prefix) + 1:].partition("/")[0]
                attempt = con.execute(
                    "SELECT input_snapshot,host_agent_id FROM work_attempts WHERE id=? AND package_id=? "
                    "AND revision=? AND subtask_id=?", (attempt_id, package_id, revision, subtask_id)).fetchone()
                snapshot = json.loads(attempt["input_snapshot"]) if attempt else {}
                agent_type = snapshot.get("agent_type")
                if agent_type in FIXED_REVIEWER_IDENTITIES:
                    if not attempt["host_agent_id"] or not snapshot.get("review_subject"):
                        return {"status": "INVALID", "error_code": "REVIEW_AGENT_REQUIRED"}
                    return verify_file(self.store, clean, f"{prefix}/{attempt_id}", "review",
                                       subject_sha256=snapshot["review_subject"], native_review=True,
                                       host_agent_id=attempt["host_agent_id"], expected_agent_type=agent_type)
                if self.store.native_review or data.get("native_review_required") or agent_type:
                    return {"status": "INVALID", "error_code": "REVIEW_ATTEMPT_REQUIRED"}
                return verify_file(self.store, path, prefix, "review", required_content)
            return self._verify_task_evidence(con, row, revision, task, path, required_content)

    def _review_subject(self, row, data: dict) -> str:
        """Digest of the reviewed definition, non-review reports and file snapshot.

        The snapshot walks package allowed_files with directory descriptors and
        O_NOFOLLOW at every scope component, so a replaced ancestor or scope
        symlink fails instead of reading outside the project root. A symlink
        inside a reviewed scope is never followed or read: its relative path
        and raw readlink target bytes (hex-encoded) enter the subject, so
        adding, removing or retargeting it invalidates an earlier ACCEPT at
        the byte level. Duplicate and overlapping
        scopes are normalized and each entry is read once. VCS/runtime/evidence
        trees are excluded, special files fail the subject, and the walk stops
        at 500 entries or 100 MB; canonical JSON keeps the digest stable so an
        ACCEPT review can be bound to it. The payload carries
        ``subject_version`` and is serialized with ASCII escapes, so every
        upgrade or encoding change invalidates older ACCEPTs (REVIEW_STALE)
        and surrogateescape names never fail digest computation.
        """
        files: dict[str, str] = {}
        links: dict[str, str] = {}
        total = 0
        docs = row["docs_root"].rstrip("/")
        evidence_prefix = docs + "/agent/evidence"

        def incomplete(message: str):
            raise ScsError("REVIEW_SUBJECT_INCOMPLETE", message)

        def read_entry(directory_fd: int, name: str, relative: str):
            nonlocal total
            if relative in links:
                # The same path was recorded as a link earlier in this walk;
                # its type changed while the subject was being computed.
                incomplete("The reviewed file set changed while computing its digest.")
            if relative in files:
                # The same normalized path can be reached through overlapping
                # scopes ("src" plus "src/a.py"); count and digest it once so
                # the file/byte limits are not falsely exceeded.
                return
            try:
                handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory_fd)
            except OSError:
                incomplete("The reviewed file set changed while computing its digest.")
            try:
                info = os.fstat(handle)
                if not stat.S_ISREG(info.st_mode):
                    incomplete("A reviewed scope contains a special file; replace it with a regular file.")
                total += info.st_size
                if len(files) + len(links) >= REVIEW_MAX_FILES or total > REVIEW_MAX_BYTES:
                    raise ScsError("REVIEW_SUBJECT_TOO_LARGE",
                                   "Review subject exceeds 500 files or 100 MB; narrow allowed_files.",
                                   {"files": len(files) + 1, "links": len(links), "bytes": total})
                digest = hashlib.sha256()
                try:
                    while True:
                        chunk = os.read(handle, 1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                except OSError:
                    incomplete("The reviewed file set changed while computing its digest.")
                files[relative] = digest.hexdigest()
            finally:
                os.close(handle)

        def read_link(directory_fd: int, name: str, relative: str):
            """Record a link's identity and target; never resolve or read it."""
            if relative in files:
                # The same path was recorded as a regular file earlier in this
                # walk; its type changed while the subject was being computed.
                incomplete("The reviewed file set changed while computing its digest.")
            if relative in links:
                # Overlapping scopes can reach the same link; record it once.
                return
            if len(files) + len(links) >= REVIEW_MAX_FILES:
                raise ScsError("REVIEW_SUBJECT_TOO_LARGE",
                               "Review subject exceeds 500 files or 100 MB; narrow allowed_files.",
                               {"files": len(files), "links": len(links) + 1, "bytes": total})
            try:
                # A bytes path makes readlink return the raw target bytes, so
                # no decode step can collapse distinct byte sequences.
                target = os.readlink(os.fsencode(name), dir_fd=directory_fd)
            except OSError:
                incomplete("The reviewed file set changed while computing its digest.")
            # Hexadecimal is lossless and unambiguous: a non-UTF-8 target and
            # its literal ASCII spelling stay different subject values, so any
            # byte-level retargeting changes the digest.
            links[relative] = target.hex()

        def walk(directory_fd: int, relative_dir: str):
            try:
                names = sorted(os.listdir(directory_fd))
            except OSError:
                incomplete("A reviewed directory cannot be enumerated; the subject is incomplete.")
            for name in names:
                relative = f"{relative_dir}/{name}" if relative_dir else name
                if (name in REVIEW_EXCLUDED or relative == evidence_prefix
                        or relative.startswith(evidence_prefix + "/")):
                    continue
                try:
                    info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                except OSError:
                    incomplete("The reviewed file set changed while computing its digest.")
                if stat.S_ISLNK(info.st_mode):
                    # A link inside the scope is part of the reviewed subject:
                    # record its path and readlink target without following or
                    # reading it, so adding, deleting or retargeting a link
                    # invalidates an earlier ACCEPT.
                    read_link(directory_fd, name, relative)
                elif stat.S_ISDIR(info.st_mode):
                    try:
                        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                        dir_fd=directory_fd)
                    except OSError:
                        incomplete("A reviewed directory is inaccessible or changed.")
                    try:
                        walk(child, relative)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    read_entry(directory_fd, name, relative)
                else:
                    incomplete("A reviewed scope contains a special file; replace it with a regular file.")

        seen_scopes: set[str] = set()
        for scope in data.get("allowed_files", []):
            if not isinstance(scope, str) or not scope:
                continue
            if any(part in REVIEW_EXCLUDED for part in scope.split("/")):
                continue
            try:
                parts = self.store.paths.parts(scope)
            except ScsError:
                incomplete("A stored review scope is not a safe project-relative path.")
            normalized = "/".join(parts)
            if any(part in REVIEW_EXCLUDED for part in parts):
                continue
            if normalized == evidence_prefix or normalized.startswith(evidence_prefix + "/"):
                continue
            if normalized in seen_scopes:
                # Repeated scopes must not read or count the same files twice.
                continue
            seen_scopes.add(normalized)
            directory_fd = os.open(self.store.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                for part in parts[:-1]:
                    try:
                        info = os.stat(part, dir_fd=directory_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        break
                    except OSError:
                        incomplete("A reviewed scope path cannot be inspected.")
                    if stat.S_ISLNK(info.st_mode):
                        incomplete("A reviewed scope traverses a symlink; replace it with a real path.")
                    if not stat.S_ISDIR(info.st_mode):
                        # A non-directory ancestor cannot contain this scope.
                        break
                    try:
                        child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                        dir_fd=directory_fd)
                    except OSError:
                        incomplete("A reviewed scope path is inaccessible or changed.")
                    os.close(directory_fd)
                    directory_fd = child
                else:
                    try:
                        info = os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        info = None
                    except OSError:
                        incomplete("A reviewed scope path cannot be inspected.")
                    if info is not None:
                        if stat.S_ISLNK(info.st_mode):
                            incomplete("A reviewed scope is a symlink; replace it with a real path.")
                        if stat.S_ISREG(info.st_mode):
                            read_entry(directory_fd, parts[-1], normalized)
                        elif stat.S_ISDIR(info.st_mode):
                            try:
                                child = os.open(parts[-1],
                                                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                                dir_fd=directory_fd)
                            except OSError:
                                incomplete("A reviewed scope is inaccessible or changed.")
                            try:
                                walk(child, normalized)
                            finally:
                                os.close(child)
                        else:
                            incomplete("A reviewed scope is a special file; replace it with a regular file or directory.")
            finally:
                os.close(directory_fd)
        definition = {
            "goal": data["goal"],
            "allowed_files": data["allowed_files"],
            "acceptance": data["acceptance"],
            "subtasks": [
                {key: task.get(key) for key in TASK_DEFINITION_FIELDS
                 if key != "execution_profile" or task.get(key) is not None}
                for task in sorted(data["subtasks"], key=lambda item: item["id"])
                if task["kind"] != "review"],
        }
        results = [
            {"id": task["id"], "status": task["status"],
             "evidence": [{"path": entry.get("path"), "sha256": entry.get("sha256")}
                          for entry in task["evidence"]]}
            for task in sorted(data["subtasks"], key=lambda item: item["id"])
            if task["kind"] != "review"]
        payload = {"subject_version": REVIEW_SUBJECT_VERSION,
                   "package_id": row["id"], "revision": row["revision"],
                   "definition": definition, "results": results, "files": files}
        if links:
            # Present only when the reviewed snapshot contains links. The
            # subject_version field (not this conditional field) is what
            # isolates the digest from pre-versioning records, so every old
            # ACCEPT is stale regardless of whether links exist.
            payload["links"] = links
        return hashlib.sha256(_review_subject_json(payload).encode()).hexdigest()

    def _review_gate(self, data: dict, row, revision: int):
        current = self._review_subject(row, data)
        stale = False
        for task in data["subtasks"]:
            if task["kind"] != "review" or task["status"] != "PASS":
                continue
            review = task.get("review") or {}
            if review.get("verdict") == "ACCEPT":
                native = self.store.native_review or data.get("native_review_required")
                if (review.get("subject_sha256") == current
                        and (not native or review.get("agent_type") in FIXED_REVIEWER_IDENTITIES)):
                    return
                stale = True
        if stale:
            raise ScsError("REVIEW_STALE",
                           "The ACCEPT review covers an older subject; run a new review attempt.")
        raise ScsError("REVIEW_REQUIRED",
                       "review_required: complete a review attempt with an ACCEPT manifest first.")

    def _assert_review_current(self, data: dict, row, revision: int):
        if self.store.native_review:
            return  # A one-time review is historical evidence, not an acceptance gate.
        # Only a PASS review, or a review that recorded an explicit subject
        # digest, is an approval bound to the current subject. A PASS record
        # without a stored digest cannot prove freshness, so it is treated as
        # stale as well. Non-PASS results (NOT_RUN/BLOCKED/SUBSTITUTED) keep
        # only their manifest evidence because _apply_result clears their
        # review metadata; bare evidence is not a decided review and must not
        # block REVIEW_READY or export. Packages with no review result at all
        # keep their previous behaviour: this is a no-op.
        registered = []
        for task in data["subtasks"]:
            if task["kind"] != "review":
                continue
            review = task.get("review") or {}
            subject = review.get("subject_sha256")
            if task.get("status") == "PASS" or (isinstance(subject, str) and subject):
                registered.append(task)
        if not registered:
            return
        current = self._review_subject(row, data)
        for task in registered:
            review = task.get("review") or {}
            if review.get("subject_sha256") != current:
                raise ScsError("REVIEW_STALE",
                               "Files, definition or non-review results changed after the review; "
                               "run a new review attempt.")

    def _verify_task_report(self, con, row, revision: int, task: dict, *,
                            require_journey_pass: bool, journey_only: bool) -> None:
        status, evidence = task["status"], task["evidence"]
        attempt_id = task.get("result_attempt_id")
        journey = self._execution_profile(con, row, revision, task) == "journey"
        verify_pass = status == "PASS" and not journey_only
        if not journey and not verify_pass and task["kind"] != "test":
            return
        if status == "PASS" and not evidence and (journey or verify_pass):
            raise ScsError("EVIDENCE_REQUIRED", "A stored report has no evidence.")

        for entry in evidence:
            result = self._verify_task_evidence(con, row, revision, task, entry["path"],
                                                entry.get("required_content"), attempt_id)
            if result.get("error_code") == "JOURNEY_ATTEMPT_REQUIRED":
                raise ScsError("JOURNEY_ATTEMPT_REQUIRED", "Stored journey evidence needs a matching journey attempt.")
            if result.get("kind") == "journey":
                attempt_id = attempt_id or result.get("journey_attempt_id")
                journey = True
            if not journey and not verify_pass:
                continue
            if result["status"] != "VERIFIED" or result["sha256"] != entry["sha256"]:
                raise ScsError("EVIDENCE_CHANGED", "Stored evidence changed; revise its report before continuing.")
            if _journey_pass_invalid(result, status, "journey" if journey else None):
                raise ScsError("JOURNEY_NOT_PASSED", "A journey PASS needs satisfied assertions and exit_code 0.")

        if journey and (status != "NOT_RUN" or evidence or attempt_id):
            attempt = self._journey_attempt(con, row["id"], revision, task["id"], attempt_id)
            recorded = json.loads(attempt["result_data"]) if attempt and attempt["result_data"] else {}
            if (not attempt or attempt["state"] != "ENDED" or attempt["result_state"] != status
                    or recorded.get("status") != status or recorded.get("evidence") != evidence):
                raise ScsError("JOURNEY_ATTEMPT_REQUIRED", "Stored journey results need the evidence from their ended attempt.")
        if journey and require_journey_pass and task.get("required", True) and status != "PASS":
            raise ScsError("JOURNEY_NOT_PASSED", "A required journey needs a PASS from an ended journey attempt.",
                           {"subtask_id": task["id"]})

    def _verify_reports(self, data: dict, row, revision: int, *, con=None,
                        require_journey_pass: bool = False, journey_only: bool = False) -> None:
        if con is None:
            # Read-only projections do not own the transaction that records attempts.
            with self.store.transaction() as read_con:
                return self._verify_reports(data, row, revision, con=read_con,
                                            require_journey_pass=require_journey_pass, journey_only=journey_only)
        for task in data["subtasks"]:
            if task["kind"] != "review":
                self._verify_task_report(con, row, revision, task, require_journey_pass=require_journey_pass,
                                         journey_only=journey_only)
                continue
            if journey_only or self.store.native_review:
                continue  # Native package receipts are verified when recorded.
            review = task.get("review") or {}
            agent_type = review.get("agent_type")
            native = agent_type is not None
            if task["status"] != "PASS" and not (native and task["status"] == "FAIL"):
                continue
            if not task["evidence"]:
                raise ScsError("EVIDENCE_REQUIRED", "A stored report has no evidence.")
            verdict = "ACCEPT" if task["status"] == "PASS" else "REJECT"
            if len(task["evidence"]) != 1 or review.get("verdict") != verdict:
                raise ScsError("REVIEW_INVALID", "A stored review needs exactly one matching manifest.")
            prefix = f"{row['docs_root']}/agent/evidence/{row['id']}/R{revision}/{task['id']}"
            if native:
                if not review.get("attempt_id") or not review.get("agent_id") or not review.get("subject_sha256"):
                    raise ScsError("REVIEW_INVALID", "A native verdict must reference its attempt, subject and host agent.")
                prefix += "/" + review["attempt_id"]
            entry = task["evidence"][0]
            check = verify_file(self.store, entry["path"], prefix, "review", entry.get("required_content"),
                                native_review=native, host_agent_id=review.get("agent_id"),
                                expected_agent_type=agent_type if native else FIXED_REVIEWER)
            if check["status"] != "VERIFIED" or check["sha256"] != entry["sha256"]:
                raise ScsError("EVIDENCE_CHANGED", "Stored evidence changed; revise its report before continuing.")
            manifest = check["review"]
            if manifest["verdict"] != verdict:
                raise ScsError("REVIEW_INVALID", "The stored verdict does not match its manifest.")
            key = "review_subject" if native else "subject_sha256"
            if manifest[key] != review.get("subject_sha256"):
                raise ScsError("REVIEW_STALE", "The stored review manifest no longer matches its subject.")

    def update(self, session_id: str, package_id: str, expected_version: int,
               state: str | None = None, results: list[dict] | None = None,
               new_revision: bool = False, close_reason: str | None = None,
               definition: dict | None = None, operation_id: str | None = None,
               suspension_reason: str | None = None, resume_condition: str | None = None,
               known_limitations: list[str] | None = None) -> dict:
        operation_id = self._operation_id(operation_id)
        if not new_revision and state is None and not results and close_reason is None and suspension_reason is None:
            raise ScsError("INPUT_INVALID", "Specify a state, results, close reason, suspension reason, or a new revision.")
        if new_revision and (state is not None or results or close_reason is not None or suspension_reason is not None
                             or resume_condition is not None or known_limitations):
            raise ScsError("INPUT_INVALID", "Start a revision separately from reporting results.")
        if definition is not None and not new_revision:
            raise ScsError("INPUT_INVALID", "A new definition is only valid when starting a revision.")
        payload = encode({"package_id": package_id, "expected_version": expected_version,
                          "state": state, "results": results, "new_revision": new_revision,
                          "close_reason": close_reason, "definition": definition,
                          "suspension_reason": suspension_reason,
                          "resume_condition": resume_condition,
                          "known_limitations": known_limitations})
        with self.store.transaction(True) as con:
            row = self._get(con, package_id)
            self._access(con, session_id, row)
            if operation_id:
                stored = self._operation(con, operation_id, "work_package_update", session_id, payload)
                if stored is not None:
                    return stored
            if row["state"] in CLOSED_STATES:
                raise ScsError("PACKAGE_CLOSED", "Closed work packages are immutable; create a new package for follow-up work.")
            if expected_version != row["version"]:
                raise ScsError("VERSION_CONFLICT", "Refresh the work package; another writer changed its version.")
            session = self.state._session(con, session_id)
            intake = self.state.intake_status(con, session_id)
            if not intake["ready"]:
                raise ScsError("INTAKE_REQUIRED", "Confirm this conversation's intake before updating a package.",
                               {"reason": intake["reason"], "source": intake["source"], "policy": intake["policy"]})
            self.store.revision_snapshot(con, row)
            data = json.loads(row["data"])
            target = state or row["state"]
            closing = target in CLOSED_STATES
            if closing:
                if results:
                    raise ScsError("INPUT_INVALID", "Close the package without new subtask results.")
                if not isinstance(close_reason, str) or not close_reason.strip():
                    raise ScsError("CLOSE_REASON_REQUIRED", "Record why this work package is completed or abandoned.")
                if len(close_reason.strip()) > 2000:
                    raise ScsError("INPUT_INVALID", "Close reason exceeds 2000 characters.")
                close_reason = close_reason.strip()
            elif close_reason is not None:
                raise ScsError("INPUT_INVALID", "A close reason is only valid with COMPLETED or ABANDONED.")
            suspending = target == "SUSPENDED"
            if suspending:
                if results:
                    raise ScsError("INPUT_INVALID", "Suspend without new subtask results.")
                if not isinstance(suspension_reason, str) or not suspension_reason.strip():
                    raise ScsError("SUSPENSION_REASON_REQUIRED", "Record why this work package is suspended.")
                if len(suspension_reason.strip()) > 2000:
                    raise ScsError("INPUT_INVALID", "Suspension reason exceeds 2000 characters.")
                if resume_condition is not None and (not isinstance(resume_condition, str) or len(resume_condition.strip()) > 2000):
                    raise ScsError("INPUT_INVALID", "Resume condition must be a bounded string.")
                if known_limitations is not None:
                    if (not isinstance(known_limitations, list) or len(known_limitations) > 50
                            or any(not isinstance(item, str) or not item.strip() or len(item.strip()) > 500
                                   for item in known_limitations)):
                        raise ScsError("INPUT_INVALID", "Known limitations must be at most 50 bounded strings.")
                suspension_reason = suspension_reason.strip()
                resume_condition = resume_condition.strip() if isinstance(resume_condition, str) else None
                known_limitations = [item.strip() for item in known_limitations or []]
            elif suspension_reason is not None or resume_condition is not None or known_limitations:
                raise ScsError("INPUT_INVALID", "Suspension details are only valid with SUSPENDED.")
            revision = row["revision"]
            if new_revision:
                self._assert_quiescent(con, package_id, revision, "starting a new revision")
                if definition is not None:
                    if not isinstance(definition, dict) or set(definition) != {"goal", "allowed_files", "acceptance", "subtasks"}:
                        raise ScsError("INPUT_INVALID", "A new definition needs goal, allowed_files, acceptance and subtasks.")
                    goal = definition["goal"]
                    acceptance = definition["acceptance"]
                    if (not isinstance(goal, str) or not goal.strip() or len(goal) > 4000
                            or not isinstance(acceptance, list) or not acceptance
                            or not all(isinstance(item, str) and item.strip() for item in acceptance)):
                        raise ScsError("INPUT_INVALID", "Supply a bounded goal and nonempty acceptance criteria.")
                    allowed = self._scope(definition["allowed_files"])
                    data.update({"goal": goal, "allowed_files": allowed, "acceptance": acceptance,
                                 "subtasks": self._tasks(definition["subtasks"], allowed)})
                else:
                    if self.store.native_review:
                        data["subtasks"] = [task for task in data["subtasks"] if not task.get("manual_review_slot")]
                    for task in data["subtasks"]:
                        if self._execution_profile(con, row, revision, task) == "journey":
                            task["execution_profile"] = "journey"
                        # A new revision must not inherit the previous review
                        # verdict/subject: exports and run_map see one revision.
                        task.pop("review", None)
                        task.pop("result_attempt_id", None)
                        task.update(status="NOT_RUN", evidence=[])
                # The package-level review policy freezes with the package; old
                # packages without the field are treated as false.
                data.setdefault("review_required", False)
                revision += 1
                target = "PENDING"
                for task in data["subtasks"]:
                    self.store.paths.mkdir(f"{row['docs_root']}/agent/evidence/{package_id}/R{revision}/{task['id']}")
            elif target != row["state"] and target not in STATES.get(row["state"], set()):
                raise ScsError("STATE_INVALID", "Invalid work package transition; final acceptance belongs to the main conversation.")
            if not new_revision:
                if results:
                    reported_ids = {result.get("id") for result in results}
                    journey_ids = {task["id"] for task in data["subtasks"]
                                   if task["id"] in reported_ids
                                   and self._execution_profile(con, row, revision, task) == "journey"}
                    if any(result.get("id") in journey_ids for result in results):
                        raise ScsError("JOURNEY_ATTEMPT_REQUIRED", "End a prepared journey attempt to record this result.")
                    self._assert_quiescent(con, package_id, revision,
                                           "reporting results directly; end the active attempt instead")
                elif target in {"REVIEW_READY", "COMPLETED", "ABANDONED", "BLOCKED", "SUSPENDED"}:
                    self._assert_quiescent(con, package_id, revision, "moving to " + target)
            seen = set()
            for result in results or []:
                if set(result) - {"id", "status", "evidence"} or result.get("id") in seen:
                    raise ScsError("SUBTASK_INVALID", "Use one result per subtask with known fields.")
                seen.add(result.get("id"))
                task = next((t for t in data["subtasks"] if t["id"] == result.get("id")), None)
                if not task or result.get("status") not in RESULTS:
                    raise ScsError("SUBTASK_INVALID", "Result must name a subtask and an allowed status.")
                if task["kind"] == "review":
                    raise ScsError("REVIEW_ATTEMPT_REQUIRED",
                                   "Review results must be recorded by ending a prepared review attempt "
                                   "bound to its subject digest.")
                validation = json.loads(session["verification"]) if session["verification"] else []
                if task["kind"] == "test" and set(validation) & {"none", "method-only"} and result["status"] != "NOT_RUN":
                    raise ScsError("VALIDATION_NOT_AUTHORIZED", "This conversation chose no testing/method-only; test results must remain NOT_RUN.")
                evidence = result.get("evidence", [])
                if result["status"] == "PASS" and not evidence:
                    raise ScsError("EVIDENCE_REQUIRED", "A PASS report needs verifiable evidence; it is not final acceptance.")
                for entry in evidence:
                    if not isinstance(entry, dict) or set(entry) - {"path", "sha256", "required_content"} or not isinstance(entry.get("path"), str):
                        raise ScsError("EVIDENCE_INVALID", "Supply evidence path, sha256 and optional required_content.")
                    check = self._verify_task_evidence(con, row, revision, task, entry["path"],
                                                       entry.get("required_content"))
                    if check.get("kind") == "journey":
                        raise ScsError("JOURNEY_ATTEMPT_REQUIRED", "End a prepared journey attempt to record journey evidence.")
                    if check["status"] != "VERIFIED" or entry.get("sha256") != check["sha256"]:
                        raise ScsError("EVIDENCE_INVALID", "Evidence is invalid, insufficient or changed since verification.")
                task.update(status=result["status"], evidence=evidence)
            if target in {"REVIEW_READY", "COMPLETED"} and any(
                    t["status"] in {"FAIL", "BLOCKED"}
                    for t in data["subtasks"] if not (self.store.native_review and t["kind"] == "review")):
                raise ScsError("RESULT_BLOCKED", "Resolve failed/blocked subtasks before reporting review readiness or completion.")
            self._verify_reports(data, row, revision, con=con,
                                 require_journey_pass=target in {"REVIEW_READY", "COMPLETED"})
            if target in {"REVIEW_READY", "COMPLETED"} and self._requires_review(data):
                self._review_gate(data, row, revision)
            if target in {"REVIEW_READY", "COMPLETED"}:
                # review_required only controls whether a review is mandatory.
                # A review that is already registered must be fresh at every
                # acceptance gate, including REVIEW_READY for packages that do
                # not require one. No-op when no review result exists.
                self._assert_review_current(data, row, revision)
            data["updated_at"] = now()
            if suspending:
                data["suspension"] = {
                    "reason": suspension_reason,
                    "resume_condition": resume_condition,
                    "known_limitations": known_limitations or [],
                    "at": data["updated_at"],
                }
            elif row["state"] == "SUSPENDED":
                previous = data.pop("suspension", None)
                if previous is not None:
                    data.setdefault("suspension_history", []).append(previous)
            if closing:
                data["closed_at"] = data["updated_at"]
                data["close_reason"] = close_reason
                data["closed_by_session"] = session_id
            # Every successful mutation consumes one version. Retrying a stale write
            # gives VERSION_CONFLICT rather than silently overwriting another writer.
            con.execute("UPDATE work_packages SET revision=?,version=version+1,state=?,data=? WHERE id=? AND version=?",
                        (revision, target, encode(data), package_id, expected_version))
            self.store.event(con, session_id, None, "work_package_update",
                             "PACKAGE_CLOSED" if closing else "PACKAGE_UPDATED")
            updated = self._get(con, package_id)
            self.store.revision_snapshot(con, updated)
            response = self._snapshot(updated)
            if operation_id:
                self._store_operation(con, operation_id, "work_package_update", session_id, payload, response)
            return response

    def force_close(self, package_id: str, expected_version: int | None,
                    state: str, close_reason: str) -> dict:
        """Operator reconciliation: close an orphan/stuck package regardless of state.

        Normal ``update`` keeps the state machine, evidence and active-attempt
        checks. This path is only for the local manager when a package has no
        owning conversation left. COMPLETED still verifies journeys. Force
        close records the operator reason, ends active attempts and audits it.
        """
        if state not in CLOSED_STATES:
            raise ScsError("INPUT_INVALID", "Force close state must be COMPLETED or ABANDONED.")
        if not isinstance(close_reason, str) or not close_reason.strip():
            raise ScsError("CLOSE_REASON_REQUIRED", "Record why this work package is force closed.")
        reason = close_reason.strip()[:2000]
        with self.store.transaction(True) as con:
            row = self._get(con, package_id)
            if row["state"] in CLOSED_STATES:
                return self._snapshot(row)
            if expected_version is not None and expected_version != row["version"]:
                raise ScsError("VERSION_CONFLICT", "Refresh before force closing.")
            at = now()
            data = json.loads(row["data"])
            if state == "COMPLETED":
                self._verify_reports(data, row, row["revision"], con=con, require_journey_pass=True, journey_only=True)
            data["updated_at"] = at
            data["closed_at"] = at
            data["close_reason"] = reason
            data["closed_by_session"] = row["session_id"]
            data["force_closed"] = True
            con.execute("UPDATE work_packages SET state=?,data=?,version=version+1 WHERE id=? AND version=?",
                        (state, encode(data), package_id, row["version"]))
            con.execute("UPDATE work_attempts SET state='ENDED', result_state='FAIL', result_data=?, "
                        "updated_at=?, last_observed_at=? WHERE package_id=? AND state!='ENDED'",
                        (encode({"force_closed": True, "reason": reason}), at, at, package_id))
            self.store.event(con, row["session_id"], None, "work_package_update", "PACKAGE_FORCE_CLOSED")
            updated = self._get(con, package_id)
            self.store.revision_snapshot(con, updated)
            result = self._snapshot(updated)
            result["forced"] = True
            return result

    def export(self, session_id: str, package_id: str, expected_version: int) -> dict:
        with self.store.transaction(True) as con:
            row = self._get(con, package_id)
            self._access(con, session_id, row)
            if expected_version != row["version"]:
                raise ScsError("VERSION_CONFLICT", "Refresh before exporting the handoff.")
            self._assert_quiescent(con, package_id, row["revision"], "exporting a handoff")
            data = json.loads(row["data"])
            self._verify_reports(data, row, row["revision"], con=con, require_journey_pass=row["state"] != "SUSPENDED")
            self._assert_review_current(data, row, row["revision"])
            snapshot = self._snapshot(row)
            snapshot["suspended"] = row["state"] == "SUSPENDED"
            # Preferences and identity are deliberately absent: a new chat does intake.
            snapshot.pop("owner_session")
            raw = encode(snapshot)
            digest = hashlib.sha256(raw.encode()).hexdigest()
            hid = "H-" + hashlib.sha256(encode([package_id, digest]).encode()).hexdigest()[:24]
            con.execute("INSERT OR IGNORE INTO handoffs VALUES(?,?,?,?,?)", (hid, package_id, raw, digest, now()))
            return {"status": "EXPORTED", "handoff_id": hid, "sha256": digest, "snapshot": snapshot,
                    "resume": "Use handoff_resume in the new conversation, then independently confirm its intake choices."}

    def resume(self, session_id: str, handoff_id: str, digest: str) -> dict:
        with self.store.transaction(True) as con:
            self.state._session(con, session_id)
            handoff = con.execute("SELECT * FROM handoffs WHERE id=?", (handoff_id,)).fetchone()
            if not handoff or handoff["digest"] != digest or hashlib.sha256(handoff["snapshot"].encode()).hexdigest() != digest:
                raise ScsError("HANDOFF_INVALID", "Handoff identity or digest does not match this project's audit record.")
            row = self._get(con, handoff["package_id"])
            snapshot = json.loads(handoff["snapshot"])
            if row["version"] != snapshot["version"]:
                raise ScsError("HANDOFF_STALE", "Package changed after export; review and export a fresh handoff.")
            self._assert_quiescent(con, row["id"], row["revision"], "resuming a handoff")
            self._verify_reports(json.loads(row["data"]), row, row["revision"], con=con,
                                 require_journey_pass=row["state"] != "SUSPENDED")
            con.execute("INSERT OR IGNORE INTO resumes VALUES(?,?)", (handoff_id, session_id))
            self.store.event(con, session_id, None, "handoff_resume", "HANDOFF_RESUMED")
            return {"status": "RESUMED", "package_id": row["id"], "version": row["version"],
                    "session_choices_copied": False, "intake_required": True}
