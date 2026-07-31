"""News feature extraction using LLM."""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib

from trading.llm.client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class NewsArticle:
    """News article data."""
    title: str
    content: str
    url: str
    source: str
    published_at: datetime
    symbols: list[str] = None
    sentiment: float = 0.0  # -1 to 1
    relevance: float = 0.0  # 0 to 1
    topics: list[str] = None
    
    def __post_init__(self):
        if self.symbols is None:
            self.symbols = []
        if self.topics is None:
            self.topics = []


@dataclass
class NewsFeatures:
    """Extracted features from news."""
    symbol: str
    timestamp: datetime
    sentiment_score: float  # -1 to 1
    sentiment_confidence: float  # 0 to 1
    relevance_score: float  # 0 to 1
    key_topics: list[str]
    event_type: str  # earnings, merger, regulatory, product, macro, other
    impact_estimate: float  # -1 to 1 expected price impact
    urgency: float  # 0 to 1
    article_count: int
    sources: list[str]
    raw_articles: list[NewsArticle]


class NewsFeatureExtractor:
    """Extract trading features from news using LLM."""
    
    SYSTEM_PROMPT = """You are a financial news analyst. Analyze news articles and extract structured trading signals.

For each article, output JSON with:
{
  "sentiment": float (-1 to 1, negative to positive),
  "confidence": float (0 to 1),
  "relevance": float (0 to 1, how relevant to trading),
  "topics": [string], // e.g., ["earnings", "guidance", "merger", "regulation", "product_launch", "macro"]
  "event_type": string, // one: earnings, merger, regulatory, product, macro, other
  "impact": float (-1 to 1, estimated price impact direction and magnitude),
  "urgency": float (0 to 1, how time-sensitive),
  "symbols": [string], // mentioned tickers
  "summary": string // 1-2 sentence summary
}"""
    
    def __init__(self, llm_client: LLMClient, cache_ttl: int = 3600):
        self.llm = llm_client
        self.cache_ttl = cache_ttl
        self._cache = {}
    
    def _cache_key(self, articles: list[NewsArticle]) -> str:
        content = "".join(a.title + a.content[:200] for a in articles)
        return hashlib.md5(content.encode()).hexdigest()
    
    async def extract(self, articles: list[NewsArticle], symbol: str) -> NewsFeatures:
        """Extract features from news articles for a symbol."""
        # Filter relevant articles
        relevant = [a for a in articles if not a.symbols or symbol in a.symbols]
        if not relevant:
            return NewsFeatures(
                symbol=symbol,
                timestamp=datetime.utcnow(),
                sentiment_score=0.0,
                sentiment_confidence=0.0,
                relevance_score=0.0,
                key_topics=[],
                event_type="none",
                impact_estimate=0.0,
                urgency=0.0,
                article_count=0,
                sources=[],
                raw_articles=[],
            )
        
        # Check cache
        cache_key = self._cache_key(relevant)
        if cache_key in self._cache:
            cached, ts = self._cache[cache_key]
            if (datetime.utcnow() - ts).seconds < self.cache_ttl:
                return cached
        
        # Prepare prompt
        articles_text = "\n\n---\n\n".join(
            f"Title: {a.title}\nSource: {a.source}\nTime: {a.published_at}\nContent: {a.content[:2000]}"
            for a in relevant[:10]  # Limit to 10 articles
        )
        
        prompt = f"""Analyze these news articles for {symbol} trading signals:

{articles_text}

Output JSON array of analysis for each article, then aggregate summary."""
        
        # Call LLM
        response = await self.llm.chat([
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ], temperature=0.1, max_tokens=2000)
        
        # Parse response
        features = self._parse_response(response, relevant, symbol)
        
        # Cache
        self._cache[cache_key] = (features, datetime.utcnow())
        
        return features
    
    def _parse_response(self, response: str, articles: list[NewsArticle], symbol: str) -> NewsFeatures:
        """Parse LLM response into features."""
        try:
            # Try to extract JSON
            start = response.find("[")
            end = response.rfind("]") + 1
            if start >= 0 and end > start:
                analyses = json.loads(response[start:end])
            else:
                # Try single object
                start = response.find("{")
                end = response.rfind("}") + 1
                analyses = [json.loads(response[start:end])]
        except Exception:
            logger.warning("Failed to parse LLM response, using defaults")
            analyses = [{} for _ in articles]
        
        # Aggregate
        sentiments = [a.get("sentiment", 0) for a in analyses]
        confidences = [a.get("confidence", 0) for a in analyses]
        relevances = [a.get("relevance", 0) for a in analyses]
        impacts = [a.get("impact", 0) for a in analyses]
        urgencies = [a.get("urgency", 0) for a in analyses]
        
        all_topics = []
        event_types = []
        for a in analyses:
            all_topics.extend(a.get("topics", []))
            event_types.append(a.get("event_type", "other"))
        
        # Weight by confidence and relevance
        weights = [c * r for c, r in zip(confidences, relevances)]
        total_weight = sum(weights) or 1
        
        weighted_sentiment = sum(s * w for s, w in zip(sentiments, weights)) / total_weight
        weighted_impact = sum(i * w for i, w in zip(impacts, weights)) / total_weight
        
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0
        avg_relevance = sum(relevances) / len(relevances) if relevances else 0
        avg_urgency = sum(urgencies) / len(urgencies) if urgencies else 0
        
        # Most common event type
        from collections import Counter
        event_type = Counter(event_types).most_common(1)[0][0] if event_types else "other"
        
        # Top topics
        topic_counts = Counter(all_topics)
        key_topics = [t for t, _ in topic_counts.most_common(5)]
        
        return NewsFeatures(
            symbol=symbol,
            timestamp=datetime.utcnow(),
            sentiment_score=weighted_sentiment,
            sentiment_confidence=avg_confidence,
            relevance_score=avg_relevance,
            key_topics=key_topics,
            event_type=event_type,
            impact_estimate=weighted_impact,
            urgency=avg_urgency,
            article_count=len(articles),
            sources=list(set(a.source for a in articles)),
            raw_articles=articles,
        )
    
    async def extract_batch(self, articles: list[NewsArticle], symbols: list[str]) -> dict[str, NewsFeatures]:
        """Extract features for multiple symbols."""
        tasks = [self.extract(articles, sym) for sym in symbols]
        results = await asyncio.gather(*tasks)
        return dict(zip(symbols, results))


