"""Social sentiment feature extraction using LLM."""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from collections import Counter

from trading.llm.client import LLMClient
from trading.llm.pool import LLMPool

# LLM backend: LLMClient (đơn) hoặc LLMPool (multi-provider failover)
LLMBackend = LLMClient | LLMPool

logger = logging.getLogger(__name__)


@dataclass
class SocialPost:
    """Social media post."""
    platform: str  # twitter, reddit, stocktwits, etc.
    author: str
    content: str
    timestamp: datetime
    symbol: str
    engagement: dict = None  # likes, retweets, replies, etc.
    followers: int = 0
    verified: bool = False
    
    def __post_init__(self):
        if self.engagement is None:
            self.engagement = {}


@dataclass
class SocialFeatures:
    """Aggregated social sentiment features."""
    symbol: str
    timestamp: datetime
    window_minutes: int
    
    # Overall sentiment
    sentiment_score: float = 0.0  # -1 to 1
    sentiment_confidence: float = 0.0  # 0 to 1
    bullish_ratio: float = 0.0  # % bullish posts
    bearish_ratio: float = 0.0  # % bearish posts
    
    # Volume
    post_count: int = 0
    unique_authors: int = 0
    total_engagement: int = 0
    
    # Influence-weighted
    influence_weighted_sentiment: float = 0.0
    top_influencers_sentiment: float = 0.0
    
    # Topics
    trending_topics: list[str] = None
    topic_sentiment: dict[str, float] = None
    
    # Anomalies
    bot_score: float = 0.0  # 0 to 1, likelihood of bot activity
    spam_score: float = 0.0
    coordination_score: float = 0.0  # coordinated posting
    
    # Momentum
    sentiment_momentum: float = 0.0  # rate of change
    volume_momentum: float = 0.0
    
    def __post_init__(self):
        if self.trending_topics is None:
            self.trending_topics = []
        if self.topic_sentiment is None:
            self.topic_sentiment = {}


