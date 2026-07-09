"""Reddit 数据提取器 — 基于 old.reddit.com 的 DOM 解析。

old.reddit.com 是服务端渲染 HTML，无需 JS 引擎即可提取数据。
所有提取器接收 Playwright Page 对象，返回标准化的 dict/list。

用法:
    from reddit_extractor import (
        build_search_url, extract_posts, extract_comments, extract_profile,
        find_next_page_url,
    )
"""

from __future__ import annotations

from urllib.parse import quote

BASE_URL = "https://old.reddit.com"

# ------------------------------------------------------------------
# URL 构建
# ------------------------------------------------------------------


def build_search_url(
    query: str,
    subreddit: str = "all",
    sort: str = "relevance",
    time_filter: str = "all",
    limit: int = 25,
) -> str:
    """构建 old.reddit.com 搜索 URL。

    subreddit="all" 表示全站搜索。
    """
    if subreddit != "all":
        return (
            f"{BASE_URL}/r/{subreddit}/search"
            f"?q={quote(query)}"
            f"&restrict_sr=on"
            f"&sort={sort}"
            f"&t={time_filter or 'all'}"
            f"&limit={limit}"
        )
    return (
        f"{BASE_URL}/search"
        f"?q={quote(query)}"
        f"&sort={sort}"
        f"&t={time_filter or 'all'}"
        f"&limit={limit}"
    )


# ------------------------------------------------------------------
# 帖子列表提取
# ------------------------------------------------------------------

_POST_EXTRACT_JS = """(flags) => {
    const results = [];
    const items = document.querySelectorAll('.search-result-link, .thing.link');
    for (const el of items) {
        if (el.classList.contains('promoted')) continue;

        const item = {};

        if (flags.tweet_id) {
            const idAttr = el.getAttribute('data-fullname') || '';
            item.tweet_id = idAttr.replace('t3_', '');
        }

        if (flags.author_name || flags.author_handle) {
            const authorEl = el.querySelector('.author');
            const author = authorEl ? authorEl.textContent.trim() : '[deleted]';
            if (flags.author_name) item.author_name = author;
            if (flags.author_handle) item.author_handle = author;
        }

        if (flags.tweet_text) {
            const titleEl = el.querySelector('a.title, a.search-title');
            item.tweet_text = titleEl ? titleEl.textContent.trim() : '';
        }

        if (flags.timestamp) {
            const timeEl = el.querySelector('time');
            item.timestamp = timeEl ? timeEl.getAttribute('datetime') || '' : '';
        }

        if (flags.likes) {
            const scoreEl = el.querySelector('.score.unvoted') || el.querySelector('.score');
            const scoreText = scoreEl ? scoreEl.textContent.trim() : '0';
            item.likes = parseInt(scoreText) || 0;
        }

        if (flags.replies) {
            const commentsEl = el.querySelector('.comments, .search-comments');
            const commentsText = commentsEl ? commentsEl.textContent.trim() : '0';
            const m = commentsText.match(/(\\d+)/);
            item.replies = m ? parseInt(m[1]) : 0;
        }

        if (flags.retweets) {
            item.retweets = 0;
        }

        // Reddit 特有字段
        const subredditEl = el.querySelector('.search-subreddit-link, .subreddit');
        item.subreddit = subredditEl ? subredditEl.textContent.trim() : '';

        const linkEl = el.querySelector('a.title, a.search-title');
        let permalink = linkEl ? linkEl.getAttribute('href') || '' : '';
        if (permalink && !permalink.startsWith('/')) {
            const m = permalink.match(/\/r\/[^\\/]+\\/comments\\/[^\\/]+\\/[^\\/]+/);
            permalink = m ? m[0] : permalink;
        }
        item.permalink = permalink;

        const domainEl = el.querySelector('.domain');
        const domain = domainEl ? domainEl.textContent.trim() : '';
        item.upvote_ratio = 0;
        item.post_url = domain ? 'https://' + domain.replace(/[\\(\\)]/g, '') : '';

        if (flags.images) {
            const thumbEl = el.querySelector('.thumbnail img');
            item.images = thumbEl ? [thumbEl.src] : [];
        } else {
            item.images = [];
        }

        results.push(item);
    }
    return results;
}"""


