"""Conversation identity and explicit, idempotent question state transitions."""

import hashlib
import json
import re
import uuid

from .errors import ScsError
from .storage import Store, encode, now

MODES = ("quick", "standard", "long", "investigate")
VERIFICATION = ("smoke", "targeted", "full", "stability", "none", "method-only")
KINDS = ("mode", "verification", "codex_review")
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
    "codex_review": [{"value": "no", "label": "不使用", "desc": DESCRIPTIONS["codex_review"]["no"]},
                     {"value": "yes", "label": "使用：收尾只读独立审查", "desc": DESCRIPTIONS["codex_review"]["yes"]}],
}


def identifier(prefix: str) -> str:
    return prefix + uuid.uuid4().hex


def _code(kind: str, value: str) -> str:
    """Accept either the stored code or the display label shown to users."""
    for option in OPTIONS[kind]:
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
    def __init__(self, store: Store):
        self.store = store

    def _session(self, con, session_id):
        row = con.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise ScsError("SESSION_NOT_FOUND", "Open or explicitly recover this conversation first.")
        return row

    def open(self, project_root: str, client_name: str, conversation_id: str | None = None,
             review_registration: str = "unknown", explicit_skip: bool = False) -> dict:
        from pathlib import Path
        if ".." in Path(project_root).parts or Path(project_root).resolve() != self.store.root:
            raise ScsError("PROJECT_MISMATCH", "Session root must match the server's configured project root.")
        if not re.fullmatch(r"[\w .-]{1,64}", client_name) or review_registration not in {"registered", "unregistered", "unknown"}:
            raise ScsError("INPUT_INVALID", "Supply a short client name and known registration state.")
        if conversation_id is not None and (not conversation_id.strip() or len(conversation_id) > 1024):
            raise ScsError("INPUT_INVALID", "Conversation ID must be a nonempty host-provided identifier.")
        temporary = conversation_id is None
        identity = hashlib.sha256(encode([str(self.store.root), client_name, conversation_id or uuid.uuid4().hex]).encode()).hexdigest()
        with self.store.transaction(True) as con:
            row = con.execute("SELECT id FROM sessions WHERE identity=?", (identity,)).fetchone()
            sid = row[0] if row else identifier("S-")
            if row is None:
                con.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?)",
                            (sid, identity, client_name, temporary, review_registration,
                             "quick" if explicit_skip else None, None, None, now()))
                self.store.event(con, sid, None, "session_open", "EXPLICIT_SKIP" if explicit_skip else "SESSION_OPENED")
            elif review_registration != "unknown":
                # Host attestation may become available after registry loading.
                con.execute("UPDATE sessions SET review_registration=? WHERE id=?", (review_registration, sid))
        result = self.get(sid)
        if temporary:
            result["warning"] = "TEMPORARY_SESSION: retain session_id in this chat; restart recognition is not automatic."
        result["destructive_operations_authorized"] = False
        return result

    def get(self, session_id: str) -> dict:
        with self.store.transaction() as con:
            session = dict(self._session(con, session_id))
            questions = [dict(r) for r in con.execute("SELECT * FROM questions WHERE session_id=? AND current=1 ORDER BY created_at", (session_id,))]
        session.pop("identity")
        session["temporary"] = bool(session["temporary"])
        session["verification"] = json.loads(session["verification"]) if session["verification"] else None
        session["codex_review"] = bool(session["codex_review"]) if session["codex_review"] is not None else None
        for q in questions:
            q["recommendation"] = json.loads(q["recommendation"])
            q["answer"] = json.loads(q["answer"]) if q["answer"] else None
        return {"status": "OK", **session, "questions": questions,
                "unresolved": [q["id"] for q in questions if q["state"] != "CONFIRMED"],
                "question_states": {k: next((q["state"] for q in questions if q["kind"] == k), "NOT_ASKED") for k in KINDS}}

    def question(self, session_id: str, question_id: str) -> dict:
        with self.store.transaction() as con:
            self._session(con, session_id)
            q = con.execute("SELECT * FROM questions WHERE id=? AND session_id=?", (question_id, session_id)).fetchone()
            if q is None:
                raise ScsError("QUESTION_NOT_FOUND", "Question is not part of this conversation.")
            return dict(q)

    def request(self, session_id: str, kind: str, recommendation: list[str], channel: str) -> dict:
        if kind not in KINDS or channel not in {"auto", "native", "text"}:
            raise ScsError("INPUT_INVALID", "Unknown question kind or interaction channel.")
        recommendation = check_selection(kind, recommendation)
        with self.store.transaction(True) as con:
            s = self._session(con, session_id)
            q = con.execute("SELECT * FROM questions WHERE session_id=? AND kind=? AND current=1", (session_id, kind)).fetchone()
            if q:
                return {"created": False, **dict(q)}
            if kind != "mode" and not s["mode"]:
                raise ScsError("QUESTION_ORDER", "Confirm mode first.")
            if kind == "verification" and s["mode"] not in {"standard", "long"}:
                return {"status": "SKIPPED", "reason": "MODE_DOES_NOT_REQUIRE_QUESTION"}
            if kind == "codex_review" and s["mode"] not in {"quick", "standard", "long"}:
                return {"status": "SKIPPED", "reason": "MODE_DOES_NOT_REQUIRE_QUESTION"}
            if kind == "mode" and s["mode"]:
                return {"status": "ALREADY_CONFIRMED", "choice": [s["mode"]]}
            if kind == "codex_review":
                if s["mode"] in {"standard", "long"} and not s["verification"]:
                    raise ScsError("QUESTION_ORDER", "Confirm verification scope first.")
                if s["review_registration"] != "registered":
                    return {"status": "SKIPPED", "reason": "CODEX_REVIEW_NOT_REGISTERED"}
                if recommendation != ["no"]:
                    raise ScsError("CHOICE_INVALID", "The review question recommends no additional review.")
            # No simultaneous native and MCP questions, even for different kinds.
            pending = con.execute("SELECT id FROM questions WHERE session_id=? AND current=1 AND state != 'CONFIRMED'", (session_id,)).fetchone()
            if pending:
                raise ScsError("QUESTION_PENDING", "Resolve or explicitly reopen the pending question first.")
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
            if not q["current"]:
                raise ScsError("LATE_REPLY", "A reply for a replaced question is isolated.")
            answer = encode(check_selection(q["kind"], choices or [])) if action == "accept" else None
            if q["state"] in TERMINAL:
                if q["state"] == target and q["answer"] == answer:
                    return {"status": target, "question_id": question_id, "idempotent": True}
                raise ScsError("REPLY_CONFLICT", "This question has already ended. Explicitly reopen to change it.")
            con.execute("UPDATE questions SET state=?,answer=?,updated_at=? WHERE id=?", (target, answer, now(), question_id))
            if action == "accept":
                selected = json.loads(answer)
                column = {"mode": "mode", "verification": "verification", "codex_review": "codex_review"}[q["kind"]]
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
            dependent = {"mode": ("verification", "codex_review"), "verification": ("codex_review",), "codex_review": ()}[kind]
            for child_kind in dependent:
                child = con.execute("SELECT * FROM questions WHERE session_id=? AND kind=? AND current=1 AND state != 'CONFIRMED'",
                                    (session_id, child_kind)).fetchone()
                if child:
                    con.execute("UPDATE questions SET current=0,updated_at=? WHERE id=?", (now(), child["id"]))
                    self.store.event(con, session_id, child["id"], "interaction_reopen", "SUPERSEDED")
            con.execute("UPDATE questions SET current=0,updated_at=? WHERE id=?", (now(), q["id"]))
            column = {"mode": "mode", "verification": "verification", "codex_review": "codex_review"}[kind]
            con.execute(f"UPDATE sessions SET {column}=NULL WHERE id=?", (session_id,))
            self.store.event(con, session_id, q["id"], "interaction_reopen", "SUPERSEDED")
        return {"status": "REOPENED", "previous_question_id": q["id"], "next": "interaction_request"}
