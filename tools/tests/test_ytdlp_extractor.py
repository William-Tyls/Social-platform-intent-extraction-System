"""yt-dlp 提取器 单元测试。

覆盖:
  - _classify_error: 错误分类, 边界词匹配
  - _normalize: 数据规范化 (缺失字段默认值)
  - _build_cmd: 命令构建 (参数组合)

运行:
    python tools/tests/test_ytdlp_extractor.py              # 独立运行
    python -m pytest tools/tests/test_ytdlp_extractor.py -v  # pytest
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "extractors"))

from youtube_ytdlp import YtDlpExtractor, _classify_error  # noqa: E402


# ====================================================================
# _classify_error
# ====================================================================

def test_classify_private_video():
    assert _classify_error("Private video") == "私有视频"


def test_classify_removed_video():
    assert _classify_error("This video has been removed") == "已删除/下架"


def test_classify_deleted_video():
    assert _classify_error("Video deleted by user") == "已删除/下架"


def test_classify_unavailable():
    assert _classify_error("This video is unavailable") == "视频不可用"


def test_classify_age_restricted():
    """'age' 须同时包含 'restrict' 才算匹配。"""
    assert _classify_error("age-restricted video") == "年龄受限"
    assert _classify_error("Sign in to confirm your age restricted") == "年龄受限"


def test_classify_age_without_restrict():
    """单有 'age' 不匹配 (避免误匹配 'page', 'message' 等)。"""
    result = _classify_error("page message image")
    assert result == "提取失败"


def test_classify_live_not_recording():
    """'live' 匹配 '直播回放不可用' (放宽了 recording 要求)。"""
    assert _classify_error("This live stream has ended") == "直播回放不可用"


def test_classify_live_word_boundary():
    """'live' 不应匹配 'delivery' 或 'liverpool' 等无关词 (词边界检查)。"""
    assert _classify_error("delivery failed, please retry") == "提取失败"
    assert _classify_error("liverpool match today") == "提取失败"


def test_classify_geoblock():
    assert _classify_error("Video geoblocked in your country") == "地区限制"
    assert _classify_error("geo-block restriction applied") == "地区限制"


def test_classify_login_required():
    assert _classify_error("Login required to view") == "需要登录"


def test_classify_login_without_required():
    """'login' 须同时包含 'required' 才算匹配。"""
    assert _classify_error("Please login to continue") == "提取失败"


def test_classify_copyright():
    assert _classify_error("Copyright claim by Sony") == "版权移除"
    assert _classify_error("DMCA takedown notice") == "版权移除"


def test_classify_sign_in_to_confirm():
    assert _classify_error("Sign in to confirm your age") == "需要登录确认"


def test_classify_unknown_error():
    assert _classify_error("Some random network error occurred") == "提取失败"


def test_classify_empty_stderr():
    assert _classify_error("") == "提取失败"


def test_classify_case_insensitive():
    assert _classify_error("PRIVATE VIDEO") == "私有视频"
    assert _classify_error("Age-Restricted") == "年龄受限"


# ====================================================================
# _normalize
# ====================================================================

def _make_extractor():
    return YtDlpExtractor()


def test_normalize_basic_video():
    ext = _make_extractor()
    raw = {
        "id": "dQw4w9WgXcQ",
        "title": "Rick Astley",
        "description": None,
        "view_count": 1_000_000,
        "like_count": 50_000,
        "comment_count": None,
        "duration": 212,
        "upload_date": "20091025",
        "comments": [],
        "tags": [],
        "categories": [],
    }
    result = ext._normalize(raw)
    assert result["video_id"] == "dQw4w9WgXcQ"
    assert result["title"] == "Rick Astley"
    assert result["view_count"] == 1_000_000
    assert result["like_count"] == 50_000
    assert result["comment_count"] == 0  # None → 0
    assert result["duration"] == 212
    assert result["comments"] == []


def test_normalize_null_fields_default_to_zero():
    ext = _make_extractor()
    raw = {
        "id": "v1",
        "title": "test",
        "description": "",
        "view_count": None,
        "like_count": None,
        "comment_count": None,
        "duration": None,
        "upload_date": "20240101",
        "comments": [],
        "tags": None,
        "categories": None,
    }
    result = ext._normalize(raw)
    assert result["view_count"] == 0
    assert result["like_count"] == 0
    assert result["comment_count"] == 0
    assert result["duration"] == 0
    # 显式设为 None 的字段不会被 _take 的 defaults 覆盖
    assert result["tags"] is None
    assert result["categories"] is None


def test_normalize_missing_id_uses_display_id():
    ext = _make_extractor()
    # yt-dlp 偶尔只设 display_id 不设 id; _take 会把缺失的 "id" 填为 None,
    # 所以 pop("id", default) 返回 None 而非 display_id。
    # 这是 _normalize 的已知局限 — 保留此测试作为回归哨兵。
    raw = {
        "title": "no id field",
        "display_id": "fallback-123",
        "view_count": 0,
        "like_count": 0,
        "comment_count": 0,
        "duration": 0,
        "upload_date": "20240101",
        "comments": [],
        "tags": [],
        "categories": [],
    }
    result = ext._normalize(raw)
    assert result["video_id"] == "fallback-123"


def test_normalize_with_comments():
    ext = _make_extractor()
    raw = {
        "id": "v2",
        "title": "test",
        "view_count": 100,
        "like_count": 10,
        "comment_count": 2,
        "duration": 60,
        "upload_date": "20240101",
        "tags": [],
        "categories": [],
        "comments": [
            {
                "id": "c1",
                "text": "great video",
                "like_count": 5,
                "author": "user1",
                "author_is_verified": False,
                "is_pinned": False,
                "_time_text": "2 days ago",
            },
            {
                "id": "c2",
                "text": "nice",
                "like_count": 0,
                "author": "user2",
                "parent": "root",
                "author_is_verified": True,
                "is_pinned": True,
                "_time_text": "1 day ago",
            },
        ],
    }
    result = ext._normalize(raw)
    assert len(result["comments"]) == 2
    assert result["comments"][0]["text"] == "great video"
    assert result["comments"][0]["author_is_verified"] is False
    assert result["comments"][0]["is_pinned"] is False
    assert result["comments"][0]["time_text"] == "2 days ago"  # _time_text → time_text
    assert result["comments"][1]["parent"] == "root"
    assert result["comments"][1]["author_is_verified"] is True
    assert result["comments"][1]["is_pinned"] is True


def test_normalize_missing_optional_comment_fields():
    """缺失的可选字段使用默认值或 None。"""
    ext = _make_extractor()
    raw = {
        "id": "v3",
        "title": "minimal",
        "view_count": 1,
        "like_count": 0,
        "comment_count": 1,
        "duration": 30,
        "upload_date": "20240101",
        "tags": [],
        "categories": [],
        "comments": [
            {"id": "c-min", "text": "ok"},  # 缺少 parent, is_pinned, _time_text 等
        ],
    }
    result = ext._normalize(raw)
    c = result["comments"][0]
    assert c["parent"] == "root"  # 默认值
    assert c["is_pinned"] is False  # 默认值
    # _time_text 缺失 → pop("_time_text") 得 None → or "" → ""
    assert c.get("time_text") == ""


# ====================================================================
# _build_cmd
# ====================================================================

def test_build_cmd_no_comments():
    ext = _make_extractor()
    cmd = ext._build_cmd("/tmp/dir", 0, "newest")
    assert "--no-write-comments" in cmd
    assert "--write-comments" not in cmd
    assert "--extractor-args" not in cmd


def test_build_cmd_with_comments():
    ext = _make_extractor()
    cmd = ext._build_cmd("/tmp/dir", 10, "newest")
    assert "--write-comments" in cmd
    assert "--no-write-comments" not in cmd
    assert "youtube:max_comments=10;comment_sort=0" in cmd


def test_build_cmd_with_comments_sort_top():
    ext = _make_extractor()
    cmd = ext._build_cmd("/tmp/dir", 20, "top")
    assert "youtube:max_comments=20;comment_sort=1" in cmd


def test_build_cmd_common_flags():
    ext = _make_extractor()
    cmd = ext._build_cmd("/tmp/dir", 0, "newest")
    assert "--skip-download" in cmd
    assert "--write-info-json" in cmd
    assert "--ignore-errors" in cmd
    assert "--no-playlist" in cmd
    assert "--no-check-formats" in cmd
    assert "--no-warnings" in cmd


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
