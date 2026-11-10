"""Bounded diagnostic facts; never guesses about invisible host behavior."""

from importlib.metadata import version

from . import __version__
from .interaction import supports_form
from .state import State


def check(store, capabilities, protocol_version, registered_tools, session_id=None):
    if session_id:
        State(store).get(session_id)
    with store.transaction() as con:
        integrity = con.execute("PRAGMA quick_check").fetchone()[0]
        schema = con.execute("PRAGMA user_version").fetchone()[0]
        events = [dict(r) for r in con.execute(
            "SELECT * FROM events WHERE session_id=? ORDER BY seq DESC LIMIT 30", (session_id,))] if session_id else []
    return {"status": "OK" if integrity == "ok" else "ERROR", "database": integrity,
            "schema_version": schema, "sdk_version": version("mcp"), "server_version": __version__,
            "registered_tools": registered_tools, "protocol_version": protocol_version,
            "connected": True, "form_capability_declared": supports_form(capabilities),
            "ui_observation": "UI_NOT_OBSERVABLE", "events": events,
            "skill_loaded": "UNKNOWN_REQUIRES_HOST_EVIDENCE", "tool_not_called": "UNKNOWN_REQUIRES_HOST_LOG",
            "state_location": ".scs/state.sqlite3", "log_location": "stderr (diagnostic events in SQLite)",
            "markdown_sync": "MAIN_AGENT_OWNED; explicit reconciliation/import required after pure Skill use."}
