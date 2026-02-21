"""
market.py — Polygon.io API wrapper
Fetches: ticker details, current price, OHLCV history, news headlines
Free tier note: 15-minute delayed prices, 5 API calls/minute
"""
import os
import httpx
from datetime import datetime, timedelta

POLYGON_KEY = os.environ.get("POLYGON_API_KEY")
BASE = "https://api.polygon.io"


async def get_ticker_details(ticker: str) -> dict:
    """Company name, description, market cap, sector."""
    url = f"{BASE}/v3/reference/tickers/{ticker.upper()}"
    async with httpx.AsyncClient() as client:
        res = await client.get(url, params={"apiKey": POLYGON_KEY})
        data = res.json()
    if "results" not in data:
        return {}
    r = data["results"]
    return {
        "ticker": r.get("ticker"),
        "name": r.get("name"),
        "description": r.get("description", "")[:500],
        "market_cap": r.get("market_cap"),
        "sector": r.get("sic_description"),
        "employees": r.get("total_employees"),
        "website": r.get("homepage_url"),
        "logo": r.get("branding", {}).get("icon_url"),
    }


async def get_snapshot(ticker: str) -> dict:
    """Current price snapshot — 15min delayed on free tier."""
    url = f"{BASE}/v2/snapshot/locale/us/markets/stocks/tickers/{ticker.upper()}"
    async with httpx.AsyncClient() as client:
        res = await client.get(url, params={"apiKey": POLYGON_KEY})
        data = res.json()
    if "ticker" not in data:
        return {}
    t = data["ticker"]
    day = t.get("day", {})
    prev = t.get("prevDay", {})
    return {
        "ticker": ticker.upper(),
        "price": t.get("lastTrade", {}).get("p") or day.get("c"),
        "open": day.get("o"),
        "high": day.get("h"),
        "low": day.get("l"),
        "close": day.get("c"),
        "volume": day.get("v"),
        "prev_close": prev.get("c"),
        "change": t.get("todaysChange"),
        "change_pct": t.get("todaysChangePerc"),
    }


async def get_price_history(ticker: str, range: str = "1M") -> list[dict]:
    """
    OHLCV bars for charting.
    range options: 1D, 1W, 1M, 1Y
    Returns list of {date, open, high, low, close, volume}
    """
    today = datetime.now()
    range_map = {
        "1D": (today - timedelta(days=1), "minute", 5),
        "1W": (today - timedelta(weeks=1), "hour", 1),
        "1M": (today - timedelta(days=30), "day", 1),
        "1Y": (today - timedelta(days=365), "day", 1),
    }
    from_date, timespan, multiplier = range_map.get(range, range_map["1M"])
    from_str = from_date.strftime("%Y-%m-%d")
    to_str = today.strftime("%Y-%m-%d")

    url = f"{BASE}/v2/aggs/ticker/{ticker.upper()}/range/{multiplier}/{timespan}/{from_str}/{to_str}"
    async with httpx.AsyncClient() as client:
        res = await client.get(url, params={"apiKey": POLYGON_KEY, "adjusted": "true", "sort": "asc", "limit": 500})
        data = res.json()

    if "results" not in data:
        return []

    bars = []
    for bar in data["results"]:
        ts = bar["t"] / 1000  # ms to seconds
        dt = datetime.fromtimestamp(ts)
        bars.append({
            "date": dt.strftime("%Y-%m-%d %H:%M" if timespan == "minute" else "%Y-%m-%d"),
            "open": bar.get("o"),
            "high": bar.get("h"),
            "low": bar.get("l"),
            "close": bar.get("c"),
            "volume": bar.get("v"),
        })
    return bars


async def get_news(ticker: str, limit: int = 8) -> list[dict]:
    """Latest news headlines for a ticker."""
    url = f"{BASE}/v2/reference/news"
    async with httpx.AsyncClient() as client:
        res = await client.get(url, params={
            "ticker": ticker.upper(),
            "limit": limit,
            "order": "desc",
            "sort": "published_utc",
            "apiKey": POLYGON_KEY,
        })
        data = res.json()

    if "results" not in data:
        return []

    articles = []
    for article in data["results"]:
        articles.append({
            "title": article.get("title"),
            "publisher": article.get("publisher", {}).get("name"),
            "published": article.get("published_utc", "")[:10],
            "url": article.get("article_url"),
            "summary": article.get("description", "")[:200],
            "sentiment": article.get("insights", [{}])[0].get("sentiment") if article.get("insights") else None,
        })
    return articles


async def search_tickers(query: str) -> list[dict]:
    """Search for tickers by company name or symbol."""
    url = f"{BASE}/v3/reference/tickers"
    async with httpx.AsyncClient() as client:
        res = await client.get(url, params={
            "search": query,
            "active": "true",
            "market": "stocks",
            "limit": 8,
            "apiKey": POLYGON_KEY,
        })
        data = res.json()

    if "results" not in data:
        return []

    return [
        {"ticker": r["ticker"], "name": r.get("name", ""), "market": r.get("market", "")}
        for r in data["results"]
    ]
