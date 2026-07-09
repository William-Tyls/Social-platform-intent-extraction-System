"""Twitter 推文提取的公共模块。

console.py 的 TwitterWorker 与 twitter_search_test.py 原本各维护一份
DOM 提取 JS,改版要改两处。这里集中维护,双方复用。

提取由 flags 字典控制字段开关,与 console.py 的 extraction 模板字段对齐:
    {author_name, author_handle, tweet_text, timestamp, tweet_id,
     likes, retweets, replies, images}
"""

from __future__ import annotations

# 默认全字段提取(向后兼容 twitter_search_test.py 的旧行为)
DEFAULT_FLAGS: dict = {
    "author_name": True,
    "author_handle": True,
    "tweet_text": True,
    "timestamp": True,
    "tweet_id": True,
    "likes": True,
    "retweets": True,
    "replies": True,
    "images": True,
}

# 单页 DOM 提取 JS。flags 由 Playwright 序列化为 JS 对象。
TWEET_EXTRACT_JS = """(flags) => {
    const articles = document.querySelectorAll('article[data-testid="tweet"]');
    const results = [];
    for (const el of articles) {
        const item = {};

        // tweet_id —— 从 /status/<id> 链接解析,作为去重主键
        let tid = '';
        const link = el.querySelector('a[href*="/status/"]');
        if (link) {
            const parts = link.href.split('/status/');
            tid = parts[1] ? parts[1].split('/')[0].split('?')[0] : '';
        }
        if (!tid) continue;  // 无 id 的卡片(广告/推荐等)跳过
        item.tweet_id = tid;

        if (flags.author_name) {
            const nameEl = el.querySelector('[data-testid="User-Name"] a span');
            item.author_name = nameEl ? nameEl.innerText.trim() : '';
        }
        if (flags.author_handle) {
            const handleEls = el.querySelectorAll('[data-testid="User-Name"] a');
            let author_handle = '';
            for (const a of handleEls) {
                const href = a.getAttribute('href') || '';
                if (href.startsWith('/') && !href.includes('/status/')) {
                    author_handle = href.replace('/', '').trim();
                    break;
                }
            }
            item.author_handle = author_handle;
        }
        if (flags.tweet_text) {
            const bodyEl = el.querySelector('[data-testid="tweetText"]');
            item.tweet_text = bodyEl ? bodyEl.innerText.trim() : '';
        }
        if (flags.timestamp) {
            const timeEl = el.querySelector('time');
            item.timestamp = timeEl ? timeEl.getAttribute('datetime') || '' : '';
        }

        const getCount = (testId) => {
            const e = el.querySelector('[data-testid="' + testId + '"]');
            if (!e) return 0;
            const aria = e.getAttribute('aria-label') || '';
            const match = aria.match(/([0-9,]+)/);
            if (match) return parseInt(match[1].replace(/,/g, '')) || 0;
            return 0;
        };
        if (flags.likes) item.likes = getCount('like');
        if (flags.retweets) item.retweets = getCount('retweet');
        if (flags.replies) item.replies = getCount('reply');

        if (flags.images) {
            const imgs = el.querySelectorAll(
                'img[src*="media"], [data-testid="tweetPhoto"] img, img[src*="pbs.twimg"]');
            item.images = Array.from(imgs).map(img => img.src);
        }

        results.push(item);
    }
    return results;
}"""


def extract_tweets(page, flags: dict | None = None) -> list[dict]:
    """在当前页面 DOM 上提取推文。flags 控制字段开关,默认全字段。"""
    return page.evaluate(TWEET_EXTRACT_JS, flags if flags is not None else DEFAULT_FLAGS)
