"""LLM 共享模块 单元测试。

覆盖 _llm.py:
  - normalize_label: 标签归一化
  - parse_llm_json: JSON 解析 (markdown 容忍)
  - parse_comment_labels: 批量标签解析
  - _build_prompt: prompt 构建 (纯函数)
  - classify: 一站式分类 (mock call_deepseek)

运行:
    python tools/tests/test_llm.py              # 独立运行
    python -m pytest tools/tests/test_llm.py -v  # pytest
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _llm import (  # noqa: E402
    _build_prompt,
    classify,
    ClassificationError,
    normalize_label,
    parse_comment_labels,
    parse_llm_json,
)
from _normalize import normalize_item  # noqa: E402


def _tw(item: dict) -> dict:
    return normalize_item(item, "twitter")


def _yt(item: dict) -> dict:
    return normalize_item(item, "youtube")


def _rd(item: dict) -> dict:
    return normalize_item(item, "reddit")


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
    assert parse_llm_json('Here is the result:\n["a"]') is None


# ====================================================================
# parse_comment_labels
# ====================================================================

def test_parse_comment_labels_exact_match():
    raw = '["TARGET", "IRRELEVANT", "AD"]'
    result = parse_comment_labels(raw, 3)
    assert result == ["TARGET", "IRRELEVANT", "AD"]


def test_parse_comment_labels_shorter_than_expected():
    raw = '["TARGET"]'
    result = parse_comment_labels(raw, 3)
    assert result == ["TARGET", "IRRELEVANT", "IRRELEVANT"]


def test_parse_comment_labels_non_array():
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
# _build_prompt — 帖子/视频 模式 (parent=None)
# ====================================================================

def test_prompt_basic():
    item = _tw({"tweet_id": "1", "author_handle": "testuser",
                "author_name": "Test User", "tweet_text": "这是一条测试推文",
                "likes": 10, "replies": 3})
    prompt = _build_prompt([item], "AI 资讯")
    assert "testuser" in prompt
    assert "测试推文" in prompt
    assert "AI 资讯" in prompt
    assert "赞10" in prompt
    assert "评3" in prompt
    assert "TARGET" in prompt
    assert "AD" in prompt
    assert "IRRELEVANT" in prompt


def test_prompt_with_comments():
    item = _tw({"tweet_id": "1", "author_handle": "a1", "tweet_text": "post",
                "comments": [
                    {"commenter_handle": "c1", "text": "comment1"},
                    {"commenter_handle": "c2", "text": "comment2"},
                ]})
    prompt = _build_prompt([item], "test")
    assert "comment1" in prompt
    assert "comment2" in prompt


def test_prompt_video():
    item = _yt({"tweet_id": "v1", "author_handle": "channel",
                "tweet_text": "video title", "view_count": 500, "duration": 120})
    prompt = _build_prompt([item], "test")
    assert "channel" in prompt
    assert "video title" in prompt
    assert "播放500" in prompt


def test_prompt_youtube_summary():
    item = _yt({"tweet_id": "v1", "tweet_text": "title",
                "description": "This is a long description"})
    prompt = _build_prompt([item], "test")
    assert "long description" in prompt


def test_prompt_empty_comments():
    item = _tw({"tweet_id": "1", "author_handle": "a", "tweet_text": "post"})
    prompt = _build_prompt([item], "test")
    # 有内容即可, 不要求特定短语
    assert "post" in prompt


def test_prompt_no_comments_field():
    item = _tw({"tweet_id": "1"})
    prompt = _build_prompt([item], "goal")
    assert "[1]" in prompt  # 编号不变


# ====================================================================
# _build_prompt — 评论模式 (parent=...)
# ====================================================================

def test_prompt_comments_mode():
    comments = [
        {"author": "u1", "content": "good"},
        {"author": "u2", "content": "bad"},
    ]
    parent = _tw({"tweet_id": "1", "author_handle": "op",
                  "tweet_text": "original post"})
    prompt = _build_prompt(comments, "AI", parent=parent)
    assert "u1" in prompt
    assert "u2" in prompt
    assert "good" in prompt
    assert "bad" in prompt
    assert "original post" in prompt
    assert "AI" in prompt
    assert "2" in prompt


def test_prompt_comments_empty():
    parent = _tw({"tweet_id": "1"})
    prompt = _build_prompt([], "goal", parent=parent)
    assert "0" in prompt


def test_prompt_comments_author_field():
    comments = [{"author": "fallback_user", "content": "hi"}]
    parent = _tw({"tweet_id": "1"})
    prompt = _build_prompt(comments, "goal", parent=parent)
    assert "fallback_user" in prompt


# ====================================================================
# _build_prompt — 批量模式
# ====================================================================

def test_prompt_batch_all_items():
    items = [
        _tw({"tweet_id": "1", "author_handle": "a1", "tweet_text": "hello"}),
        _tw({"tweet_id": "2", "author_handle": "a2", "tweet_text": "world"}),
    ]
    prompt = _build_prompt(items, "测试")
    assert "hello" in prompt
    assert "world" in prompt
    assert "2" in prompt
    assert "测试" in prompt


def test_prompt_batch_empty():
    prompt = _build_prompt([], "goal")
    assert "0" in prompt


def test_prompt_batch_cross_platform():
    """三平台混合批量 — 各自作者和内容正确渲染，平台无关。"""
    items = [
        _tw({"tweet_id": "1", "author_handle": "a1", "tweet_text": "tweet"}),
        _yt({"tweet_id": "v1", "author_handle": "ch", "tweet_text": "video"}),
        _rd({"tweet_id": "r1", "author_handle": "u", "tweet_text": "post"}),
    ]
    prompt = _build_prompt(items, "test")
    assert "a1" in prompt
    assert "ch" in prompt
    assert "u" in prompt
    assert "tweet" in prompt
    assert "video" in prompt
    assert "post" in prompt


def test_prompt_batch_truncation():
    long = _tw({"tweet_id": "x", "tweet_text": "y" * 500})
    prompt = _build_prompt([long], "goal")
    assert "y" * 350 not in prompt


def test_prompt_batch_stats():
    item = _tw({"tweet_id": "1", "author_handle": "a",
                "tweet_text": "x", "likes": 42, "replies": 7})
    prompt = _build_prompt([item], "goal")
    assert "赞42" in prompt
    assert "评7" in prompt


# ====================================================================
# classify (一站式，mock API)
# ====================================================================


def test_classify_returns_labels():
    item = _tw({"tweet_id": "1", "author_handle": "a", "tweet_text": "x"})
    with patch("_llm.call_deepseek", return_value='["TARGET"]'):
        labels = classify([item], "goal", api_key="sk-test")
    assert labels == ["TARGET"]


def test_classify_empty_items():
    assert classify([], "goal", api_key="sk-test") == []


def test_classify_raises_on_bad_response():
    item = _tw({"tweet_id": "1"})
    with patch("_llm.call_deepseek", return_value='not json'):
        try:
            classify([item], "goal", api_key="sk-test")
            assert False, "should have raised"
        except ClassificationError:
            pass


def test_classify_comment_mode():
    comments = [
        {"author": "c1", "content": "great"},
        {"author": "c2", "content": "bad"},
    ]
    parent = _tw({"tweet_id": "1", "author_handle": "op",
                   "tweet_text": "original"})
    with patch("_llm.call_deepseek", return_value='["TARGET", "IRRELEVANT"]'):
        labels = classify(comments, "goal", parent=parent, api_key="sk-test")
    assert labels == ["TARGET", "IRRELEVANT"]


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
