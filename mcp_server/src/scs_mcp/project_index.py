"""Local project registrations shared by the MCP and desktop manager."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .errors import ScsError
from .paths import Paths


def index_directory() -> Path:
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    if not state.is_absolute():
        state = Path.home() / ".local/state"
    return state / "scs-manager/projects"


def entry_name(root: Path) -> str:
    return hashlib.sha256(os.fsencode(root)).hexdigest() + ".json"


def publish(root: Path, vault_root: str, overview_path: str) -> dict:
    entry = {
        "project_root": str(root),
        "vault_root": vault_root,
        "overview_path": str(root / overview_path),
    }
    directory = index_directory()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".project-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(entry, stream, ensure_ascii=False)
        os.replace(temporary, directory / entry_name(root))
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"status": "REGISTERED", **entry}


def registered_projects() -> list[str]:
    directory = index_directory()
    if not directory.is_dir():
        return []
    paths = Paths(directory)
    projects = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if len(projects) >= 500:
                break
            if not entry.name.endswith(".json") or not entry.is_file(follow_symlinks=False):
                continue
            try:
                record = json.loads(paths.read(entry.name, limit=32_000))
                root = Path(record["project_root"])
                if root.is_absolute() and root.is_dir() and entry_name(root) == entry.name:
                    projects.append(str(root))
            except (ScsError, OSError, ValueError, TypeError, KeyError):
                continue
    return sorted(projects)
