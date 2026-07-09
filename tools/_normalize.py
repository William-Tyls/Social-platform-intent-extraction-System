"""跨平台数据归一化模块。

将 Twitter / Reddit / YouTube 三种提取器的异构输出映射为统一的
两级结构 (item → content + comments)，供 LLM 批处理和 JSON 导出使用。

统一 schema::

    {
        "id": str,              # 平台唯一 ID
        "platform": str,        # "twitter" | "reddit" | "youtube"
        "author": str,          # 作者 handle / username / channel id
        "author_name": str,     # 作者显示名
        "content": {
            "title": str,       # 标题 (推文正文 / 帖子标题 / 视频标题)
            "body": str,        # 正文补充 (帖子正文 / 视频摘要, Twitter 为空)
        },
        "meta": {
            "likes": int,
            "replies": int,
            "retweets": int,    # Reddit / YouTube 固定为 0
            "views": int,       # 仅 YouTube 有效
            "duration": int,    # 仅 YouTube 有效 (秒)
            "timestamp": str,
            "url": str,
            "images": [str],
            "tags": [str],
            "categories": [str],
        },
        "comments": [
            {
                "author": str,
                "content": str,
                "likes": int,
                "timestamp": str,
            }
        ],
        "profile": dict | None, # 作者主页信息 (可选)
        "label": str,           # LLM 过滤后追加
    }

用法::

    from _normalize import normalize_item

    item = normalize_item(raw_twitter_dict, "twitter")
    items = normalize_batch(raw_list, "reddit")
"""

from __future__ import annotations

# ------------------------------------------------------------------
# 平台 → 归一化
# ------------------------------------------------------------------


def normalize_item(raw: dict, platform: str) -> dict:
    """将单条原始字典转换为统一 schema。

    ``raw`` 是 worker 产出的扁平字典（如 ``tweet_text``、
    ``author_handle``、``view_count`` 等）。
    ``platform`` 为 ``"twitter"``、``"reddit"`` 或 ``"youtube"``。
    """
    if platform == "twitter":
        return _from_twitter(raw)
    if platform == "reddit":
        return _from_reddit(raw)
    if platform == "youtube":
        return _from_youtube(raw)
    raise ValueError(f"未知平台: {platform}")


def normalize_batch(items: list[dict], platform: str) -> list[dict]:
    """批量归一化。"""
    return [normalize_item(it, platform) for it in items]


# ------------------------------------------------------------------
# 平台转换函数
# ------------------------------------------------------------------


def _from_twitter(t: dict) -> dict:
    return {
        "id": t.get("tweet_id", ""),
        "platform": "twitter",
        "author": t.get("author_handle", ""),
        "author_name": t.get("author_name", ""),
        "content": {
            "title": t.get("tweet_text", ""),
            "body": "",
        },
        "meta": {
            "likes": t.get("likes", 0) or 0,
            "replies": t.get("replies", 0) or 0,
            "retweets": t.get("retweets", 0) or 0,
            "views": 0,
            "duration": 0,
            "timestamp": t.get("timestamp", ""),
            "url": f"https://x.com/{t.get('author_handle', '')}/status/{t.get('tweet_id', '')}",
            "images": t.get("images") or [],
            "tags": [],
            "categories": [],
        },
        "comments": _normalize_comments(t.get("comments") or []),
        "profile": _normalize_twitter_profile(t.get("profile")),
        "label": t.get("label", ""),
    }


def _from_reddit(p: dict) -> dict:
    return {
        "id": p.get("tweet_id", ""),
        "platform": "reddit",
        "author": p.get("author_handle", ""),
        "author_name": p.get("author_name", ""),
        "content": {
            "title": p.get("tweet_text", ""),
            "body": p.get("post_url", ""),
        },
        "meta": {
            "likes": p.get("likes", 0) or 0,
            "replies": p.get("replies", 0) or 0,
            "retweets": 0,
            "views": 0,
            "duration": 0,
            "timestamp": p.get("timestamp", ""),
            "url": (p.get("permalink") and f"https://old.reddit.com{p.get('permalink')}") or "",
            "images": p.get("images") or [],
            "tags": [],
            "categories": [],
        },
        "comments": _normalize_comments(p.get("comments") or []),
        "profile": p.get("profile"),
        "label": p.get("label", ""),
    }


def _from_youtube(v: dict) -> dict:
    return {
        "id": v.get("tweet_id", ""),
        "platform": "youtube",
        "author": v.get("author_handle", ""),
        "author_name": v.get("author_name", ""),
        "content": {
            "title": v.get("tweet_text", ""),
            "body": v.get("description", ""),
        },
        "meta": {
            "likes": v.get("likes", 0) or 0,
            "replies": v.get("replies", 0) or 0,
            "retweets": 0,
            "views": v.get("view_count", 0) or 0,
            "duration": v.get("duration", 0) or 0,
            "timestamp": v.get("timestamp", ""),
            "url": v.get("webpage_url", ""),
            "images": v.get("images") or [],
            "tags": v.get("tags") or [],
            "categories": v.get("categories") or [],
        },
        "comments": _normalize_comments(v.get("comments") or []),
        "profile": None,
        "label": v.get("label", ""),
    }


def _normalize_comments(comments: list[dict]) -> list[dict]:
    """规范化评论列表。兼容三种平台的评论字段名差异。"""
    result = []
    for c in comments:
        result.append({
            "author": c.get("commenter_handle")
                      or c.get("author", ""),
            "content": c.get("text") or c.get("content", ""),
            "likes": c.get("likes", 0) or 0,
            "timestamp": c.get("timestamp", ""),
        })
    return result


def _normalize_twitter_profile(p: dict | None) -> dict | None:
    """轻微归一化 Twitter profile（保持一致键名）。"""
    if not p or not isinstance(p, dict):
        return p
    return {
        "bio": p.get("bio", ""),
        "followers": p.get("followers", 0) or 0,
        "following": p.get("following", 0) or 0,
        "verified": p.get("verified", False),
        "join_date": p.get("join_date", ""),
        "location": p.get("location", ""),
        "website": p.get("website", ""),
    }
