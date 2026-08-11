import logging

import feedparser

log = logging.getLogger(__name__)

NEWS_URL = (
    "https://news.google.com/rss/search?q={query}+stock+NSE+India"
    "&hl=en-IN&gl=IN&ceid=IN:en"
)
MAX_ARTICLES = 5


def get_recent_news(ticker: str) -> str:
    query = ticker.replace(".NS", "")
    url = NEWS_URL.format(query=query)
    try:
        feed = feedparser.parse(url)
        entries = feed.entries[:MAX_ARTICLES]
        if not entries:
            return "No recent news found."
        lines = []
        for e in entries:
            published = e.get("published", "unknown date")
            title = e.get("title", "").strip()
            lines.append(f"- {title} ({published})")
        return "\n".join(lines)
    except Exception as exc:
        log.warning("News fetch failed for %s: %s", ticker, exc)
        return "No recent news found."
