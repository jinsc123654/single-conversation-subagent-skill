"""Read-only project context for questions. No writes, no network, no subprocess."""

import time
from pathlib import Path

_CACHE = {}
_TTL = 30.0


def _read_head(path: Path, limit: int = 220) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [line.strip() for line in text.splitlines()
             if line.strip() and not line.strip().startswith("![")]
    return " / ".join(lines[:3])[:limit]


def scan_workspace(root, ttl: float = _TTL) -> str:
    """Bounded, read-only summary of the configured project root.

    Returns README/PRODUCT_CLI head, key app/component dirs, build/dist/test
    artifacts and the top-level layout. Cached briefly because questions repeat.
    """
    root = Path(root)
    key = str(root)
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < ttl:
        return cached[1]

    parts = []
    for name in ("README.md", "README.txt"):
        head = _read_head(root / name)
        if head:
            parts.append(f"{name}: {head}")
    for sub in ("applications", "components", "board", "rt-thread"):
        directory = root / sub
        if directory.is_dir():
            try:
                names = sorted(x.name for x in directory.iterdir() if not x.name.startswith("."))[:8]
            except OSError:
                names = []
            if names:
                parts.append(f"{sub}: " + ", ".join(names))
    head = _read_head(root / "PRODUCT_CLI.md")
    if head:
        parts.append(f"PRODUCT_CLI.md: {head}")
    for sub, label in (("build", "build"), ("dist", "dist"), ("04_test", "测试")):
        directory = root / sub
        if directory.is_dir():
            try:
                names = sorted(x.name for x in directory.iterdir() if not x.name.startswith("."))[:5]
            except OSError:
                names = []
            if names:
                parts.append(f"{label}: " + ", ".join(names))
    try:
        dirs = sorted(p.name for p in root.iterdir()
                      if p.is_dir() and not p.name.startswith(".") and p.name != "__pycache__")[:12]
        files = sorted(p.name for p in root.iterdir() if p.is_file() and not p.name.startswith("."))[:8]
    except OSError:
        dirs, files = [], []
    if dirs:
        parts.append("顶层目录: " + ", ".join(dirs))
    if files:
        parts.append("顶层文件: " + ", ".join(files))
    text = ("；".join(parts) or "空目录或无法读取")[:900]
    _CACHE[key] = (now, text)
    return text
