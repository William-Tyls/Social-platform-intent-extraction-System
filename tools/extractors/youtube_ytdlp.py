"""YouTube 数据提取层 — 基于 yt-dlp。

从视频 ID 列表提取: 标题、摘要、评论、评论人信息。
零 API Key、零配额、零代理。

依赖: pip install yt-dlp

用法:
    from youtube_ytdlp import YtDlpExtractor

    extractor = YtDlpExtractor()
    results = extractor.extract(["dQw4w9WgXcQ"], max_comments_per_video=10)
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable


# ------------------------------------------------------------------
# yt-dlp 错误关键词 → 中文原因
# ------------------------------------------------------------------

_ERROR_PATTERNS: list[tuple[str, str]] = [
    ("private video", "私有视频"),
    ("removed", "已删除/下架"), ("deleted", "已删除/下架"),
    ("unavailable", "视频不可用"),
    ("age", "年龄受限"),           # AND: "age" + "restrict"
    ("live", "直播回放不可用"),     # 放宽: 不需要 "recording"
    ("geoblock", "地区限制"), ("geo-block", "地区限制"),
    ("login", "需要登录"),          # AND: "login" + "required"
    ("copyright", "版权移除"), ("dmca", "版权移除"),
    ("sign in to confirm", "需要登录确认"),
]

_AND_CONDITIONS = {
    "age": "restrict",
    "login": "required",
}


def _classify_error(stderr_text: str) -> str:
    """从 yt-dlp stderr 归类失败原因,返回中文描述。"""
    lower = stderr_text.lower()
    for a, label in _ERROR_PATTERNS:
        if a not in lower:
            continue
        # 单字母关键词要求词边界 — 避免 "live" 匹配 "delivery" 等
        if len(a) <= 6 and a.isalpha() and not re.search(r'\b' + re.escape(a) + r'\b', lower):
            continue
        # 检查是否需要同时满足第二个条件
        if a in _AND_CONDITIONS:
            if _AND_CONDITIONS[a] not in lower:
                continue
        return label
    return "提取失败"


# ------------------------------------------------------------------
# 提取器
# ------------------------------------------------------------------

class YtDlpExtractor:
    """基于 yt-dlp 的数据提取器。"""

    def __init__(
        self,
        ytdlp_path: str | None = None,
        sleep_interval: int = 2,
        max_sleep_interval: int = 8,
        timeout_per_video: int = 60,
    ):
        self.ytdlp_path = ytdlp_path or self._find_ytdlp()
        self.sleep_interval = sleep_interval
        self.max_sleep_interval = max_sleep_interval
        self.timeout_per_video = timeout_per_video

    # ---- 自动查找 yt-dlp ----

    @staticmethod
    def _find_ytdlp() -> str:
        found = shutil.which("yt-dlp")
        if found:
            return found
        for p in [f"{sys.prefix}/bin/yt-dlp", os.path.expanduser("~/.local/bin/yt-dlp")]:
            if Path(p).exists():
                return p
        try:
            subprocess.run([sys.executable, "-m", "yt_dlp", "--version"],
                           capture_output=True, timeout=5)
            return f"{shlex.quote(sys.executable)} -m yt_dlp"
        except Exception:
            pass
        raise RuntimeError("找不到 yt-dlp。pip install yt-dlp")

    # ---- 核心 ----

    def extract(
        self,
        video_ids: list[str],
        max_comments_per_video: int = 10,
        comment_sort: str = "newest",
        on_progress: Callable | None = None,
        on_error: Callable | None = None,
        verbose: bool = True,
    ) -> list[dict]:
        """批量提取视频数据。

        on_progress: (current: int, total: int, video_id: str) -> None
        on_error:    (errors: list[str], total: int) -> None — 替代 stdout
        verbose:     False 抑制库内 print
        """
        if not video_ids:
            return []

        total = len(video_ids)
        results: list[dict] = []
        errors: list[str] = []

        for batch_start in range(0, total, 20):
            batch = video_ids[batch_start : batch_start + 20]

            with tempfile.TemporaryDirectory() as tmpdir:
                batch_results, batch_errors = self._extract_batch(
                    batch, tmpdir, max_comments_per_video, comment_sort,
                )
                results.extend(batch_results)
                errors.extend(batch_errors)

                if on_progress:
                    success_ids = {r["video_id"] for r in batch_results}
                    for i, vid in enumerate(batch):
                        label = vid if vid in success_ids else f"{vid} (失败)"
                        on_progress(batch_start + i + 1, total, label)

        if errors:
            if on_error:
                on_error(errors, total)
            elif verbose:
                print(f"\n  ⚠️ {len(errors)}/{total} 个视频提取失败")
                for e in errors[:5]:
                    print(f"     {e}")

        return results

    def _extract_batch(
        self,
        video_ids: list[str],
        tmpdir: str,
        max_comments: int,
        comment_sort: str,
    ) -> tuple[list[dict], list[str]]:
        """在临时目录处理一批视频,返回 (成功结果, 失败列表)。"""

        urls = [f"https://www.youtube.com/watch?v={vid}" for vid in video_ids]
        cmd = self._build_cmd(tmpdir, max_comments, comment_sort) + urls

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=len(video_ids) * self.timeout_per_video,
                env={**os.environ, "LC_ALL": "en_US.UTF-8"},
            )
        except subprocess.TimeoutExpired:
            return [], [f"批次超时 ({len(video_ids)} 个视频)"]

        return self._parse_batch_output(video_ids, tmpdir, proc.stderr)

    def _build_cmd(self, tmpdir: str, max_comments: int, comment_sort: str) -> list[str]:
        """构建 yt-dlp 子进程命令。"""
        cmd = shlex.split(self.ytdlp_path) if " " in self.ytdlp_path else [self.ytdlp_path]

        cmd += [
            "--write-comments" if max_comments > 0 else "--no-write-comments",
            "--skip-download",
            "--write-info-json",
            "--ignore-errors",
            "--no-playlist",
            "--no-check-formats",
            "--no-warnings",
            "--sleep-interval", str(self.sleep_interval),
            "--max-sleep-interval", str(self.max_sleep_interval),
            "-o", f"{tmpdir}/%(id)s.%(ext)s",
        ]

        if max_comments > 0:
            sort = "0" if comment_sort == "newest" else "1"
            cmd += ["--extractor-args", f"youtube:max_comments={max_comments};comment_sort={sort}"]

        return cmd

    def _parse_batch_output(
        self, video_ids: list[str], tmpdir: str, stderr: str
    ) -> tuple[list[dict], list[str]]:
        """解析 yt-dlp 输出文件,分离成功和失败。"""
        results: list[dict] = []
        errors: list[str] = []

        for vid in video_ids:
            info_path = Path(tmpdir) / f"{vid}.info.json"
            if info_path.exists():
                try:
                    data = json.loads(info_path.read_text(encoding="utf-8"))
                    results.append(self._normalize(data))
                except (json.JSONDecodeError, OSError):
                    errors.append(f"{vid}: JSON 解析失败")
            else:
                reason = _classify_error(stderr)
                errors.append(f"{vid}: {reason}")

        return results, errors

    # ---- 数据规范化 ----

    _COMMENT_FIELDS = [
        "id", "parent", "text", "like_count", "author", "author_id",
        "author_url", "author_is_verified", "author_thumbnail",
        "is_pinned", "timestamp", "_time_text",
    ]

    _VIDEO_FIELDS = [
        "id", "title", "description", "uploader", "uploader_id",
        "channel_id", "uploader_url", "view_count", "like_count",
        "comment_count", "duration", "upload_date", "tags", "categories",
        "thumbnail", "webpage_url",
    ]

    def _normalize(self, raw: dict) -> dict:
        """将 yt-dlp 原始 JSON 扁平化为结构化 dict。"""

        def _take(d: dict, keys: list[str], defaults: dict | None = None) -> dict:
            """从 dict 中提取指定 key,用 defaults 填充默认值。"""
            defaults = defaults or {}
            return {k: d.get(k, defaults.get(k)) for k in keys}

        comments = []
        for c in raw.get("comments", []):
            # yt-dlp 内部用 _time_text, 我们映射为 time_text
            norm = _take(c, self._COMMENT_FIELDS, {"parent": "root", "is_pinned": False})
            norm["time_text"] = norm.pop("_time_text", "")
            comments.append(norm)

        video = _take(raw, self._VIDEO_FIELDS, {"tags": [], "categories": []})
        video["video_id"] = video.pop("id", raw.get("display_id", ""))
        video["comments"] = comments

        # 处理 None 值
        for k in ("view_count", "like_count", "comment_count", "duration"):
            if video.get(k) is None:
                video[k] = 0

        return video
