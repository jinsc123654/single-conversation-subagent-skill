"""Plugin-free project views in docs-root. Never edits the global vault registry."""

import hashlib
import html
import json
import posixpath
import re
from pathlib import PurePosixPath
from urllib.parse import quote, urlencode

from .errors import ScsError
from .state import State
from .storage import Store, encode, now
from .work import Work
from .vault_profile import prepare_profile, profile_info, profile_uri

BEGIN = "<!-- manual:begin -->"
END = "<!-- manual:end -->"
HEADER = "<!-- scs-project-view:1 -->\n"
PAGES = {
    "home": "00_项目总览.md",
    "attention": "10_需要关注.md",
    "progress": "20_进行中.md",
    "review": "25_待验收.md",
    "complete": "30_已完成子任务.md",
    "unknown": "50_未分类.md",
    "recent": "90_最近修订.md",
    "all": "95_全部工作包.md",
    "help": "99_视图说明.md",
}
LABELS = {"PENDING": "待开始", "IN_PROGRESS": "进行中", "REVIEW_READY": "待主对话验收", "BLOCKED": "受阻",
          "PASS": "已报告通过", "FAIL": "失败", "NOT_RUN": "未运行", "SUBSTITUTED": "替代条件完成"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def text(value) -> str:
    value = " ".join(str(value).split())
    value = html.escape(value, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|])", r"\\\1", value)


def split_note(data: bytes) -> tuple[str, str, str]:
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ScsError("VIEW_CONFLICT", "Managed notes must be UTF-8; existing content was preserved.") from None
    if not content.startswith(HEADER) or content.count(BEGIN) != 1 or content.count(END) != 1:
        raise ScsError("VIEW_CONFLICT", "Unknown generated note or malformed manual markers; reconcile before refresh.")
    start = content.index(BEGIN) + len(BEGIN)
    end = content.index(END)
    if start > end:
        raise ScsError("VIEW_CONFLICT", "Manual markers are reversed; existing content was preserved.")
    return content[:start], content[start:end], content[end:]


def managed_hash(data: bytes) -> str:
    before, _, after = split_note(data)
    return digest((before + after).encode())


def note(body: str, manual: str = "\n\n") -> bytes:
    return (HEADER + body.rstrip() + "\n\n" + BEGIN + manual + END + "\n").encode()