def extract_posts(page, flags: dict) -> list[dict]:
    """从 old.reddit.com 搜索结果页提取帖子列表。

    flags: 字段开关 dict，如 {"tweet_id": True, "author_name": True, ...}
          与 Twitter twitter_extractor.py 的 DEFAULT_FLAGS 字段对齐。
    """
    return page.evaluate(_POST_EXTRACT_JS, flags)


# ------------------------------------------------------------------
# 评论提取
# ------------------------------------------------------------------

_COMMENT_EXTRACT_JS = """(maxC) => {
    const results = [];
    const topLevel = document.querySelectorAll('.sitetable.nestedlisting > .thing.comment');
    for (let i = 0; i < Math.min(topLevel.length, maxC); i++) {
        const el = topLevel[i];
        if (el.classList.contains('deleted')) continue;

        const authorEl = el.querySelector('.author');
        const author = authorEl ? authorEl.textContent.trim() : '[deleted]';

        const bodyEl = el.querySelector('.usertext-body .md, .md');
        const text = bodyEl ? bodyEl.textContent.trim() : '';
        if (!text) continue;

        const timeEl = el.querySelector('time');
        const timestamp = timeEl ? timeEl.getAttribute('datetime') || '' : '';

        const scoreEl = el.querySelector('.score.unvoted') || el.querySelector('.score');
        const scoreText = scoreEl ? scoreEl.textContent.trim() : '0';

        results.push({
            commenter_name: author,
            commenter_handle: author,
            text: text,
            timestamp: timestamp,
            likes: parseInt(scoreText.match(/-?\\d+/)?.[0] || '0') || 0,
        });
    }
    return results;
}"""


def extract_comments(page, permalink: str, max_comments: int) -> list[dict]:
    """进入 old.reddit.com 帖子详情页，提取一级评论。

    需要先调用 page.goto(BASE_URL + permalink) 导航到详情页。
    """
    return page.evaluate(_COMMENT_EXTRACT_JS, max_comments)


# ------------------------------------------------------------------
# 用户主页提取
# ------------------------------------------------------------------

_PROFILE_EXTRACT_JS = """() => {
    const linkKarma = document.querySelector('.linkkarma .karma, .profile-karma .link span');
    const commentKarma = document.querySelector('.commentkarma .karma, .profile-karma .comment span');
    const created = document.querySelector('time:first-child');

    return {
        link_karma: parseInt((linkKarma?.textContent || '0').replace(/,/g, '')) || 0,
        comment_karma: parseInt((commentKarma?.textContent || '0').replace(/,/g, '')) || 0,
        join_date: created ? created.getAttribute('datetime') || '' : '',
        verified: false,
    };
}"""


def extract_profile(page, username: str) -> dict | None:
    """访问 old.reddit.com 用户主页提取信息。

    需要先调用 page.goto(f"{BASE_URL}/user/{username}") 导航到主页。
    返回 dict 含 link_karma, comment_karma, join_date 等,
    以及兼容控制台详情面板的 followers/following/bio 映射。
    """
    if username == "[deleted]":
        return None

    raw = page.evaluate(_PROFILE_EXTRACT_JS)
    if raw:
        raw["followers"] = raw["link_karma"]
        raw["following"] = raw["comment_karma"]
        raw["bio"] = f"发帖karma:{raw['link_karma']} | 评论karma:{raw['comment_karma']}"
    return raw


# ------------------------------------------------------------------
# 翻页
# ------------------------------------------------------------------

_NEXT_PAGE_JS = """() => {
    const selectors = [
        '.nextprev a[rel$="next"]',
        '.nextprev a:last-child',
        'span.next-button a',
        'span.nextprev a:last-child',
        '.nav-buttons a:last-child',
        'a[href*="after="]',
        'a[href*="count="]',
    ];
    for (const sel of selectors) {
        try {
            const el = document.querySelector(sel);
            if (el && el.href && el.href !== location.href) {
                return el.href;
            }
        } catch(e) {}
    }
    // 后备: 遍历所有链接
    const allLinks = document.querySelectorAll('a');
    for (const a of allLinks) {
        const h = a.getAttribute('href') || '';
        if ((h.includes('after=') || h.includes('count=')) && h !== location.href) {
            if (h.startsWith('/')) return 'https://old.reddit.com' + h;
            if (h.startsWith('http')) return h;
        }
    }
    return '';
}"""


def find_next_page_url(page) -> str:
    """在当前 old.reddit.com 搜索结果页查找"下一页"链接。返回空字符串表示已是最后一页。"""
    return page.evaluate(_NEXT_PAGE_JS)
