"""YouTube Data API v3 — 仅负责搜索发现 video_id。

配额: search.list = 100 units/次 (每次最多 50 条), 10,000 units/天。

用法:
    from youtube_api import YouTubeAPI
    api = YouTubeAPI()
    ids = api.discover("World Cup", count=100)  # → 100 个 video_id
"""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path

_QUOTA_FILE = Path.home() / ".cloakbrowser_youtube_quota.json"
_QUOTA_PER_SEARCH = 100
_DAILY_LIMIT = 10_000

_LOCK_TIMEOUT = 1.0  # 获取文件锁的最大等待秒数


class QuotaTracker:
    """跨进程持久化配额追踪, 按天自动重置, 带文件锁防并发写。

    使用 fcntl.flock 保证读-改-写原子性:
    - 两个进程同时 add_used() 时, 后到的等待锁释放
    - 最多等待 _LOCK_TIMEOUT 秒, 超时抛出 RuntimeError
    - macOS/Linux 可用; Windows 不支持 fcntl
    """

    def __init__(self, file_path: Path = _QUOTA_FILE, daily_limit: int = _DAILY_LIMIT):
        self.file_path = file_path
        self.daily_limit = daily_limit

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _locked_read_write(self, amount: int, source: str) -> int:
        """加锁 → 读取 → 累加 → 回写 → 释放锁。返回更新后的 used 值。"""
        # 确保文件存在
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.touch(exist_ok=True)

        with open(self.file_path, "r+", encoding="utf-8") as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                # 等待最多 _LOCK_TIMEOUT 秒
                import time as _time
                deadline = _time.monotonic() + _LOCK_TIMEOUT
                locked = False
                while _time.monotonic() < deadline:
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                        break
                    except (BlockingIOError, OSError):
                        _time.sleep(0.05)
                if not locked:
                    raise RuntimeError(
                        f"无法获取配额文件锁 (超时 {_LOCK_TIMEOUT}s), 可能有僵尸进程持有锁"
                    )

            try:
                # 读取
                try:
                    f.seek(0)
                    data = json.loads(f.read() or "{}")
                except json.JSONDecodeError:
                    data = {}

                # 按天重置
                today = self._today()
                if data.get("date") != today:
                    data = {"date": today, "used": 0}

                # 累加
                data["used"] = data.get("used", 0) + amount
                data["_last_source"] = source
                data["_last_update"] = datetime.now(timezone.utc).isoformat()

                # 回写
                f.seek(0)
                f.truncate()
                json.dump(data, f)
                f.flush()

                return data["used"]
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def get_used_today(self) -> int:
        """获取今天已累计消耗的配额(无锁读取, 允许最终一致性)。"""
        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return 0
        if data.get("date") != self._today():
            return 0
        return data.get("used", 0)

    def get_remaining(self) -> int:
        return max(0, self.daily_limit - self.get_used_today())

    def add_used(self, amount: int, source: str = "search") -> int:
        """增加消耗量, 返回今日累计消耗。加锁保证并发安全。"""
        return self._locked_read_write(amount, source)


def get_quota_remaining() -> int:
    """便捷函数: 返回当前剩余配额。"""
    return QuotaTracker().get_remaining()


class YouTubeAPI:
    """YouTube Data API v3 轻量封装, 只做搜索发现, 带跨进程配额追踪。

    api = YouTubeAPI()
    ids = api.discover("World Cup", count=100)    # → 100 个 video_id
    print(f"本次: {api.quota_used}, 今日累计: {api.quota_used_today}")
    """

    def __init__(self, api_key: str | None = None):
        key = api_key or os.environ.get("YOUTUBE_API_KEY", "")
        if not key:
            raise ValueError(
                "需要 YouTube Data API v3 密钥。\n"
                "  1. https://console.cloud.google.com/apis/credentials\n"
                "  2. 创建 API 密钥 → 启用 YouTube Data API v3\n"
                "  3. export YOUTUBE_API_KEY=your_key"
            )
        self.api_key = key
        self._service = None
        self.quota_used: int = 0           # 本次会话消耗
        self.quota_used_today: int = 0     # 今日累计消耗(含其他进程)
        self._tracker = QuotaTracker()

    @property
    def service(self):
        if self._service is None:
            try:
                from googleapiclient.discovery import build
            except ImportError:
                raise ImportError("需要 google-api-python-client: pip install google-api-python-client")
            self._service = build("youtube", "v3", developerKey=self.api_key)
        return self._service

    def discover(
        self,
        query: str,
        count: int = 50,
        order: str = "relevance",
        region_code: str | None = None,
    ) -> list[str]:
        """搜索并返回 video_id 列表。自动持久化配额消耗。

        配额: 100 units × ceil(count / 50)。

        返回: ["dQw4w9WgXcQ", "jfKfPfyJRdk", ...]
        """
        ids: list[str] = []
        page_token = None

        while len(ids) < count:
            # maxResults capped at 50 (YouTube API limit); quota is per-request
            # so there's no benefit to requesting fewer than 50
            batch = 50

            params = {
                "part": "snippet",
                "q": query,
                "maxResults": batch,
                "type": "video",
                "order": order,
                "safeSearch": "none",
            }
            if region_code:
                params["regionCode"] = region_code
            if page_token:
                params["pageToken"] = page_token

            resp = self.service.search().list(**params).execute()
            self.quota_used += _QUOTA_PER_SEARCH

            new_count = 0
            for item in resp.get("items", []):
                vid = (item.get("id") or {}).get("videoId", "")
                if vid:
                    ids.append(vid)
                    new_count += 1

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

            if new_count < 20 and len(ids) < count and page_token:
                continue

        # 持久化配额消耗到磁盘, 跨进程共享
        self.quota_used_today = self._tracker.add_used(self.quota_used)

        return ids[:count]
