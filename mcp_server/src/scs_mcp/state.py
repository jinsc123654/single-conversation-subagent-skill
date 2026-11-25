"""Conversation identity and explicit, idempotent question state transitions."""

import hashlib
import json
import re
import uuid

from .errors import ScsError
from .storage import Store, encode, now

MODES = ("quick", "standard", "long", "investigate")
VERIFICATION = ("smoke", "targeted", "full", "stability", "none", "method-only")
KINDS = ("mode", "verification", "codex_gate", "codex_review")
PENDING = ("REQUESTED", "WAITING_USER", "UNSUPPORTED", "FAILED")
TERMINAL = ("CONFIRMED", "DECLINED", "CANCELLED", "TIMED_OUT")
DESCRIPTIONS = {
    "mode": {
        "quick": "小改动/单文件/直接回答，不建子 Agent",
        "standard": "先确认验证范围，计划后实现与验证",
        "long": "分阶段执行，使用执行子 Agent 与证据",
        "investigate": "只读梳理/评估，不改代码",
    },
    "verification": {
        "smoke": "本轮快速冒烟",
        "targeted": "收尾定向回归",
        "full": "收尾全量回归",
        "stability": "收尾稳定性（长时）",
        "none": "不做测试，结果标 NOT_RUN",
        "method-only": "不执行测试，只记录命令/环境/预期，标 NOT_RUN",
    },
    "codex_gate": {
        "none": "不做 Codex 复核或收尾审查",
        "closure": "仅收尾做一次 Codex 只读审查",
        "packages": "每个工作包做 Codex 复核，收尾再做一次审查（推荐）",
    },
    "codex_review": {
        "no": "收尾不追加 Codex 审查",
        "yes": "收尾用 codex-review 做只读独立审查",
    },
}
OPTIONS = {
    "mode": [{"value": v, "label": label, "desc": DESCRIPTIONS["mode"][v]}
             for v, label in zip(MODES, ("快速处理", "标准执行", "长任务", "只调查"))],
    "verification": [{"value": v, "label": label, "desc": DESCRIPTIONS["verification"][v]}
                     for v, label in zip(VERIFICATION,
        ("本轮冒烟", "收尾定向回归", "收尾全量回归", "收尾稳定性", "不做测试", "不做测试，记录方法"))],
    "codex_gate": [{"value": v, "label": label, "desc": DESCRIPTIONS["codex_gate"][v]}
                   for v, label in (("none", "不做 Codex 复核"), ("closure", "仅收尾审查"),
                                    ("packages", "包级复核 + 收尾审查"))],
    "codex_review": [{"value": "no", "label": "不使用", "desc": DESCRIPTIONS["codex_review"]["no"]},
                     {"value": "yes", "label": "使用：收尾只读独立审查", "desc": DESCRIPTIONS["codex_review"]["yes"]}],
}
NATIVE_OPTIONS = {
    **OPTIONS,
    "codex_gate": [
        {"value": "none", "label": "不做独立复核", "desc": "不做包级复核或收尾审查"},
        {"value": "closure", "label": "仅收尾审查", "desc": "由固定审阅员在收尾做一次只读审查"},
    ],
    "codex_review": [
        {"value": "no", "label": "不使用", "desc": "收尾不追加独立审查"},
        {"value": "yes", "label": "使用固定审阅员", "desc": "收尾启动独立只读审查"},
    ],
}


def identifier(prefix: str) -> str:
    return prefix + uuid.uuid4().hex


def _code(kind: str, value: str) -> str:
    """Accept either the stored code or the display label shown to users."""
    for option in OPTIONS[kind] + NATIVE_OPTIONS[kind]:
        if value == option["value"] or value == option["label"] or value.startswith(option["value"] + " — "):
            return option["value"]
    return value


