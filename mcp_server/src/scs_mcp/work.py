"""Versioned work packages and auditable handoffs, without agent scheduling."""

import hashlib
import json
import re

from .errors import ScsError
from .evidence import verify_file
from .locks import Leases
from .state import State, identifier
from .storage import Store, encode, now

STATES = {"PENDING": {"IN_PROGRESS", "BLOCKED", "ABANDONED"},
          "IN_PROGRESS": {"REVIEW_READY", "BLOCKED", "ABANDONED"},
          "BLOCKED": {"IN_PROGRESS", "ABANDONED"},
          "REVIEW_READY": {"IN_PROGRESS", "BLOCKED", "COMPLETED", "ABANDONED"},
          "COMPLETED": set(), "ABANDONED": set()}
RESULTS = {"PASS", "FAIL", "BLOCKED", "NOT_RUN", "SUBSTITUTED"}
CLOSED_STATES = {"COMPLETED", "ABANDONED"}
ATTEMPT_STATES = {"PREPARED", "LAUNCHING", "RUNNING", "DRAINING", "ENDED", "UNKNOWN"}
ATTEMPT_TRANSITIONS = {
    "PREPARED": {"LAUNCHING", "UNKNOWN", "ENDED"},
    "LAUNCHING": {"RUNNING", "DRAINING", "UNKNOWN", "ENDED"},
    "RUNNING": {"DRAINING", "UNKNOWN", "ENDED"},
    "DRAINING": {"ENDED", "UNKNOWN"},
    "UNKNOWN": {"RUNNING", "DRAINING", "ENDED"},
    "ENDED": set(),
}


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
        mode = "write" if kind == "implement" else "read"
        return [{"resource": "file:" + scope, "mode": mode} for scope in scopes]

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
            if resource.startswith("file:"):
                path = resource[5:]
                if not any(path == parent or path.startswith(parent + "/") for parent in scopes):
                    raise ScsError("SCOPE_EXPANDED", "Resource file must be inside the subtask scope.")
            if resource not in merged or mode == "write":
                merged[resource] = mode
        return [{"resource": key, "mode": merged[key]} for key in sorted(merged)]

    def _normalize_task(self, task: dict) -> dict:
        task = dict(task)
        task.setdefault("depends_on", [])
        task.setdefault("required", True)
        if "resources" not in task:
            task["resources"] = self._default_resources(task["kind"], task["allowed_files"])
        return task

    def _tasks(self, subtasks: list[dict], allowed: list[str]) -> list[dict]:
        if not subtasks or len(subtasks) > 100:
            raise ScsError("SUBTASK_INVALID", "Declare between one and 100 bounded subtasks.")
        out = []
        for task in subtasks:
            if set(task) - {"id", "kind", "allowed_files", "acceptance", "depends_on", "resources", "required"} or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(task.get("id", ""))):
                raise ScsError("SUBTASK_INVALID", "Use unique simple subtask identifiers and known fields.")
            criteria = task.get("acceptance")
            if (task.get("kind") not in {"analyze", "implement", "test"} or not isinstance(criteria, list)
                    or not criteria or not all(isinstance(x, str) and x.strip() for x in criteria)):
                raise ScsError("SUBTASK_INVALID", "Every subtask needs a kind and acceptance criteria.")
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
        data = {"goal": goal, "allowed_files": allowed, "acceptance": acceptance, "subtasks": tasks, "request_key": request_key}
        with self.store.transaction(True) as con:
            session = self.state._session(con, session_id)
            if not session["mode"]:
                raise ScsError("INTAKE_REQUIRED", "Confirm this conversation's mode before creating a work package.")
            if session["mode"] in {"standard", "long"} and (not session["verification"] or
                    (session["review_registration"] == "registered" and session["codex_review"] is None)):
                raise ScsError("INTAKE_REQUIRED", "Confirm verification and any registered review question first.")
            old = con.execute("SELECT * FROM work_packages WHERE id=?", (pid,)).fetchone()
            if old:
                # Compare the immutable original definition, not later result updates.
                original = json.loads(old["data"])
                original = {k: original[k] for k in data}
                original["subtasks"] = [{**self._normalize_task(task), "status": "NOT_RUN", "evidence": []}
                                        for task in original["subtasks"]]
                if original != data or old["revision"] != 1:
                    raise ScsError("IDEMPOTENCY_CONFLICT", "request_key was already used for another definition or revision.")
                return self._snapshot(old)
            docs = self.store.docs_root(con)
            self._records(docs)
            for task in tasks:
                self.store.paths.mkdir(f"{docs}/agent/evidence/{pid}/R1/{task['id']}")
            con.execute("INSERT INTO work_packages VALUES(?,?,?,?,?,?,?,?)", (pid, session_id, 1, 1, "PENDING", encode(data), docs, now()))
            self.store.event(con, session_id, None, "work_package_create", "PACKAGE_CREATED")
            row = self._get(con, pid)
            self.store.revision_snapshot(con, row)
            return self._snapshot(row)

    def _snapshot(self, row) -> dict:
        return {"status": "OK", "id": row["id"], "owner_session": row["session_id"], "revision": row["revision"],
                "version": row["version"], "state": row["state"], **json.loads(row["data"]),
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

    def prepare_attempt(self, session_id: str, package_id: str, expected_version: int,
                        subtask_id: str, host_agent_id: str | None = None,
                        ttl_seconds: float = 120) -> dict:
        if host_agent_id is not None and (not isinstance(host_agent_id, str) or len(host_agent_id) > 256):
            raise ScsError("INPUT_INVALID", "host_agent_id must be a bounded host identifier.")
        with self.store.transaction(True) as con:
            row = self._get(con, package_id)
            self._access(con, session_id, row)
            if row["state"] in CLOSED_STATES:
                raise ScsError("PACKAGE_CLOSED", "Closed work packages cannot start new attempts.")
            if expected_version != row["version"]:
                raise ScsError("VERSION_CONFLICT", "Refresh the work package before preparing an attempt.")
            data = json.loads(row["data"])
            task = next((t for t in data["subtasks"] if t["id"] == subtask_id), None)
            if task is None:
                raise ScsError("SUBTASK_INVALID", "Subtask does not exist in this revision.")
            active = con.execute("SELECT id FROM work_attempts WHERE package_id=? AND revision=? AND subtask_id=? AND state!='ENDED'",
                                 (package_id, row["revision"], subtask_id)).fetchone()
            if active:
                raise ScsError("ATTEMPT_ACTIVE", "This subtask already has an active attempt.",
                               {"attempt_id": active["id"]})
            dependencies = []
            for dep_id in task.get("depends_on", []):
                dep_task = next((t for t in data["subtasks"] if t["id"] == dep_id), None)
                if dep_task is None:
                    raise ScsError("SUBTASK_INVALID", "Dependency does not exist in this revision.")
                dep_active = con.execute("SELECT id FROM work_attempts WHERE package_id=? AND revision=? AND subtask_id=? AND state!='ENDED'",
                                         (package_id, row["revision"], dep_id)).fetchone()
                if dep_active:
                    raise ScsError("DEPENDENCY_NOT_READY", "Dependency still has an active attempt.",
                                   {"subtask_id": dep_id, "attempt_id": dep_active["id"]})
                dep_attempt = con.execute("SELECT * FROM work_attempts WHERE package_id=? AND revision=? AND subtask_id=? AND state='ENDED' ORDER BY attempt_no DESC LIMIT 1",
                                          (package_id, row["revision"], dep_id)).fetchone()
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
                                     (package_id, row["revision"], subtask_id)).fetchone()[0]
            attempt_id = identifier("A-")
            resources = task.get("resources") or self._default_resources(task["kind"], task["allowed_files"])
            leases = self.leases.acquire_many(con, session_id, attempt_id, package_id, resources, ttl_seconds)
            state = "LAUNCHING" if host_agent_id else "PREPARED"
            stamp = now()
            evidence_dir = f"{row['docs_root']}/agent/evidence/{package_id}/R{row['revision']}/{subtask_id}/{attempt_id}"
            self.store.paths.mkdir(evidence_dir)
            input_snapshot = {"revision": row["revision"], "subtask_id": subtask_id,
                              "dependencies": dependencies}
            con.execute("INSERT INTO work_attempts(id,package_id,revision,subtask_id,attempt_no,state,host_agent_id,controller_session,input_snapshot,resources,result_state,result_data,created_at,updated_at,last_observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,NULL,NULL,?,?,?)",
                        (attempt_id, package_id, row["revision"], subtask_id, attempt_no, state,
                         host_agent_id, session_id, encode(input_snapshot), encode(resources), stamp, stamp, stamp))
            self.store.event(con, session_id, None, "work_attempt_prepare", "ATTEMPT_PREPARED")
            attempt = self._get_attempt(con, attempt_id)
            return {"status": "OK", "package_id": package_id, "revision": row["revision"],
                    "version": row["version"], "subtask_id": subtask_id,
                    "attempt": self._attempt_snapshot(attempt), "leases": leases,
                    "evidence_dir": evidence_dir, "input_snapshot": input_snapshot}

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
            result_data = None
            if target == "ENDED":
                if not isinstance(result, dict) or set(result) - {"status", "evidence", "note"}:
                    raise ScsError("INPUT_INVALID", "An ending attempt needs a status, evidence and optional note.")
                if result.get("status") not in RESULTS:
                    raise ScsError("SUBTASK_INVALID", "Result status is not allowed.")
                evidence = result.get("evidence", [])
                if not isinstance(evidence, list):
                    raise ScsError("EVIDENCE_INVALID", "Evidence must be a list.")
                if result["status"] == "PASS" and not evidence:
                    raise ScsError("EVIDENCE_REQUIRED", "A PASS attempt needs verifiable evidence.")
                prefix = f"{row['docs_root']}/agent/evidence/{row['id']}/R{row['revision']}/{task['id']}/{attempt_id}"
                for entry in evidence:
                    if not isinstance(entry, dict) or set(entry) - {"path", "sha256", "required_content"} or not isinstance(entry.get("path"), str):
                        raise ScsError("EVIDENCE_INVALID", "Supply evidence path, sha256 and optional required_content.")
                    check = verify_file(self.store, entry["path"], prefix, task["kind"], entry.get("required_content"))
                    if check["status"] != "VERIFIED" or entry.get("sha256") != check["sha256"]:
                        raise ScsError("EVIDENCE_INVALID", "Attempt evidence is invalid, insufficient or changed.")
                task.update(status=result["status"], evidence=evidence)
                data["updated_at"] = now()
                self._verify_reports(data, row, row["revision"])
                con.execute("UPDATE work_packages SET version=version+1,data=? WHERE id=? AND version=?",
                            (encode(data), row["id"], expected_version))
                self.store.revision_snapshot(con, self._get(con, row["id"]))
                result_data = {"status": result["status"], "evidence": evidence, "note": result.get("note")}
                self.leases.release_attempt(con, attempt_id, session_id)
            stamp = now()
            host = host_agent_id if host_agent_id is not None else attempt["host_agent_id"]
            con.execute("UPDATE work_attempts SET state=?,host_agent_id=?,result_state=?,result_data=?,updated_at=?,last_observed_at=? WHERE id=?",
                        (target, host,
                         result_data["status"] if result_data else attempt["result_state"],
                         encode(result_data) if result_data else attempt["result_data"],
                         stamp, stamp, attempt_id))
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

    def evidence(self, session_id: str, package_id: str, revision: int, subtask_id: str,
                 path: str, required_content: list[str] | None = None) -> dict:
        with self.store.transaction() as con:
            row = self._get(con, package_id)
            self._access(con, session_id, row)
            if revision != row["revision"]:
                raise ScsError("REVISION_CONFLICT", "Evidence is for another revision.")
            task = next((t for t in json.loads(row["data"])["subtasks"] if t["id"] == subtask_id), None)
            if not task:
                raise ScsError("SUBTASK_INVALID", "Subtask does not exist in this revision.")
            prefix = f"{row['docs_root']}/agent/evidence/{package_id}/R{revision}/{subtask_id}"
            return verify_file(self.store, path, prefix, task["kind"], required_content)

    def _verify_reports(self, data: dict, row, revision: int):
        for task in data["subtasks"]:
            if task["status"] != "PASS":
                continue
            if not task["evidence"]:
                raise ScsError("EVIDENCE_REQUIRED", "A stored PASS report has no evidence.")
            for entry in task["evidence"]:
                result = verify_file(self.store, entry["path"],
                    f"{row['docs_root']}/agent/evidence/{row['id']}/R{revision}/{task['id']}",
                    task["kind"], entry.get("required_content"))
                if result["status"] != "VERIFIED" or result["sha256"] != entry["sha256"]:
                    raise ScsError("EVIDENCE_CHANGED", "Stored PASS evidence changed; revise its report before continuing.")

    def update(self, session_id: str, package_id: str, expected_version: int,
               state: str | None = None, results: list[dict] | None = None,
               new_revision: bool = False, close_reason: str | None = None,
               definition: dict | None = None, operation_id: str | None = None) -> dict:
        operation_id = self._operation_id(operation_id)
        if not new_revision and state is None and not results and close_reason is None:
            raise ScsError("INPUT_INVALID", "Specify a state, results, close reason, or a new revision.")
        if new_revision and (state is not None or results or close_reason is not None):
            raise ScsError("INPUT_INVALID", "Start a revision separately from reporting results.")
        if definition is not None and not new_revision:
            raise ScsError("INPUT_INVALID", "A new definition is only valid when starting a revision.")
        payload = encode({"package_id": package_id, "expected_version": expected_version,
                          "state": state, "results": results, "new_revision": new_revision,
                          "close_reason": close_reason, "definition": definition})
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
            if not session["mode"] or (session["mode"] in {"standard", "long"} and
                    (not session["verification"] or (session["review_registration"] == "registered" and session["codex_review"] is None))):
                raise ScsError("INTAKE_REQUIRED", "Confirm the new conversation's own intake before updating a package.")
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
                    for task in data["subtasks"]:
                        task.update(status="NOT_RUN", evidence=[])
                revision += 1
                target = "PENDING"
                for task in data["subtasks"]:
                    self.store.paths.mkdir(f"{row['docs_root']}/agent/evidence/{package_id}/R{revision}/{task['id']}")
            elif target != row["state"] and target not in STATES.get(row["state"], set()):
                raise ScsError("STATE_INVALID", "Invalid work package transition; final acceptance belongs to the main conversation.")
            if not new_revision:
                if target in {"REVIEW_READY", "COMPLETED", "ABANDONED", "BLOCKED"}:
                    self._assert_quiescent(con, package_id, revision, "moving to " + target)
                if results:
                    self._assert_quiescent(con, package_id, revision,
                                           "reporting results directly; end the active attempt instead")
            seen = set()
            for result in results or []:
                if set(result) - {"id", "status", "evidence"} or result.get("id") in seen:
                    raise ScsError("SUBTASK_INVALID", "Use one result per subtask with known fields.")
                seen.add(result.get("id"))
                task = next((t for t in data["subtasks"] if t["id"] == result.get("id")), None)
                if not task or result.get("status") not in RESULTS:
                    raise ScsError("SUBTASK_INVALID", "Result must name a subtask and an allowed status.")
                validation = json.loads(session["verification"]) if session["verification"] else []
                if task["kind"] == "test" and set(validation) & {"none", "method-only"} and result["status"] != "NOT_RUN":
                    raise ScsError("VALIDATION_NOT_AUTHORIZED", "This conversation chose no testing/method-only; test results must remain NOT_RUN.")
                evidence = result.get("evidence", [])
                if result["status"] == "PASS" and not evidence:
                    raise ScsError("EVIDENCE_REQUIRED", "A PASS report needs verifiable evidence; it is not final acceptance.")
                for entry in evidence:
                    if not isinstance(entry, dict) or set(entry) - {"path", "sha256", "required_content"} or not isinstance(entry.get("path"), str):
                        raise ScsError("EVIDENCE_INVALID", "Supply evidence path, sha256 and optional required_content.")
                    check = verify_file(self.store, entry["path"],
                        f"{row['docs_root']}/agent/evidence/{package_id}/R{revision}/{task['id']}", task["kind"], entry.get("required_content"))
                    if check["status"] != "VERIFIED" or entry.get("sha256") != check["sha256"]:
                        raise ScsError("EVIDENCE_INVALID", "Evidence is invalid, insufficient or changed since verification.")
                task.update(status=result["status"], evidence=evidence)
            if target in {"REVIEW_READY", "COMPLETED"} and any(t["status"] in {"FAIL", "BLOCKED"} for t in data["subtasks"]):
                raise ScsError("RESULT_BLOCKED", "Resolve failed/blocked subtasks before reporting review readiness or completion.")
            self._verify_reports(data, row, revision)
            data["updated_at"] = now()
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

    def export(self, session_id: str, package_id: str, expected_version: int) -> dict:
        with self.store.transaction(True) as con:
            row = self._get(con, package_id)
            self._access(con, session_id, row)
            if expected_version != row["version"]:
                raise ScsError("VERSION_CONFLICT", "Refresh before exporting the handoff.")
            self._assert_quiescent(con, package_id, row["revision"], "exporting a handoff")
            self._verify_reports(json.loads(row["data"]), row, row["revision"])
            snapshot = self._snapshot(row)
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
            self._verify_reports(json.loads(row["data"]), row, row["revision"])
            con.execute("INSERT OR IGNORE INTO resumes VALUES(?,?)", (handoff_id, session_id))
            self.store.event(con, session_id, None, "handoff_resume", "HANDOFF_RESUMED")
            return {"status": "RESUMED", "package_id": row["id"], "version": row["version"],
                    "session_choices_copied": False, "intake_required": True}
