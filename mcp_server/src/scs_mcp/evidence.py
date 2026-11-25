"""Verify bounded evidence files; never infer final acceptance or run tests."""

import hashlib
import json
import posixpath
import re
from collections.abc import Callable
from pathlib import PurePosixPath

from .errors import ScsError
from .storage import Store

FIXED_REVIEWER = "固定审阅员"

REVIEW_FIELDS = {"verdict", "session_id", "model", "subject_sha256", "final_message_sha256",
                 "command", "exit_code", "raw_path"}
CONCLUSION = re.compile(r"^\s*\*{0,2}\s*结论\s*[:：]\s*(ACCEPT|REJECT)\s*[。.]?\s*\*{0,2}\s*$")
NATIVE_CONCLUSION = re.compile(r"结论\s*[:：]\s*(ACCEPT|REJECT)")


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
    if verdict not in ("ACCEPT", "REJECT"):
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


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def native_review_receipt(manifest, subject_key: str, subject: str | None,
                          host_agent_id: str | None = None) -> dict:
    """Validate the receipt structure and asserted provenance, not the host."""
    def invalid():
        return ScsError("REVIEW_MANIFEST_INVALID", "Expected a strict native review receipt (schema_version=2).")

    fields = {"schema_version", subject_key, "agent_type", "agent_id", "model",
              "status", "verdict", "final_message"}
    if not isinstance(manifest, dict) or set(manifest) != fields:
        raise invalid()
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 2:
        raise invalid()
    submitted_subject = manifest[subject_key]
    if not isinstance(submitted_subject, str) or not re.fullmatch(r"[0-9a-f]{64}", submitted_subject):
        raise invalid()
    if subject is not None and submitted_subject != subject:
        raise ScsError("REVIEW_STALE", "The receipt covers a different subject.")
    if manifest["agent_type"] != FIXED_REVIEWER:
        raise ScsError("REVIEW_AGENT_MISMATCH", "Native review requires the configured fixed reviewer role.")
    agent_id, model = manifest["agent_id"], manifest["model"]
    if (not isinstance(agent_id, str) or not agent_id.strip() or agent_id != agent_id.strip()
            or len(agent_id) > 256 or not agent_id.isprintable()):
        raise invalid()
    if host_agent_id is not None and agent_id != host_agent_id:
        raise ScsError("REVIEW_AGENT_MISMATCH", "The receipt agent_id does not match the recorded host_agent_id.")
    if (not isinstance(model, str) or len(model) > 256
            or (model != "unobserved" and not re.fullmatch(r"[^/\s]+/[^\s]+", model))
            or not model.isprintable()):
        raise invalid()
    if manifest["status"] != "completed" or manifest["verdict"] not in ("ACCEPT", "REJECT"):
        raise invalid()
    final = manifest["final_message"]
    if not isinstance(final, dict) or set(final) != {"path", "sha256"}:
        raise invalid()
    path, digest = final["path"], final["sha256"]
    if (not isinstance(path, str) or not path.strip() or len(path) > 512
            or path.startswith("/") or "\\" in path or "\x00" in path
            or ".." in PurePosixPath(path).parts
            or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise invalid()
    return manifest


def verify_native_final(store: Store, path: str, sha256: str, verdict: str) -> None:
    raw = store.paths.read(path)
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise ScsError("REVIEW_FINAL_MESSAGE_HASH_MISMATCH", "The final message hash does not match the receipt.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ScsError("REVIEW_FINAL_MESSAGE_NOT_TEXT", "The final message must be UTF-8 text.") from None
    # Also count legacy-style conclusion lines as conflicting signals, but
    # require the single retained conclusion to follow the native grammar.
    conclusions = [line.strip() for line in text.splitlines() if CONCLUSION.fullmatch(line)]
    candidate = conclusions[0] if len(conclusions) == 1 else ""
    if candidate.startswith("**") and candidate.endswith("**"):
        candidate = candidate[2:-2].strip()
    conclusion = NATIVE_CONCLUSION.fullmatch(candidate)
    if conclusion is None:
        raise ScsError("REVIEW_CONCLUSION_MISSING_OR_AMBIGUOUS", "The final message must contain exactly one conclusion line.")
    if conclusion.group(1) != verdict:
        raise ScsError("REVIEW_VERDICT_CONFLICT", "The final conclusion does not match the receipt verdict.")


def _native_review_check(store: Store, text: str, clean: str, prefix: str,
                         subject: str | None, host_agent_id: str | None) -> dict:
    try:
        manifest = json.loads(text, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        return {"status": "INVALID", "error_code": "REVIEW_MANIFEST_INVALID"}
    try:
        receipt = native_review_receipt(manifest, "review_subject", subject, host_agent_id)
        final = receipt["final_message"]
        raw = posixpath.normpath(posixpath.join(posixpath.dirname(clean), final["path"]))
        if not raw.startswith(prefix + "/") or raw == clean:
            raise ScsError("REVIEW_RAW_SCOPE", "The separate final message must stay inside the attempt.")
        verify_native_final(store, raw, final["sha256"], receipt["verdict"])
    except ScsError as exc:
        return {"status": "INVALID", "error_code": exc.code, "message": str(exc)}
    return {"status": "VERIFIED", "review": {**receipt, "final_message": {**final, "path": raw}}}


def _safe_relative(value) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 512
            and not value.startswith("/") and "\\" not in value and "\x00" not in value
            and ".." not in PurePosixPath(value).parts)


def _valid_source(value) -> bool:
    if isinstance(value, str):
        return bool(re.fullmatch(r"[0-9a-f]{64}", value))
    if isinstance(value, dict):
        return (len(value) <= 100
                and all(_safe_relative(key) and isinstance(digest, str)
                        and re.fullmatch(r"[0-9a-f]{64}", digest) for key, digest in value.items()))
    return False


def _valid_artifacts(value) -> bool:
    if not isinstance(value, list) or len(value) > 200:
        return False
    return all(isinstance(item, dict) and set(item) == {"path", "sha256"}
               and _safe_relative(item["path"]) and isinstance(item["sha256"], str)
               and re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) for item in value)


def _journey_assertions(manifest: dict):
    raw = manifest.get("assertions")
    if raw is None and isinstance(manifest.get("journey"), dict):
        raw = manifest["journey"].get("assertions")
    if not isinstance(raw, list) or not raw:
        return None
    for item in raw:
        if (not isinstance(item, dict) or not set(item).issubset({"name", "ok", "observed"})
                or not isinstance(item.get("name"), str) or not item["name"].strip()
                or type(item.get("ok")) is not bool):
            return None
    return raw


def _verify_artifacts(store: Store, prefix: str, artifacts: list) -> bool:
    for item in artifacts:
        relative = posixpath.normpath(posixpath.join(prefix, item["path"]))
        if not relative.startswith(prefix + "/"):
            return False
        try:
            data = store.paths.read(relative)
        except ScsError:
            return False
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            return False
    return True


def verify_file(store: Store, path: str, prefix: str, kind: str,
                required_content: list[str] | None = None,
                subject_sha256: str | None = None, *, native_review: bool = False,
                host_agent_id: str | None = None,
                journey_scope: Callable[[str], str | None] | None = None) -> dict:
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
        if not native_review:
            checked = _review_check(store, text, clean, prefix, subject_sha256)
        else:
            checked = _native_review_check(store, text, clean, prefix, subject_sha256, host_agent_id)
            base["execution_provenance"] = "STRUCTURAL_RECEIPT_ONLY_HOST_NOT_AUTHENTICATED"
        if checked["status"] != "VERIFIED":
            return checked
        return {"status": "VERIFIED", **base, "review": checked["review"]}
    missing_content = required_content and any(fragment not in text for fragment in required_content)
    manifest = None
    if kind == "test" or text.lstrip().startswith("{"):
        try:
            manifest = json.loads(text)
        except (ValueError, RecursionError):
            if kind == "test" and not missing_content:
                return {"status": "INSUFFICIENT", "error_code": "TEST_MANIFEST_REQUIRED"}
    journey = isinstance(manifest, dict) and manifest.get("kind") == "journey"

    def invalid(code: str, status: str = "INVALID") -> dict:
        result = {"status": status, "error_code": code}
        if journey:
            result["kind"] = "journey"
        return result

    if journey:
        prefix = journey_scope(clean) if kind == "test" and journey_scope else None
        if prefix is None:
            return invalid("JOURNEY_ATTEMPT_REQUIRED")
        base["journey_attempt_id"] = prefix.rsplit("/", 1)[1]
    if missing_content:
        return invalid("REQUIRED_CONTENT_MISSING", "INSUFFICIENT")
    if kind == "test":
        if not isinstance(manifest, dict):
            return {"status": "INVALID", "error_code": "TEST_MANIFEST_INVALID"}
        declared = manifest.get("kind", "test")
        if declared not in ("test", "build", "lint", "smoke", "journey"):
            return {"status": "INVALID", "error_code": "EVIDENCE_KIND_INVALID"}
        if type(manifest.get("exit_code")) is not int:
            return invalid("TEST_EXIT_NONZERO_OR_UNKNOWN")
        if declared != "journey" and manifest["exit_code"] != 0:
            return {"status": "INVALID", "error_code": "TEST_EXIT_NONZERO_OR_UNKNOWN"}
        if not isinstance(manifest.get("command"), str) or not manifest["command"].strip():
            return invalid("TEST_RESULT_MISSING", "INSUFFICIENT")
        if declared == "test" and (type(manifest.get("passed")) is not int or manifest["passed"] <= 0):
            return {"status": "INSUFFICIENT", "error_code": "TEST_RESULT_MISSING"}
        if declared == "smoke":
            observation = manifest.get("observations") or manifest.get("journey")
            if not observation:
                return {"status": "INSUFFICIENT", "error_code": "SMOKE_OBSERVATION_MISSING"}
        artifacts = manifest.get("artifacts")
        if artifacts is not None and not _valid_artifacts(artifacts):
            return invalid("EVIDENCE_KIND_INVALID")
        if declared == "journey":
            if manifest.get("channel") not in ("adb", "serial", "browser"):
                return invalid("EVIDENCE_KIND_INVALID")
            assertions = _journey_assertions(manifest)
            if assertions is None:
                return invalid("JOURNEY_OBSERVATION_MISSING", "INSUFFICIENT")
            if not artifacts:
                return invalid("JOURNEY_ARTIFACT_REQUIRED", "INSUFFICIENT")
            if not _verify_artifacts(store, prefix, artifacts):
                return invalid("JOURNEY_ARTIFACT_INVALID")
            base["journey_passed"] = all(item.get("ok") is True for item in assertions)
            base["journey_exit_code"] = manifest["exit_code"]
            base["journey_assertions"] = assertions
        source = manifest.get("source_sha256")
        if source is not None and not _valid_source(source):
            return invalid("EVIDENCE_KIND_INVALID")
        log_path = manifest.get("log_path")
        if log_path is not None and not _safe_relative(log_path):
            return invalid("EVIDENCE_KIND_INVALID")
        base["kind"] = declared
        base["provenance"] = {
            "source_sha256": source, "log_path": log_path, "artifacts": artifacts}
    return {"status": "VERIFIED", **base}