def check_selection(kind: str, selected: list[str]) -> list[str]:
    selected = [_code(kind, value) for value in selected]
    allowed = {x["value"] for x in OPTIONS[kind]}
    if not selected or len(selected) != len(set(selected)) or any(v not in allowed for v in selected):
        raise ScsError("CHOICE_INVALID", "Choose only listed options, without duplicates.")
    if kind != "verification" and len(selected) != 1:
        raise ScsError("CHOICE_INVALID", "This question requires exactly one option.")
    if kind == "verification" and len(selected) > 1 and set(selected) & {"none", "method-only"}:
        raise ScsError("CHOICE_EXCLUSIVE", "No-testing options are mutually exclusive with all other options.")
    return sorted(selected)


class State:
    """Explicit question state plus the single intake predicate.

    ``codex_gate`` exists only as a question row for long-mode conversations;
    no sessions column and no schema change are needed. Its answer is:
    ``packages`` = per-work-package Codex review plus a closure review;
    ``closure`` = only a closure review; ``none`` = no Codex review at all.
    A recommendation is never written as a confirmed answer, and legacy long
    sessions without a gate question are asked once instead of being migrated.
    """

    def __init__(self, store: Store):
        self.store = store

    def _session(self, con, session_id):
        row = con.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise ScsError("SESSION_NOT_FOUND", "Open or explicitly recover this conversation first.")
        return row

    def _confirmed(self, con, session_id: str, kind: str) -> list[str] | None:
        row = con.execute(
            "SELECT answer FROM questions WHERE session_id=? AND kind=? AND current=1 AND state='CONFIRMED'",
            (session_id, kind)).fetchone()
        if row is None or not row["answer"]:
            return None
        return json.loads(row["answer"])

    def intake_status(self, con, session_id: str) -> dict:
        """One shared intake predicate for work packages, views and projections.

        long -> a confirmed codex_gate is required; standard -> the existing
        verification plus registered codex_review rule; quick/investigate have
        no follow-up requirement. Older long sessions are asked once; this
        function never invents a confirmed gate.
        """
        session = self._session(con, session_id)
        if not session["mode"]:
            return {"ready": False, "policy": None, "source": "not_required",
                    "reason": "MODE_REQUIRED"}
        if session["mode"] not in {"standard", "long"}:
            # quick/investigate (and any unknown legacy value) have no follow-up
            # requirement; this keeps old databases usable instead of deadlocking.
            return {"ready": True, "policy": None, "source": "not_required", "reason": None}
        if not session["verification"]:
            return {"ready": False, "policy": None, "source": "verification",
                    "reason": "VERIFICATION_REQUIRED"}
        if session["mode"] == "long":
            gate = self._confirmed(con, session_id, "codex_gate")
            if gate:
                # A historical 'packages' answer included closure, but no
                # longer grants or requires any package review in ZCode.
                policy = "closure" if self.store.native_review and gate[0] == "packages" else gate[0]
                return {"ready": True, "policy": policy, "source": "codex_gate", "reason": None}
            return {"ready": False, "policy": None, "source": "codex_gate",
                    "reason": "CODEX_GATE_REQUIRED"}
        if session["review_registration"] == "registered" and session["codex_review"] is None:
            return {"ready": False, "policy": None, "source": "codex_review",
                    "reason": "CODEX_REVIEW_REQUIRED"}
        policy = "closure" if session["codex_review"] else "none"
        return {"ready": True, "policy": policy, "source": "codex_review", "reason": None}

    def open(self, project_root: str, client_name: str, conversation_id: str | None = None,
             review_registration: str = "unknown", explicit_skip: bool = False,
             *, resume_session_id: str | None = None) -> dict:
        from pathlib import Path
        if ".." in Path(project_root).parts or Path(project_root).resolve() != self.store.root:
            raise ScsError("PROJECT_MISMATCH", "Session root must match the server's configured project root.")
        if not re.fullmatch(r"[\w .-]{1,64}", client_name) or review_registration not in {"registered", "unregistered", "unknown"}:
            raise ScsError("INPUT_INVALID", "Supply a short client name and known registration state.")
        if conversation_id is not None and (not conversation_id.strip() or len(conversation_id) > 1024):
            raise ScsError("INPUT_INVALID", "Conversation ID must be a nonempty host-provided identifier.")
        if resume_session_id is not None and (conversation_id is not None or explicit_skip):
            raise ScsError("INPUT_INVALID", "Omit conversation_id and explicit_skip when resuming an existing session.")
        temporary = conversation_id is None
        with self.store.transaction(True) as con:
            if resume_session_id is not None:
                row = con.execute(
                    "SELECT id,temporary FROM sessions WHERE id=? AND client=?", (resume_session_id, client_name)
                ).fetchone()
                if row is None:
                    raise ScsError("SESSION_NOT_FOUND", "No matching session for this project and host client.")
                temporary = bool(row["temporary"])
            else:
                identity = hashlib.sha256(
                    encode([str(self.store.root), client_name, conversation_id or uuid.uuid4().hex]).encode()
                ).hexdigest()
                row = con.execute("SELECT id FROM sessions WHERE identity=?", (identity,)).fetchone()
            sid = row["id"] if row else identifier("S-")
            if row is None:
                con.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?)",
                            (sid, identity, client_name, temporary, review_registration,
                             "quick" if explicit_skip else None, None, None, now()))
                self.store.event(con, sid, None, "session_open", "EXPLICIT_SKIP" if explicit_skip else "SESSION_OPENED")
            else:
                if resume_session_id is not None:
                    self.store.event(con, sid, None, "session_open", "SESSION_RESUMED")
                if review_registration != "unknown":
                    # Host attestation may become available after registry loading.
                    updated = con.execute(
                        "UPDATE sessions SET review_registration=? WHERE id=? AND review_registration!=?",
                        (review_registration, sid, review_registration),
                    )
                    if updated.rowcount:
                        self.store.event(con, sid, None, "session_open", "REVIEW_REGISTRATION_UPDATED")
        result = self.get(sid)
        if temporary:
            result["warning"] = "TEMPORARY_SESSION: retain session_id in this chat and pass it as resume_session_id after reconnecting."
        result["destructive_operations_authorized"] = False
        return result

    def get(self, session_id: str) -> dict:
        with self.store.transaction() as con:
            session = dict(self._session(con, session_id))
            questions = [dict(r) for r in con.execute(
                "SELECT * FROM questions WHERE session_id=? AND current=1 AND kind!='review_route' ORDER BY created_at",
                (session_id,))]
            intake = self.intake_status(con, session_id)
        session.pop("identity")
        session["temporary"] = bool(session["temporary"])
        session["verification"] = json.loads(session["verification"]) if session["verification"] else None
        session["codex_review"] = bool(session["codex_review"]) if session["codex_review"] is not None else None
        for q in questions:
            q["recommendation"] = json.loads(q["recommendation"])
            q["answer"] = json.loads(q["answer"]) if q["answer"] else None
        # codex_gate is a projection of the current confirmed question row; a
        # legacy long session without one stays null and is asked once.
        gate = next((q["answer"][0] for q in questions
                     if q["kind"] == "codex_gate" and q["state"] == "CONFIRMED" and q["answer"]), None)
        session["codex_gate"] = intake["policy"] if self.store.native_review and gate == "packages" else gate
        return {"status": "OK", **session, "questions": questions, "native_review": self.store.native_review,
                "unresolved": [q["id"] for q in questions if q["state"] != "CONFIRMED"],
                "question_states": {k: next((q["state"] for q in questions if q["kind"] == k), "NOT_ASKED") for k in KINDS},
                "intake": intake}

    def question(self, session_id: str, question_id: str) -> dict:
        with self.store.transaction() as con:
            self._session(con, session_id)
            q = con.execute("SELECT * FROM questions WHERE id=? AND session_id=?", (question_id, session_id)).fetchone()
            if q is None:
                raise ScsError("QUESTION_NOT_FOUND", "Question is not part of this conversation.")
            return self.render_question(q)

    def render_question(self, q) -> dict:
        return {**dict(q), "native_review": self.store.native_review}

    def _retire_legacy_review_question(self, con, session_id: str) -> int:
        """Retire a current codex_review row that long mode no longer asks.

        Older versions stored a closure-only review question. If a long
        conversation still has a WAITING_USER/DECLINED/TIMED_OUT row, the
        pending check would return it and hide the codex_gate question forever.
        Confirmed rows stay as history; they never block the pending check.
        """
        rows = con.execute(
            "SELECT id FROM questions WHERE session_id=? AND kind='codex_review' "
            "AND current=1 AND state != 'CONFIRMED'", (session_id,)).fetchall()
        for row in rows:
            con.execute("UPDATE questions SET current=0,updated_at=? WHERE id=?", (now(), row["id"]))
            self.store.event(con, session_id, row["id"], "interaction_request",
                             "SUPERSEDED", None, "LONG_MODE_USES_CODEX_GATE")
        return len(rows)

    def request(self, session_id: str, kind: str, recommendation: list[str], channel: str) -> dict:
        if kind not in KINDS or channel not in {"auto", "native", "text"}:
            raise ScsError("INPUT_INVALID", "Unknown question kind or interaction channel.")
        recommendation = check_selection(kind, recommendation)
        if self.store.native_review and kind == "codex_gate" and recommendation == ["packages"]:
            recommendation = ["closure"]
        with self.store.transaction(True) as con:
            s = self._session(con, session_id)
            if s["mode"] == "long":
                # Long mode asks codex_gate instead of codex_review. Retire a
                # stale legacy row before any pending/reuse lookup.
                self._retire_legacy_review_question(con, session_id)
            if s["mode"] is not None:
                # Mode applicability comes before question reuse and the pending
                # check: a question that no longer applies must not shadow the
                # current mode's flow or its gate.
                if kind == "verification" and s["mode"] not in {"standard", "long"}:
                    return {"status": "SKIPPED", "reason": "MODE_DOES_NOT_REQUIRE_QUESTION"}
                if kind == "codex_gate" and s["mode"] != "long":
                    return {"status": "SKIPPED", "reason": "MODE_DOES_NOT_REQUIRE_QUESTION"}
                if kind == "codex_review" and s["mode"] not in {"quick", "standard", "long"}:
                    return {"status": "SKIPPED", "reason": "MODE_DOES_NOT_REQUIRE_QUESTION"}
                if kind == "codex_review" and s["mode"] == "long":
                    # Long mode asks the single codex_gate question instead of
                    # the legacy closure-only review question.
                    return {"status": "SKIPPED", "reason": "USE_CODEX_GATE"}
            q = con.execute("SELECT * FROM questions WHERE session_id=? AND kind=? AND current=1", (session_id, kind)).fetchone()
            if q:
                return {"created": False, **dict(q)}
            # The experimental route selector remains as history, but no longer blocks intake.
            pending = con.execute(
                "SELECT id,kind,state FROM questions WHERE session_id=? AND current=1 "
                "AND state != 'CONFIRMED' AND kind!='review_route'",
                (session_id,),
            ).fetchone()
            if pending:
                return {"status": "QUESTION_PENDING", "question_id": pending["id"],
                        "kind": pending["kind"], "state": pending["state"],
                        "next": "Use interaction_submit with this question_id after an explicit user reply. "
                                "For an ended question, reopen only on explicit user request."}
            if kind != "mode" and not s["mode"]:
                raise ScsError("QUESTION_ORDER", "Confirm mode first.")
            if kind == "codex_gate" and not s["verification"]:
                raise ScsError("QUESTION_ORDER", "Confirm verification scope first.")
            if kind == "codex_review":
                if s["mode"] == "standard" and not s["verification"]:
                    raise ScsError("QUESTION_ORDER", "Confirm verification scope first.")
                if s["review_registration"] == "unknown":
                    return {"status": "SKIPPED", "reason": "CODEX_REVIEW_REGISTRATION_UNKNOWN"}
                if s["review_registration"] == "unregistered":
                    return {"status": "SKIPPED", "reason": "CODEX_REVIEW_NOT_REGISTERED"}
            if kind == "mode" and s["mode"]:
                return {"status": "ALREADY_CONFIRMED", "choice": [s["mode"]]}
            qid = identifier("Q-")
            stamp = now()
            con.execute("INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (qid, session_id, kind, 1, "REQUESTED", channel, encode(recommendation), None, stamp, stamp))
            self.store.event(con, session_id, qid, "interaction_request", "REQUESTED")
            return {"created": True, **dict(con.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone())}

    def claim(self, session_id: str, question_id: str) -> tuple[bool, dict]:
        """Atomically reserve the single UI request across concurrent MCP processes."""
        with self.store.transaction(True) as con:
            q = con.execute("SELECT * FROM questions WHERE id=? AND session_id=?", (question_id, session_id)).fetchone()
            if not q or not q["current"]:
                raise ScsError("LATE_REPLY", "This question was replaced.")
            if q["state"] != "REQUESTED":
                return False, dict(q)
            con.execute("UPDATE questions SET state='WAITING_USER',updated_at=? WHERE id=?", (now(), question_id))
            self.store.event(con, session_id, question_id, "interaction_request", "WAITING_USER")
            return True, dict(con.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone())

    def mark(self, session_id: str, question_id: str, state: str, error_code: str | None = None) -> dict:
        if state not in {"WAITING_USER", "UNSUPPORTED", "FAILED"}:
            raise ScsError("STATE_INVALID", "Unsupported internal transition.")
        with self.store.transaction(True) as con:
            q = con.execute("SELECT * FROM questions WHERE id=? AND session_id=?", (question_id, session_id)).fetchone()
            if not q or not q["current"]:
                raise ScsError("LATE_REPLY", "Question was replaced; this result cannot affect the current question.")
            if q["state"] in TERMINAL:
                return dict(q)
            con.execute("UPDATE questions SET state=?,updated_at=? WHERE id=?", (state, now(), question_id))
            self.store.event(con, session_id, question_id, "interaction_request", state, error_code,
                             error_code if state in {"UNSUPPORTED", "FAILED"} else None)
        return self.question(session_id, question_id)

    def submit(self, session_id: str, question_id: str, action: str, choices: list[str] | None = None,
               source: str = "text", observed_timeout: bool = False) -> dict:
        if action not in {"accept", "decline", "cancel", "timeout", "unsupported", "error"} or source not in {"native", "mcp", "text"}:
            raise ScsError("INPUT_INVALID", "Unknown action or reply source.")
        if action == "timeout" and not observed_timeout:
            raise ScsError("TIMEOUT_UNOBSERVED", "An RPC deadline or sleep is not an observed user-interface timeout.")
        if action != "accept" and choices:
            raise ScsError("CHOICE_INVALID", "Only an explicit accepted reply can carry choices.")
        target = {"accept": "CONFIRMED", "decline": "DECLINED", "cancel": "CANCELLED", "timeout": "TIMED_OUT", "unsupported": "UNSUPPORTED", "error": "FAILED"}[action]
        codes = {"accept": "USER_CONFIRMED", "decline": "USER_DECLINED", "cancel": "USER_CANCELLED",
                 "timeout": "HOST_OBSERVED_TIMEOUT", "unsupported": "ELICITATION_UNSUPPORTED", "error": "RPC_ERROR"}
        with self.store.transaction(True) as con:
            self._session(con, session_id)
            q = con.execute("SELECT * FROM questions WHERE id=? AND session_id=?", (question_id, session_id)).fetchone()
            if q is None:
                raise ScsError("QUESTION_NOT_FOUND", "Question is not part of this conversation.")
            if not q["current"] or q["kind"] not in KINDS:
                raise ScsError("LATE_REPLY", "A reply for a replaced question is isolated.")
            selected = check_selection(q["kind"], choices or []) if action == "accept" else None
            if (self.store.native_review and q["kind"] == "codex_gate"
                    and selected == ["packages"] and q["answer"] != '["packages"]'):
                raise ScsError("CHOICE_INVALID", "Choose closure or none. Package review is manual and one-time.")
            answer = encode(selected) if selected is not None else None
            if q["state"] in TERMINAL:
                if q["state"] == target and q["answer"] == answer:
                    return {"status": target, "question_id": question_id, "idempotent": True}
                raise ScsError("REPLY_CONFLICT", "This question has already ended. Explicitly reopen to change it.")
            con.execute("UPDATE questions SET state=?,answer=?,updated_at=? WHERE id=?", (target, answer, now(), question_id))
            if action == "accept":
                selected = json.loads(answer)
                # codex_gate has no sessions column: the confirmed question row
                # is the only source and is projected by get().
                column = {"mode": "mode", "verification": "verification",
                          "codex_review": "codex_review"}.get(q["kind"])
                if column:
                    value = answer if q["kind"] == "verification" else (int(selected[0] == "yes") if q["kind"] == "codex_review" else selected[0])
                    con.execute(f"UPDATE sessions SET {column}=? WHERE id=?", (value, session_id))
            self.store.event(con, session_id, question_id, "interaction_submit", codes[action],
                             codes[action] if action in {"error", "unsupported", "timeout"} else None)
        return {"status": target, "question_id": question_id, "choices": choices if action == "accept" else None,
                "error_code": codes[action], "source": source, "destructive_operations_authorized": False}

    def reopen(self, session_id: str, kind: str, explicit_user_change: bool = False) -> dict:
        if kind not in KINDS:
            raise ScsError("INPUT_INVALID", "Unknown question kind.")
        with self.store.transaction(True) as con:
            session = self._session(con, session_id)
            q = con.execute("SELECT * FROM questions WHERE session_id=? AND kind=? AND current=1", (session_id, kind)).fetchone()
            if not q:
                # The explicit skip path confirms quick without creating a question.
                # A later explicit mode change must still be representable in this chat.
                if kind == "mode" and session["mode"] and explicit_user_change:
                    con.execute("UPDATE sessions SET mode=NULL WHERE id=?", (session_id,))
                    self.store.event(con, session_id, None, "interaction_reopen", "EXPLICIT_MODE_CHANGE")
                    return {"status": "REOPENED", "previous_question_id": None, "next": "interaction_request"}
                raise ScsError("QUESTION_NOT_FOUND", "There is no current question to reopen.")
            if q["state"] in {"REQUESTED", "WAITING_USER", "CONFIRMED"} and not explicit_user_change:
                raise ScsError("EXPLICIT_CHANGE_REQUIRED", "Reopening an active or confirmed question requires an explicit user change.")
            # Invalidate unfinished dependent questions when an earlier choice
            # changes. Already confirmed scopes remain this conversation's choices.
            dependent = {"mode": ("verification", "codex_gate", "codex_review"),
                         "verification": ("codex_gate", "codex_review"),
                         "codex_gate": ("codex_review",), "codex_review": ()}[kind]
            for child_kind in dependent:
                child = con.execute("SELECT * FROM questions WHERE session_id=? AND kind=? AND current=1 AND state != 'CONFIRMED'",
                                    (session_id, child_kind)).fetchone()
                if child:
                    con.execute("UPDATE questions SET current=0,updated_at=? WHERE id=?", (now(), child["id"]))
                    self.store.event(con, session_id, child["id"], "interaction_reopen", "SUPERSEDED")
            con.execute("UPDATE questions SET current=0,updated_at=? WHERE id=?", (now(), q["id"]))
            column = {"mode": "mode", "verification": "verification",
                      "codex_review": "codex_review"}.get(kind)
            if column:
                con.execute(f"UPDATE sessions SET {column}=NULL WHERE id=?", (session_id,))
            self.store.event(con, session_id, q["id"], "interaction_reopen", "SUPERSEDED")
        return {"status": "REOPENED", "previous_question_id": q["id"], "next": "interaction_request"}
