"""Protocol-level destructive-operation gate records.

MCP records an explicit decision and answers a read-only pre-execution check.
It never executes or intercepts host commands. Records reuse the existing
``events`` table (``tool=operation_gate``) as a bounded JSON summary, so this
module adds no schema migration.
"""

import hashlib
import json
import re
from pathlib import Path

from .errors import ScsError
from .evidence import native_review_receipt, verify_native_final
from .state import State
from .storage import Store, encode, now

GATE_TOOL = "operation_gate"
VERDICTS = ("ACCEPT", "REJECT", "UNAVAILABLE_OVERRIDE")
MAX_EVENT_BYTES = 16 * 1024
MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_TARGETS = 100
MAX_BASELINE_FILES = 200
DEVICE_PREFIXES = ("device:", "serial:", "programmer:")
DEVICE_ID = re.compile(r"[A-Za-z0-9_.-]{1,128}")
OPERATION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
HEX_REVISION = re.compile(r"[0-9a-fA-F]{7,64}")
SHA256_ANY_CASE = re.compile(r"[0-9a-fA-F]{64}")
SHA256_LOWER = re.compile(r"[0-9a-f]{64}")
CONCLUSION = re.compile(r"结论\s*[:：]\s*(ACCEPT|REJECT)")


def _invalid(message: str) -> ScsError:
    return ScsError("GATE_INVALID", message)


