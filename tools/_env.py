"""极简 .env 加载器(纯标准库,无第三方依赖)。

按顺序查找 .env 文件并加载到 os.environ。用 setdefault,因此
**真实 shell 环境变量优先**, .env 只是补默认值,不会覆盖已导出的变量。

查找顺序:
  1. 当前工作目录的 .env
  2. 本文件所在目录(tools/)的 .env
  3. 仓库根目录的 .env

支持格式:
    # 注释行
    KEY=VALUE
    KEY="带空格的值"
    KEY='带#号的值'
    export KEY=VALUE     # 兼容 shell 风格的 export 前缀

用法:
    from _env import load_env
    load_env()           # 在读取任何 os.environ 之前调用

    # 直接运行可查看加载了哪些变量(不打印值,避免泄露):
    python tools/_env.py
"""

from __future__ import annotations

import os
from pathlib import Path


def _candidate_paths() -> list[Path]:
    here = Path(__file__).resolve().parent
    return [
        Path.cwd() / ".env",
        here / ".env",
        here.parent / ".env",
    ]


def _parse_line(line: str) -> tuple[str, str] | None:
    """解析单行,返回 (key, value) 或 None(空行/注释/格式错误)。"""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    # 兼容 `export KEY=VALUE`
    if line.startswith("export "):
        line = line[len("export "):].lstrip()
    if "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip()
    if not key:
        return None
    value = value.strip()
    # 去掉两侧成对的单/双引号
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value


def load_env() -> dict[str, str]:
    """加载 .env 到 os.environ(setdefault 不覆盖已存在的)。返回已加载的映射。"""
    loaded: dict[str, str] = {}
    for path in _candidate_paths():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw_line in text.splitlines():
            parsed = _parse_line(raw_line)
            if parsed is None:
                continue
            key, value = parsed
            loaded[key] = value
            os.environ.setdefault(key, value)
    return loaded


if __name__ == "__main__":
    result = load_env()
    names = ", ".join(sorted(result)) or "(无)"
    print(f"已加载 {len(result)} 个环境变量: {names}")
    print("提示: 变量值未打印。用 echo $KEY 查看具体值。")
