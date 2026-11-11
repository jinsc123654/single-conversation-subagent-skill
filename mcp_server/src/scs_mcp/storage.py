"""SQLite schema v2 (migrates v1 without losing records). One connection per transaction; all writes serialize."""

import json
import os
import sqlite3
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .errors import ScsError
from .paths import Paths


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
 id TEXT PRIMARY KEY, identity TEXT UNIQUE NOT NULL, client TEXT NOT NULL,
 temporary INTEGER NOT NULL, review_registration TEXT NOT NULL,
 mode TEXT, verification TEXT, codex_review INTEGER, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS questions(
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 kind TEXT NOT NULL, current INTEGER NOT NULL DEFAULT 1, state TEXT NOT NULL,
 channel TEXT NOT NULL, recommendation TEXT NOT NULL, answer TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS current_question ON questions(session_id, kind) WHERE current=1;
CREATE TABLE IF NOT EXISTS events(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
 session_id TEXT, question_id TEXT, client TEXT, tool TEXT NOT NULL,
 event TEXT NOT NULL, error_code TEXT, fallback_reason TEXT
);
CREATE TABLE IF NOT EXISTS work_packages(
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 revision INTEGER NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
 data TEXT NOT NULL, docs_root TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work_package_revisions(
 package_id TEXT NOT NULL REFERENCES work_packages(id), revision INTEGER NOT NULL,
 version INTEGER NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL,
 docs_root TEXT NOT NULL, captured_at TEXT NOT NULL,
 PRIMARY KEY(package_id, revision)
);
CREATE TABLE IF NOT EXISTS leases(
 resource TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES sessions(id),
 token TEXT UNIQUE NOT NULL, expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS handoffs(
 id TEXT PRIMARY KEY, package_id TEXT NOT NULL REFERENCES work_packages(id),
 snapshot TEXT NOT NULL, digest TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resumes(
 handoff_id TEXT NOT NULL REFERENCES handoffs(id), session_id TEXT NOT NULL REFERENCES sessions(id),
 PRIMARY KEY(handoff_id,session_id)
);
"""


class Store:
    def __init__(self, root: str | Path, busy_timeout_ms: int = 3000):
        self.paths = Paths(root)
        self.root = self.paths.root
        self.timeout = busy_timeout_ms
        # Keep DB and all SQLite companions under the configured root only.
        with self.paths.directory((".scs",), create=True):
            pass
        self.db = self.root / ".scs" / "state.sqlite3"
        self._safe_db()
        con = self._connect()
        try:
            con.execute("PRAGMA journal_mode=WAL")
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise ScsError("SCHEMA_UNSUPPORTED", "Database was created by a newer server.")
            # executescript is atomic via an explicit transaction in the script.
            con.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + """
INSERT OR IGNORE INTO work_package_revisions
SELECT id,revision,version,state,data,docs_root,strftime('%Y-%m-%dT%H:%M:%fZ','now') FROM work_packages;
PRAGMA user_version=2; COMMIT;
""")
            with con:
                stored = con.execute("SELECT value FROM metadata WHERE key='project_root'").fetchone()
                if stored and stored[0] != str(self.root):
                    raise ScsError("PROJECT_MISMATCH", "State belongs to another canonical project root.")
                con.execute("INSERT OR IGNORE INTO metadata VALUES('project_root', ?)", (str(self.root),))
            os.chmod(self.db, 0o600)
        finally:
            con.close()

    def docs_root(self, con) -> str:
        row = con.execute("SELECT value FROM metadata WHERE key='docs_root'").fetchone()
        if row is None:
            return self.paths.docs_root()
        return self.paths.validate(row[0])

    def _safe_db(self):
        if (self.root / ".scs").is_symlink():
            raise ScsError("PATH_UNSAFE", "State directory cannot be a symlink.")
        for suffix in ("", "-wal", "-shm", "-journal"):
            p = Path(str(self.db) + suffix)
            # WAL/SHM may disappear when another process closes its connection.
            # One lstat snapshot avoids an exists()/is_file() lifecycle race.
            try:
                info = p.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ScsError("PATH_UNSAFE", "SQLite files must be regular files.")

    def _connect(self):
        self._safe_db()
        con = sqlite3.connect(self.db, timeout=self.timeout / 1000, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute(f"PRAGMA busy_timeout={int(self.timeout)}")
        return con

    @contextmanager
    def transaction(self, write: bool = False):
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield con
            con.commit()
        except sqlite3.OperationalError:
            con.rollback()
            raise ScsError("DB_BUSY", "Database operation failed or lock wait expired; retry safely.") from None
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def revision_snapshot(con, row):
        con.execute("""INSERT INTO work_package_revisions VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(package_id,revision) DO UPDATE SET version=excluded.version,
            state=excluded.state,data=excluded.data,docs_root=excluded.docs_root,captured_at=excluded.captured_at""",
            (row["id"], row["revision"], row["version"], row["state"], row["data"], row["docs_root"], now()))

    @staticmethod
    def event(con, session_id: str | None, question_id: str | None, tool: str,
              event: str, error_code: str | None = None, fallback_reason: str | None = None):
        row = con.execute("SELECT client FROM sessions WHERE id=?", (session_id,)).fetchone()
        con.execute("INSERT INTO events(at,session_id,question_id,client,tool,event,error_code,fallback_reason) VALUES(?,?,?,?,?,?,?,?)",
                    (now(), session_id, question_id, row[0] if row else None, tool, event, error_code, fallback_reason))