class Gate:
    """Record and read one session's operation decisions.

    The record is keyed by ``session_id`` plus ``operation_id``. A digest binds
    the exact command, cwd, sorted targets and baseline; a changed value is
    STALE rather than silently reusing an old approval.
    """

    def __init__(self, store: Store):
        self.store = store
        self.paths = store.paths

    # ── validation and canonicalization ─────────────────────────────

    @staticmethod
    def _session(con, session_id: str):
        row = con.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise ScsError("SESSION_NOT_FOUND", "Open or explicitly recover this conversation first.")

    @staticmethod
    def _operation_id(value) -> str:
        if not isinstance(value, str) or not OPERATION_ID.fullmatch(value):
            raise ScsError("INPUT_INVALID", "operation_id must be a simple identifier.")
        return value

    def _cwd(self, value) -> str:
        if (not isinstance(value, str) or not value or len(value) > 4096
                or "\x00" in value or "\\" in value or ".." in Path(value).parts):
            raise _invalid("cwd must be an absolute path inside the project root.")
        raw = Path(value)
        if not raw.is_absolute():
            raise _invalid("cwd must be an absolute path inside the project root.")
        root = self.paths.root
        try:
            relative = raw.relative_to(root)
        except ValueError:
            raise _invalid("cwd must be an absolute path inside the project root.") from None
        if relative.parts:
            try:
                normalized = self.paths.validate(relative.as_posix())
            except ScsError:
                raise _invalid("cwd must stay inside the project without symlink traversal.") from None
            full = root / normalized
        else:
            full = root
        if full.is_symlink() or not full.is_dir():
            raise _invalid("cwd must be an existing directory inside the project root.")
        return str(full)

    def _targets(self, value) -> list[str]:
        if not isinstance(value, list) or not 1 <= len(value) <= MAX_TARGETS:
            raise _invalid("targets must contain between one and 100 entries.")
        out = []
        for item in value:
            if not isinstance(item, str) or not item or len(item) > 512 or "\x00" in item:
                raise _invalid("Each target must be a bounded project-relative path or device identifier.")
            if item.startswith(DEVICE_PREFIXES):
                _, _, ident = item.partition(":")
                if not DEVICE_ID.fullmatch(ident):
                    raise _invalid("Use a simple device:/serial:/programmer: logical identifier.")
                out.append(item)
                continue
            # A colon in the first path segment would make file: and drive
            # prefixes ambiguous; device identifiers were handled above.
            if ":" in item.split("/", 1)[0]:
                raise _invalid("Use a project-relative path or a device:/serial:/programmer: identifier.")
            try:
                out.append(self.paths.validate(item))
            except ScsError:
                raise _invalid("Each non-device target must be a project-relative path "
                               "without traversal or symlinks.") from None
        if len(set(out)) != len(out):
            raise _invalid("targets must be unique.")
        return sorted(out)

    @staticmethod
    def _narrative(value, name: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 2000:
            raise _invalid(name + " must be 1..2000 characters.")
        return value.strip()

    @staticmethod
    def _reason(value) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 2000:
            raise _invalid("reason must be 1..2000 characters when supplied.")
        return value.strip()

    def _baseline(self, value):
        if value is None:
            return None
        if not isinstance(value, dict) or len(value) != 1:
            raise _invalid('baseline must be {"head": "<sha>"} or {"files": {path: sha256}}.')
        key, payload = next(iter(value.items()))
        if key == "head":
            if not isinstance(payload, str) or not HEX_REVISION.fullmatch(payload):
                raise _invalid("baseline.head must be a 7..64 character hexadecimal revision.")
            return {"head": payload.lower()}
        if key != "files":
            raise _invalid('baseline must use "head" or "files".')
        if not isinstance(payload, dict) or len(payload) > MAX_BASELINE_FILES:
            raise _invalid("baseline.files must contain at most 200 files.")
        files = {}
        for path, digest in payload.items():
            if (not isinstance(path, str) or not isinstance(digest, str)
                    or not SHA256_ANY_CASE.fullmatch(digest)):
                raise _invalid("baseline.files maps project-relative paths to sha256 hex digests.")
            try:
                normalized = self.paths.validate(path)
            except ScsError:
                raise _invalid("baseline.files paths must stay inside the project.") from None
            files[normalized] = digest.lower()
        if len(files) != len(payload):
            raise _invalid("baseline.files paths must be unique after normalization.")
        return {"files": dict(sorted(files.items()))}

    def _report_path(self, value, *, required: bool) -> str | None:
        if value is None:
            if required:
                raise _invalid("report_path is required for ACCEPT and REJECT.")
            return None
        if not isinstance(value, str) or not value or len(value) > 1024 or "\x00" in value:
            raise _invalid("report_path must be a project-relative path.")
        try:
            return self.paths.validate(value)
        except ScsError:
            raise _invalid("report_path must stay inside the project without traversal or symlinks.") from None

    def _report_digest(self, path: str, verdict: str) -> str:
        try:
            data = self.paths.read(path, limit=MAX_REPORT_BYTES)
        except ScsError:
            raise _invalid("The gate report must be an existing bounded project file "
                           "without symlinks.") from None
        digest = hashlib.sha256(data).hexdigest()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise _invalid("The gate report must be UTF-8 text.") from None
        if text.startswith("\ufeff"):
            text = text[1:]
        conclusions = []
        for line in text.splitlines():
            candidate = line.strip()
            if candidate.startswith("**") and candidate.endswith("**") and len(candidate) >= 4:
                candidate = candidate[2:-2].strip()
            match = CONCLUSION.fullmatch(candidate)
            if match:
                conclusions.append(match.group(1))
        if len(conclusions) != 1:
            raise _invalid("The gate report must contain exactly one final conclusion line "
                           "结论: ACCEPT or 结论: REJECT.")
        if conclusions[0] != verdict:
            raise _invalid("The report conclusion does not match the requested verdict.")
        return digest

    def _optional_report_digest(self, path: str) -> str | None:
        try:
            data = self.paths.read_optional(path, limit=MAX_REPORT_BYTES)
        except ScsError:
            raise _invalid("report_path must be a bounded regular project file when supplied.") from None
        if data is None:
            return None
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _digest(command: str, cwd: str, targets: list[str], baseline) -> str:
        return hashlib.sha256(encode({
            "command": command, "cwd": cwd, "targets": sorted(targets), "baseline": baseline,
        }).encode()).hexdigest()

    @staticmethod
    def _find(con, session_id: str, operation_id: str) -> list[dict]:
        # LIKE is only a cheap prefilter: '_' is a wildcard, and the exact
        # operation_id is confirmed after parsing. Other tools' events never
        # match because tool is filtered too.
        rows = con.execute(
            "SELECT seq,at,event FROM events WHERE tool=? AND session_id=? AND event LIKE ? ORDER BY seq",
            (GATE_TOOL, session_id, '%"operation_id":"' + operation_id + '"%')).fetchall()
        found = []
        for row in rows:
            try:
                payload = json.loads(row["event"])
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict) or payload.get("operation_id") != operation_id:
                continue
            if payload.get("verdict") not in VERDICTS:
                continue
            digest = payload.get("operation_digest")
            if not isinstance(digest, str) or not SHA256_LOWER.fullmatch(digest):
                continue
            entry = dict(payload)
            entry["seq"] = row["seq"]
            entry["event_at"] = row["at"]
            found.append(entry)
        return found

    # ── public tools ────────────────────────────────────────────────

    def record(self, session_id: str, operation_id: str, command: str, cwd: str,
               targets: list[str], impact: str, backup: str, rollback: str,
               verdict: str, user_confirmed: bool, report_path: str | None = None,
               reason: str | None = None, baseline: dict | None = None,
               review_receipt: dict | None = None) -> dict:
        operation_id = self._operation_id(operation_id)
        if verdict not in VERDICTS:
            raise _invalid("verdict must be ACCEPT, REJECT or UNAVAILABLE_OVERRIDE.")
        if not isinstance(user_confirmed, bool):
            raise _invalid("user_confirmed must be a boolean.")
        if (not isinstance(command, str) or not command.strip()
                or len(command) > 4000 or "\x00" in command):
            raise _invalid("command must be a non-empty command line or argv string (max 4000 characters).")
        cwd = self._cwd(cwd)
        targets = self._targets(targets)
        impact = self._narrative(impact, "impact")
        backup = self._narrative(backup, "backup")
        rollback = self._narrative(rollback, "rollback")
        reason = self._reason(reason)
        baseline = self._baseline(baseline)
        needs_report = verdict in {"ACCEPT", "REJECT"}
        native = self.store.native_review
        if review_receipt is not None and (not native or not needs_report):
            raise _invalid("review_receipt is only accepted for native ACCEPT and REJECT.")
        report_path = self._report_path(report_path, required=needs_report and not native)
        digest = self._digest(command, cwd, targets, baseline)
        with self.store.transaction(True) as con:
            self._session(con, session_id)
            records = self._find(con, session_id, operation_id)
            if records:
                if any(record["operation_digest"] != digest for record in records):
                    raise ScsError("OPERATION_CONFLICT",
                                   "operation_id was already recorded with another command, cwd, targets or baseline.")
                if verdict != "REJECT" and any(record["verdict"] == "REJECT" for record in records):
                    raise ScsError("GATE_REJECTED", "This operation and digest were rejected; a rejection is terminal.")
                if verdict == "REJECT":
                    terminal = next((record for record in reversed(records) if record["verdict"] == "REJECT"), None)
                    if terminal is not None:
                        return self._record_response(terminal, idempotent=True)
                if not native and needs_report and any("review_receipt" in record for record in records):
                    raise ScsError("NATIVE_REVIEW_DISABLED", "This operation already uses native review receipts.")
            route = None
            if native and needs_report:
                route = State(self.store).require_review_route(con, session_id)
                review_receipt = native_review_receipt(review_receipt, "operation_digest", digest, route)
                if verdict != review_receipt["verdict"]:
                    raise _invalid("The receipt verdict does not match the requested operation verdict.")
                final = review_receipt["final_message"]
                final_path = self._report_path(final["path"], required=True)
                if report_path is not None and report_path != final_path:
                    raise _invalid("report_path must match review_receipt.final_message.path when supplied.")
                report_path = final_path
            if records:
                same = next((record for record in reversed(records) if record["verdict"] == verdict
                             and (not native or not needs_report or record.get("review_route") == route)), None)
                if same is not None:
                    if native and needs_report and same.get("review_receipt") != review_receipt:
                        raise ScsError("OPERATION_CONFLICT", "The recorded native receipt differs from this request.")
                    # A replay returns the original record without re-reading
                    # the report file, but the required confirmation fields
                    # must still be present and valid.
                    if verdict == "ACCEPT" and not user_confirmed:
                        raise _invalid("ACCEPT requires user_confirmed=true.")
                    if verdict == "UNAVAILABLE_OVERRIDE":
                        if not user_confirmed:
                            raise _invalid("UNAVAILABLE_OVERRIDE requires user_confirmed=true.")
                        if reason is None or len(reason) < 10:
                            raise _invalid("UNAVAILABLE_OVERRIDE requires a reason of at least 10 characters.")
                    return self._record_response(same, idempotent=True)
                if verdict == "UNAVAILABLE_OVERRIDE" and any(
                        record["verdict"] == "ACCEPT" for record in records):
                    raise ScsError("OPERATION_CONFLICT",
                                   "This operation and digest already have an ACCEPT record.")
            if needs_report:
                if verdict == "ACCEPT" and not user_confirmed:
                    raise _invalid("ACCEPT requires user_confirmed=true.")
                if native:
                    report_sha256 = review_receipt["final_message"]["sha256"]
                    verify_native_final(self.store, report_path, report_sha256, verdict)
                else:
                    report_sha256 = self._report_digest(report_path, verdict)
            else:
                if not user_confirmed:
                    raise _invalid("UNAVAILABLE_OVERRIDE requires user_confirmed=true.")
                if reason is None or len(reason) < 10:
                    raise _invalid("UNAVAILABLE_OVERRIDE requires a reason of at least 10 characters.")
                report_sha256 = self._optional_report_digest(report_path) if report_path else None
            at = now()
            summary = {
                "schema": 1,
                "operation_id": operation_id,
                "operation_digest": digest,
                "digest": digest,
                "command": command,
                "cwd": cwd,
                "targets": targets,
                "impact": impact,
                "backup": backup,
                "rollback": rollback,
                "verdict": verdict,
                "report_path": report_path,
                "report_sha256": report_sha256,
                "user_confirmed": user_confirmed,
                "reason": reason,
                "baseline": baseline,
                "at": at,
                "protocol_only": True,
            }
            if route is not None:
                summary.update(review_route=route, review_receipt=review_receipt)
            encoded = encode(summary)
            if len(encoded.encode("utf-8")) > MAX_EVENT_BYTES:
                raise _invalid("The gate record exceeds the 16 KB event limit; "
                               "shorten its text or baseline.")
            self.store.event(con, session_id, None, GATE_TOOL, encoded)
            summary["seq"] = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            summary["event_at"] = at
            return self._record_response(summary, idempotent=False)

    def check(self, session_id: str, operation_id: str, command: str | None = None,
              cwd: str | None = None, targets: list[str] | None = None,
              baseline: dict | None = None) -> dict:
        operation_id = self._operation_id(operation_id)
        with self.store.transaction() as con:
            self._session(con, session_id)
            records = self._find(con, session_id, operation_id)
            route = State(self.store).review_route(con, session_id)
        if not records:
            missing = self._missing(operation_id)
            if self.store.native_review and command is not None and cwd is not None and targets is not None:
                if (not isinstance(command, str) or not command.strip()
                        or len(command) > 4000 or "\x00" in command):
                    raise _invalid("command must be a non-empty command line or argv string (max 4000 characters).")
                missing["operation_digest"] = self._digest(command, self._cwd(cwd), self._targets(targets),
                                                           self._baseline(baseline))
            return missing
        digests = {record["operation_digest"] for record in records}
        if len(digests) != 1:
            return self._stale(operation_id, None, None, ["RECORD_CONFLICT"], record=records[-1])
        latest = records[-1]
        stored_digest = latest["operation_digest"]
        stored_command = latest.get("command")
        stored_cwd = latest.get("cwd")
        stored_targets = latest.get("targets")
        stored_baseline = latest.get("baseline")
        if (not isinstance(stored_command, str)
                or not isinstance(stored_cwd, str)
                or not isinstance(stored_targets, list)
                or not 1 <= len(stored_targets) <= MAX_TARGETS
                or any(not isinstance(item, str) for item in stored_targets)):
            return self._stale(operation_id, stored_digest, None, ["RECORD_INVALID"], record=latest)
        expected = self._digest(stored_command, stored_cwd, stored_targets, stored_baseline)
        if expected != stored_digest:
            return self._stale(operation_id, stored_digest, expected, ["RECORD_INVALID"], record=latest)
        reasons = []
        effective_command = stored_command
        if command is not None:
            if not isinstance(command, str) or not command.strip() or len(command) > 4000:
                return self._stale(operation_id, stored_digest, None, ["COMMAND_INVALID"], record=latest)
            effective_command = command
            if command != stored_command:
                reasons.append("COMMAND_MISMATCH")
        effective_cwd = stored_cwd
        if cwd is not None:
            try:
                effective_cwd = self._cwd(cwd)
            except ScsError:
                return self._stale(operation_id, stored_digest, None, ["CWD_INVALID"], record=latest)
            if effective_cwd != stored_cwd:
                reasons.append("CWD_MISMATCH")
        effective_targets = stored_targets
        if targets is not None:
            try:
                effective_targets = self._targets(targets)
            except ScsError:
                return self._stale(operation_id, stored_digest, None, ["TARGETS_INVALID"], record=latest)
            if effective_targets != stored_targets:
                reasons.append("TARGETS_MISMATCH")
        effective_baseline = stored_baseline
        if baseline is not None:
            try:
                effective_baseline = self._baseline(baseline)
            except ScsError:
                return self._stale(operation_id, stored_digest, None, ["BASELINE_INVALID"], record=latest)
            if effective_baseline != stored_baseline:
                reasons.append("BASELINE_MISMATCH")
        computed = self._digest(effective_command, effective_cwd, effective_targets, effective_baseline)
        if computed != stored_digest:
            return self._stale(operation_id, stored_digest, computed,
                               reasons or ["DIGEST_MISMATCH"], record=latest)
        reject = next((record for record in reversed(records) if record["verdict"] == "REJECT"), None)
        if reject is not None:
            return self._check_response("REJECTED", reject, reasons)
        accept = next((record for record in reversed(records)
                       if record["verdict"] == "ACCEPT" and record.get("user_confirmed") is True), None)
        if accept is not None:
            # Native approvals cannot be replayed as a legacy report. A native
            # checker also cannot approve a pre-native ACCEPT or a different route.
            if self.store.native_review or "review_receipt" in accept:
                recorded_route = accept.get("review_route")
                if route is None or recorded_route != route:
                    return self._stale(operation_id, stored_digest, computed, ["REVIEW_ROUTE_MISMATCH"], record=accept)
                try:
                    receipt = native_review_receipt(accept.get("review_receipt"), "operation_digest",
                                                    stored_digest, recorded_route)
                    final = receipt["final_message"]
                    if (receipt["verdict"] != "ACCEPT" or final["sha256"] != accept.get("report_sha256")
                            or self._report_path(final["path"], required=True) != accept.get("report_path")):
                        raise _invalid("The stored review receipt does not match the approval.")
                except ScsError:
                    return self._stale(operation_id, stored_digest, computed, ["REVIEW_RECEIPT_INVALID"], record=accept)
            return self._check_response("APPROVED", accept, reasons)
        override = next((record for record in reversed(records)
                         if record["verdict"] == "UNAVAILABLE_OVERRIDE"), None)
        if override is not None:
            return self._check_response("UNAVAILABLE_OVERRIDE", override, reasons)
        # An ACCEPT record without the required confirmation, or an unknown
        # record shape, must never be reported as approved.
        return self._stale(operation_id, stored_digest, computed, ["RECORD_INVALID"], record=latest)

    # ── response shaping ────────────────────────────────────────────

    @staticmethod
    def _record_response(record: dict, *, idempotent: bool) -> dict:
        result = {
            "status": "OK",
            "recorded": not idempotent,
            "idempotent": idempotent,
            "operation_id": record.get("operation_id"),
            "operation_digest": record.get("operation_digest"),
            "digest": record.get("digest") or record.get("operation_digest"),
            "command": record.get("command"),
            "cwd": record.get("cwd"),
            "targets": record.get("targets"),
            "impact": record.get("impact"),
            "backup": record.get("backup"),
            "rollback": record.get("rollback"),
            "verdict": record.get("verdict"),
            "report_path": record.get("report_path"),
            "report_sha256": record.get("report_sha256"),
            "user_confirmed": record.get("user_confirmed") is True,
            "reason": record.get("reason"),
            "baseline": record.get("baseline"),
            "at": record.get("at") or record.get("event_at"),
            "event_seq": record.get("seq"),
            "protocol_only": True,
            "execution_intercepted": False,
            "message": "Decision recorded. MCP does not execute or intercept host commands.",
        }
        if "review_receipt" in record:
            result.update(review_route=record["review_route"], review_receipt=record["review_receipt"],
                          execution_provenance="STRUCTURAL_RECEIPT_ONLY_HOST_NOT_AUTHENTICATED")
        return result

    @staticmethod
    def _missing(operation_id: str) -> dict:
        return {
            "status": "MISSING",
            "operation_id": operation_id,
            "approved": False,
            "protocol_only": True,
            "execution_intercepted": False,
            "user_confirmation_still_required": True,
            "message": "No gate record exists for this session and operation_id.",
        }

    @staticmethod
    def _stale(operation_id: str, expected: str | None, computed: str | None,
               reasons: list[str], *, record: dict | None = None) -> dict:
        result = {
            "status": "STALE",
            "operation_id": operation_id,
            "approved": False,
            "expected_digest": expected,
            "computed_digest": computed,
            "reasons": reasons,
            "protocol_only": True,
            "execution_intercepted": False,
            "user_confirmation_still_required": True,
            "message": "The gate record does not match the supplied operation or baseline; record it again.",
        }
        if record is not None:
            result["record_verdict"] = record.get("verdict")
            result["record_at"] = record.get("at") or record.get("event_at")
            result["event_seq"] = record.get("seq")
        return result

    @staticmethod
    def _check_response(status: str, record: dict, reasons: list[str]) -> dict:
        return {
            "status": status,
            "operation_id": record.get("operation_id"),
            "operation_digest": record.get("operation_digest"),
            "verdict": record.get("verdict"),
            "approved": status == "APPROVED",
            "user_confirmed": record.get("user_confirmed") is True,
            "reasons": reasons,
            "command": record.get("command"),
            "cwd": record.get("cwd"),
            "targets": record.get("targets"),
            "baseline": record.get("baseline"),
            "report_path": record.get("report_path"),
            "report_sha256": record.get("report_sha256"),
            "reason": record.get("reason"),
            "at": record.get("at") or record.get("event_at"),
            "event_seq": record.get("seq"),
            "protocol_only": True,
            "execution_intercepted": False,
            "user_confirmation_still_required": True,
            "message": ("An ACCEPT record exists, but the user's independent confirmation is still "
                        "required and MCP cannot intercept the command." if status == "APPROVED"
                        else "This decision does not authorize execution."),
        }
