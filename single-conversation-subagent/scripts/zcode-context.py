#!/usr/bin/env python3
"""Read a bounded context estimate for one native ZCode child; never dispatch."""

import argparse
import json
import os
import re
import sqlite3
from pathlib import Path


AGENT_ID = re.compile(r"agent_[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")


def unknown(reason, **details):
    return {"decision": "UNKNOWN", "reason": reason, **details}


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def resolve_db_path(db_arg=None):
    raw = db_arg if db_arg is not None else (os.environ.get("SCS_ZCODE_DB") or None)
    path = Path(raw).expanduser() if raw is not None else Path.home() / ".zcode/cli/db/db.sqlite"
    return Path(os.path.abspath(path))


def budget(db, parent, agent, expected_model, window, reserve):
    """Use the latest subagent request, not the sum of all requests in a turn."""
    if window is None:
        return unknown("CONTEXT_WINDOW_UNVERIFIED")
    child = "sess_subagent_" + agent
    session = db.execute("SELECT last_outcome, time_compacting FROM session WHERE id=? AND parent_id=? AND task_type='subagent_child'", (child, parent)).fetchone()
    if not session:
        return unknown("CHILD_NOT_VERIFIED")
    columns = {row[1].lower() for row in db.execute("PRAGMA table_info(model_usage)")}
    if "cache_creation_input_tokens" in columns:
        usage = db.execute("SELECT id, turn_id, provider_id, model_id, status, input_tokens, output_tokens, context_exceeded, completed_at, cache_read_input_tokens, cache_creation_input_tokens FROM model_usage WHERE session_id=? AND +query_source='subagent' ORDER BY started_at DESC, attempt_index DESC, id DESC LIMIT 1", (child,)).fetchone()
    else:
        usage = db.execute("SELECT id, turn_id, provider_id, model_id, status, input_tokens, output_tokens, context_exceeded, completed_at, cache_read_input_tokens, 0 FROM model_usage WHERE session_id=? AND +query_source='subagent' ORDER BY started_at DESC, attempt_index DESC, id DESC LIMIT 1", (child,)).fetchone()
    if not usage:
        return unknown("USAGE_UNAVAILABLE")
    request, turn, provider, model, status, inp, out, exceeded, completed_at, cached, cache_creation = usage
    if f"{provider}/{model}" != expected_model:
        return unknown("MODEL_MISMATCH")
    identity = {"request_id": request, "turn_id": turn, "model": expected_model}
    latest = db.execute("SELECT turn_id, status, context_exceeded FROM turn_usage WHERE session_id=? ORDER BY started_at DESC, turn_id DESC LIMIT 1", (child,)).fetchone()
    if not turn or not latest or latest[0] != turn or latest[1] not in {"completed", "error"}:
        return unknown("TURN_NOT_VERIFIED", **identity)
    if exceeded or latest[2]:
        return {"decision": "ROTATE", "reason": "CONTEXT_EXCEEDED", **identity}
    if session[1] is not None or db.execute(
        "SELECT 1 FROM model_usage WHERE session_id=? AND +query_source='compact' LIMIT 1",
        (child,),
    ).fetchone() is not None:
        return unknown("COMPACTION_STATE_PRESENT", **identity)
    cache_creation_value = 0 if cache_creation is None else cache_creation
    if (session[0] != "completed" or latest[1] != "completed" or status != "completed"
            or not completed_at
            or not (is_int(inp) and is_int(out) and is_int(cached) and is_int(cache_creation_value))
            or inp <= 0 or out < 0 or not 0 <= cached <= inp or cache_creation_value != 0):
        return unknown("USAGE_NOT_USABLE", **identity)
    projected = inp + out + reserve
    # Compare before rounding; exactly 70% remains eligible.
    return {
        "decision": "ROTATE" if 10 * projected > 7 * window else "REUSE",
        **identity,
        "input_tokens": inp,
        "output_tokens": out,
        "reserve_tokens": reserve,
        "context_window": window,
        "projected_percent": round(100 * projected / window, 2),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", required=True, help="observed ZCode parent session ID")
    parser.add_argument("--agent", required=True, help="original Agent tool ID")
    parser.add_argument("--model", required=True, help="observed provider/model for the supplied window")
    parser.add_argument("--window", type=int, help="verified effective window for this exact provider/model")
    parser.add_argument("--reserve", type=int, default=4096, help="budget for the next bounded turn")
    parser.add_argument("--db", help="override ZCode SQLite database path (default: $SCS_ZCODE_DB or ~/.zcode/cli/db/db.sqlite)")
    args = parser.parse_args()
    if not AGENT_ID.fullmatch(args.agent) or not args.parent.startswith("sess_") or len(args.parent) > 200:
        parser.error("invalid parent or agent ID")
    if (args.window is not None and not 0 < args.window <= 10**9) or not 0 <= args.reserve <= 10**9:
        parser.error("window must be positive and reserve must be nonnegative (maximum: 1000000000)")
    if args.db is not None and not args.db.strip():
        parser.error("db path must not be empty")
    try:
        uri = resolve_db_path(args.db).as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=0.5)
        try:
            with db:
                db.execute("BEGIN")
                result = budget(db, args.parent, args.agent, args.model, args.window, args.reserve)
        finally:
            db.close()
    except (sqlite3.Error, OSError, RuntimeError, ValueError):
        result = unknown("HOST_USAGE_UNAVAILABLE")
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=True))


if __name__ == "__main__":
    main()
