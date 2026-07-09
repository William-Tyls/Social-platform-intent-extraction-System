"""LLM 共享模块 单元测试。

覆盖 _llm.py 的纯函数 (均接受归一化 schema):
  - normalize_label: 标签归一化
  - parse_llm_json: JSON 解析 (markdown 容忍)
  - parse_comment_labels: 批量标签解析
  - build_classify_prompt: 单条分类提示词
  - build_comment_batch_prompt: 评论批量提示词
  - build_unified_batch_prompt: 跨平台批量提示词

运行:
    python tools/tests/test_llm.py              # 独立运行
    python -m pytest tools/tests/test_llm.py -v  # pytest
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _llm import (  # noqa: E402
    build_classify_prompt,
    build_comment_batch_prompt,
    build_unified_batch_prompt,
    normalize_label,
    parse_comment_labels,
    parse_llm_json,
)
from _normalize import normalize_item  # noqa: E402


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
# build_classify_prompt (归一化 item)
# ====================================================================

def test_build_classify_prompt_basic():
    item = normalize_item({
        "tweet_id": "1", "author_handle": "testuser",
        "author_name": "Test User", "tweet_text": "这是一条测试推文",
        "likes": 10, "replies": 3,
    }, "twitter")
    prompt = build_classify_prompt(item, "AI 资讯")
    assert "@testuser" in prompt
    assert "Test User" in prompt
    assert "测试推文" in prompt
    assert "AI 资讯" in prompt
    assert "[TARGET]" in prompt
    assert "[AD]" in prompt
    assert "[IRRELEVANT]" in prompt


def test_build_classify_prompt_with_comments():
    item = normalize_item({
        "tweet_id": "1", "author_handle": "a1", "tweet_text": "post",
        "comments": [
            {"commenter_handle": "c1", "text": "comment1"},
            {"commenter_handle": "c2", "text": "comment2"},
        ],
    }, "twitter")
    prompt = build_classify_prompt(item, "test")
    assert "comment1" in prompt
    assert "comment2" in prompt
    assert "@c1" in prompt


def test_build_classify_prompt_with_profile():
    item = normalize_item({
        "tweet_id": "1", "author_handle": "a1",
        "tweet_text": "post",
        "profile": {"bio": "developer", "followers": 100, "following": 50},
    }, "twitter")
    prompt = build_classify_prompt(item, "test")
    assert "developer" in prompt
    assert "粉丝: 100" in prompt


def test_build_classify_prompt_video_mode():
    item = normalize_item({
        "tweet_id": "v1", "author_handle": "channel",
        "tweet_text": "video title",
        "view_count": 500,
    }, "youtube")
    prompt = build_classify_prompt(item, "test")
    assert "视频" in prompt


def test_build_classify_prompt_no_comments():
    item = normalize_item({
        "tweet_id": "1", "author_handle": "a",
        "tweet_text": "post",
    }, "twitter")
    prompt = build_classify_prompt(item, "test")
    assert "(无评论)" in prompt


# ====================================================================
# build_comment_batch_prompt (归一化 comments + parent item)
# ====================================================================

def test_build_comment_batch_prompt():
    comments = [
        {"author": "u1", "content": "good"},
        {"author": "u2", "content": "bad"},
    ]
    parent = normalize_item({
        "tweet_id": "1", "author_handle": "op",
        "tweet_text": "original post",
    }, "twitter")
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
    parent = normalize_item({"tweet_id": "1"}, "twitter")
    prompt = build_comment_batch_prompt([], parent, "goal")
    assert "0 条" in prompt


def test_build_comment_batch_prompt_uses_author():
    """归一化格式的 author 字段被正确渲染。"""
    comments = [{"author": "fallback_user", "content": "hi"}]
    parent = normalize_item({"tweet_id": "1"}, "twitter")
    prompt = build_comment_batch_prompt(comments, parent, "goal")
    assert "@fallback_user" in prompt


# ====================================================================
# build_unified_batch_prompt (归一化 item 列表)
# ====================================================================

def _make(item, platform="twitter"):
    return normalize_item(item, platform)


def test_build_batch_prompt_all_items():
    items = [
        _make({"tweet_id": "1", "author_handle": "a1", "tweet_text": "hello"}),
        _make({"tweet_id": "2", "author_handle": "a2", "tweet_text": "world"}),
    ]
    prompt = build_unified_batch_prompt(items, "测试")
    assert "hello" in prompt
    assert "world" in prompt
    assert "2 条" in prompt
    assert "测试" in prompt


def test_build_batch_prompt_empty():
    prompt = build_unified_batch_prompt([], "goal")
    assert "0 条" in prompt


def test_build_batch_prompt_truncation():
    """正文超过 300 字符时截断。"""
    items = [_make({"tweet_id": "x", "tweet_text": "y" * 500})]
    prompt = build_unified_batch_prompt(items, "goal")
    assert "y" * 350 not in prompt


_UNIFIED_TWITTER = normalize_item({
    "tweet_id": "1",
    "author_handle": "user1",
    "author_name": "User One",
    "tweet_text": "这是一条测试推文",
    "likes": 10,
    "replies": 3,
    "comments": [
        {"commenter_handle": "c1", "commenter_name": "C1",
         "text": "good post", "timestamp": "", "likes": 2},
    ],
}, "twitter")

_UNIFIED_YOUTUBE = normalize_item({
    "tweet_id": "vid1",
    "author_handle": "channel",
    "author_name": "Channel",
    "tweet_text": "Video Title",
    "view_count": 1000,
    "duration": 120,
    "comments": [],
}, "youtube")

_UNIFIED_REDDIT = normalize_item({
    "tweet_id": "r1",
    "author_handle": "u_reddit",
    "author_name": "Redditor",
    "tweet_text": "Reddit Post",
    "comments": [
        {"commenter_handle": "rc1", "text": "nice", "likes": 1},
    ],
}, "reddit")


def test_unified_prompt_includes_platform_label():
    items = [_UNIFIED_TWITTER, _UNIFIED_YOUTUBE, _UNIFIED_REDDIT]
    prompt = build_unified_batch_prompt(items, "AI 资讯")
    assert "【推文】" in prompt
    assert "【视频】" in prompt
    assert "【帖子】" in prompt


def test_unified_prompt_includes_authors():
    items = [_UNIFIED_TWITTER]
    prompt = build_unified_batch_prompt(items, "test")
    assert "@user1" in prompt


def test_unified_prompt_includes_comments():
    items = [_UNIFIED_TWITTER]
    prompt = build_unified_batch_prompt(items, "test")
    assert "评论1" in prompt
    assert "@c1" in prompt
    assert "good post" in prompt


def test_unified_prompt_item_count():
    items = [_UNIFIED_TWITTER, _UNIFIED_YOUTUBE]
    prompt = build_unified_batch_prompt(items, "test")
    assert "2 条" in prompt


def test_unified_prompt_goal():
    prompt = build_unified_batch_prompt([_UNIFIED_TWITTER], "筛选真实用户")
    assert "筛选真实用户" in prompt


def test_unified_prompt_empty_items():
    prompt = build_unified_batch_prompt([], "goal")
    assert "0 条" in prompt


def test_unified_prompt_truncation():
    """正文超过 300 字符时截断。"""
    long = normalize_item({
        "tweet_id": "x",
        "tweet_text": "y" * 500,
    }, "twitter")
    prompt = build_unified_batch_prompt([long], "goal")
    assert "y" * 350 not in prompt


def test_unified_prompt_no_comments_field():
    """无 comments 字段的 item 不崩溃。"""
    item = normalize_item({"tweet_id": "1"}, "twitter")
    prompt = build_unified_batch_prompt([item], "goal")
    assert "[1]" in prompt


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
