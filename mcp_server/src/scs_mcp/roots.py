"""Per-project state roots for a shared local MCP server.

The configured root is a workspace boundary; each session binds to its own
project root and gets its own ``<project>/.scs`` database. This is what makes
"start a task in any project and ask the four-way question" work without
reconfiguring the server for every project.
"""

from pathlib import Path

from .errors import ScsError
from .locks import Leases
from .obsidian import ProjectView
from .state import State
from .storage import Store
from .work import Work


class Roots:
    def __init__(self, default_root, allowed_roots=()):
        self.default_root = Path(default_root).resolve()
        self.allowed_roots = (self.default_root, *(Path(root).resolve() for root in allowed_roots))
        self.states = {}
        self.sessions = {}
        self.works = {}
        self._leases = {}
        self._views = {}

    def resolve(self, project_root):
        root = Path(project_root or self.default_root).resolve()
        if not any(root.is_relative_to(allowed) for allowed in self.allowed_roots):
            raise ScsError("PROJECT_MISMATCH", "Project must be inside the configured workspace root.")
        return root

    def state(self, root):
        root = Path(root).resolve()
        if root not in self.states:
            self.states[root] = State(Store(root))
        return self.states[root]

    def open(self, project_root, client_name, conversation_id, review_registration, explicit_skip):
        root = self.resolve(project_root)
        state = self.state(root)
        result = state.open(str(root), client_name, conversation_id, review_registration, explicit_skip)
        self.sessions[result["id"]] = root
        return root, state, result

    def for_session(self, session_id):
        root = self.sessions.get(session_id)
        if root is None:
            raise ScsError("SESSION_NOT_FOUND", "Call session_open again with the project root in this server process.")
        return self.states[root]

    def _root(self, session_id):
        return self.for_session(session_id).store.root

    def work(self, session_id):
        root = self._root(session_id)
        if root not in self.works:
            self.works[root] = Work(self.states[root].store)
        return self.works[root]

    def leases(self, session_id):
        root = self._root(session_id)
        if root not in self._leases:
            self._leases[root] = Leases(self.states[root].store)
        return self._leases[root]

    def views(self, session_id):
        root = self._root(session_id)
        if root not in self._views:
            self._views[root] = ProjectView(self.states[root].store)
        return self._views[root]

    def default_state(self):
        return self.state(self.default_root)

    def package(self, package_id):
        for root, state in self.states.items():
            work = self.works.get(root)
            if work is None:
                work = Work(state.store)
                self.works[root] = work
            with state.store.transaction() as con:
                try:
                    pkg = work._get(con, package_id)
                except ScsError:
                    continue
                return state.store, work, work._snapshot(pkg)
        raise ScsError("PACKAGE_NOT_FOUND", "No loaded project contains this package.")
