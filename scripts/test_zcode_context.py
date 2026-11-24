#!/usr/bin/env python3
"""Unit and CLI tests for single-conversation-subagent/scripts/zcode-context.py."""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "single-conversation-subagent" / "scripts" / "zcode-context.py"
PARENT_ID = "sess_parent_01"
AGENT_ID = "agent_11111111-2222-3333-4444-555555555555"
CHILD_ID = "sess_subagent_" + AGENT_ID
MODEL = "deepseek/deepseek-chat"


def _load_module():
    spec = importlib.util.spec_from_file_location("zcode_context", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ZCODE_CONTEXT = _load_module()


def _create_schema(db, *, include_cache_creation=True, nullable_tokens=True):
    """Create ZCode session/model_usage/turn_usage tables and real ZCode indexes."""
    token_constraint = "" if nullable_tokens else " NOT NULL DEFAULT 0"
    cache_creation_col = (
        f"cache_creation_input_tokens INTEGER{token_constraint},"
        if include_cache_creation
        else ""
    )
    db.executescript(
        f"""
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            parent_id TEXT,
            task_type TEXT NOT NULL DEFAULT 'interactive',
            last_outcome TEXT,
            time_compacting INTEGER
        );
        CREATE INDEX session_parent_idx ON session(parent_id);
        CREATE INDEX session_task_type_idx ON session(task_type);

        CREATE TABLE model_usage (
            id TEXT PRIMARY KEY,
            attempt_index INTEGER NOT NULL DEFAULT 0,
            session_id TEXT NOT NULL,
            turn_id TEXT,
            trace_id TEXT,
            query_source TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at INTEGER NOT NULL,
            completed_at INTEGER,
            input_tokens INTEGER{token_constraint},
            output_tokens INTEGER{token_constraint},
            {cache_creation_col}
            cache_read_input_tokens INTEGER{token_constraint},
            context_exceeded INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX model_usage_query_source_idx ON model_usage(query_source);
        CREATE INDEX model_usage_session_turn_idx ON model_usage(session_id, turn_id);
        CREATE INDEX model_usage_started_model_idx ON model_usage(started_at, provider_id, model_id);
        CREATE INDEX model_usage_trace_idx ON model_usage(trace_id);

        CREATE TABLE turn_usage (
            session_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at INTEGER NOT NULL,
            context_exceeded INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (session_id, turn_id)
        );
        CREATE INDEX turn_usage_started_idx ON turn_usage(started_at);
        """
    )


def _insert_session(
    db,
    *,
    session_id=CHILD_ID,
    parent_id=PARENT_ID,
    task_type="subagent_child",
    last_outcome="completed",
    time_compacting=None,
):
    db.execute(
        "INSERT INTO session (id, parent_id, task_type, last_outcome, time_compacting) VALUES (?, ?, ?, ?, ?)",
        (session_id, parent_id, task_type, last_outcome, time_compacting),
    )


def _insert_turn(
    db,
    *,
    session_id=CHILD_ID,
    turn_id="turn_1",
    status="completed",
    started_at=1000,
    context_exceeded=0,
):
    db.execute(
        "INSERT INTO turn_usage (session_id, turn_id, status, started_at, context_exceeded) VALUES (?, ?, ?, ?, ?)",
        (session_id, turn_id, status, started_at, context_exceeded),
    )


def _insert_usage(
    db,
    *,
    request_id="req_1",
    attempt_index=0,
    session_id=CHILD_ID,
    turn_id="turn_1",
    query_source="subagent",
    provider_id="deepseek",
    model_id="deepseek-chat",
    status="completed",
    started_at=1000,
    completed_at=1500,
    input_tokens=40000,
    output_tokens=2000,
    cache_read_input_tokens=10000,
    cache_creation_input_tokens=0,
    context_exceeded=0,
    include_cache_creation=True,
):
    if include_cache_creation:
        db.execute(
            """
            INSERT INTO model_usage (
                id, attempt_index, session_id, turn_id, query_source,
                provider_id, model_id, status, started_at, completed_at,
                input_tokens, output_tokens, cache_creation_input_tokens,
                cache_read_input_tokens, context_exceeded
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                attempt_index,
                session_id,
                turn_id,
                query_source,
                provider_id,
                model_id,
                status,
                started_at,
                completed_at,
                input_tokens,
                output_tokens,
                cache_creation_input_tokens,
                cache_read_input_tokens,
                context_exceeded,
            ),
        )
    else:
        db.execute(
            """
            INSERT INTO model_usage (
                id, attempt_index, session_id, turn_id, query_source,
                provider_id, model_id, status, started_at, completed_at,
                input_tokens, output_tokens, cache_read_input_tokens,
                context_exceeded
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                attempt_index,
                session_id,
                turn_id,
                query_source,
                provider_id,
                model_id,
                status,
                started_at,
                completed_at,
                input_tokens,
                output_tokens,
                cache_read_input_tokens,
                context_exceeded,
            ),
        )


def _seed_valid_db(
    db,
    *,
    input_tokens=40000,
    output_tokens=2000,
    cache_read_input_tokens=10000,
    cache_creation_input_tokens=0,
    include_cache_creation=True,
    nullable_tokens=True,
    last_outcome="completed",
    time_compacting=None,
    usage_status="completed",
    turn_status="completed",
    usage_exceeded=0,
    turn_exceeded=0,
    completed_at=1500,
):
    _create_schema(
        db,
        include_cache_creation=include_cache_creation,
        nullable_tokens=nullable_tokens,
    )
    _insert_session(db, last_outcome=last_outcome, time_compacting=time_compacting)
    _insert_turn(db, status=turn_status, context_exceeded=turn_exceeded)
    _insert_usage(
        db,
        status=usage_status,
        completed_at=completed_at,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        context_exceeded=usage_exceeded,
        include_cache_creation=include_cache_creation,
    )
    db.commit()


class _RecordingConnection:
    """Thin sqlite3 proxy capturing actual SQL executed by budget()."""

    def __init__(self, conn):
        self._conn = conn
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        return self._conn.execute(sql, params)


class _StubCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _BoolTokenDB:
    """In-memory stub DB to test Python bool token rejection directly."""

    def __init__(self, usage_row):
        self._usage_row = usage_row

    def execute(self, sql, params=()):
        if sql.startswith("SELECT last_outcome, time_compacting"):
            return _StubCursor([("completed", None)])
        if sql.startswith("PRAGMA table_info(model_usage)"):
            return _StubCursor([(0, "cache_creation_input_tokens", "INTEGER", 0, None, 0)])
        if "+query_source='subagent'" in sql:
            return _StubCursor([self._usage_row])
        if sql.startswith("SELECT turn_id, status, context_exceeded FROM turn_usage"):
            return _StubCursor([("turn_1", "completed", 0)])
        if "+query_source='compact'" in sql:
            return _StubCursor([])
        raise AssertionError(f"Unexpected SQL: {sql}")


class BudgetUnitTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")

    def tearDown(self):
        self.db.close()

    def test_reuse_below_threshold(self):
        _seed_valid_db(self.db, input_tokens=40000, output_tokens=2000, cache_read_input_tokens=15000)
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(
            res,
            {
                "decision": "REUSE",
                "request_id": "req_1",
                "turn_id": "turn_1",
                "model": MODEL,
                "input_tokens": 40000,
                "output_tokens": 2000,
                "reserve_tokens": 4096,
                "context_window": 100000,
                "projected_percent": 46.1,
            },
        )

    def test_exact_70_percent_remains_reuse(self):
        # 65000 + 904 + 4096 = 70000 == 70.0% of 100000
        _seed_valid_db(self.db, input_tokens=65000, output_tokens=904, cache_read_input_tokens=65000)
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(res["decision"], "REUSE")
        self.assertEqual(res["projected_percent"], 70.0)

    def test_above_70_percent_triggers_rotate_before_rounding(self):
        # 65000 + 905 + 4096 = 70001 -> 70.001% rounds to 70.0, still must ROTATE.
        _seed_valid_db(self.db, input_tokens=65000, output_tokens=905, cache_read_input_tokens=0)
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(res["decision"], "ROTATE")
        self.assertEqual(res["projected_percent"], 70.0)

    def test_clearly_above_70_percent_triggers_rotate(self):
        # 70000 + 5000 + 4096 = 79096 -> 79.1%
        _seed_valid_db(self.db, input_tokens=70000, output_tokens=5000, cache_read_input_tokens=5000)
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(res["decision"], "ROTATE")
        self.assertEqual(res["projected_percent"], 79.1)

    def test_uses_latest_subagent_request_not_sum_of_turn(self):
        _create_schema(self.db)
        _insert_session(self.db)
        _insert_turn(self.db, turn_id="turn_1")
        # Earlier request in the same turn with huge tokens + newer retry/step with smaller tokens.
        _insert_usage(
            self.db,
            request_id="req_1",
            attempt_index=0,
            turn_id="turn_1",
            started_at=1000,
            input_tokens=60000,
            output_tokens=1000,
        )
        _insert_usage(
            self.db,
            request_id="req_2",
            attempt_index=1,
            turn_id="turn_1",
            started_at=1000,
            input_tokens=20000,
            output_tokens=500,
        )
        # Non-subagent usage row at a later timestamp must be ignored.
        _insert_usage(
            self.db,
            request_id="req_title",
            query_source="title",
            turn_id="turn_1",
            started_at=2000,
            input_tokens=95000,
            output_tokens=100,
        )
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(res["decision"], "REUSE")
        self.assertEqual(res["request_id"], "req_2")
        self.assertEqual(res["input_tokens"], 20000)
        self.assertEqual(res["output_tokens"], 500)

    def test_context_exceeded_triggers_rotate_with_turn_error(self):
        _seed_valid_db(
            self.db,
            last_outcome="error",
            usage_status="error",
            turn_status="error",
            usage_exceeded=1,
            turn_exceeded=0,
            completed_at=None,
            input_tokens=0,
            output_tokens=0,
            cache_read_input_tokens=0,
        )
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(
            res,
            {
                "decision": "ROTATE",
                "reason": "CONTEXT_EXCEEDED",
                "request_id": "req_1",
                "turn_id": "turn_1",
                "model": MODEL,
            },
        )

    def test_turn_usage_context_exceeded_also_triggers_rotate(self):
        _seed_valid_db(
            self.db,
            last_outcome="error",
            usage_status="error",
            turn_status="error",
            usage_exceeded=0,
            turn_exceeded=1,
            completed_at=None,
            input_tokens=None,
            output_tokens=None,
            cache_read_input_tokens=None,
        )
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(res["decision"], "ROTATE")
        self.assertEqual(res["reason"], "CONTEXT_EXCEEDED")
        self.assertEqual(res["request_id"], "req_1")
        self.assertEqual(res["turn_id"], "turn_1")

    def test_context_window_unverified(self):
        _seed_valid_db(self.db)
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, None, 4096)
        self.assertEqual(res, {"decision": "UNKNOWN", "reason": "CONTEXT_WINDOW_UNVERIFIED"})

    def test_child_not_verified_missing_or_wrong_parent_or_task_type(self):
        _create_schema(self.db)
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(res, {"decision": "UNKNOWN", "reason": "CHILD_NOT_VERIFIED"})

        _insert_session(self.db, parent_id="sess_other_parent")
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(res, {"decision": "UNKNOWN", "reason": "CHILD_NOT_VERIFIED"})

        self.db.execute("UPDATE session SET parent_id=?, task_type='interactive' WHERE id=?", (PARENT_ID, CHILD_ID))
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(res, {"decision": "UNKNOWN", "reason": "CHILD_NOT_VERIFIED"})

    def test_usage_unavailable(self):
        _create_schema(self.db)
        _insert_session(self.db)
        _insert_turn(self.db)
        _insert_usage(self.db, request_id="req_compact_only", query_source="compact")
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(res, {"decision": "UNKNOWN", "reason": "USAGE_UNAVAILABLE"})

    def test_model_mismatch(self):
        _seed_valid_db(self.db)
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, "anthropic/claude-3-7-sonnet", 100000, 4096)
        self.assertEqual(res, {"decision": "UNKNOWN", "reason": "MODEL_MISMATCH"})

    def test_turn_not_verified_variants(self):
        # Missing turn_usage row
        _create_schema(self.db)
        _insert_session(self.db)
        _insert_usage(self.db, turn_id="turn_1")
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(res["decision"], "UNKNOWN")
        self.assertEqual(res["reason"], "TURN_NOT_VERIFIED")
        self.assertEqual(res["request_id"], "req_1")
        self.assertEqual(res["turn_id"], "turn_1")

        # Latest turn_usage is a different (newer) turn
        _insert_turn(self.db, turn_id="turn_1", started_at=1000)
        _insert_turn(self.db, turn_id="turn_2", started_at=2000)
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(res["reason"], "TURN_NOT_VERIFIED")

        # Turn status is running (not completed/error)
        self.db.execute("DELETE FROM turn_usage WHERE turn_id='turn_2'")
        self.db.execute("UPDATE turn_usage SET status='running' WHERE turn_id='turn_1'")
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(res["reason"], "TURN_NOT_VERIFIED")

        # Usage turn_id is NULL
        self.db.execute("UPDATE turn_usage SET status='completed' WHERE turn_id='turn_1'")
        self.db.execute("UPDATE model_usage SET turn_id=NULL WHERE id='req_1'")
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(res["reason"], "TURN_NOT_VERIFIED")

    def test_compaction_state_present_via_time_compacting(self):
        _seed_valid_db(self.db, time_compacting=1700000000000)
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(
            res,
            {
                "decision": "UNKNOWN",
                "reason": "COMPACTION_STATE_PRESENT",
                "request_id": "req_1",
                "turn_id": "turn_1",
                "model": MODEL,
            },
        )

    def test_compaction_state_present_via_compact_model_usage_row(self):
        _seed_valid_db(self.db, time_compacting=None)
        # Historical completed compaction row on the same child session.
        _insert_usage(
            self.db,
            request_id="req_compact_0",
            session_id=CHILD_ID,
            turn_id="turn_0",
            query_source="compact",
            started_at=500,
            completed_at=600,
            input_tokens=50000,
            output_tokens=1500,
        )
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(
            res,
            {
                "decision": "UNKNOWN",
                "reason": "COMPACTION_STATE_PRESENT",
                "request_id": "req_1",
                "turn_id": "turn_1",
                "model": MODEL,
            },
        )

    def test_compact_row_on_other_session_does_not_affect_child(self):
        _seed_valid_db(self.db, time_compacting=None)
        _insert_session(self.db, session_id=PARENT_ID, parent_id=None, task_type="interactive")
        _insert_usage(
            self.db,
            request_id="req_parent_compact",
            session_id=PARENT_ID,
            turn_id="turn_p",
            query_source="compact",
            started_at=500,
            completed_at=600,
        )
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(res["decision"], "REUSE")

    def test_usage_not_usable_null_tokens_no_typeerror(self):
        for field in ("input_tokens", "output_tokens", "cache_read_input_tokens"):
            with self.subTest(null_field=field):
                db = sqlite3.connect(":memory:")
                try:
                    kwargs = {
                        "input_tokens": 10000,
                        "output_tokens": 500,
                        "cache_read_input_tokens": 1000,
                    }
                    kwargs[field] = None
                    _seed_valid_db(db, **kwargs)
                    res = ZCODE_CONTEXT.budget(db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
                    self.assertEqual(res["decision"], "UNKNOWN")
                    self.assertEqual(res["reason"], "USAGE_NOT_USABLE")
                    self.assertEqual(res["request_id"], "req_1")
                finally:
                    db.close()

    def test_usage_not_usable_non_int_and_bool_tokens(self):
        # Dynamic SQLite text/float values on token columns (non-numeric strings stay TEXT under INTEGER affinity)
        for col, bad_val in (
            ("input_tokens", "not-an-int"),
            ("output_tokens", 12.5),
            ("cache_read_input_tokens", "not-an-int"),
            ("cache_creation_input_tokens", "not-an-int"),
            ("cache_creation_input_tokens", 1.5),
        ):
            with self.subTest(col=col, bad_val=bad_val):
                db = sqlite3.connect(":memory:")
                try:
                    _seed_valid_db(db)
                    db.execute(f"UPDATE model_usage SET {col}=? WHERE id='req_1'", (bad_val,))
                    res = ZCODE_CONTEXT.budget(db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
                    self.assertEqual(res["decision"], "UNKNOWN")
                    self.assertEqual(res["reason"], "USAGE_NOT_USABLE")
                finally:
                    db.close()

        # Python bool values (isinstance(True, int) is True, must still be rejected)
        base = ("req_1", "turn_1", "deepseek", "deepseek-chat", "completed", 1000, 100, 0, 1500, 0, 0)
        for idx, name in ((5, "inp"), (6, "out"), (9, "cached"), (10, "cache_creation")):
            for bval in (True, False):
                with self.subTest(field=name, bool_val=bval):
                    row = list(base)
                    row[idx] = bval
                    res = ZCODE_CONTEXT.budget(_BoolTokenDB(tuple(row)), PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
                    self.assertEqual(res["decision"], "UNKNOWN")
                    self.assertEqual(res["reason"], "USAGE_NOT_USABLE")

    def test_usage_not_usable_cached_exceeds_input_and_negative_bounds(self):
        cases = [
            {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 1001},
            {"input_tokens": 0, "output_tokens": 100, "cache_read_input_tokens": 0},
            {"input_tokens": -10, "output_tokens": 100, "cache_read_input_tokens": 0},
            {"input_tokens": 1000, "output_tokens": -1, "cache_read_input_tokens": 0},
            {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": -1},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                db = sqlite3.connect(":memory:")
                try:
                    _seed_valid_db(db, **kwargs)
                    res = ZCODE_CONTEXT.budget(db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
                    self.assertEqual(res["decision"], "UNKNOWN")
                    self.assertEqual(res["reason"], "USAGE_NOT_USABLE")
                finally:
                    db.close()

    def test_usage_not_usable_cache_creation_input_tokens(self):
        # > 0 fails closed
        _seed_valid_db(self.db, cache_creation_input_tokens=128)
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(res["decision"], "UNKNOWN")
        self.assertEqual(res["reason"], "USAGE_NOT_USABLE")

        # < 0 fails closed
        self.db.execute("UPDATE model_usage SET cache_creation_input_tokens=-1 WHERE id='req_1'")
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(res["decision"], "UNKNOWN")
        self.assertEqual(res["reason"], "USAGE_NOT_USABLE")

        # NULL is treated as 0 (allowed)
        self.db.execute("UPDATE model_usage SET cache_creation_input_tokens=NULL WHERE id='req_1'")
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(res["decision"], "REUSE")

    def test_legacy_schema_without_cache_creation_column(self):
        _seed_valid_db(self.db, include_cache_creation=False)
        res = ZCODE_CONTEXT.budget(self.db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(res["decision"], "REUSE")
        self.assertEqual(res["input_tokens"], 40000)

    def test_usage_not_usable_status_and_outcome_checks(self):
        cases = [
            {"last_outcome": "error"},
            {"last_outcome": None},
            {"turn_status": "error"},
            {"usage_status": "running"},
            {"completed_at": None},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                db = sqlite3.connect(":memory:")
                try:
                    _seed_valid_db(db, **kwargs)
                    res = ZCODE_CONTEXT.budget(db, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
                    self.assertEqual(res["decision"], "UNKNOWN")
                    self.assertEqual(res["reason"], "USAGE_NOT_USABLE")
                finally:
                    db.close()

    def test_explain_query_plan_uses_session_turn_idx_not_query_source_idx(self):
        """Verify actual model_usage queries executed by budget() hit session_id index."""
        _seed_valid_db(self.db, nullable_tokens=False)
        recorder = _RecordingConnection(self.db)
        res = ZCODE_CONTEXT.budget(recorder, PARENT_ID, AGENT_ID, MODEL, 100000, 4096)
        self.assertEqual(res["decision"], "REUSE")

        model_usage_queries = [
            (sql, params)
            for sql, params in recorder.executed
            if "FROM model_usage" in sql
        ]
        # Expect both latest subagent usage query and compact detection query.
        self.assertEqual(len(model_usage_queries), 2, model_usage_queries)

        for sql, params in model_usage_queries:
            plan_rows = self.db.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
            plan_text = "\n".join(str(row[3]) for row in plan_rows)
            self.assertIn("model_usage_session_turn_idx", plan_text, msg=f"SQL: {sql}\nPlan:\n{plan_text}")
            self.assertIn("session_id=?", plan_text, msg=f"SQL: {sql}\nPlan:\n{plan_text}")
            self.assertNotIn(
                "model_usage_query_source_idx",
                plan_text,
                msg=f"SQL mistakenly chose query_source index: {sql}\nPlan:\n{plan_text}",
            )

        # Control check: without unary '+', SQLite chooses model_usage_query_source_idx on this schema.
        unfixed_sql = (
            "SELECT id FROM model_usage WHERE session_id=? AND query_source='subagent' "
            "ORDER BY started_at DESC, attempt_index DESC, id DESC LIMIT 1"
        )
        control_plan = "\n".join(
            str(row[3]) for row in self.db.execute("EXPLAIN QUERY PLAN " + unfixed_sql, (CHILD_ID,)).fetchall()
        )
        self.assertIn("model_usage_query_source_idx", control_plan)


class CliTests(unittest.TestCase):
    def _run_cli(self, extra_args, *, env_updates=None, cwd=None):
        env = {k: v for k, v in os.environ.items() if k != "SCS_ZCODE_DB"}
        if env_updates:
            for k, v in env_updates.items():
                if v is None:
                    env.pop(k, None)
                else:
                    env[k] = v
        cmd = [
            sys.executable,
            "-I",
            str(SCRIPT_PATH),
            "--parent",
            PARENT_ID,
            "--agent",
            AGENT_ID,
            "--model",
            MODEL,
            *extra_args,
        ]
        return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd, check=False)

    def _make_db_file(self, path, **seed_kwargs):
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        try:
            _seed_valid_db(conn, **seed_kwargs)
        finally:
            conn.close()

    def test_cli_db_flag_and_relative_path(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "probe.sqlite"
            self._make_db_file(db_path, input_tokens=25000, output_tokens=1000)

            proc = self._run_cli(["--window", "100000", "--db", "probe.sqlite"], cwd=td)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stderr, "")
            # Single-line compact JSON output format unchanged.
            lines = proc.stdout.splitlines()
            self.assertEqual(len(lines), 1)
            self.assertNotIn(": ", lines[0])
            data = json.loads(lines[0])
            self.assertEqual(data["decision"], "REUSE")
            self.assertEqual(data["input_tokens"], 25000)

            # Omitting --window with a valid DB returns CONTEXT_WINDOW_UNVERIFIED.
            proc_no_win = self._run_cli(["--db", str(db_path)])
            self.assertEqual(proc_no_win.returncode, 0, proc_no_win.stderr)
            self.assertEqual(
                json.loads(proc_no_win.stdout),
                {"decision": "UNKNOWN", "reason": "CONTEXT_WINDOW_UNVERIFIED"},
            )

            # Path containing spaces, '?' and '#' must still open cleanly via file URI + ?mode=ro.
            special_db = Path(td) / "dir with space" / "db?v=1#frag.sqlite"
            self._make_db_file(special_db, input_tokens=18000, output_tokens=200)
            proc_special = self._run_cli(["--window", "100000", "--db", str(special_db)])
            self.assertEqual(proc_special.returncode, 0, proc_special.stderr)
            self.assertEqual(json.loads(proc_special.stdout)["input_tokens"], 18000)

    def test_cli_env_override_and_priority_over_default(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home_dir = base / "fake_home"
            default_db = home_dir / ".zcode/cli/db/db.sqlite"
            env_db = base / "env.sqlite"
            cli_db = base / "cli.sqlite"

            # Three distinct token counts prove which DB was opened.
            self._make_db_file(default_db, input_tokens=11000, output_tokens=100)
            self._make_db_file(env_db, input_tokens=22000, output_tokens=100)
            self._make_db_file(cli_db, input_tokens=33000, output_tokens=100)

            # 1. Default path ($HOME/.zcode/cli/db/db.sqlite) when neither --db nor SCS_ZCODE_DB is set.
            p_def = self._run_cli(["--window", "100000"], env_updates={"HOME": str(home_dir)})
            self.assertEqual(p_def.returncode, 0, p_def.stderr)
            self.assertEqual(json.loads(p_def.stdout)["input_tokens"], 11000)

            # 2. Empty SCS_ZCODE_DB falls back to default path.
            p_empty_env = self._run_cli(
                ["--window", "100000"],
                env_updates={"HOME": str(home_dir), "SCS_ZCODE_DB": ""},
            )
            self.assertEqual(p_empty_env.returncode, 0, p_empty_env.stderr)
            self.assertEqual(json.loads(p_empty_env.stdout)["input_tokens"], 11000)

            # 3. SCS_ZCODE_DB overrides default path (including under python -I).
            p_env = self._run_cli(
                ["--window", "100000"],
                env_updates={"HOME": str(home_dir), "SCS_ZCODE_DB": str(env_db)},
            )
            self.assertEqual(p_env.returncode, 0, p_env.stderr)
            self.assertEqual(json.loads(p_env.stdout)["input_tokens"], 22000)

            # 4. --db overrides both SCS_ZCODE_DB and default path.
            p_cli = self._run_cli(
                ["--window", "100000", "--db", str(cli_db)],
                env_updates={"HOME": str(home_dir), "SCS_ZCODE_DB": str(env_db)},
            )
            self.assertEqual(p_cli.returncode, 0, p_cli.stderr)
            self.assertEqual(json.loads(p_cli.stdout)["input_tokens"], 33000)

    def test_host_usage_unavailable_and_mode_ro_does_not_create_file(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            missing = base / "does_not_exist.sqlite"
            valid_env = base / "valid_env.sqlite"
            self._make_db_file(valid_env)

            # Missing --db must fail closed without falling back to SCS_ZCODE_DB and without creating file.
            proc = self._run_cli(
                ["--window", "100000", "--db", str(missing)],
                env_updates={"SCS_ZCODE_DB": str(valid_env)},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout), {"decision": "UNKNOWN", "reason": "HOST_USAGE_UNAVAILABLE"})
            self.assertFalse(missing.exists(), "mode=ro must never create a nonexistent database file")

            # Corrupt (non-SQLite) file returns HOST_USAGE_UNAVAILABLE.
            corrupt = base / "corrupt.sqlite"
            corrupt.write_text("not a sqlite file", encoding="utf-8")
            proc_corrupt = self._run_cli(["--window", "100000", "--db", str(corrupt)])
            self.assertEqual(proc_corrupt.returncode, 0, proc_corrupt.stderr)
            self.assertEqual(
                json.loads(proc_corrupt.stdout),
                {"decision": "UNKNOWN", "reason": "HOST_USAGE_UNAVAILABLE"},
            )

            # Empty SQLite DB missing tables returns HOST_USAGE_UNAVAILABLE.
            empty_db = base / "empty.sqlite"
            sqlite3.connect(empty_db).close()
            proc_empty = self._run_cli(["--window", "100000", "--db", str(empty_db)])
            self.assertEqual(proc_empty.returncode, 0, proc_empty.stderr)
            self.assertEqual(
                json.loads(proc_empty.stdout),
                {"decision": "UNKNOWN", "reason": "HOST_USAGE_UNAVAILABLE"},
            )

    def test_unresolvable_tilde_user_returns_host_usage_unavailable(self):
        # Path.expanduser() raises RuntimeError for an unknown ~user; must return JSON without traceback.
        bad_tilde = "~scs_nonexistent_user_9f8e7d6c/db.sqlite"
        proc_cli = self._run_cli(["--window", "100000", "--db", bad_tilde])
        self.assertEqual(proc_cli.returncode, 0, proc_cli.stderr)
        self.assertEqual(proc_cli.stderr, "")
        self.assertEqual(
            json.loads(proc_cli.stdout),
            {"decision": "UNKNOWN", "reason": "HOST_USAGE_UNAVAILABLE"},
        )

        proc_env = self._run_cli(["--window", "100000"], env_updates={"SCS_ZCODE_DB": bad_tilde})
        self.assertEqual(proc_env.returncode, 0, proc_env.stderr)
        self.assertEqual(proc_env.stderr, "")
        self.assertEqual(
            json.loads(proc_env.stdout),
            {"decision": "UNKNOWN", "reason": "HOST_USAGE_UNAVAILABLE"},
        )

    def test_cli_strict_argument_validation(self):
        invalid_cases = [
            (["--parent", "bad_parent", "--agent", AGENT_ID, "--model", MODEL, "--window", "100000"], "parent prefix"),
            (
                ["--parent", "sess_" + "a" * 200, "--agent", AGENT_ID, "--model", MODEL, "--window", "100000"],
                "parent too long",
            ),
            (["--parent", PARENT_ID, "--agent", "agent_not-a-uuid", "--model", MODEL, "--window", "100000"], "agent id"),
            (["--parent", PARENT_ID, "--agent", AGENT_ID, "--model", MODEL, "--window", "0"], "window zero"),
            (["--parent", PARENT_ID, "--agent", AGENT_ID, "--model", MODEL, "--window", "-1"], "window negative"),
            (
                ["--parent", PARENT_ID, "--agent", AGENT_ID, "--model", MODEL, "--window", str(10**9 + 1)],
                "window too large",
            ),
            (
                ["--parent", PARENT_ID, "--agent", AGENT_ID, "--model", MODEL, "--window", "100000", "--reserve", "-1"],
                "reserve negative",
            ),
            (
                [
                    "--parent",
                    PARENT_ID,
                    "--agent",
                    AGENT_ID,
                    "--model",
                    MODEL,
                    "--window",
                    "100000",
                    "--reserve",
                    str(10**9 + 1),
                ],
                "reserve too large",
            ),
            (["--parent", PARENT_ID, "--agent", AGENT_ID, "--model", MODEL, "--db", ""], "empty db"),
        ]
        for argv, label in invalid_cases:
            with self.subTest(label=label):
                proc = subprocess.run(
                    [sys.executable, "-I", str(SCRIPT_PATH), *argv],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(proc.returncode, 0, f"Expected argument error for {label}")


if __name__ == "__main__":
    unittest.main()
