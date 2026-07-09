"""YouTube 数据采集 — API 搜索 → yt-dlp 提取。

用法:
    python tools/youtube_run.py "World Cup" -n 100 -c 10  搜索+提取
    python tools/youtube_run.py --ids dQw4w9WgXcQ -c 20   已有 ID
    python tools/youtube_run.py "World Cup" -n 50 -c 0    只拿元数据

环境变量: YOUTUBE_API_KEY (仅搜索需要)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "extractors"))
from _env import load_env; load_env()


def main():
    args = _parse_args()

    # ---- 获取 video_id 列表 ----
    if args.ids:
        video_ids = [v.strip() for v in args.ids.split(",") if v.strip()]
        print(f"📋 直接提取: {len(video_ids)} 个 ID")
    elif args.query:
        video_ids = _discover(args)
        if not video_ids:
            print("❌ 搜索结果为空")
            return
    else:
        print("❌ 需要搜索关键词 或 --ids")
        return

    # ---- 去重 ----
    from youtube_dedup import DedupStore
    db = DedupStore(args.dedup_db)
    video_ids = db.filter_new(video_ids)
    if not video_ids:
        print("全部已处理过(去重),无需提取")
        db.close()
        return
    print(f"  去重后: {len(video_ids)} 个新视频")

    # ---- 提取 ----
    from youtube_ytdlp import YtDlpExtractor

    print(f"\n{'='*60}")
    comment_hint = f"(每个视频 {args.comments} 条评论)" if args.comments else "(仅元数据)"
    print(f"yt-dlp 提取: {len(video_ids)} 个视频 {comment_hint}")
    print(f"{'='*60}")

    t0 = time.time()
    extractor = YtDlpExtractor()

    results = extractor.extract(
        video_ids,
        max_comments_per_video=args.comments,
        on_progress=lambda c, t, v: print(
            f"\r  [{c*20//max(t,1)*'█' + (20 - c*20//max(t,1))*'░'}] {c}/{t}  {v[:20]}",
            end="", flush=True),
    )
    elapsed = time.time() - t0
    print(f"\n  完成! 耗时 {elapsed:.0f}s")

    # ---- 保存 ----
    out_dir = Path(args.output).parent if args.output else Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output or str(out_dir / f"youtube_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    record = {
        "extracted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "search_query": args.query or None,
        "count": len(results),
        "comments_per_video": args.comments,
        "elapsed_seconds": round(elapsed, 1),
        "videos": results,
    }
    Path(out_path).write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

    # 更新去重
    db.batch_mark_seen([r["video_id"] for r in results])
    db.close()

    # ---- 摘要 ----
    _summary(results)
    print(f"\n已保存: {out_path}")


# ======================================================================
# helpers
# ======================================================================

def _parse_args():
    p = argparse.ArgumentParser(description="YouTube 数据采集 — API + yt-dlp")
    p.add_argument("query", nargs="?", default="", help="搜索关键词")
    p.add_argument("--ids", default="", help="视频 ID 列表(逗号分隔)")
    p.add_argument("-n", "--count", type=int, default=100)
    p.add_argument("-c", "--comments", type=int, default=10, help="评论数/视频(0=不要)")
    p.add_argument("--region", default="US")
    p.add_argument("--order", default="relevance",
                   choices=["relevance", "date", "rating", "viewCount"])
    p.add_argument("-o", "--output", default="")
    p.add_argument("--dedup-db", default="youtube_dedup.db")
    return p.parse_args()


def _discover(args) -> list[str]:
    from youtube_api import YouTubeAPI

    api = YouTubeAPI()
    print(f"{'='*60}")
    print(f"API 搜索: \"{args.query}\" (地区:{args.region} 排序:{args.order})")
    print(f"目标: {args.count} 个 video_id")
    print(f"{'='*60}")

    ids = api.discover(args.query, count=args.count, order=args.order, region_code=args.region)
    print(f"  发现 {len(ids)} 个 | 本次: {api.quota_used} | 今日累计: {api.quota_used_today} / 10,000 units")
    return ids


def _summary(results: list[dict]):
    print(f"\n{'='*60}\n📋 {len(results)} 个视频\n{'='*60}")
    for i, v in enumerate(results[:5]):
        title = (v.get("title") or "无标题")[:70]
        uploader = v.get("uploader", "?")
        views = v.get("view_count", 0) or 0
        n_com = len(v.get("comments", []))
        print(f"\n  [{i+1}] {title}")
        print(f"      {uploader} | 播放:{views:,} | 评论:{n_com} 条")
        if v.get("comments"):
            c = v["comments"][0]
            print(f"      首评: [{c['author']}] {c['text'][:60]}...")


if __name__ == "__main__":
    main()
