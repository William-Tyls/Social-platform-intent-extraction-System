"""Reddit 提取器 单元测试。

覆盖:
  - build_search_url: URL 参数组合
  - 全站 vs 版块搜索
  - 编码特殊字符

运行:
    python tools/tests/test_reddit_extractor.py              # 独立运行
    python -m pytest tools/tests/test_reddit_extractor.py -v  # pytest
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "extractors"))

from reddit_extractor import (
    BASE_URL,
    build_search_url,
    find_next_page_url,
)  # noqa: E402


# ====================================================================
# build_search_url
# ====================================================================

def test_build_url_all_default():
    url = build_search_url("test")
    assert url.startswith(f"{BASE_URL}/search")
    assert f"q={quote('test')}" in url
    assert "sort=relevance" in url
    assert "t=all" in url
    assert "limit=25" in url
    assert "restrict_sr=on" not in url  # all 模式不需要


def test_build_url_subreddit():
    url = build_search_url("python", subreddit="learnpython")
    assert url.startswith(f"{BASE_URL}/r/learnpython/search")
    assert f"q={quote('python')}" in url
    assert "restrict_sr=on" in url


def test_build_url_with_sort_and_time():
    url = build_search_url("ai", sort="top", time_filter="year")
    assert "sort=top" in url
    assert "t=year" in url


def test_build_url_with_limit():
    url = build_search_url("test", limit=50)
    assert "limit=50" in url


def test_build_url_empty_time_filter():
    """time_filter=None/'' 默认为 'all'。"""
    url = build_search_url("test", time_filter="")
    assert "t=all" in url


def test_build_url_special_characters():
    """特殊字符应被正确 URL 编码。"""
    url = build_search_url("C++ & Rust")
    assert quote("C++ & Rust") in url
    # 验证不抛出异常
    assert url.startswith("https://")


def test_build_url_chinese_characters():
    url = build_search_url("人工智能")
    assert quote("人工智能") in url


def test_build_url_subreddit_all_explicit():
    """subreddit='all' 与全站搜索等效。"""
    url = build_search_url("test", subreddit="all")
    assert "restrict_sr=on" not in url
    assert "/search?q=" in url


# ====================================================================
# find_next_page_url (纯 JS 的静态测试, 不需要浏览器)
# ====================================================================

def test_find_next_page_url_exists():
    """验证函数本身存在且可调用。"""
    assert callable(find_next_page_url)


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
