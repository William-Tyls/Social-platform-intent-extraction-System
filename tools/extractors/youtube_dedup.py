"""YouTube 视频去重 — 基于 SQLite 的跨进程 ID 去重。

多 Project / 多浏览器实例并行采集时,保证同一个视频不会被重复处理。

用法:
    from youtube_dedup import DedupStore

    store = DedupStore("youtube.db")
    new_ids = store.filter_new(video_ids)      # 过滤已处理的
    # ... 提取数据 ...
    store.batch_mark_seen(processed_ids)        # 标记已处理
    store.close()

    # 或使用 with 语句自动关闭
    with DedupStore("youtube.db") as store:
        new_ids = store.filter_new(video_ids)
        ...
        store.batch_mark_seen(processed_ids)
"""

from __future__ import annotations

import sqlite3
from typing import Self


class DedupStore:
    """轻量去重存储。WAL 模式,多读单写,并发友好。"""

    def __init__(self, db_path: str = "youtube_dedup.db"):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS seen (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL DEFAULT 'video',
                    source TEXT DEFAULT 'unknown'
                )
            """)
            self._conn.commit()
        return self._conn

    def filter_new(self, ids: list[str]) -> list[str]:
        """过滤: 返回 ids 中未在 seen 表里的部分。"""
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        existing = {
            row[0]
            for row in self.conn.execute(
                f"SELECT id FROM seen WHERE id IN ({placeholders})", ids
            ).fetchall()
        }
        return [vid for vid in ids if vid not in existing]

    def batch_mark_seen(
        self, ids: list[str], item_type: str = "video", source: str = "unknown"
    ):
        """批量标记已处理。重复项静默忽略。"""
        if not ids:
            return
        self.conn.executemany(
            "INSERT OR IGNORE INTO seen(id, type, source) VALUES(?, ?, ?)",
            [(vid, item_type, source) for vid in ids],
        )
        self.conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
