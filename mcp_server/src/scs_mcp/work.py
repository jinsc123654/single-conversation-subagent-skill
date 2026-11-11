"""Versioned work packages and auditable handoffs, without agent scheduling."""

import hashlib
import json
import re

from .errors import ScsError
from .evidence import verify_file
from .state import State
from .storage import Store, encode, now

STATES = {"PENDING": {"IN_PROGRESS", "BLOCKED"}, "IN_PROGRESS": {"REVIEW_READY", "BLOCKED"},
          "BLOCKED": {"IN_PROGRESS"}, "REVIEW_READY": {"IN_PROGRESS", "BLOCKED"}}
RESULTS = {"PASS", "FAIL", "BLOCKED", "NOT_RUN", "SUBSTITUTED"}


class Work:
    def __init__(self, store: Store):
        self.store = store
        self.state = State(store)

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

    def _tasks(self, subtasks: list[dict], allowed: list[str]) -> list[dict]:
        if not subtasks or len(subtasks) > 100:
            raise ScsError("SUBTASK_INVALID", "Declare between one and 100 bounded subtasks.")
        out = []
        for task in subtasks:
            if set(task) - {"id", "kind", "allowed_files", "acceptance"} or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(task.get("id", ""))):
                raise ScsError("SUBTASK_INVALID", "Use unique simple subtask identifiers and known fields.")
            criteria = task.get("acceptance")
            if (task.get("kind") not in {"analyze", "implement", "test"} or not isinstance(criteria, list)
                    or not criteria or not all(isinstance(x, str) and x.strip() for x in criteria)):
                raise ScsError("SUBTASK_INVALID", "Every subtask needs a kind and acceptance criteria.")
            scopes = self._scope(task.get("allowed_files", []))
            if any(not any(p == parent or p.startswith(parent + "/") for parent in allowed) for p in scopes):
                raise ScsError("SCOPE_EXPANDED", "Subtask scope must be inside the work package's scope.")
            out.append({**task, "allowed_files": scopes, "status": "NOT_RUN", "evidence": []})
        if len({t["id"] for t in out}) != len(out):
            raise ScsError("SUBTASK_INVALID", "Subtask IDs must be unique.")
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
                for task in original["subtasks"]:
                    task.update(status="NOT_RUN", evidence=[])
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
            return self._snapshot(row)

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
               new_revision: bool = False) -> dict:
        if not new_revision and state is None and not results:
            raise ScsError("INPUT_INVALID", "Specify a state, results, or a new revision.")
        if new_revision and (state is not None or results):
            raise ScsError("INPUT_INVALID", "Start a revision separately from reporting results.")
        with self.store.transaction(True) as con:
            row = self._get(con, package_id)
            self._access(con, session_id, row)
            if expected_version != row["version"]:
                raise ScsError("VERSION_CONFLICT", "Refresh the work package; another writer changed its version.")
            session = self.state._session(con, session_id)
            if not session["mode"] or (session["mode"] in {"standard", "long"} and
                    (not session["verification"] or (session["review_registration"] == "registered" and session["codex_review"] is None))):
                raise ScsError("INTAKE_REQUIRED", "Confirm the new conversation's own intake before updating a package.")
            self.store.revision_snapshot(con, row)
            data = json.loads(row["data"])
            target = state or row["state"]
            revision = row["revision"]
            if new_revision:
                revision += 1
                target = "PENDING"
                for task in data["subtasks"]:
                    task.update(status="NOT_RUN", evidence=[])
                    self.store.paths.mkdir(f"{row['docs_root']}/agent/evidence/{package_id}/R{revision}/{task['id']}")
            elif target != row["state"] and target not in STATES.get(row["state"], set()):
                raise ScsError("STATE_INVALID", "Invalid work package transition; final acceptance belongs to the main conversation.")
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
            if target == "REVIEW_READY" and any(t["status"] in {"FAIL", "BLOCKED"} for t in data["subtasks"]):
                raise ScsError("RESULT_BLOCKED", "Resolve failed/blocked subtasks before reporting review readiness.")
            self._verify_reports(data, row, revision)
            # Every successful mutation consumes one version. Retrying a stale write
            # gives VERSION_CONFLICT rather than silently overwriting another writer.
            con.execute("UPDATE work_packages SET revision=?,version=version+1,state=?,data=? WHERE id=? AND version=?",
                        (revision, target, encode(data), package_id, expected_version))
            self.store.event(con, session_id, None, "work_package_update", "PACKAGE_UPDATED")
            updated = self._get(con, package_id)
            self.store.revision_snapshot(con, updated)
            return self._snapshot(updated)

    def export(self, session_id: str, package_id: str, expected_version: int) -> dict:
        with self.store.transaction(True) as con:
            row = self._get(con, package_id)
            self._access(con, session_id, row)
            if expected_version != row["version"]:
                raise ScsError("VERSION_CONFLICT", "Refresh before exporting the handoff.")
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
