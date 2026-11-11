"""Bounded local inspection; no GUI imports and no database migrations."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

DOCS = ("00_doc", "00_docs", "docs", "doc")
EXCLUDED = {".git", ".venv", "venv", "node_modules", ".cache", "__pycache__"}


def canonical(root):
    path = Path(root).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("项目目录不存在")
    return path


def inside(root, path):
    root = canonical(root)
    path = Path(path)
    if not path.is_absolute():
        path = root / path
    # Disallow symbolic links even if their target happens to be inside root.
    relative = path.absolute().relative_to(root)
    current = root
    for part in relative.parts:
        if part in {"..", "."}:
            raise ValueError("路径不能离开项目")
        current = current / part
        if current.is_symlink():
            raise ValueError("不操作符号链接，请直接添加真实项目路径")
    resolved = current.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("路径不能离开项目")
    return resolved


def registered_docs_root(root):
    if inside(root, ".scs/state.sqlite3").is_file():
        with database(root) as con:
            row = con.execute("SELECT value FROM metadata WHERE key='docs_root'").fetchone()
        if row:
            return inside(root, row[0])
    return None


def docs_root(root):
    root = canonical(root)
    registered = registered_docs_root(root)
    if registered is not None:
        return registered
    for name in DOCS:
        p = inside(root, name)
        if p.is_dir():
            return p
    return root / "docs"


def read_json(path, maximum=2_000_000):
    with Path(path).open("rb") as handle:
        raw = handle.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("记录过大，停止读取")
    return json.loads(raw)


@contextmanager
def database(root, *, write=False, timeout=2):
    root = canonical(root)
    db = inside(root, ".scs/state.sqlite3")
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = inside(root, str(db) + suffix)
        if p.exists() and not stat.S_ISREG(p.stat().st_mode):
            raise ValueError("状态文件类型异常")
    if not db.is_file():
        raise ValueError("项目尚无 MCP 状态库；添加项目不会初始化它")
    con = sqlite3.connect(db.as_uri() + ("?mode=rw" if write else "?mode=ro"), timeout=timeout, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        if not write:
            con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version not in (1, 2):
            raise ValueError("状态库版本不受支持；管理器不会迁移它")
        record = con.execute("SELECT value FROM metadata WHERE key='project_root'").fetchone()
        if not record or record[0] != str(root):
            raise ValueError("状态库属于另一项目路径；请核对原始项目")
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def manifest(root):
    root = canonical(root)
    docs = docs_root(root)
    view = docs / "agent/acceptance"
    mpath = inside(root, view / ".scs-view.json")
    if not mpath.exists():
        return None
    value = read_json(mpath)
    if (value.get("schema") != 1 or value.get("docs_root") != docs.relative_to(root).as_posix()
            or value.get("project_hash") != hashlib.sha256(str(root).encode()).hexdigest()
            or not isinstance(value.get("files"), dict) or len(value["files"]) > 10000):
        raise ValueError("视图归属清单无效或属于另一项目")
    prefix = view.relative_to(root).as_posix() + "/"
    for name, hashes in value["files"].items():
        path = inside(root, name)
        if (not name.startswith(prefix) or not name.endswith(".md") or not path.is_relative_to(view)
                or not isinstance(hashes, list) or not hashes
                or any(not isinstance(h, str) or len(h) != 64 for h in hashes)):
            raise ValueError("受管文件清单不合法")
    return value


def snapshot(root):
    root = canonical(root)
    docs = docs_root(root)
    result = {"root": str(root), "name": root.name, "docs_root": str(docs), "host_connection": "不可观测",
              "database": "未初始化", "schema": None, "packages": [], "sessions": [], "events": [],
              "view": {"state": "未生成", "count": 0, "auto_refresh": False}}
    if inside(root, ".scs/state.sqlite3").is_file():
        with database(root) as con:
            result.update(database="可读取", schema=con.execute("PRAGMA user_version").fetchone()[0])
            result["sessions"] = [dict(r) for r in con.execute(
                "SELECT id,client,mode,verification,codex_review,review_registration,created_at FROM sessions ORDER BY created_at DESC LIMIT 200")]
            rows = list(con.execute("SELECT * FROM work_packages ORDER BY created_at DESC,id LIMIT 500"))
            for row in rows:
                data = json.loads(row["data"])
                result["packages"].append({"id": row["id"], "goal": data["goal"], "revision": row["revision"],
                    "state": row["state"], "tasks": len(data["subtasks"]), "docs_root": row["docs_root"]})
            result["events"] = [dict(r) for r in con.execute(
                "SELECT at,tool,event,error_code FROM events ORDER BY seq DESC LIMIT 40")]
            result["counts"] = {table: con.execute("SELECT count(*) FROM " + table).fetchone()[0]
                                for table in ("sessions", "work_packages", "events")}
        m = manifest(root)
        if m:
            missing = sum(not inside(root, name).is_file() for name in m["files"])
            result["view"] = {"state": "刷新未完成" if m.get("pending") else "已生成（需连接检查有效性）",
                "count": len(m["files"]), "auto_refresh": bool(m.get("auto_refresh")),
                "refreshed_at": m.get("refreshed_at"), "missing": missing, "files": sorted(m["files"])}
    return result


def discover(root, depth=2, maximum=2000):
    """Only scan the chosen directory and at most two descendant levels."""
    root = canonical(root)
    found, queue, visited, truncated = [], [(root, 0)], 0, False
    while queue and visited < maximum:
        current, level = queue.pop(0)
        visited += 1
        if not (current / ".scs").is_symlink() and (current / ".scs/state.sqlite3").is_file():
            found.append(str(current))
        if level < depth:
            with os.scandir(current) as entries:
                # Bound broad directories too, rather than materializing all children.
                for entry in entries:
                    if len(queue) + visited >= maximum:
                        truncated = True
                        break
                    if entry.name not in EXCLUDED and not entry.name.startswith(".") and entry.is_dir(follow_symlinks=False):
                        queue.append((Path(entry.path), level + 1))
    return {"projects": sorted(found), "visited": visited, "truncated": truncated or bool(queue), "depth": depth}


class Registry:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        if not self.path.exists():
            return {"projects": [], "server_python": ""}
        data = read_json(self.path, 2_000_000)
        if not isinstance(data.get("projects"), list) or len(data["projects"]) > 500:
            raise ValueError("项目列表格式错误")
        if not all(isinstance(v, str) for v in data["projects"]):
            raise ValueError("项目路径必须为文本")
        hidden = data.get("hidden_projects", [])
        if not isinstance(hidden, list) or len(hidden) > 500 or not all(isinstance(v, str) for v in hidden):
            raise ValueError("隐藏项目列表格式错误")
        for key in ('collapsed_projects','last_opened'):
            values=data.get(key,{})
            if not isinstance(values,dict) or len(values)>500:
                raise ValueError('项目栏设置格式错误')
            for path,value in values.items():
                if not isinstance(path,str) or (key=='collapsed_projects' and type(value) is not bool):
                    raise ValueError('项目栏设置格式错误')
                if key=='last_opened' and (type(value) not in (int,float) or not __import__('math').isfinite(value) or value<0):
                    raise ValueError('项目时间格式错误')
        return data

    def save(self, data):
        if len(data["projects"]) > 500:
            raise ValueError("项目列表最多保存 500 个项目")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".projects-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)


def obsidian_target(root, selection=None):
    root = canonical(root)
    selected = inside(root, selection or root)
    if not selected.exists():
        raise ValueError("选中的路径不存在")
    base = selected if selected.is_dir() else selected.parent
    # Prefer the nearest real vault, including nested projects' docs folders.
    vault = None
    if selected == root:
        vault = registered_docs_root(root)
    for parent in (base, *base.parents):
        if vault or not parent.is_relative_to(root):
            break
        if inside(root, parent / ".obsidian").is_dir():
            vault = parent
            break
        for name in DOCS:
            candidate = inside(root, parent / name)
            if candidate.is_dir() and inside(root, candidate / ".obsidian").is_dir():
                vault = candidate
                break
        if vault:
            break
    vault = vault or docs_root(root)
    overview = vault / "agent/acceptance/00_项目总览.md"
    if selected.suffix.lower() == ".md" and selected.is_file() and selected.is_relative_to(vault):
        target = selected
    elif inside(root, overview).is_file():
        target = overview
    else:
        # A folder is not a note: use the absolute vault path parameter.
        return {"vault_root": str(vault), "uri": "obsidian://open?" + urlencode({"vault": str(vault)}, quote_via=quote)}
    return {"vault_root": str(vault), "note_path": str(target), "uri": "obsidian://open?" + urlencode({"path": str(target)}, quote_via=quote)}


def archive_view(root):
    """Move the whole owned view, preserving annotations/extras; serialize with MCP writers.

    No state/evidence/vault configuration deletion. Explicit GUI confirmation precedes
    this command. External editor writes are outside SQLite's coordination boundary.
    """
    root = canonical(root)
    with database(root, write=True):
        m = manifest(root)
        if m is None:
            raise ValueError("没有可归档的受管视图")
        source = inside(root, docs_root(root) / "agent/acceptance")
        # Preserve everything, refuse symlinks/special files and bound traversal.
        count = 0
        for base, dirs, files in os.walk(source, followlinks=False):
            for name in dirs + files:
                p = inside(root, Path(base) / name)
                if not (p.is_file() or p.is_dir()):
                    raise ValueError("视图含特殊文件，停止归档")
                count += 1
                if count > 12000:
                    raise ValueError("视图太大，请人工管理")
        destination = inside(root, source.parent / "view-archives")
        destination.mkdir(exist_ok=True)
        tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        destination = destination / tag
        source.rename(destination)
    return {"archive": str(destination), "files_and_folders": count,
            "message": "已归档整个视图（含批注及额外笔记）；自动刷新暂停。下次刷新会重新生成。"}


def timestamp(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
    except (ValueError,TypeError,OverflowError):
        return 0.0


def registered_projects():
    source = Path(__file__).resolve().parents[3] / "mcp_server/src"
    if source.is_dir() and str(source) not in sys.path:
        sys.path.insert(0, str(source))
    from scs_mcp.project_index import registered_projects as read_index
    return read_index()


def project_catalog(roots):
    """Small sidebar summaries; reads never update the activity timestamp."""
    if not isinstance(roots,list) or len(roots)>500 or any(not isinstance(p,str) for p in roots):
        raise ValueError('项目列表格式错误或超过500项')
    projects=[]
    for raw in roots:
        entry={'root':raw,'name':Path(raw).name,'last_activity':0.0,'packages':[],'error':None}
        try:
            root=canonical(raw)
            if inside(root,'.scs/state.sqlite3').is_file():
                with database(root,timeout=0.15) as con:
                    latest=con.execute('SELECT max(at) FROM events').fetchone()[0]
                    entry['last_activity']=timestamp(latest)
                    has_revisions=con.execute("SELECT 1 FROM sqlite_master WHERE name='work_package_revisions'").fetchone()
                    if has_revisions:
                        query='''SELECT p.id,p.state,p.data,p.created_at,coalesce((SELECT max(captured_at)
                          FROM work_package_revisions r WHERE r.package_id=p.id),p.created_at) AS activity
                          FROM work_packages p ORDER BY activity DESC,p.id LIMIT 8'''
                    else:
                        query='SELECT id,state,data,created_at,created_at AS activity FROM work_packages ORDER BY created_at DESC,id LIMIT 8'
                    for row in con.execute(query):
                        last=timestamp(row['activity'])
                        entry['last_activity']=max(entry['last_activity'],last)
                        goal=json.loads(row['data']).get('goal',row['id'])
                        entry['packages'].append({'id':row['id'],'goal':str(goal)[:160],'state':row['state'],'last_activity':last})
            else:
                entry['last_activity']=root.stat().st_mtime
        except (ValueError,OSError,sqlite3.Error,KeyError,TypeError):
            entry['error']='项目暂不可读取'
        projects.append(entry)
    return projects


def prepare_obsidian(root, selection=None):
    """Use bundled, SDK-free profile helper; no process or global config changes."""
    import shutil
    import sys
    source=Path(__file__).resolve().parents[3] / 'mcp_server/src'
    if source.is_dir() and str(source) not in sys.path:
        sys.path.insert(0,str(source))
    from scs_mcp.paths import Paths
    from scs_mcp.errors import ScsError
    from scs_mcp.vault_profile import prepare_profile, profile_uri
    executable=shutil.which('obsidian')
    if not executable:
        raise ValueError('未找到 Obsidian 程序；请先安装 Obsidian 并加入 PATH')
    target=obsidian_target(root,selection)
    root=canonical(root); vault=inside(root,target['vault_root'])
    if not vault.is_dir():
        raise ValueError('文档库尚不存在；请先生成项目文档或 MCP 视图')
    try:
        profile=prepare_profile(Paths(root),vault.relative_to(root).as_posix())
    except ScsError as error:
        raise ValueError("Obsidian 启动配置无法准备："+str(error)) from None
    note=Path(target['note_path']).relative_to(vault).as_posix() if target.get('note_path') else None
    uri=profile_uri(profile,note)
    program=executable
    arguments=[profile['launch_flag'],uri]
    # Give Obsidian its own user service where available, so restarting the
    # manager cannot terminate an Obsidian process in the manager's cgroup.
    runner=shutil.which('systemd-run')
    if runner and os.environ.get('XDG_RUNTIME_DIR'):
        import subprocess
        try:
            status=subprocess.run(['systemctl','--user','is-system-running'],capture_output=True,text=True,timeout=2)
            if status.stdout.strip() in {'running','degraded'}:
                program=runner
                arguments=['--user','--collect','--quiet','--description=Obsidian project',executable,*arguments]
        except (OSError,subprocess.TimeoutExpired):
            pass
    return {**profile,'executable':executable,'uri':uri,'vault_root':str(vault),
            'launch_program':program,'launch_arguments':arguments}