class SocialSentimentExtractor:
    """Extract trading features from social media using LLM."""
    
    SYSTEM_PROMPT = """You are a social media analyst for financial markets. Analyze posts for trading signals.

For each post, output JSON with:
{
  "sentiment": float,  // -1 to 1
  "confidence": float,  // 0 to 1
  "intent": "bullish|bearish|neutral|question|news_share|meme",
  "topics": [string],  // e.g., ["breakout", "earnings", "short_squeeze", "fud", "hodl"]
  "urgency": float,  // 0 to 1
  "credibility": float,  // 0 to 1
  "is_spam": boolean,
  "is_bot_like": boolean,
  "price_target": float or null,  // mentioned price target
  "time_horizon": "intraday|swing|long_term|unknown"
}"""
    
    def __init__(self, llm_client: LLMBackend, batch_size: int = 50):
        self.llm = llm_client
        self.batch_size = batch_size
    
    async def extract(self, posts: list[SocialPost], symbol: str) -> SocialFeatures:
        """Extract features from social posts."""
        if not posts:
            return SocialFeatures(
                symbol=symbol,
                timestamp=datetime.utcnow(),
                window_minutes=60,
            )
        
        # Filter for symbol
        relevant = [p for p in posts if p.symbol.upper() == symbol.upper()]
        if not relevant:
            return SocialFeatures(
                symbol=symbol,
                timestamp=datetime.utcnow(),
                window_minutes=60,
            )
        
        # Analyze in batches
        analyses = []
        for i in range(0, len(relevant), self.batch_size):
            batch = relevant[i:i + self.batch_size]
            batch_analyses = await self._analyze_batch(batch)
            analyses.extend(batch_analyses)
        
        return self._aggregate(analyses, relevant, symbol)
    
    async def _analyze_batch(self, posts: list[SocialPost]) -> list[dict]:
        """Analyze a batch of posts."""
        posts_text = "\n\n---\n\n".join(
            f"Platform: {p.platform}\nAuthor: {p.author} (followers: {p.followers}, verified: {p.verified})\n"
            f"Engagement: {p.engagement}\nTime: {p.timestamp}\nContent: {p.content[:500]}"
            for p in posts
        )
        
        prompt = f"Analyze these {len(posts)} social posts:\n\n{posts_text}"
        
        response = await self.llm.chat([
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ], temperature=0.2, max_tokens=2000)
        
        return self._parse_batch_response(response, len(posts))
    
    def _parse_batch_response(self, response: str, expected_count: int) -> list[dict]:
        """Parse batch response."""
        try:
            start = response.find("[")
            end = response.rfind("]") + 1
            if start >= 0 and end > start:
                analyses = json.loads(response[start:end])
            else:
                analyses = []
        except Exception:
            analyses = []
        
        # Pad if needed
        while len(analyses) < expected_count:
            analyses.append({
                "sentiment": 0, "confidence": 0, "intent": "neutral",
                "topics": [], "urgency": 0, "credibility": 0.5,
                "is_spam": False, "is_bot_like": False,
                "price_target": None, "time_horizon": "unknown"
            })
        
        return analyses[:expected_count]
    
    def _aggregate(
        self, analyses: list[dict], posts: list[SocialPost], symbol: str
    ) -> SocialFeatures:
        """Aggregate analyses into features."""
        n = len(analyses)
        if n == 0:
            return SocialFeatures(symbol=symbol, timestamp=datetime.utcnow(), window_minutes=60)
        
        # Basic stats
        sentiments = [a.get("sentiment", 0) for a in analyses]
        confidences = [a.get("confidence", 0) for a in analyses]
        intents = [a.get("intent", "neutral") for a in analyses]
        
        bullish = sum(1 for i in intents if i == "bullish")
        bearish = sum(1 for i in intents if i == "bearish")
        
        # Engagement-weighted sentiment
        weighted_sentiments = []
        weighted_confidences = []
        for a, p in zip(analyses, posts):
            engagement = sum(p.engagement.values()) if p.engagement else 1
            influence = p.followers * 0.001 + engagement * 0.1 + (10 if p.verified else 1)
            weight = influence / 1000  # Normalize
            weighted_sentiments.append(a.get("sentiment", 0) * weight)
            weighted_confidences.append(a.get("confidence", 0) * weight)
        
        total_weight = sum(1 for _ in posts)  # Simplified
        
        # Topics
        all_topics = []
        topic_sentiments = {}
        for a in analyses:
            for topic in a.get("topics", []):
                all_topics.append(topic)
                if topic not in topic_sentiments:
                    topic_sentiments[topic] = []
                topic_sentiments[topic].append(a.get("sentiment", 0))
        
        # Average topic sentiment
        avg_topic_sentiment = {
            t: sum(s) / len(s) for t, s in topic_sentiments.items()
        }
        
        # Top topics
        topic_counts = Counter(all_topics)
        trending = [t for t, _ in topic_counts.most_common(10)]
        
        # Anomaly detection
        bot_like = sum(1 for a in analyses if a.get("is_bot_like", False))
        spam = sum(1 for a in analyses if a.get("is_spam", False))
        
        # Coordination detection (similar content at same time)
        coordination = self._detect_coordination(posts, analyses)
        
        # Influencer sentiment (top 10% by followers)
        sorted_posts = sorted(zip(posts, analyses), key=lambda x: x[0].followers, reverse=True)
        top_n = max(1, n // 10)
        influencer_sentiments = [a.get("sentiment", 0) for _, a in sorted_posts[:top_n]]
        
        return SocialFeatures(
            symbol=symbol,
            timestamp=datetime.utcnow(),
            window_minutes=60,
            sentiment_score=sum(sentiments) / n,
            sentiment_confidence=sum(confidences) / n,
            bullish_ratio=bullish / n,
            bearish_ratio=bearish / n,
            post_count=n,
            unique_authors=len(set(p.author for p in posts)),
            total_engagement=sum(sum(p.engagement.values()) for p in posts),
            influence_weighted_sentiment=sum(weighted_sentiments) / max(1, sum(1 for _ in weighted_sentiments)),
            top_influencers_sentiment=sum(influencer_sentiments) / len(influencer_sentiments) if influencer_sentiments else 0,
            trending_topics=trending,
            topic_sentiment=avg_topic_sentiment,
            bot_score=bot_like / n,
            spam_score=spam / n,
            coordination_score=coordination,
        )
    
    def _detect_coordination(self, posts: list[SocialPost], analyses: list[dict]) -> float:
        """Detect coordinated posting behavior."""
        if len(posts) < 5:
            return 0.0
        
        # Check for similar content posted within short time
        content_groups = {}
        for p, a in zip(posts, analyses):
            # Simple content fingerprint
            words = set(re.findall(r'\w+', p.content.lower()))
            fingerprint = tuple(sorted(words)[:10])
            key = (fingerprint, p.timestamp.minute // 5)  # 5-min buckets
            content_groups.setdefault(key, 0)
            content_groups[key] += 1
        
        # Coordination score = max group size / total
        max_group = max(content_groups.values()) if content_groups else 1
        return min(1.0, max_group / len(posts) * 2)
    
    async def extract_multi_platform(
        self, 
        platform_posts: dict[str, list[SocialPost]], 
        symbol: str
    ) -> dict[str, SocialFeatures]:
        """Extract features per platform."""
        tasks = [
            self.extract(posts, symbol) 
            for posts in platform_posts.values()
        ]
        results = await asyncio.gather(*tasks)
        return dict(zip(platform_posts.keys(), results))


class SocialFeatureAggregator:
    """Aggregate social features over time."""
    
    def __init__(self, window_minutes: int = 60):
        self.window = window_minutes
        self.buffer: dict[str, list[SocialFeatures]] = {}
    
    def add(self, features: SocialFeatures) -> None:
        """Add features to buffer."""
        if features.symbol not in self.buffer:
            self.buffer[features.symbol] = []
        self.buffer[features.symbol].append(features)
        self._cleanup(features.symbol)
    
    def _cleanup(self, symbol: str) -> None:
        cutoff = datetime.utcnow() - timedelta(minutes=self.window)
        self.buffer[symbol] = [f for f in self.buffer[symbol] if f.timestamp > cutoff]
    
    def get_aggregated(self, symbol: str) -> SocialFeatures | None:
        """Get time-aggregated features."""
        if symbol not in self.buffer or not self.buffer[symbol]:
            return None
        
        feats = self.buffer[symbol]
        n = len(feats)
        
        # Weight by recency
        now = datetime.utcnow()
        total_weight = 0
        weighted_sentiment = 0
        weighted_bullish = 0
        weighted_bearish = 0
        weighted_volume = 0
        
        for f in feats:
            age = (now - f.timestamp).total_seconds() / 60
            weight = max(0, 1 - age / self.window)
            
            weighted_sentiment += f.sentiment_score * weight
            weighted_bullish += f.bullish_ratio * weight
            weighted_bearish += f.bearish_ratio * weight
            weighted_volume += f.post_count * weight
            total_weight += weight
        
        if total_weight == 0:
            return feats[-1]
        
        # Momentum
        if n >= 2:
            recent = feats[-1]
            older = feats[max(0, n // 2)]
            sentiment_momentum = recent.sentiment_score - older.sentiment_score
            volume_momentum = recent.post_count - older.post_count
        else:
            sentiment_momentum = 0
            volume_momentum = 0
        
        # Merge topics
        all_topics = []
        for f in feats:
            all_topics.extend(f.trending_topics)
        topic_counts = Counter(all_topics)
        
        return SocialFeatures(
            symbol=symbol,
            timestamp=now,
            window_minutes=self.window,
            sentiment_score=weighted_sentiment / total_weight,
            sentiment_confidence=sum(f.sentiment_confidence for f in feats) / n,
            bullish_ratio=weighted_bullish / total_weight,
            bearish_ratio=weighted_bearish / total_weight,
            post_count=int(weighted_volume / total_weight),
            unique_authors=max(f.unique_authors for f in feats),
            total_engagement=sum(f.total_engagement for f in feats),
            influence_weighted_sentiment=sum(f.influence_weighted_sentiment for f in feats) / n,
            top_influencers_sentiment=sum(f.top_influencers_sentiment for f in feats) / n,
            trending_topics=[t for t, _ in topic_counts.most_common(10)],
            topic_sentiment={},  # Would need more complex merge
            bot_score=max(f.bot_score for f in feats),
            spam_score=max(f.spam_score for f in feats),
            coordination_score=max(f.coordination_score for f in feats),
            sentiment_momentum=sentiment_momentum,
            volume_momentum=volume_momentum,
        )


from datetime import timedelta
