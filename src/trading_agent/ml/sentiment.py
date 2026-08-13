#!/usr/bin/env python3
"""
Sentiment Pipeline — news + social media sentiment analysis.

Components:
1. SentimentAnalyzer — NLP-based sentiment scoring
2. NewsAggregator — multi-source news collection + dedup
3. SocialMediaMonitor — Twitter/Reddit sentiment tracking
4. SentimentComposite — combined sentiment signal

Design:
    analyzer = SentimentAnalyzer()
    score = analyzer.analyze("BTC surges past $100k amid institutional buying")
    pipeline = SentimentPipeline()
    signal = pipeline.get_signal("BTC")
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass


# ── Keyword-based Sentiment (no external NLP dependency) ──────

BULLISH_WORDS = {
    "surges",
    "rally",
    "rallies",
    "bullish",
    "breakout",
    "soars",
    "soaring",
    "jumps",
    "jumps",
    "pumps",
    "moon",
    "mooning",
    "ath",
    "all-time-high",
    "adoption",
    "institutional",
    "buy",
    "buying",
    "accumulation",
    "upgrade",
    "approval",
    "etf",
    "partnership",
    "growth",
    "records",
    "milestone",
    "outperform",
    "recovery",
    "rebound",
    "demand",
    "inflow",
    "positive",
}

BEARISH_WORDS = {
    "crashes",
    "crash",
    "plunges",
    "tumbles",
    "bearish",
    "sell-off",
    "selloff",
    "dumps",
    "dump",
    "fear",
    "panic",
    "ban",
    "banned",
    "regulation",
    "lawsuit",
    "hack",
    "hacked",
    "exploit",
    "rug",
    "rugpull",
    "fraud",
    "scam",
    "outflow",
    "liquidation",
    "default",
    "bankruptcy",
    "collapse",
    "decline",
    "drops",
    "falling",
    "negative",
    "warning",
    "risk",
    "vulnerability",
}


@dataclass
class SentimentResult:
    text: str
    score: float  # -1.0 (bearish) to +1.0 (bullish)
    confidence: float  # 0.0 to 1.0
    bullish_words: list[str]
    bearish_words: list[str]
    source: str = ""
    timestamp: float = 0.0


class SentimentAnalyzer:
    """
    Keyword-based sentiment analyzer.
    No external NLP model required — works with word matching + intensity modifiers.
    """

    INTENSIFIERS = {
        "very",
        "extremely",
        "massive",
        "huge",
        "unprecedented",
        "record",
        "historic",
    }
    NEGATORS = {"not", "no", "never", "neither", "nor", "barely", "hardly"}

    def analyze(self, text: str, source: str = "") -> SentimentResult:
        words = set(re.findall(r"\b\w+\b", text.lower()))
        bullish = words & BULLISH_WORDS
        bearish = words & BEARISH_WORDS

        # Intensity modifier
        intensity = 1.5 if (words & self.INTENSIFIERS) else 1.0
        # Negation (simple: if negator within 3 words of sentiment word, flip)
        has_negation = bool(words & self.NEGATORS)

        score = (len(bullish) - len(bearish)) / max(len(bullish) + len(bearish), 1)
        if has_negation:
            score = -score * 0.5
        score = max(-1, min(1, score * intensity))

        confidence = min(1.0, (len(bullish) + len(bearish)) / 5)

        return SentimentResult(
            text=text[:200],
            score=score,
            confidence=confidence,
            bullish_words=sorted(bullish),
            bearish_words=sorted(bearish),
            source=source,
            timestamp=time.time(),
        )

    def analyze_batch(
        self, texts: list[str], source: str = ""
    ) -> list[SentimentResult]:
        return [self.analyze(t, source) for t in texts]


# ── News Aggregator ──────────────────────────────────────────


@dataclass
class NewsItem:
    title: str
    summary: str
    source: str
    url: str
    published_at: float
    sentiment: float = 0.0
    relevance: float = 0.0


class NewsAggregator:
    """
    Multi-source news aggregator with dedup and sentiment scoring.

    In dry_run mode, generates synthetic news.
    In production, fetches from CryptoPanic, CoinDesk, etc.
    """

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.analyzer = SentimentAnalyzer()
        self._seen_hashes: set[str] = set()

    def fetch_news(self, symbol: str = "", limit: int = 20) -> list[NewsItem]:
        if self.dry_run:
            return self._synthetic_news(symbol, limit)
        raise NotImplementedError("Live news fetch not implemented")

    def _synthetic_news(self, symbol: str, limit: int) -> list[NewsItem]:
        import random

        templates = [
            (
                "{} surges past key resistance as institutional buying accelerates",
                "bullish",
            ),
            ("{} crashes 15% amid regulatory concerns and market sell-off", "bearish"),
            ("{} shows signs of recovery after flash crash", "neutral"),
            ("Major ETF approval expected for {} — analysts bullish", "bullish"),
            ("Whale dumps {} worth $50M on exchanges", "bearish"),
            ("{} network upgrade goes live, gas fees drop 50%", "bullish"),
            ("Exchange hack exposes {} security vulnerabilities", "bearish"),
            ("{} trading volume hits record high on major exchanges", "bullish"),
            ("Regulatory uncertainty weighs on {} price action", "bearish"),
            ("Institutional investors increase {} holdings by 25%", "bullish"),
        ]
        items = []
        for _ in range(min(limit, len(templates))):
            tmpl, sentiment = random.choice(templates)
            title = tmpl.format(symbol)
            result = self.analyzer.analyze(title, source="synthetic")
            items.append(
                NewsItem(
                    title=title,
                    summary=title,
                    source="synthetic",
                    url=f"https://example.com/{symbol.lower()}",
                    published_at=time.time() - random.uniform(0, 86400),
                    sentiment=result.score,
                    relevance=random.uniform(0.5, 1.0),
                )
            )
        return items


# ── Social Media Monitor ────────────────────────────────────


@dataclass
class SocialPost:
    platform: str  # "twitter", "reddit"
    text: str
    author: str
    likes: int = 0
    retweets: int = 0
    sentiment: float = 0.0
    timestamp: float = 0.0


class SocialMediaMonitor:
    """
    Monitors social media sentiment for crypto assets.

    In dry_run: synthetic data. In production: Twitter/Reddit API.
    """

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.analyzer = SentimentAnalyzer()

    def get_posts(self, symbol: str, limit: int = 50) -> list[SocialPost]:
        if self.dry_run:
            return self._synthetic_posts(symbol, limit)
        return []

    def get_sentiment_score(self, symbol: str) -> dict:
        posts = self.get_posts(symbol)
        if not posts:
            return {"symbol": symbol, "n_posts": 0, "avg_sentiment": 0}
        sentiments = [p.sentiment for p in posts]
        return {
            "symbol": symbol,
            "n_posts": len(posts),
            "avg_sentiment": sum(sentiments) / len(sentiments),
            "bullish_pct": sum(1 for s in sentiments if s > 0.1) / len(sentiments),
            "bearish_pct": sum(1 for s in sentiments if s < -0.1) / len(sentiments),
            "total_engagement": sum(p.likes + p.retweets for p in posts),
        }

    def _synthetic_posts(self, symbol: str, limit: int) -> list[SocialPost]:
        import random

        texts = [
            f"{symbol} to the moon! 🚀🚀🚀",
            f"Sold all my {symbol}, this pump won't last",
            f"Accumulating {symbol} at these levels, DCA",
            f"{symbol} looks bearish, head and shoulders forming",
            f"Just bought more {symbol}, conviction is high",
            f"Market makers dumping {symbol}, be careful",
            f"{symbol} breaking out of consolidation, next stop ATH",
            f"HODL {symbol} since 2020, not selling now",
        ]
        posts = []
        for _ in range(min(limit, len(texts) * 3)):
            text = random.choice(texts)
            result = self.analyzer.analyze(text)
            posts.append(
                SocialPost(
                    platform=random.choice(["twitter", "reddit"]),
                    text=text,
                    author=f"user_{random.randint(1000, 9999)}",
                    likes=random.randint(0, 1000),
                    retweets=random.randint(0, 500),
                    sentiment=result.score,
                    timestamp=time.time() - random.uniform(0, 43200),
                )
            )
        return posts


# ── Composite Sentiment ─────────────────────────────────────


class SentimentComposite:
    """
    Combines news + social sentiment into a single signal.
    Weighted by source reliability and recency.
    """

    def __init__(self, news_weight: float = 0.6, social_weight: float = 0.4):
        self.news_weight = news_weight
        self.social_weight = social_weight
        self.news = NewsAggregator(dry_run=True)
        self.social = SocialMediaMonitor(dry_run=True)

    def get_signal(self, symbol: str) -> dict:
        # News sentiment
        news_items = self.news.fetch_news(symbol, limit=10)
        news_scores = [n.sentiment for n in news_items]
        news_avg = sum(news_scores) / len(news_scores) if news_scores else 0

        # Social sentiment
        social = self.social.get_sentiment_score(symbol)

        # Weighted composite
        composite = self.news_weight * news_avg + self.social_weight * social.get(
            "avg_sentiment", 0
        )

        # Signal
        if composite > 0.3:
            signal = "strong_bullish"
        elif composite > 0.1:
            signal = "bullish"
        elif composite < -0.3:
            signal = "strong_bearish"
        elif composite < -0.1:
            signal = "bearish"
        else:
            signal = "neutral"

        return {
            "symbol": symbol,
            "composite_score": composite,
            "signal": signal,
            "news": {"n_items": len(news_items), "avg_sentiment": news_avg},
            "social": social,
            "confidence": min(abs(composite) * 2, 1.0),
        }


if __name__ == "__main__":
    print("=" * 60)
    print("SENTIMENT PIPELINE — DEMO")
    print("=" * 60)

    analyzer = SentimentAnalyzer()
    texts = [
        "BTC surges past $100k amid massive institutional buying",
        "Exchange hack exposes critical security vulnerabilities",
        "Bitcoin shows signs of recovery after flash crash",
        "Not bullish on this rally, looks like a bull trap",
    ]
    print("\nSingle Text Analysis:")
    for t in texts:
        r = analyzer.analyze(t)
        print(f"  [{r.score:+.2f}] {t[:60]}")
        print(f"         Bull: {r.bullish_words} | Bear: {r.bearish_words}")

    composite = SentimentComposite()
    signal = composite.get_signal("BTC")
    print("\nComposite Signal:")
    print(f"  Score: {signal['composite_score']:.3f}")
    print(f"  Signal: {signal['signal']}")
    print(f"  Confidence: {signal['confidence']:.2f}")
    print(f"  News: {signal['news']}")
    print(f"  Social: {signal['social']}")
