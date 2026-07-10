"""llm_filter 纯函数单元测试。

覆盖:
  - _make_output_path: 绝不覆盖输入文件
  - parse_llm_json: 容忍 markdown 代码块包裹

运行方式:
    python tools/tests/test_llm_filter.py
    python -m pytest tools/tests/test_llm_filter.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps"))

import llm_filter  # noqa: E402
from _llm import parse_llm_json  # noqa: E402


# ---- _make_output_path ----

def test_output_path_replaces_extracted():
    p = Path("/tmp/abc_extracted.json")
    out = llm_filter._make_output_path(p)
    assert out.name == "abc_filtered.json"
    assert out.resolve() != p.resolve()


def test_output_path_appends_suffix_when_no_extracted():
    p = Path("/tmp/twitter_results_20260617_143911.json")
    out = llm_filter._make_output_path(p)
    assert out.name == "twitter_results_20260617_143911_filtered.json"
    assert out.resolve() != p.resolve(), "输出路径绝不能与输入相同"


def test_output_path_double_safety_never_overwrites():
    p = Path("/tmp/filtered.json")
    out = llm_filter._make_output_path(p)
    assert out.resolve() != p.resolve()


# ---- parse_llm_json ----

def test_parse_json_plain_array():
    assert parse_llm_json('["a", "b"]') == ["a", "b"]


def test_parse_json_markdown_wrapped():
    raw = '```json\n["HIGH", "NONE"]\n```'
    assert parse_llm_json(raw) == ["HIGH", "NONE"]


def test_parse_json_bare_code_fence():
    raw = '```\n["x"]\n```'
    assert parse_llm_json(raw) == ["x"]


def test_parse_json_invalid_returns_none():
    assert parse_llm_json("not json at all") is None
    assert parse_llm_json("") is None
    assert parse_llm_json(None) is None


def test_parse_json_object():
    assert parse_llm_json('{"k": 1}') == {"k": 1}


# ---- 独立运行入口 ----

def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
