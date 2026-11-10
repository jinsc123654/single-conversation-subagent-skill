"""Source launcher. --check and --version need no graphical session."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys

from . import __version__


def main():
    parser = argparse.ArgumentParser(description="Optional SCS project/MCP manager")
    parser.add_argument("--version", action="version", version="scs-manager " + __version__)
    parser.add_argument("--check", action="store_true", help="Read-only environment check; no window")
    parser.add_argument("--project", action="append", default=[], help="Initial project; can be repeated")
    parser.add_argument("--config", type=Path, help="Override this application's project-list file")
    parser.add_argument("--mcp-python", help="Python containing scs-mcp / official MCP SDK 2.3")
    parser.add_argument("--theme", choices=("dark", "light"))
    args = parser.parse_args()
    config = args.config or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "scs-manager/projects.json"
    available = importlib.util.find_spec("PyQt5") is not None
    if args.check:
        print(json.dumps({"app": "scs-manager", "version": __version__, "python": sys.executable,
            "pyqt5_available": available, "gui_session": bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
            "own_config": str(config), "system_install": "not required", "mcp_dependency": "separate Python, mcp 2.3",
            "writes_performed": False}, ensure_ascii=False, indent=2))
        return 0 if available else 1
    if not available:
        print("可选管理器需要 PyQt5；见 desktop_tool/README.md。MCP 与 Skill 不受影响。", file=sys.stderr)
        return 1
    from PyQt5.QtWidgets import QApplication
    from . import theme
    from .window import MainWindow
    app = QApplication([sys.argv[0]])
    app.setApplicationName("scs-manager")
    app.setApplicationDisplayName("SCS 项目管理器")
    app.setDesktopFileName("scs-manager")
    theme.apply(app, force=args.theme)
    if args.theme:
        theme.set_forced(args.theme)
    try:
        window = MainWindow(config, initial_projects=args.project)
        if args.mcp_python:
            window.settings["server_python"] = args.mcp_python
    except (ValueError, OSError) as error:
        print("无法加载项目列表：" + str(error), file=sys.stderr)
        return 1
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
