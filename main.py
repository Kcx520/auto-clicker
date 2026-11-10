"""连点器 AutoClicker Pro —— 程序入口。

双击「启动连点器.bat」或运行 ``python main.py`` 即可。
"""

from __future__ import annotations

import ctypes
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _single_instance() -> bool:
    """只允许开一个窗口；重复启动直接退出。"""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW(None, False, "Global\\AutoClickerPro_SingleInstance")
        return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def main() -> int:
    from app import winapi as W

    if not _single_instance():
        try:
            ctypes.windll.user32.MessageBoxW(None, "连点器已经在运行啦，看看任务栏或托盘图标。",
                                             "连点器", 0x40)
        except Exception:
            pass
        return 0

    # 必须在创建窗口之前设置，坐标才准
    W.set_dpi_awareness()

    try:
        from app.ui.app import App
    except Exception:
        _show_error("启动失败：界面模块加载出错\n\n" + traceback.format_exc())
        return 1

    try:
        app = App()
    except Exception:
        _show_error("启动失败\n\n" + traceback.format_exc())
        return 1

    try:
        app.mainloop()
    except KeyboardInterrupt:
        pass
    except Exception:
        _show_error("运行中出错\n\n" + traceback.format_exc())
        return 1
    return 0


def _show_error(text: str) -> None:
    try:
        with open(os.path.join(ROOT, "启动错误.log"), "a", encoding="utf-8") as fh:
            fh.write(text + "\n" + "-" * 60 + "\n")
    except Exception:
        pass
    try:
        ctypes.windll.user32.MessageBoxW(None, text[:1500], "连点器", 0x10)
    except Exception:
        print(text)


if __name__ == "__main__":
    sys.exit(main())
