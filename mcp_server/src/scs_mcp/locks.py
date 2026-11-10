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

    def acquire(self, session_id: str, resource: str, ttl_seconds: float = 120,
                lease_token: str | None = None) -> dict:
        resource = self.resource(resource)
        if not math.isfinite(ttl_seconds) or not 1 <= ttl_seconds <= 3600:
            raise ScsError("TTL_INVALID", "Lease TTL must be between one second and one hour.")
        stamp = self.clock()
        with self.store.transaction(True) as con:
            self.state._session(con, session_id)
            row = con.execute("SELECT * FROM leases WHERE resource=?", (resource,)).fetchone()
            if row and row["expires"] > stamp:
                if row["owner"] != session_id or row["token"] != lease_token:
                    return {"status": "CONFLICT", "resource": resource, "owner_session": row["owner"], "expires_at": row["expires"], "advisory_only": True}
                token = row["token"]
            else:
                token = identifier("L-")
            con.execute("INSERT INTO leases VALUES(?,?,?,?) ON CONFLICT(resource) DO UPDATE SET owner=excluded.owner,token=excluded.token,expires=excluded.expires",
                        (resource, session_id, token, stamp + ttl_seconds))
            return {"status": "ACQUIRED", "resource": resource, "lease_token": token, "expires_at": stamp + ttl_seconds,
                    "advisory_only": True, "external_processes_excluded": False}

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
