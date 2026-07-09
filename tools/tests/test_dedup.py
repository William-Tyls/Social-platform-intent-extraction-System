"""DedupStore 单元测试。

覆盖:
  - filter_new: 过滤已见 ID
  - batch_mark_seen: 批量标记 (executemany)
  - 幂等性: 重复标记不报错
  - 上下文管理器: __enter__ / __exit__
  - 空输入处理

运行:
    python tools/tests/test_dedup.py              # 独立运行
    python -m pytest tools/tests/test_dedup.py -v  # pytest
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "extractors"))

from youtube_dedup import DedupStore  # noqa: E402


# ---- filter_new ----

def test_filter_new_returns_all_when_empty_db():
    store = DedupStore(tempfile.mktemp(suffix=".db"))
    try:
        result = store.filter_new(["a", "b", "c"])
        assert result == ["a", "b", "c"]
    finally:
        store.close()


def test_filter_new_excludes_existing():
    store = DedupStore(tempfile.mktemp(suffix=".db"))
    try:
        store.batch_mark_seen(["a", "c"])
        result = store.filter_new(["a", "b", "c", "d"])
        assert result == ["b", "d"]
    finally:
        store.close()


def test_filter_new_empty_input():
    store = DedupStore(tempfile.mktemp(suffix=".db"))
    try:
        assert store.filter_new([]) == []
    finally:
        store.close()


def test_filter_new_all_existing():
    store = DedupStore(tempfile.mktemp(suffix=".db"))
    try:
        store.batch_mark_seen(["x", "y"])
        assert store.filter_new(["x", "y"]) == []
    finally:
        store.close()


# ---- batch_mark_seen ----

def test_batch_mark_seen_persists():
    store = DedupStore(tempfile.mktemp(suffix=".db"))
    try:
        store.batch_mark_seen(["v1", "v2", "v3"])
        assert store.filter_new(["v1", "v2", "v3", "v4"]) == ["v4"]
    finally:
        store.close()


def test_batch_mark_seen_empty_input():
    store = DedupStore(tempfile.mktemp(suffix=".db"))
    try:
        store.batch_mark_seen([])  # 不抛异常
    finally:
        store.close()


def test_batch_mark_seen_idempotent():
    """重复插入同一 ID 不报错 (INSERT OR IGNORE)。"""
    store = DedupStore(tempfile.mktemp(suffix=".db"))
    try:
        store.batch_mark_seen(["dup"])
        store.batch_mark_seen(["dup", "dup"])
        assert store.filter_new(["dup"]) == []
    finally:
        store.close()


def test_batch_mark_seen_custom_type():
    store = DedupStore(tempfile.mktemp(suffix=".db"))
    try:
        store.batch_mark_seen(["v1"], item_type="short", source="api")
        # 可以重复标记, 不影响
        store.batch_mark_seen(["v1"], item_type="short", source="api")
    finally:
        store.close()


# ---- 上下文管理器 ----

def test_context_manager_auto_closes():
    path = tempfile.mktemp(suffix=".db")
    with DedupStore(path) as store:
        store.batch_mark_seen(["c1"])
        assert store._conn is not None
    # 退出 with 后连接已关闭
    assert store._conn is None


def test_context_manager_can_reopen():
    """关闭后重新 with 应正常工作。"""
    path = tempfile.mktemp(suffix=".db")
    with DedupStore(path) as store:
        store.batch_mark_seen(["r1"])
    with DedupStore(path) as store2:
        assert store2.filter_new(["r1"]) == []


# ---- 跨实例持久化 ----

def test_persistence_across_instances():
    path = tempfile.mktemp(suffix=".db")
    store1 = DedupStore(path)
    try:
        store1.batch_mark_seen(["p1", "p2"])
        store1.close()

        store2 = DedupStore(path)
        try:
            assert store2.filter_new(["p1", "p2", "p3"]) == ["p3"]
        finally:
            store2.close()
    finally:
        if store1._conn:
            store1.close()


# ---- 大数量批量 ----

def test_large_batch_executemany():
    """验证 executemany 正确处理大批量。"""
    path = tempfile.mktemp(suffix=".db")
    large_ids = [f"video_{i:04d}" for i in range(500)]
    with DedupStore(path) as store:
        store.batch_mark_seen(large_ids)
        assert store.filter_new(large_ids) == []
        assert store.filter_new(["video_0500"]) == ["video_0500"]


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
