#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
挑一个可用解释器启动桌宠（Windows 双击 start_pet.bat 时会调用它）。

优先级：环境变量 DSH_PET_PYTHON > 常见安装路径 > py 启动器 > PATH 上的 python。
判定标准：能同时 import tkinter 和 PIL。
"""

from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

CANDIDATES = [
    r"C:\Python314\python.exe",
    r"C:\Python313\python.exe",
    r"C:\Python312\python.exe",
    r"C:\Python311\python.exe",
    os.path.expanduser(r"~\AppData\Local\Programs\Python\Python313\python.exe"),
    os.path.expanduser(r"~\AppData\Local\Programs\Python\Python312\python.exe"),
    os.path.expanduser(r"~\AppData\Local\Programs\Python\Python311\python.exe"),
    os.path.expanduser(r"~\AppData\Local\Programs\Python\Python310\python.exe"),
    os.path.expanduser(r"~\AppData\Local\Programs\Python\Python39\python.exe"),
    # 本机 DSH 运行环境自带的 Python（通常已带 tkinter + Pillow）
    r"C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe",
    sys.executable,
    "py",
    "python",
]

PROBE = "import tkinter, PIL"


def usable(exe: str) -> bool:
    try:
        return subprocess.run([exe, "-c", PROBE],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except Exception:
        return False


def find() -> str:
    env = os.environ.get("DSH_PET_PYTHON")
    if env and usable(env):
        return env
    for exe in CANDIDATES:
        if exe in ("py", "python") or os.path.exists(exe):
            if usable(exe):
                return exe
    return ""


def main() -> int:
    exe = find()
    if not exe:
        sys.stderr.write(
            "[x] 没找到同时装了 tkinter 和 Pillow 的 Python。\n"
            "    安装 Python 后执行:  pip install pillow\n"
            "    或用环境变量指定解释器: set DSH_PET_PYTHON=C:\\path\\python.exe\n"
        )
        return 1
    args = [exe, os.path.join(HERE, "pet.py")] + sys.argv[1:]
    try:
        return subprocess.call(args)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