class ProjectView:
    def __init__(self, store: Store):
        self.store = store
        self.paths = store.paths
        self.state = State(store)
        self.work = Work(store)

    def _layout(self):
        docs = self.paths.docs_root()
        view = docs + "/agent/acceptance"
        return docs, view, view + "/.scs-view.json"

    def _snapshot(self, con):
        packages = [dict(r) for r in con.execute("SELECT * FROM work_packages ORDER BY created_at,id")]
        revisions = [dict(r) for r in con.execute("SELECT * FROM work_package_revisions ORDER BY package_id,revision")]
        return packages, revisions, digest(encode([packages, revisions]).encode())

    def _manifest(self, path: str, docs: str):
        raw = self.paths.read_optional(path)
        if raw is None:
            return None, None
        try:
            data = json.loads(raw)
            assert data["schema"] == 1 and data["docs_root"] == docs
            assert data["project_hash"] == digest(str(self.store.root).encode())
            assert isinstance(data["files"], dict) and len(data["files"]) <= 10000
            prefix = docs + "/agent/acceptance/"
            for name, hashes in data["files"].items():
                assert self.paths.validate(name) == name and name.startswith(prefix) and name.endswith(".md")
                assert isinstance(hashes, list) and 1 <= len(hashes) <= 64
                assert all(isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes)
            assert isinstance(data["snapshot"], str)
        except (ValueError, KeyError, TypeError, AssertionError):
            raise ScsError("VIEW_MANIFEST_INVALID", "View ownership manifest is invalid or belongs to another project; no files were changed.") from None
        return data, raw

    def _vault_config(self, docs: str) -> bool:
        with self.paths.directory(self.paths.parts(docs + "/.obsidian"), missing_ok=True) as fd:
            return fd is not None

    def _link(self, source: str, target: str, label: str, docs: str) -> str:
        # A project may retain records under an older docs-root. Do not make
        # Obsidian links that escape the selected vault; show their source path.
        try:
            target = self.paths.validate(target)
        except ScsError:
            return text(label) + "（路径需核对：" + text(target) + "）"
        if not target.startswith(docs + "/"):
            return text(label) + "（库外：" + text(target) + "）"
        relative = posixpath.relpath(target, posixpath.dirname(source))
        return "[" + text(label) + "](" + quote(relative, safe="/") + ")"

    def _sources(self, source: str, docs: str, original_docs: str) -> str:
        lines = ["## 记忆来源", ""]
        for name in ("AGENT_PROJECT.md", "AGENT_COORDINATION.md"):
            lines.append("- " + self._link(source, original_docs + "/agent/" + name, name, docs))
        return "\n".join(lines)

    def _check_report(self, row) -> tuple[bool, str | None]:
        try:
            self.work._verify_reports(json.loads(row["data"]), row, row["revision"])
        except ScsError as error:
            return False, error.code
        return True, None

    def _render(self, docs: str, view: str, packages: list[dict], revisions: list[dict]) -> tuple[dict[str, str], dict]:
        pages = {}
        groups = {key: [] for key in ("attention", "progress", "review", "complete", "unknown")}
        by_package = {}
        for row in revisions:
            by_package.setdefault(row["package_id"], []).append(row)
        outside = set()
        invalid = []
        for row in packages:
            data = json.loads(row["data"])
            pid = row["id"]
            if not re.fullmatch(r"WP-[A-Za-z0-9_-]+", pid):
                raise ScsError("VIEW_DATA_INVALID", "Work package ID cannot be used as a note path.")
            page = view + "/work-packages/" + pid + ".md"
            valid, reason = self._check_report(row)
            if not valid:
                invalid.append({"package_id": pid, "error_code": reason})
            if row["docs_root"] != docs:
                outside.add(row["docs_root"])
            tasks = data["subtasks"]
            bucket = ("attention" if not valid or row["state"] == "BLOCKED" or any(t["status"] in {"FAIL", "BLOCKED"} for t in tasks)
                      else "review" if row["state"] == "REVIEW_READY"
                      else "progress" if row["state"] in {"PENDING", "IN_PROGRESS"} else "unknown")
            summary = f"{pid} · {data['goal']} · R{row['revision']}"
            groups[bucket].append((page, summary))
            lines = ["# " + text(data["goal"]), "", f"工作包：`{pid}` · 当前修订：R{row['revision']} · 版本：{row['version']}", "",
                     "状态：" + LABELS.get(row["state"], text(row["state"])), "", "## 验收条件", ""]
            lines.extend("- " + text(item) for item in data["acceptance"])
            lines += ["", "## 子任务", ""]
            for task in tasks:
                label = LABELS.get(task["status"], task["status"])
                if task["status"] == "PASS" and not valid:
                    label = "已报告通过；证据需重新核对"
                lines.append("- **" + text(task["id"]) + "**：" + label)
                for entry in task["evidence"]:
                    lines.append("  - " + self._link(page, entry["path"], "证据：" + PurePosixPath(entry["path"]).name, docs))
                if task["status"] == "PASS" and valid:
                    groups["complete"].append((page, f"{pid} / {task['id']} · {data['goal']}"))
            history = by_package.get(pid, [])
            lines += ["", "## 修订记录", ""]
            if history and history[0]["revision"] > 1:
                lines += ["早期修订没有结构化快照；原始记忆和证据保留，未推断其状态。", ""]
            for old in history:
                target = view + f"/revisions/{pid}-R{old['revision']}.md"
                lines.append("- " + self._link(page, target, f"R{old['revision']} · {LABELS.get(old['state'], old['state'])}", docs))
            lines += ["", self._sources(page, docs, row["docs_root"]), "",
                      self._link(page, view + "/" + PAGES["home"], "返回项目总览", docs)]
            pages[page] = "\n".join(lines)
        for row in revisions:
            pid = row["package_id"]
            if not re.fullmatch(r"WP-[A-Za-z0-9_-]+", pid):
                raise ScsError("VIEW_DATA_INVALID", "Revision package ID is invalid.")
            data = json.loads(row["data"])
            page = view + f"/revisions/{pid}-R{row['revision']}.md"
            lines = [f"# {text(data['goal'])} / R{row['revision']}", "", f"记录版本：{row['version']} · 状态：{LABELS.get(row['state'], text(row['state']))}", "",
                     "快照记录时间：" + text(row["captured_at"]), "", "## 当时的子任务报告", ""]
            for task in data["subtasks"]:
                lines.append("- " + text(task["id"]) + "：" + LABELS.get(task["status"], text(task["status"])))
                for entry in task["evidence"]:
                    lines.append("  - " + self._link(page, entry["path"], "证据：" + PurePosixPath(entry["path"]).name, docs))
            lines += ["", "历史快照保留当时报告；当前证据有效性请查看工作包页。", "",
                      self._link(page, view + "/work-packages/" + pid + ".md", "查看工作包", docs), "", self._sources(page, docs, row["docs_root"])]
            pages[page] = "\n".join(lines)
        titles = {"attention": "需要关注", "progress": "进行中", "review": "待验收", "complete": "已完成子任务", "unknown": "未分类"}
        for key, title in titles.items():
            page = view + "/" + PAGES[key]
            lines = ["# " + title, ""]
            if key == "complete":
                lines += ["这里汇总已报告通过且本次检查证据未变化的子任务。工作包的最终验收由主对话记录在项目记忆中。", ""]
            lines += ["- " + self._link(page, target, label, docs) for target, label in groups[key]] or ["暂无条目。"]
            lines += ["", self._link(page, view + "/" + PAGES["home"], "返回项目总览", docs)]
            pages[page] = "\n".join(lines)
        recent = view + "/" + PAGES["recent"]
        ordered = sorted(revisions, key=lambda r: (r["captured_at"], r["package_id"], r["revision"]), reverse=True)
        pages[recent] = "# 最近修订\n\n" + ("\n".join("- " + self._link(recent, view + f"/revisions/{r['package_id']}-R{r['revision']}.md", f"{r['package_id']} / R{r['revision']} · {r['captured_at']}", docs) for r in ordered[:50]) or "暂无修订。")
        all_page = view + "/" + PAGES["all"]
        pages[all_page] = "# 全部工作包\n\n" + ("\n".join("- " + self._link(all_page, view + "/work-packages/" + r["id"] + ".md", r["id"] + " · " + json.loads(r["data"])["goal"], docs) for r in packages) or "暂无工作包。")
        home = view + "/" + PAGES["home"]
        lines = ["# " + text(self.store.root.name) + " · 项目总览", "", "项目资料与工作记录都在本库中。", "", "## 按状态查看", ""]
        for key, title in titles.items():
            lines.append("- " + self._link(home, view + "/" + PAGES[key], f"{title}（{len(groups[key])}）", docs))
        lines += ["", "## 修订与说明", "", "- " + self._link(home, recent, "最近修订", docs),
                  "- " + self._link(home, view + "/" + PAGES["help"], "使用与更新说明", docs), "", self._sources(home, docs, docs)]
        if not packages:
            lines += ["", "目前没有 MCP 工作包。既有手工项目记录仍可通过上方记忆来源打开；未自动推断或导入其状态。"]
        if outside:
            lines += ["", "部分原始记录位于旧文档目录，已显示其项目相对路径；没有移动原文件。"]
        pages[home] = "\n".join(lines)
        help_page = view + "/" + PAGES["help"]
        pages[help_page] = "\n".join(["# 视图说明", "", "此目录是项目记录的浏览视图，无需安装社区插件。工作包、修订和证据来自 MCP 记录；最终验收仍由主对话负责。", "",
            "## 手工批注", "", "可以在每页底部 manual:begin 与 manual:end 两条标记之间写批注，刷新时原样保留。生成区域修改或标记异常时刷新会停止；不要直接修改生成区域来改变任务状态。", "",
            "## 首次打开", "", "从配套管理器点击“用 Obsidian 打开”可使用项目独立启动配置，首次无需手工登记。普通 URI 方式则在 Obsidian 中选择“打开文件夹为仓库”，选择本项目的 " + text(docs) + " 文件夹。以后使用项目总览的 Obsidian 打开链接。仅有 .obsidian 文件夹不能证明已注册或已打开，本服务不改全局仓库列表。", "",
            "## 更新", "", "阶段结束或工作包变更后调用 project_view_refresh；project_view_info 只检查是否过期和返回打开链接。修改手工记忆不会自动导入 SQLite，需主对话先核对。", "",
            self._link(help_page, all_page, "查找全部工作包", docs), "", self._link(help_page, home, "返回项目总览", docs)])
        return pages, {"work_packages": len(packages), "revisions": len(revisions), "groups": {k: len(v) for k, v in groups.items()},
                       "evidence_issues": invalid, "sources_outside_vault": sorted(outside)}

    def _info(self, con, docs: str, view: str, manifest, snapshot: str) -> dict:
        overview = view + "/" + PAGES["home"]
        configured = self._vault_config(docs)
        drift = []
        if manifest:
            for path, hashes in manifest["files"].items():
                raw = self.paths.read_optional(path)
                try:
                    if raw is None or managed_hash(raw) not in hashes:
                        drift.append(path)
                except ScsError:
                    drift.append(path)
        evidence_issues = []
        for row in con.execute("SELECT * FROM work_packages"):
            valid, reason = self._check_report(row)
            if not valid:
                evidence_issues.append({"package_id": row["id"], "error_code": reason})
        evidence_stale = manifest is None or encode(evidence_issues) != encode(manifest.get("evidence_issues", []))
        desktop_profile=profile_info(self.paths,docs)
        desktop_profile["open_uri"]=profile_uri(desktop_profile, "agent/acceptance/"+PAGES["home"])
        return {"status": "OK", "project_name": self.store.root.name, "docs_root": docs,
                "vault_root": str(self.store.root / docs), "view_root": view, "overview_path": overview,
                "overview_exists": self.paths.read_optional(overview) is not None,
                "open_uri": "obsidian://open?" + urlencode({"path": str(self.store.root / overview)}, quote_via=quote),
                "vault_manager_uri": "obsidian://choose-vault", "local_config_present": configured,
                "vault_registration": "NOT_OBSERVABLE", "application_opened": False,
                "first_open_instructions": "Use the desktop companion for automatic opening with the dedicated profile. Generic open_uri still requires a registered vault.",
                "desktop_profile": desktop_profile,
                "view_initialized": manifest is not None, "auto_refresh_enabled": bool(manifest and manifest.get("auto_refresh")),
                "snapshot_stale": manifest is None or manifest["snapshot"] != snapshot,
                "refresh_incomplete": bool(manifest and manifest.get("pending")), "generated_content_drift": drift,
                "evidence_issues": evidence_issues, "evidence_stale": evidence_stale,
                "needs_refresh": manifest is None or manifest["snapshot"] != snapshot or bool(drift) or evidence_stale or bool(manifest.get("pending")),
                "plugin_required": False, "markdown_sync_owner": "MAIN_CONVERSATION"}

    def info(self, session_id: str) -> dict:
        with self.store.transaction() as con:
            self.state._session(con, session_id)
            docs, view, path = self._layout()
            manifest, _ = self._manifest(path, docs)
            _, _, snapshot = self._snapshot(con)
            return self._info(con, docs, view, manifest, snapshot)

    def refresh(self, session_id: str, initialize_vault: bool = False, auto_refresh: bool = True) -> dict:
        with self.store.transaction(True) as con:
            session = self.state._session(con, session_id)
            if not session["mode"] or (session["mode"] in {"standard", "long"} and
                    (not session["verification"] or (session["review_registration"] == "registered" and session["codex_review"] is None))):
                raise ScsError("INTAKE_REQUIRED", "Confirm this conversation's intake before creating project view files.")
            if session["mode"] == "investigate":
                raise ScsError("READ_ONLY_MODE", "Investigation mode can inspect project_view_info but cannot write a view.")
            docs, view, manifest_path = self._layout()
            manifest, old_manifest = self._manifest(manifest_path, docs)
            configured = self._vault_config(docs)  # preflight symlinks before any writes
            packages, revisions, snapshot = self._snapshot(con)
            pages, summary = self._render(docs, view, packages, revisions)
            if len(pages) > 2000:
                raise ScsError("VIEW_LIMIT", "View exceeds 2000 generated notes; narrow the project before refreshing.")
            owned = dict(manifest["files"]) if manifest else {}
            planned = []
            conflicts = []
            for path, body in sorted(pages.items()):
                previous = self.paths.read_optional(path)
                try:
                    if previous is not None:
                        if path not in owned or managed_hash(previous) not in owned[path]:
                            conflicts.append(path)
                            continue
                        _, manual, _ = split_note(previous)
                    else:
                        manual = "\n\n"
                    replacement = note(body, manual)
                except ScsError:
                    conflicts.append(path)
                    continue
                old_hashes = owned.get(path, [])
                new_hash = managed_hash(replacement)
                owned[path] = list(dict.fromkeys([*old_hashes, new_hash]))
                if len(owned[path]) > 64:
                    raise ScsError("VIEW_LIMIT", "Too many interrupted generations; reconcile the ownership manifest.")
                if previous != replacement:
                    planned.append((path, replacement, digest(previous) if previous is not None else None))
            if conflicts:
                raise ScsError("VIEW_CONFLICT", "Generated content changed or belongs to another generator; existing files were preserved.", {"paths": conflicts})
            # Preflight config file too. Existing vault configurations are never edited.
            if initialize_vault and not configured:
                self.paths.validate(docs + "/.obsidian/app.json")
            if initialize_vault:
                prepare_profile(self.paths, docs)
            self.work._records(docs)
            if not planned and manifest and not manifest.get("pending") and manifest["snapshot"] == snapshot and bool(manifest.get("auto_refresh")) == auto_refresh:
                if initialize_vault and not configured:
                    self.paths.create_once(docs + "/.obsidian/app.json", "{}\n")
                return {**self._info(con, docs, view, manifest, snapshot), **summary, "updated_files": 0, "result": "NO_CHANGES"}
            # Record both old/new ownership hashes first. If interrupted between
            # atomic file replacements, a retry recognizes its own partial output.
            pending = {"schema": 1, "project_hash": digest(str(self.store.root).encode()), "docs_root": docs,
                       "files": owned, "snapshot": snapshot, "pending": True, "auto_refresh": auto_refresh, "refreshed_at": now(), "evidence_issues": summary["evidence_issues"]}
            pending_raw = (encode(pending) + "\n").encode()
            if len(pending_raw) > 2_000_000:
                raise ScsError("VIEW_LIMIT", "View manifest exceeds the bounded file size.")
            self.paths.replace_checked(manifest_path, pending_raw, digest(old_manifest) if old_manifest is not None else None)
            for path, replacement, expected in planned:
                self.paths.replace_checked(path, replacement, expected)
            pending.update(pending=False, files={path: [hashes[-1]] for path, hashes in owned.items()})
            final_raw = (encode(pending) + "\n").encode()
            self.paths.replace_checked(manifest_path, final_raw, digest(pending_raw))
            if initialize_vault and not configured:
                self.paths.create_once(docs + "/.obsidian/app.json", "{}\n")
            self.store.event(con, session_id, None, "project_view_refresh", "VIEW_REFRESHED")
            return {**self._info(con, docs, view, pending, snapshot), **summary, "updated_files": len(planned), "result": "REFRESHED"}
