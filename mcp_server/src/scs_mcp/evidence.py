"""Verify bounded evidence files; never infer final acceptance or run tests."""

import hashlib
import json
import posixpath
import re
from pathlib import PurePosixPath

from .errors import ScsError
from .storage import Store

REVIEW_FIELDS = {"verdict", "session_id", "model", "subject_sha256", "final_message_sha256",
                 "command", "exit_code", "raw_path"}
CONCLUSION = re.compile(r"^\s*\*{0,2}\s*结论\s*[:：]\s*(ACCEPT|REJECT)\s*[。.]?\s*\*{0,2}\s*$")


def _review_check(store: Store, text: str, clean: str, prefix: str,
                  subject_sha256: str | None) -> dict:
    """Strict JSON manifest: verdict is the only conclusion signal.

    ACCEPT and REJECT are both valid reports (exit_code must be 0); only ACCEPT
    maps to a PASS subtask result in Work. The raw final message is a sibling
    file whose sha256 and single conclusion line must agree with the manifest.
    """
    try:
        manifest = json.loads(text)
    except (ValueError, TypeError):
        return {"status": "INSUFFICIENT", "error_code": "REVIEW_MANIFEST_REQUIRED"}
    if not isinstance(manifest, dict) or set(manifest) != REVIEW_FIELDS:
        return {"status": "INVALID", "error_code": "REVIEW_MANIFEST_INVALID"}
    verdict = manifest.get("verdict")
    if verdict not in {"ACCEPT", "REJECT"}:
        return {"status": "INVALID", "error_code": "REVIEW_VERDICT_INVALID"}
    for field, limit in (("session_id", 256), ("model", 128), ("command", 4000)):
        value = manifest.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            return {"status": "INVALID", "error_code": "REVIEW_MANIFEST_INVALID"}
    if type(manifest.get("exit_code")) is not int or manifest["exit_code"] != 0:
        return {"status": "INVALID", "error_code": "REVIEW_EXIT_NONZERO_OR_UNKNOWN"}
    for field in ("subject_sha256", "final_message_sha256"):
        value = manifest.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            return {"status": "INVALID", "error_code": "REVIEW_MANIFEST_INVALID"}
    if subject_sha256 is not None and manifest["subject_sha256"] != subject_sha256:
        return {"status": "INVALID", "error_code": "REVIEW_STALE",
                "message": "The reviewed subject changed; run a new review attempt."}
    raw_path = manifest.get("raw_path")
    if (not isinstance(raw_path, str) or not raw_path.strip() or len(raw_path) > 512
            or raw_path.startswith("/") or "\\" in raw_path
            or ".." in PurePosixPath(raw_path).parts):
        return {"status": "INVALID", "error_code": "REVIEW_MANIFEST_INVALID"}
    raw_relative = posixpath.normpath(posixpath.join(posixpath.dirname(clean), raw_path))
    if not raw_relative.startswith(prefix + "/"):
        return {"status": "INVALID", "error_code": "REVIEW_RAW_SCOPE"}
    try:
        raw = store.paths.read(raw_relative)
    except ScsError as exc:
        return {"status": "INVALID", "error_code": exc.code, "message": str(exc)}
    if hashlib.sha256(raw).hexdigest() != manifest["final_message_sha256"]:
        return {"status": "INVALID", "error_code": "REVIEW_FINAL_MESSAGE_HASH_MISMATCH"}
    try:
        raw_text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"status": "INSUFFICIENT", "error_code": "REVIEW_FINAL_MESSAGE_NOT_TEXT"}
    conclusions = [match.group(1) for match in
                   (CONCLUSION.match(line) for line in raw_text.splitlines()) if match]
    if len(conclusions) != 1:
        return {"status": "INVALID", "error_code": "REVIEW_CONCLUSION_MISSING_OR_AMBIGUOUS"}
    if conclusions[0] != verdict:
        return {"status": "INVALID", "error_code": "REVIEW_VERDICT_CONFLICT"}
    return {"status": "VERIFIED", "review": {
        "verdict": verdict, "session_id": manifest["session_id"], "model": manifest["model"],
        "subject_sha256": manifest["subject_sha256"],
        "final_message_sha256": manifest["final_message_sha256"],
        "command": manifest["command"], "exit_code": manifest["exit_code"],
        "raw_path": raw_relative}}


def verify_file(store: Store, path: str, prefix: str, kind: str,
                required_content: list[str] | None = None,
                subject_sha256: str | None = None) -> dict:
    try:
        clean = store.paths.validate(path)
        if not clean.startswith(prefix + "/"):
            raise ScsError("EVIDENCE_SCOPE", "Evidence must be under this work package revision and subtask.")
        data = store.paths.read(clean)
    except ScsError as exc:
        return {"status": "INVALID", "error_code": exc.code, "message": str(exc)}
    if not data.strip():
        return {"status": "INSUFFICIENT", "error_code": "EVIDENCE_EMPTY"}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"status": "INSUFFICIENT", "error_code": "EVIDENCE_NOT_TEXT"}
    base = {"path": clean, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "required_content": required_content or [], "scope": "FILE_AND_CONTENT_ONLY",
            "final_acceptance": False,
            "execution_provenance": "MAIN_AGENT_MUST_REVIEW_ORIGINAL_LOGS"}
    if kind == "review":
        # required_content is ignored here: verdict is the single judgement and
        # never a caller-supplied substring.
        checked = _review_check(store, text, clean, prefix, subject_sha256)
        if checked["status"] != "VERIFIED":
            return checked
        return {"status": "VERIFIED", **base, "review": checked["review"]}
    if required_content and any(fragment not in text for fragment in required_content):
        return {"status": "INSUFFICIENT", "error_code": "REQUIRED_CONTENT_MISSING"}
    if kind == "test":
        try:
            manifest = json.loads(text)
        except (ValueError, TypeError):
            return {"status": "INSUFFICIENT", "error_code": "TEST_MANIFEST_REQUIRED"}
        if not isinstance(manifest, dict):
            return {"status": "INVALID", "error_code": "TEST_MANIFEST_INVALID"}
        if type(manifest.get("exit_code")) is not int or manifest["exit_code"] != 0:
            return {"status": "INVALID", "error_code": "TEST_EXIT_NONZERO_OR_UNKNOWN"}
        if type(manifest.get("passed")) is not int or manifest["passed"] <= 0 or not isinstance(manifest.get("command"), str) or not manifest["command"].strip():
            return {"status": "INSUFFICIENT", "error_code": "TEST_RESULT_MISSING"}
    return {"status": "VERIFIED", **base}
