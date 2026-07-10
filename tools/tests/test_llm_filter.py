"""llm_filter 纯函数单元测试。

覆盖三个关键修复:
  - _make_output_path: 绝不覆盖输入文件(原 bug:不含 'extracted' 时同名覆盖)
  - _parse_llm_json: 容忍 markdown 代码块包裹
  - _normalize_label: 标签文本归一化

运行方式:
    python tools/test_llm_filter.py        # 独立运行
    python -m pytest tools/test_llm_filter.py  # pytest
"""

import sys
from pathlib import Path

# 把 tools/ 和 tools/apps/ 加入 sys.path 以导入 llm_filter
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps"))

import llm_filter  # noqa: E402  模块级已无副作用,可安全导入

# llm_filter.py 内部从 _llm import, 但未重新导出; 直接从 _llm 导入
from _llm import normalize_label, parse_llm_json  # noqa: E402


# ---- _make_output_path ----

def test_output_path_replaces_extracted():
    p = Path("/tmp/abc_extracted.json")
    out = llm_filter._make_output_path(p)
    assert out.name == "abc_filtered.json"
    assert out.resolve() != p.resolve()


def test_output_path_appends_suffix_when_no_extracted():
    """关键: 输入不含 'extracted' 时(如 twitter_results_*.json),
    原实现会因 replace 无效而覆盖输入。现在必须追加 _filtered。"""
    p = Path("/tmp/twitter_results_20260617_143911.json")
    out = llm_filter._make_output_path(p)
    assert out.name == "twitter_results_20260617_143911_filtered.json"
    assert out.resolve() != p.resolve(), "输出路径绝不能与输入相同"


def test_output_path_double_safety_never_overwrites():
    """极端情况:即便构造出的路径与输入相同,也要再追加 _out。"""
    p = Path("/tmp/filtered.json")  # replace("extracted","filtered") 不触发,走追加分支
    out = llm_filter._make_output_path(p)
    assert out.resolve() != p.resolve()


# ---- _parse_llm_json ----

def test_parse_json_plain_array():
    assert parse_llm_json('["a", "b"]') == ["a", "b"]


def test_parse_json_markdown_wrapped():
    raw = '```json\n["TARGET", "AD"]\n```'
    assert parse_llm_json(raw) == ["TARGET", "AD"]


def test_parse_json_bare_code_fence():
    raw = '```\n["x"]\n```'
    assert parse_llm_json(raw) == ["x"]


def test_parse_json_invalid_returns_none():
    assert parse_llm_json("not json at all") is None
    assert parse_llm_json("") is None
    assert parse_llm_json(None) is None  # type: ignore[arg-type]


def test_parse_json_object():
    assert parse_llm_json('{"k": 1}') == {"k": 1}


# ---- _normalize_label ----

def test_normalize_label_target():
    assert normalize_label("[TARGET]") == "TARGET"
    assert normalize_label("target") == "TARGET"


def test_normalize_label_ad():
    assert normalize_label("[AD]") == "AD"
    assert normalize_label("AD") == "AD"


def test_normalize_label_irrelevant_fallback():
    assert normalize_label("[IRRELEVANT]") == "IRRELEVANT"
    assert normalize_label("随机文字") == "IRRELEVANT"
    assert normalize_label("") == "IRRELEVANT"


# ---- _build_prompt (prompt 构建纯函数) ----

def test_batch_prompt_includes_all_tweets_and_count():
    from _normalize import normalize_item
    from _llm import _build_prompt
    items = [
        normalize_item({"tweet_id": "1", "author_handle": "a1", "tweet_text": "hello"}, "twitter"),
        normalize_item({"tweet_id": "2", "author_handle": "a2", "tweet_text": "world"}, "twitter"),
        normalize_item({"tweet_id": "3", "author_handle": "a3", "tweet_text": "foo"}, "twitter"),
    ]
    prompt = _build_prompt(items, "测试目标", None)
    assert "hello" in prompt
    assert "world" in prompt
    assert "foo" in prompt
    assert "3" in prompt  # 数组长度声明


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
