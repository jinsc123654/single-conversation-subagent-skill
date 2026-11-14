"""Token-fenced advisory leases. No claims about nonparticipating processes."""

import math
import re
import time

from .errors import ScsError
from .state import State, identifier
from .storage import Store


class Leases:
    def __init__(self, store: Store, clock=time.time):
        self.store = store
        self.state = State(store)
        self.clock = clock

    def resource(self, resource: str) -> str:
        kind, sep, value = resource.partition(":")
        if not sep or kind not in {"file", "device", "serial", "programmer"}:
            raise ScsError("RESOURCE_INVALID", "Use file:, device:, serial: or programmer: identifiers.")
        if kind == "file":
            return "file:" + self.store.paths.validate(value)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value):
            raise ScsError("RESOURCE_INVALID", "Use a simple logical device identifier, not a URL or credential.")
        return resource

    @staticmethod
    def mode(value: str) -> str:
        if value not in {"read", "write"}:
            raise ScsError("RESOURCE_INVALID", "Resource mode must be read or write.")
        return value

    @staticmethod
    def _covers(parent: str, child: str) -> bool:
        return parent == child or child.startswith(parent + "/")

    def _conflict(self, left: str, left_mode: str, right: str, right_mode: str) -> bool:
        # R/R may share a resource; any writer conflicts with readers and writers.
        if left_mode == "read" and right_mode == "read":
            return False
        if left.startswith("file:") and right.startswith("file:"):
            a, b = left[5:], right[5:]
            return self._covers(a, b) or self._covers(b, a)
        return left == right

    def _validate_ttl(self, ttl_seconds: float) -> float:
        if not math.isfinite(ttl_seconds) or not 1 <= ttl_seconds <= 3600:
            raise ScsError("TTL_INVALID", "Lease TTL must be between one second and one hour.")
        return ttl_seconds

    def _normalize_set(self, resources: list[dict]) -> list[dict]:
        if not isinstance(resources, list) or len(resources) > 100:
            raise ScsError("RESOURCE_INVALID", "Declare at most 100 resources.")
        merged = {}
        for item in resources:
            if not isinstance(item, dict) or set(item) - {"resource", "mode"}:
                raise ScsError("RESOURCE_INVALID", "Resource entries contain resource and mode only.")
            key = self.resource(item.get("resource", ""))
            mode = self.mode(item.get("mode", "write"))
            # A write requirement dominates a duplicate read entry.
            if key not in merged or mode == "write":
                merged[key] = mode
        return [{"resource": key, "mode": merged[key]} for key in sorted(merged)]

    def _expired_active(self, con, row, stamp: float) -> bool:
        if row["expires"] > stamp:
            return True
        if not row["attempt_id"]:
            return False
        attempt = con.execute("SELECT state FROM work_attempts WHERE id=?", (row["attempt_id"],)).fetchone()
        return attempt is None or attempt["state"] != "ENDED"

    def acquire_many(self, con, session_id: str, attempt_id: str, package_id: str,
                     resources: list[dict], ttl_seconds: float = 120) -> list[dict]:
        """Atomically acquire a resource set inside an existing write transaction.

        The caller owns the transaction: either every requested lease is inserted
        or the whole attempt preparation is rolled back. Conflicting paths are
        checked as a set before any row is changed.
        """
        self.state._session(con, session_id)
        self._validate_ttl(ttl_seconds)
        requested = self._normalize_set(resources)
        stamp = self.clock()
        for row in list(con.execute("SELECT * FROM leases")):
            if not self._expired_active(con, row, stamp):
                continue
            for item in requested:
                if row["attempt_id"] == attempt_id:
                    continue
                if self._conflict(item["resource"], item["mode"], row["resource"], row["mode"]):
                    raise ScsError("RESOURCE_BUSY",
                        "Resource conflicts with an active lease.",
                        {"resource": item["resource"], "holder": row["resource"],
                         "owner_session": row["owner"], "expires_at": row["expires"]})
        expires = stamp + ttl_seconds
        result = []
        for item in requested:
            token = identifier("L-")
            con.execute("INSERT OR REPLACE INTO leases(resource,owner,token,expires,mode,attempt_id,package_id) VALUES(?,?,?,?,?,?,?)",
                        (item["resource"], session_id, token, expires, item["mode"], attempt_id, package_id))
            result.append({"resource": item["resource"], "mode": item["mode"],
                           "lease_token": token, "expires_at": expires})
        return result

    def release_attempt(self, con, attempt_id: str, session_id: str) -> list[str]:
        rows = list(con.execute("SELECT resource FROM leases WHERE attempt_id=? AND owner=?", (attempt_id, session_id)))
        con.execute("DELETE FROM leases WHERE attempt_id=? AND owner=?", (attempt_id, session_id))
        return [row["resource"] for row in rows]

    def acquire(self, session_id: str, resource: str, ttl_seconds: float = 120,
                lease_token: str | None = None) -> dict:
        resource = self.resource(resource)
        self._validate_ttl(ttl_seconds)
        stamp = self.clock()
        with self.store.transaction(True) as con:
            self.state._session(con, session_id)
            exact = con.execute("SELECT * FROM leases WHERE resource=?", (resource,)).fetchone()
            if exact and self._expired_active(con, exact, stamp):
                if exact["owner"] != session_id or exact["token"] != lease_token:
                    return {"status": "CONFLICT", "resource": resource, "owner_session": exact["owner"],
                            "expires_at": exact["expires"], "advisory_only": True}
            for row in con.execute("SELECT * FROM leases"):
                if row["resource"] == resource or not self._expired_active(con, row, stamp):
                    continue
                if self._conflict(resource, "write", row["resource"], row["mode"]):
                    raise ScsError("RESOURCE_BUSY",
                        "Resource conflicts with an active lease.",
                        {"resource": resource, "holder": row["resource"], "owner_session": row["owner"]})
            token = exact["token"] if exact and exact["owner"] == session_id and exact["token"] == lease_token else identifier("L-")
            con.execute("INSERT OR REPLACE INTO leases(resource,owner,token,expires,mode,attempt_id,package_id) VALUES(?,?,?,?,'write',NULL,NULL)",
                        (resource, session_id, token, stamp + ttl_seconds))
            return {"status": "ACQUIRED", "resource": resource, "lease_token": token,
                    "expires_at": stamp + ttl_seconds, "advisory_only": True,
                    "external_processes_excluded": False}

    def release(self, session_id: str, resource: str, lease_token: str) -> dict:
        resource = self.resource(resource)
        with self.store.transaction(True) as con:
            self.state._session(con, session_id)
            row = con.execute("SELECT * FROM leases WHERE resource=?", (resource,)).fetchone()
            if row is None:
                return {"status": "ALREADY_RELEASED", "resource": resource}
            if row["owner"] != session_id or row["token"] != lease_token:
                raise ScsError("LEASE_OWNER_MISMATCH", "A stale owner/token cannot release this lease.")
            con.execute("DELETE FROM leases WHERE resource=?", (resource,))
            return {"status": "RELEASED", "resource": resource}
