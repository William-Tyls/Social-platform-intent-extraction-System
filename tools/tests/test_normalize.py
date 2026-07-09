"""跨平台数据归一化 单元测试。

覆盖 _normalize.py:
  - twitter → 统一 schema
  - reddit → 统一 schema
  - youtube → 统一 schema
  - normalize_batch 批量
  - 空字段默认值
  - 评论归一化 (字段名兼容)

运行:
    python tools/tests/test_normalize.py              # 独立运行
    python -m pytest tools/tests/test_normalize.py -v  # pytest
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "extractors"))

from _normalize import normalize_batch, normalize_item  # noqa: E402


# ====================================================================
# Twitter → 统一 schema
# ====================================================================

def test_twitter_basic():
    raw = {
        "tweet_id": "123",
        "author_handle": "user1",
        "author_name": "User One",
        "tweet_text": "hello world",
        "likes": 42,
        "retweets": 7,
        "replies": 3,
        "timestamp": "2026-07-01T00:00:00Z",
        "images": ["img1.jpg"],
        "comments": [],
        "profile": None,
        "label": "",
    }
    item = normalize_item(raw, "twitter")
    assert item["id"] == "123"
    assert item["platform"] == "twitter"
    assert item["author"] == "user1"
    assert item["author_name"] == "User One"
    assert item["content"]["title"] == "hello world"
    assert item["content"]["body"] == ""
    assert item["meta"]["likes"] == 42
    assert item["meta"]["retweets"] == 7
    assert item["meta"]["replies"] == 3
    assert item["meta"]["views"] == 0
    assert item["meta"]["duration"] == 0
    assert item["meta"]["url"] == "https://x.com/user1/status/123"
    assert item["comments"] == []
    assert item["label"] == ""


def test_twitter_label():
    raw = {"tweet_id": "1", "label": "TARGET"}
    item = normalize_item(raw, "twitter")
    assert item["label"] == "TARGET"


def test_twitter_profile():
    raw = {
        "tweet_id": "1",
        "author_handle": "u",
        "author_name": "U",
        "profile": {
            "bio": "dev",
            "followers": 100,
            "following": 50,
            "verified": True,
            "join_date": "2020-01",
            "location": "Seoul",
            "website": "https://example.com",
        },
    }
    item = normalize_item(raw, "twitter")
    p = item["profile"]
    assert p["bio"] == "dev"
    assert p["followers"] == 100
    assert p["verified"] is True


def test_twitter_empty_fields():
    """缺失字段使用空默认值。"""
    raw = {"tweet_id": "x"}
    item = normalize_item(raw, "twitter")
    assert item["author"] == ""
    assert item["content"]["title"] == ""
    assert item["meta"]["likes"] == 0
    assert item["meta"]["images"] == []
    assert item["comments"] == []


def test_twitter_none_values():
    """None 值转换为 0 / 空。"""
    raw = {
        "tweet_id": "x",
        "likes": None,
        "retweets": None,
        "images": None,
    }
    item = normalize_item(raw, "twitter")
    assert item["meta"]["likes"] == 0
    assert item["meta"]["retweets"] == 0
    assert item["meta"]["images"] == []


# ====================================================================
# Reddit → 统一 schema
# ====================================================================

def test_reddit_basic():
    raw = {
        "tweet_id": "abc",
        "author_handle": "redditor",
        "author_name": "Redditor Name",
        "tweet_text": "Check this out",
        "likes": 150,
        "replies": 20,
        "timestamp": "2026-07-01",
        "subreddit": "python",
        "permalink": "/r/python/comments/abc/title/",
        "post_url": "",
        "images": [],
        "comments": [],
    }
    item = normalize_item(raw, "reddit")
    assert item["id"] == "abc"
    assert item["platform"] == "reddit"
    assert item["author"] == "redditor"
    assert item["content"]["title"] == "Check this out"
    assert item["content"]["body"] == ""
    assert item["meta"]["likes"] == 150
    assert item["meta"]["retweets"] == 0  # Reddit 无转发
    assert item["meta"]["replies"] == 20
    assert item["meta"]["url"] == "https://old.reddit.com/r/python/comments/abc/title/"


def test_reddit_with_post_url():
    raw = {
        "tweet_id": "r1",
        "author_handle": "u",
        "tweet_text": "post",
        "post_url": "https://linked-site.com/page",
        "permalink": "/r/test/comments/r1/t/",
    }
    item = normalize_item(raw, "reddit")
    assert item["content"]["body"] == "https://linked-site.com/page"


# ====================================================================
# YouTube → 统一 schema
# ====================================================================

def test_youtube_basic():
    raw = {
        "tweet_id": "dQw4w9WgXcQ",
        "author_name": "Rick Astley",
        "author_handle": "RickAstleyYT",
        "tweet_text": "Never Gonna Give You Up",
        "description": "Official video",
        "likes": 50000,
        "replies": 3000,
        "view_count": 1000000,
        "duration": 212,
        "timestamp": "20091025",
        "webpage_url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "tags": ["music", "80s"],
        "categories": ["Music"],
        "images": ["thumb.jpg"],
        "comments": [],
    }
    item = normalize_item(raw, "youtube")
    assert item["id"] == "dQw4w9WgXcQ"
    assert item["platform"] == "youtube"
    assert item["author"] == "RickAstleyYT"
    assert item["content"]["title"] == "Never Gonna Give You Up"
    assert item["content"]["body"] == "Official video"
    assert item["meta"]["likes"] == 50000
    assert item["meta"]["replies"] == 3000
    assert item["meta"]["views"] == 1000000
    assert item["meta"]["duration"] == 212
    assert item["meta"]["tags"] == ["music", "80s"]
    assert item["meta"]["categories"] == ["Music"]
    assert item["meta"]["url"] == "https://youtube.com/watch?v=dQw4w9WgXcQ"


def test_youtube_empty():
    raw = {"tweet_id": "v"}
    item = normalize_item(raw, "youtube")
    assert item["meta"]["views"] == 0
    assert item["meta"]["duration"] == 0
    assert item["meta"]["tags"] == []
    assert item["content"]["body"] == ""
    assert item["profile"] is None


# ====================================================================
# 评论归一化 (三种平台评论字段名差异)
# ====================================================================

def test_comments_twitter_format():
    """Twitter 评论: commenter_handle + text"""
    item = normalize_item({
        "tweet_id": "1",
        "comments": [
            {"commenter_handle": "u1", "commenter_name": "U1",
             "text": "great", "timestamp": "t1", "likes": 5},
        ],
    }, "twitter")
    c = item["comments"][0]
    assert c["author"] == "u1"
    assert c["content"] == "great"
    assert c["likes"] == 5
    assert c["timestamp"] == "t1"


def test_comments_youtube_format():
    """YouTube 评论: author + text (via _to_tweet_format → commenter_handle/commenter_name)"""
    item = normalize_item({
        "tweet_id": "v",
        "comments": [
            {"commenter_handle": "", "commenter_name": "",
             "text": "nice video", "likes": 3, "timestamp": ""},
        ],
    }, "youtube")
    c = item["comments"][0]
    # author 字段为空时 fallback 到 author 键 (不存在) → ""
    assert c["content"] == "nice video"


def test_comments_empty():
    item = normalize_item({"tweet_id": "1", "comments": []}, "twitter")
    assert item["comments"] == []


def test_comments_missing_key():
    item = normalize_item({"tweet_id": "1"}, "twitter")
    assert item["comments"] == []


# ====================================================================
# normalize_batch
# ====================================================================

def test_normalize_batch():
    raws = [
        {"tweet_id": "1", "author_handle": "a"},
        {"tweet_id": "2", "author_handle": "b"},
    ]
    items = normalize_batch(raws, "twitter")
    assert len(items) == 2
    assert items[0]["id"] == "1"
    assert items[1]["id"] == "2"


def test_normalize_batch_empty():
    assert normalize_batch([], "twitter") == []


# ====================================================================
# 错误输入
# ====================================================================

def test_unknown_platform_raises():
    try:
        normalize_item({}, "unknown")
        assert False, "should have raised"
    except ValueError:
        pass


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
