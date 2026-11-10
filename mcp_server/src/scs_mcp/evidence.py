"""Verify bounded evidence files; never infer final acceptance or run tests."""

import hashlib
import json

from .errors import ScsError
from .storage import Store


def verify_file(store: Store, path: str, prefix: str, kind: str,
                required_content: list[str] | None = None) -> dict:
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
    return {"status": "VERIFIED", "path": clean, "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "required_content": required_content or [],
            "scope": "FILE_AND_CONTENT_ONLY", "final_acceptance": False,
            "execution_provenance": "MAIN_AGENT_MUST_REVIEW_ORIGINAL_LOGS"}