class NewsFeatureAggregator:
    """Aggregate news features over time windows."""
    
    def __init__(self, window_minutes: int = 60):
        self.window = timedelta(minutes=window_minutes)
        self.features_buffer: list[NewsFeatures] = []
    
    def add(self, features: NewsFeatures) -> None:
        """Add features to buffer."""
        self.features_buffer.append(features)
        self._cleanup()
    
    def _cleanup(self) -> None:
        """Remove old features."""
        cutoff = datetime.utcnow() - self.window
        self.features_buffer = [f for f in self.features_buffer if f.timestamp > cutoff]
    
    def get_aggregated(self, symbol: str) -> NewsFeatures | None:
        """Get aggregated features for symbol."""
        symbol_features = [f for f in self.features_buffer if f.symbol == symbol]
        if not symbol_features:
            return None
        
        # Weight by recency and confidence
        now = datetime.utcnow()
        total_weight = 0
        weighted_sentiment = 0
        weighted_impact = 0
        weighted_urgency = 0
        total_confidence = 0
        total_relevance = 0
        all_topics = []
        all_sources = set()
        total_articles = 0
        
        for f in symbol_features:
            age = (now - f.timestamp).total_seconds() / 60  # minutes
            recency_weight = max(0, 1 - age / self.window.total_seconds() * 60)
            weight = f.sentiment_confidence * f.relevance_score * recency_weight
            
            if weight > 0:
                weighted_sentiment += f.sentiment_score * weight
                weighted_impact += f.impact_estimate * weight
                weighted_urgency += f.urgency * weight
                total_weight += weight
            
            total_confidence += f.sentiment_confidence
            total_relevance += f.relevance_score
            all_topics.extend(f.key_topics)
            all_sources.update(f.sources)
            total_articles += f.article_count
        
        if total_weight == 0:
            return None
        
        from collections import Counter
        key_topics = [t for t, _ in Counter(all_topics).most_common(5)]
        
        return NewsFeatures(
            symbol=symbol,
            timestamp=now,
            sentiment_score=weighted_sentiment / total_weight,
            sentiment_confidence=total_confidence / len(symbol_features),
            relevance_score=total_relevance / len(symbol_features),
            key_topics=key_topics,
            event_type=symbol_features[-1].event_type,  # Most recent
            impact_estimate=weighted_impact / total_weight,
            urgency=weighted_urgency / total_weight,
            article_count=total_articles,
            sources=list(all_sources),
            raw_articles=[],
        )


__all__ = [
    "NewsFeatureExtractor",
    "NewsArticle",
    "NewsFeatures",
    "NewsFeatureAggregator",
]