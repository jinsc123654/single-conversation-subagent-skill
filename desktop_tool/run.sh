#!/usr/bin/env bash
# No installation or global configuration changes.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$ROOT/src"
PYTHON="${SCS_GUI_PYTHON:-python3}"

# --check / --version are headless by contract; never require XWayland for them.
case " $* " in
    *" --check "*|*" --version "*)
        exec "$PYTHON" -m scs_manager "$@"
        ;;
esac

# This desktop UI is styled like GTK and Qt5 needs XWayland (xcb) for correct
# fractional scaling and window decorations. Reuse the shared qt-fractional
# wrapper used by the other local PyQt tools. Explicit offscreen/wayland is
# respected (tests and native-Wayland debugging); xcb/empty go through it.
case "${QT_QPA_PLATFORM:-}" in
    offscreen|wayland) ;;
    *)
        if [ -x "${HOME}/.local/bin/qt-fractional" ]; then
            if [ -z "${DISPLAY:-}" ]; then
                echo "错误：未检测到 XWayland（DISPLAY 为空）。请开启 XWayland 后重试；" >&2
                echo "      如确实要原生 Wayland，显式设置 QT_QPA_PLATFORM=wayland（界面可能异常）。" >&2
                exit 1
            fi
            exec "${HOME}/.local/bin/qt-fractional" "$PYTHON" -m scs_manager "$@"
        fi
        ;;
esac
exec "$PYTHON" -m scs_manager "$@"
