"""LLM 共享模块 单元测试。

覆盖 _llm.py:
  - normalize_label: 标签归一化
  - parse_llm_json: JSON 解析
  - parse_comment_labels: 批量标签解析
  - classify: prompt 构建 + API 调用 + 标签解析 (mock)

运行:
    python tools/tests/test_llm.py
    python -m pytest tools/tests/test_llm.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _llm import (  # noqa: E402
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


# --- helper: classify with mock, inspect the prompt ---

def _assert_prompt(items, goal, parent=None, **checks):
    """Mock call_deepseek, then assert prompt content."""
    # Generate a dummy JSON array of the correct length
    response = json.dumps(["TARGET"] * max(len(items), 1))
    with patch("_llm.call_deepseek") as mock_call:
        mock_call.return_value = response
        classify(items, goal, parent=parent, api_key="sk-test")
        # call_args[0] = (messages_list,), messages = [{system}, {user}]
        prompt = mock_call.call_args[0][0][1]["content"]
    for text, expected in checks.items():
        if expected:
            assert text in prompt, f"{text!r} not in prompt"
        else:
            assert text not in prompt, f"{text!r} in prompt"


# ====================================================================
# normalize_label
# ====================================================================

def test_normalize_target():
    assert normalize_label("[TARGET]") == "TARGET"
    assert normalize_label("target") == "TARGET"


def test_normalize_ad():
    assert normalize_label("[AD]") == "AD"
    assert normalize_label("ad") == "AD"


def test_normalize_irrelevant():
    assert normalize_label("随机文字") == "IRRELEVANT"
    assert normalize_label("") == "IRRELEVANT"


def test_normalize_none_input():
    assert normalize_label(None) == "IRRELEVANT"


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
    assert parse_llm_json('```json\n["TARGET", "AD"]\n```') == ["TARGET", "AD"]


def test_parse_bare_code_fence():
    assert parse_llm_json('```\n["x", "y"]\n```') == ["x", "y"]


def test_parse_empty_string():
    assert parse_llm_json("") is None


def test_parse_none_input():
    assert parse_llm_json(None) is None


def test_parse_invalid_json():
    assert parse_llm_json("not json") is None
    assert parse_llm_json("{broken") is None


# ====================================================================
# parse_comment_labels
# ====================================================================

def test_parse_comment_labels_exact():
    assert parse_comment_labels('["TARGET", "IRRELEVANT", "AD"]', 3) == \
           ["TARGET", "IRRELEVANT", "AD"]


def test_parse_comment_labels_short():
    assert parse_comment_labels('["TARGET"]', 3) == \
           ["TARGET", "IRRELEVANT", "IRRELEVANT"]


def test_parse_comment_labels_non_array():
    assert parse_comment_labels('{"k": 1}', 2) == ["ERROR", "ERROR"]


def test_parse_comment_labels_invalid():
    assert parse_comment_labels("garbage", 2) == ["ERROR", "ERROR"]


def test_parse_comment_labels_case():
    assert parse_comment_labels('["target", "ad", "irrelevant"]', 3) == \
           ["TARGET", "AD", "IRRELEVANT"]


# ====================================================================
# classify — 帖子/视频 prompt
# ====================================================================

def test_prompt_basic():
    item = _tw({"tweet_id": "1", "author_handle": "testuser",
                "tweet_text": "测试推文", "likes": 10, "replies": 3})
    _assert_prompt([item], "AI 资讯",
                   testuser=True, 测试推文=True, **{"AI 资讯": True},
                   赞10=True, 评3=True, TARGET=True, AD=True, IRRELEVANT=True)


def test_prompt_with_comments():
    item = _tw({"tweet_id": "1", "author_handle": "a1", "tweet_text": "post",
                "comments": [
                    {"commenter_handle": "c1", "text": "c1_text"},
                    {"commenter_handle": "c2", "text": "c2_text"},
                ]})
    _assert_prompt([item], "test", c1_text=True, c2_text=True)


def test_prompt_video_stats():
    item = _yt({"tweet_id": "v1", "author_handle": "ch",
                "tweet_text": "video", "view_count": 500})
    _assert_prompt([item], "test", ch=True, video=True, 播放500=True)


def test_prompt_youtube_body():
    item = _yt({"tweet_id": "v1", "tweet_text": "t",
                "description": "long description"})
    _assert_prompt([item], "test", **{"long description": True})


def test_prompt_has_numbering():
    item = _tw({"tweet_id": "1"})
    _assert_prompt([item], "goal", **{"[1]": True})


# ====================================================================
# classify — 评论 prompt
# ====================================================================

def test_prompt_comment_mode():
    comments = [{"author": "u1", "content": "good"},
                {"author": "u2", "content": "bad"}]
    parent = _tw({"tweet_id": "1", "author_handle": "op",
                  "tweet_text": "original"})
    _assert_prompt(comments, "AI", parent=parent,
                   u1=True, u2=True, good=True, bad=True,
                   **{"original": True}, AI=True)


def test_prompt_comment_author_field():
    comments = [{"author": "fallback", "content": "hi"}]
    parent = _tw({"tweet_id": "1"})
    _assert_prompt(comments, "goal", parent=parent, **{"fallback": True})


# ====================================================================
# classify — 批量
# ====================================================================

def test_prompt_batch():
    items = [
        _tw({"tweet_id": "1", "author_handle": "a1", "tweet_text": "hello"}),
        _tw({"tweet_id": "2", "author_handle": "a2", "tweet_text": "world"}),
    ]
    _assert_prompt(items, "测试", hello=True, world=True,
                   **{"2": True}, 测试=True)


def test_prompt_batch_cross_platform():
    items = [
        _tw({"tweet_id": "1", "author_handle": "a1", "tweet_text": "t"}),
        _yt({"tweet_id": "v", "author_handle": "ch", "tweet_text": "v"}),
        _rd({"tweet_id": "r", "author_handle": "u", "tweet_text": "p"}),
    ]
    _assert_prompt(items, "x", a1=True, ch=True, u=True, t=True, v=True, p=True)


def test_prompt_truncation():
    long = _tw({"tweet_id": "x", "tweet_text": "y" * 500})
    _assert_prompt([long], "goal", **{"y" * 350: False})


def test_prompt_stats():
    item = _tw({"tweet_id": "1", "author_handle": "a",
                "tweet_text": "x", "likes": 42, "replies": 7})
    _assert_prompt([item], "goal", 赞42=True, 评7=True)


# ====================================================================
# classify — 返回值
# ====================================================================

def test_classify_returns_labels():
    item = _tw({"tweet_id": "1", "author_handle": "a", "tweet_text": "x"})
    with patch("_llm.call_deepseek", return_value='["TARGET"]'):
        assert classify([item], "goal", api_key="sk-test") == ["TARGET"]


def test_classify_empty():
    assert classify([], "goal", api_key="sk-test") == []


def test_classify_bad_response():
    item = _tw({"tweet_id": "1"})
    with patch("_llm.call_deepseek", return_value="not json"):
        try:
            classify([item], "goal", api_key="sk-test")
            assert False, "should have raised"
        except ClassificationError:
            pass


def test_classify_comment_mode_returns():
    comments = [{"author": "c1", "content": "x"},
                {"author": "c2", "content": "y"}]
    parent = _tw({"tweet_id": "1"})
    with patch("_llm.call_deepseek", return_value='["TARGET", "IRRELEVANT"]'):
        assert classify(comments, "goal", parent=parent, api_key="sk-test") == \
               ["TARGET", "IRRELEVANT"]


# ---- run ----

def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
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
