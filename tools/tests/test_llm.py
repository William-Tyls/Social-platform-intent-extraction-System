"""LLM 共享模块 单元测试。

覆盖 _llm.py 的纯函数:
  - normalize_label: 标签归一化
  - parse_llm_json: JSON 解析 (markdown 容忍)
  - parse_comment_labels: 批量标签解析
  - build_classify_prompt: 单条分类提示词
  - build_comment_batch_prompt: 评论批量提示词
  - build_batch_classify_prompt: 推文批量提示词

运行:
    python tools/tests/test_llm.py              # 独立运行
    python -m pytest tools/tests/test_llm.py -v  # pytest
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _llm import (  # noqa: E402
    build_batch_classify_prompt,
    build_classify_prompt,
    build_comment_batch_prompt,
    normalize_label,
    parse_comment_labels,
    parse_llm_json,
)


# ====================================================================
# normalize_label
# ====================================================================

def test_normalize_target():
    assert normalize_label("[TARGET]") == "TARGET"
    assert normalize_label("target") == "TARGET"
    assert normalize_label("TARGET") == "TARGET"


def test_normalize_ad():
    assert normalize_label("[AD]") == "AD"
    assert normalize_label("ad") == "AD"
    assert normalize_label("AD") == "AD"


def test_normalize_irrelevant():
    assert normalize_label("[IRRELEVANT]") == "IRRELEVANT"
    assert normalize_label("随机文字") == "IRRELEVANT"
    assert normalize_label("") == "IRRELEVANT"
    assert normalize_label("SOMETHING ELSE") == "IRRELEVANT"


def test_normalize_none_input():
    assert normalize_label(None) == "IRRELEVANT"  # type: ignore[arg-type]


def test_normalize_partial_match():
    """'TARGET' 关键词在任意位置都匹配。"""
    assert normalize_label("IS_TARGET_MATCH") == "TARGET"
    assert normalize_label("SOME-AD-HERE") == "AD"


# ====================================================================
# parse_llm_json
# ====================================================================

def test_parse_plain_array():
    assert parse_llm_json('["a", "b"]') == ["a", "b"]


def test_parse_plain_object():
    assert parse_llm_json('{"key": "value"}') == {"key": "value"}


def test_parse_markdown_json_block():
    raw = '```json\n["TARGET", "AD"]\n```'
    assert parse_llm_json(raw) == ["TARGET", "AD"]


def test_parse_bare_code_fence():
    raw = '```\n["x", "y"]\n```'
    assert parse_llm_json(raw) == ["x", "y"]


def test_parse_empty_string():
    assert parse_llm_json("") is None


def test_parse_none_input():
    assert parse_llm_json(None) is None  # type: ignore[arg-type]


def test_parse_invalid_json():
    assert parse_llm_json("not valid json at all") is None
    assert parse_llm_json("{broken") is None


def test_parse_mixed_content():
    """LLM 偶尔会在 JSON 前面加说明文字。"""
    assert parse_llm_json('Here is the result:\n["a"]') is None  # 严格模式


# ====================================================================
# parse_comment_labels
# ====================================================================

def test_parse_comment_labels_exact_match():
    raw = '["TARGET", "IRRELEVANT", "AD"]'
    result = parse_comment_labels(raw, 3)
    assert result == ["TARGET", "IRRELEVANT", "AD"]


def test_parse_comment_labels_shorter_than_expected():
    """返回数组比预期短,尾部补 IRRELEVANT。"""
    raw = '["TARGET"]'
    result = parse_comment_labels(raw, 3)
    assert result == ["TARGET", "IRRELEVANT", "IRRELEVANT"]


def test_parse_comment_labels_non_array():
    """非数组返回全 ERROR。"""
    raw = '{"key": "value"}'
    result = parse_comment_labels(raw, 2)
    assert result == ["ERROR", "ERROR"]


def test_parse_comment_labels_invalid_json():
    result = parse_comment_labels("garbage", 2)
    assert result == ["ERROR", "ERROR"]


def test_parse_comment_labels_case_insensitive():
    raw = '["target", "ad", "irrelevant"]'
    result = parse_comment_labels(raw, 3)
    assert result == ["TARGET", "AD", "IRRELEVANT"]


# ====================================================================
# build_classify_prompt
# ====================================================================

def test_build_classify_prompt_basic():
    item = {
        "author_handle": "testuser",
        "author_name": "Test User",
        "tweet_text": "这是一条测试推文",
        "likes": 10,
        "replies": 3,
    }
    prompt = build_classify_prompt(item, "AI 资讯")
    assert "@testuser" in prompt
    assert "Test User" in prompt
    assert "测试推文" in prompt
    assert "AI 资讯" in prompt
    assert "[TARGET]" in prompt
    assert "[AD]" in prompt
    assert "[IRRELEVANT]" in prompt


def test_build_classify_prompt_with_comments():
    item = {
        "author_handle": "a1",
        "author_name": "A",
        "tweet_text": "post",
        "likes": 0,
        "replies": 0,
        "comments": [
            {"commenter_handle": "c1", "text": "comment1"},
            {"commenter_handle": "c2", "text": "comment2"},
        ],
    }
    prompt = build_classify_prompt(item, "test")
    assert "comment1" in prompt
    assert "comment2" in prompt
    assert "@c1" in prompt


def test_build_classify_prompt_with_profile():
    item = {
        "author_handle": "a1",
        "author_name": "A",
        "tweet_text": "post",
        "likes": 0,
        "replies": 0,
        "profile": {"bio": "developer", "followers": 100, "following": 50},
    }
    prompt = build_classify_prompt(item, "test")
    assert "developer" in prompt
    assert "粉丝: 100" in prompt


def test_build_classify_prompt_video_mode():
    """有 view_count 时归类为视频。"""
    item = {
        "author_handle": "channel",
        "author_name": "Channel",
        "tweet_text": "video title",
        "likes": 0,
        "replies": 0,
        "view_count": 500,
    }
    prompt = build_classify_prompt(item, "test")
    assert "视频" in prompt


def test_build_classify_prompt_no_comments():
    item = {
        "author_handle": "a",
        "author_name": "A",
        "tweet_text": "post",
        "likes": 0,
        "replies": 0,
    }
    prompt = build_classify_prompt(item, "test")
    assert "(无评论)" in prompt


# ====================================================================
# build_comment_batch_prompt
# ====================================================================

def test_build_comment_batch_prompt():
    comments = [
        {"commenter_handle": "u1", "commenter_name": "U1", "text": "good"},
        {"commenter_handle": "u2", "commenter_name": "U2", "text": "bad"},
    ]
    parent = {"author_handle": "op", "tweet_text": "original post"}
    prompt = build_comment_batch_prompt(comments, parent, "AI")
    assert "@u1" in prompt
    assert "@u2" in prompt
    assert "good" in prompt
    assert "bad" in prompt
    assert "original post" in prompt
    assert "AI" in prompt
    assert "2 条" in prompt
    assert "JSON 数组" in prompt


def test_build_comment_batch_prompt_empty_comments():
    prompt = build_comment_batch_prompt([], {"author_handle": "op", "tweet_text": "post"}, "goal")
    assert "0 条" in prompt


def test_build_comment_batch_prompt_fallback_author():
    """commenter_handle 缺失时回退到 author。"""
    comments = [{"author": "fallback_user", "text": "hi"}]
    parent = {"author_handle": "op", "tweet_text": "post"}
    prompt = build_comment_batch_prompt(comments, parent, "goal")
    assert "@fallback_user" in prompt


# ====================================================================
# build_batch_classify_prompt
# ====================================================================

def test_build_batch_prompt_all_items():
    items = [
        {"author_handle": "a1", "tweet_text": "hello"},
        {"author_handle": "a2", "tweet_text": "world"},
    ]
    prompt = build_batch_classify_prompt(items, "测试")
    assert "[1] @a1: hello" in prompt
    assert "[2] @a2: world" in prompt
    assert "2 条" in prompt
    assert "测试" in prompt


def test_build_batch_prompt_empty():
    prompt = build_batch_classify_prompt([], "goal")
    assert "0 条" in prompt


def test_build_batch_prompt_truncation():
    """正文超过 300 字符时截断。"""
    long_text = "x" * 500
    items = [{"author_handle": "a", "tweet_text": long_text}]
    prompt = build_batch_classify_prompt(items, "goal")
    # 不应包含完整的 500 字符
    assert "x" * 350 not in prompt


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
