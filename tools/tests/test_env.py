"""环境变量加载器 单元测试。

覆盖 _env.py:
  - _parse_line: 各种格式解析
  - load_env: 文件加载 + setdefault 语义

运行:
    python tools/tests/test_env.py              # 独立运行
    python -m pytest tools/tests/test_env.py -v  # pytest
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _env import _parse_line, load_env  # noqa: E402


# ====================================================================
# _parse_line
# ====================================================================

def test_parse_simple_key_value():
    assert _parse_line("KEY=VALUE") == ("KEY", "VALUE")


def test_parse_with_export_prefix():
    assert _parse_line("export KEY=VALUE") == ("KEY", "VALUE")


def test_parse_with_export_and_spaces():
    assert _parse_line("  export  KEY = VALUE ") == ("KEY", "VALUE")


def test_parse_double_quoted_value():
    assert _parse_line('KEY="hello world"') == ("KEY", "hello world")


def test_parse_single_quoted_value():
    assert _parse_line("KEY='hello world'") == ("KEY", "hello world")


def test_parse_quoted_value_preserves_hash():
    """引号内的 # 不应被当作注释。"""
    assert _parse_line("KEY='value with # hash'") == ("KEY", "value with # hash")


def test_parse_comment_line():
    assert _parse_line("# this is a comment") is None
    assert _parse_line("  # indented comment") is None


def test_parse_empty_line():
    assert _parse_line("") is None
    assert _parse_line("   ") is None


def test_parse_no_equals():
    assert _parse_line("INVALID_LINE") is None


def test_parse_empty_key():
    """'=value' 这种格式 key 为空,应跳过。"""
    assert _parse_line("=value") is None


def test_parse_value_with_embedded_equals():
    assert _parse_line("URL=https://example.com?x=1") == ("URL", "https://example.com?x=1")


def test_parse_unmatched_quotes():
    """只闭合一侧引号: 当作普通值。"""
    result = _parse_line("KEY=\"unmatched")
    assert result is not None
    assert result[0] == "KEY"


# ====================================================================
# load_env
# ====================================================================

def test_load_env_from_temp_file():
    """从临时 .env 文件加载, 验证 setdefault 语义。"""
    tmp = Path(tempfile.mktemp(suffix=".env"))
    tmp.write_text("TEST_A=hello\nTEST_B=world\n")
    # 伪造文件列表
    original_candidates = _env_orig_candidates

    import _env
    _env._candidate_paths = lambda: [tmp]

    try:
        # 先设一个已有的环境变量
        os.environ["TEST_A"] = "already_set"
        loaded = load_env()
        assert loaded["TEST_A"] == "hello"  # loaded 记录的是文件里的值
        assert os.environ["TEST_A"] == "already_set"  # 但环境变量不覆盖
        assert os.environ["TEST_B"] == "world"
    finally:
        os.environ.pop("TEST_A", None)
        os.environ.pop("TEST_B", None)
        _env._candidate_paths = original_candidates
        tmp.unlink(missing_ok=True)


def test_load_env_missing_file_does_not_raise():
    import _env
    _env._candidate_paths = lambda: [Path("/nonexistent/.env")]
    try:
        result = load_env()
        assert result == {}
    finally:
        _env._candidate_paths = _env_orig_candidates


def test_load_env_unicode():
    """验证 UTF-8 编码正确处理中文。"""
    tmp = Path(tempfile.mktemp(suffix=".env"))
    tmp.write_text("CITY=北京\n", encoding="utf-8")
    import _env
    _env._candidate_paths = lambda: [tmp]
    try:
        result = load_env()
        assert result["CITY"] == "北京"
    finally:
        _env._candidate_paths = _env_orig_candidates
        tmp.unlink(missing_ok=True)
        os.environ.pop("CITY", None)


# ---- 保存原始函数供恢复 ----
import _env as _env_module
_env_orig_candidates = _env_module._candidate_paths


# ---- 独立运行 ----

def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
