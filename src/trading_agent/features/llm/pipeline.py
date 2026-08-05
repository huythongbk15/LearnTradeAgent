"""LLM Feature Pipeline - combines news, earnings, social features."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from trading_agent.features.llm.news import NewsFeatureExtractor, NewsFeatures, NewsArticle
from trading_agent.features.llm.earnings import EarningsFeatureExtractor, EarningsFeatures, EarningsData
from trading_agent.features.llm.social import SocialSentimentExtractor, SocialFeatures, SocialPost
from trading_agent.llm.client import LLMClient
from trading_agent.llm.pool import LLMPool, create_llm_pool

# LLM backend: LLMClient (đơn) hoặc LLMPool (multi-provider failover)
LLMBackend = LLMClient | LLMPool

logger = logging.getLogger(__name__)


@dataclass
class LLMFeatureSet:
    """Combined LLM-derived features for a symbol."""
    symbol: str
    timestamp: datetime
    
    # News features
    news: Optional[NewsFeatures] = None
    
    # Earnings features
    earnings: Optional[EarningsFeatures] = None
    
    # Social features
    social: Optional[SocialFeatures] = None
    
    # Combined signals
    combined_sentiment: float = 0.0  # -1 to 1
    combined_confidence: float = 0.0
    signal_strength: float = 0.0  # 0 to 1
    signal_direction: str = "neutral"  # bullish, bearish, neutral
    
    # Feature vector for ML
    feature_vector: np.ndarray = field(default_factory=lambda: np.array([]))
    feature_names: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "news_sentiment": self.news.sentiment_score if self.news else 0,
            "news_confidence": self.news.sentiment_confidence if self.news else 0,
            "news_impact": self.news.impact_estimate if self.news else 0,
            "news_topics": self.news.key_topics if self.news else [],
            "earnings_surprise": self.earnings.eps_surprise if self.earnings else 0,
            "earnings_revenue_surprise": self.earnings.revenue_surprise if self.earnings else 0,
            "earnings_guidance": "raised" if self.earnings and self.earnings.guidance_raised else
                               "lowered" if self.earnings and self.earnings.guidance_lowered else "inline",
            "earnings_tone": self.earnings.management_tone if self.earnings else 0,
            "social_sentiment": self.social.sentiment_score if self.social else 0,
            "social_bullish_ratio": self.social.bullish_ratio if self.social else 0,
            "social_volume": self.social.post_count if self.social else 0,
            "social_momentum": self.social.sentiment_momentum if self.social else 0,
            "combined_sentiment": self.combined_sentiment,
            "combined_confidence": self.combined_confidence,
            "signal_strength": self.signal_strength,
            "signal_direction": self.signal_direction,
        }
    
    def to_dataframe(self) -> pd.DataFrame:
        """Convert to single-row DataFrame."""
        return pd.DataFrame([self.to_dict()])


class LLMFeaturePipeline:
    """Pipeline for extracting and combining LLM-derived features."""
    
    def __init__(
        self,
        llm_client: LLMBackend,
        news_weight: float = 0.4,
        earnings_weight: float = 0.3,
        social_weight: float = 0.3,
    ):
        self.llm = llm_client
        self.news_weight = news_weight
        self.earnings_weight = earnings_weight
        self.social_weight = social_weight
        
        # Initialize extractors
        self.news_extractor = NewsFeatureExtractor(llm_client)
        self.earnings_extractor = EarningsFeatureExtractor(llm_client)
        self.social_extractor = SocialSentimentExtractor(llm_client)
        
        # Aggregators
        self._news_aggregators = {}
        self._social_aggregators = {}
    
    async def extract_all(
        self,
        symbol: str,
        news_articles: list[NewsArticle] = None,
        earnings_data: EarningsData = None,
        social_posts: list[SocialPost] = None,
    ) -> LLMFeatureSet:
        """Extract all features for a symbol."""
        tasks = []
        
        # News
        if news_articles:
            tasks.append(self.news_extractor.extract(news_articles, symbol))
        else:
            tasks.append(asyncio.sleep(0, result=None))
        
        # Earnings
        if earnings_data:
            tasks.append(self.earnings_extractor.extract(earnings_data))
        else:
            tasks.append(asyncio.sleep(0, result=None))
        
        # Social
        if social_posts:
            tasks.append(self.social_extractor.extract(social_posts, symbol))
        else:
            tasks.append(asyncio.sleep(0, result=None))
        
        news_feat, earnings_feat, social_feat = await asyncio.gather(*tasks)
        
        # Combine
        combined = self._combine_features(
            symbol, news_feat, earnings_feat, social_feat
        )
        
        return combined
    
    def _combine_features(
        self,
        symbol: str,
        news: Optional[NewsFeatures],
        earnings: Optional[EarningsFeatures],
        social: Optional[SocialFeatures],
    ) -> LLMFeatureSet:
        """Combine features into unified signal."""
        
        # News component
        news_sent = news.sentiment_score if news else 0
        news_conf = news.sentiment_confidence * news.relevance_score if news else 0
        news_impact = news.impact_estimate if news else 0
        news_weight = self.news_weight * (news_conf if news else 0)
        
        # Earnings component
        earn_sent = 0
        earn_conf = 0
        if earnings:
            # Convert surprise to sentiment
            earn_sent = np.clip(earnings.eps_surprise * 2 + earnings.revenue_surprise, -1, 1)
            # Guidance adjustment
            if earnings.guidance_raised:
                earn_sent += 0.3
            elif earnings.guidance_lowered:
                earn_sent -= 0.3
            earn_sent = np.clip(earn_sent, -1, 1)
            earn_conf = earnings.sentiment_confidence
        earn_weight = self.earnings_weight * earn_conf
        
        # Social component
        social_sent = social.sentiment_score if social else 0
        social_conf = social.sentiment_confidence if social else 0
        social_weight = self.social_weight * social_conf * min(1, social.post_count / 100)
        
        # Weighted combination
        total_weight = news_weight + earn_weight + social_weight
        
        if total_weight > 0:
            combined_sentiment = (
                news_sent * news_weight +
                earn_sent * earn_weight +
                social_sent * social_weight
            ) / total_weight
            
            combined_confidence = (
                news_conf * news_weight +
                earn_conf * earn_weight +
                social_conf * social_weight
            ) / total_weight
        else:
            combined_sentiment = 0
            combined_confidence = 0
        
        # Signal strength and direction
        signal_strength = abs(combined_sentiment) * combined_confidence
        
        if combined_sentiment > 0.2 and combined_confidence > 0.3:
            signal_direction = "bullish"
        elif combined_sentiment < -0.2 and combined_confidence > 0.3:
            signal_direction = "bearish"
        else:
            signal_direction = "neutral"
        
        # Build feature vector
        feature_vector, feature_names = self._build_feature_vector(
            news, earnings, social
        )
        
        return LLMFeatureSet(
            symbol=symbol,
            timestamp=datetime.utcnow(),
            news=news,
            earnings=earnings,
            social=social,
            combined_sentiment=combined_sentiment,
            combined_confidence=combined_confidence,
            signal_strength=signal_strength,
            signal_direction=signal_direction,
            feature_vector=feature_vector,
            feature_names=feature_names,
        )
    
    def _build_feature_vector(
        self,
        news: Optional[NewsFeatures],
        earnings: Optional[EarningsFeatures],
        social: Optional[SocialFeatures],
    ) -> tuple[np.ndarray, list[str]]:
        """Build ML feature vector."""
        features = []
        names = []
        
        # News features
        if news:
            features.extend([
                news.sentiment_score,
                news.sentiment_confidence,
                news.relevance_score,
                news.impact_estimate,
                news.urgency,
                float(news.article_count),
                len(news.key_topics),
            ])
            names.extend([
                "news_sentiment", "news_confidence", "news_relevance",
                "news_impact", "news_urgency", "news_count", "news_topic_count"
            ])
            
            # Topic one-hot (top 10 topics)
            common_topics = ["earnings", "guidance", "merger", "regulation", "product",
                           "macro", "analyst", "dividend", "buyback", "lawsuit"]
            for topic in common_topics:
                features.append(1.0 if topic in news.key_topics else 0.0)
                names.append(f"news_topic_{topic}")
        else:
            features.extend([0] * 7 + [0] * 10)
            names.extend([
                "news_sentiment", "news_confidence", "news_relevance",
                "news_impact", "news_urgency", "news_count", "news_topic_count"
            ] + [f"news_topic_{t}" for t in ["earnings", "guidance", "merger", "regulation", 
                                              "product", "macro", "analyst", "dividend", 
                                              "buyback", "lawsuit"]])
        
        # Earnings features
        if earnings:
            features.extend([
                earnings.eps_surprise,
                earnings.revenue_surprise,
                earnings.management_tone,
                earnings.sentiment_confidence,
                float(earnings.guidance_raised),
                float(earnings.guidance_lowered),
                earnings.expected_move,
                earnings.implied_volatility,
            ])
            names.extend([
                "earn_eps_surprise", "earn_rev_surprise", "earn_tone",
                "earn_confidence", "earn_guidance_up", "earn_guidance_down",
                "earn_expected_move", "earn_iv"
            ])
            
            # Growth outlook
            outlook_map = {"bearish": -1, "neutral": 0, "bullish": 1}
            features.append(outlook_map.get(earnings.growth_outlook, 0))
            features.append(outlook_map.get(earnings.margin_outlook, 0))
            names.extend(["earn_growth_outlook", "earn_margin_outlook"])
        else:
            features.extend([0] * 10)
            names.extend([
                "earn_eps_surprise", "earn_rev_surprise", "earn_tone",
                "earn_confidence", "earn_guidance_up", "earn_guidance_down",
                "earn_expected_move", "earn_iv", "earn_growth_outlook", "earn_margin_outlook"
            ])
        
        # Social features
        if social:
            features.extend([
                social.sentiment_score,
                social.sentiment_confidence,
                social.bullish_ratio,
                social.bearish_ratio,
                social.influence_weighted_sentiment,
                social.top_influencers_sentiment,
                np.log1p(social.post_count),
                np.log1p(social.unique_authors),
                np.log1p(social.total_engagement),
                social.sentiment_momentum,
                social.volume_momentum,
                social.bot_score,
                social.spam_score,
                social.coordination_score,
            ])
            names.extend([
                "social_sentiment", "social_confidence", "social_bullish",
                "social_bearish", "social_infl_sentiment", "social_top_infl_sentiment",
                "social_log_posts", "social_log_authors", "social_log_engagement",
                "social_sent_momentum", "social_vol_momentum",
                "social_bot_score", "social_spam_score", "social_coord_score"
            ])
            
            # Topic one-hot (top 10)
            social_topics = ["breakout", "earnings", "short_squeeze", "fud", "hodl",
                           "dip_buy", "take_profit", "whale", "manipulation", "fomo"]
            for topic in social_topics:
                features.append(1.0 if topic in social.trending_topics else 0.0)
                names.append(f"social_topic_{topic}")
        else:
            features.extend([0] * 14 + [0] * 10)
            names.extend([
                "social_sentiment", "social_confidence", "social_bullish",
                "social_bearish", "social_infl_sentiment", "social_top_infl_sentiment",
                "social_log_posts", "social_log_authors", "social_log_engagement",
                "social_sent_momentum", "social_vol_momentum",
                "social_bot_score", "social_spam_score", "social_coord_score"
            ] + [f"social_topic_{t}" for t in ["breakout", "earnings", "short_squeeze", "fud", "hodl",
                                                "dip_buy", "take_profit", "whale", "manipulation", "fomo"]])
        
        return np.array(features, dtype=np.float32), names
    
    async def extract_batch(
        self,
        symbols: list[str],
        news_data: dict[str, list[NewsArticle]] = None,
        earnings_data: dict[str, EarningsData] = None,
        social_data: dict[str, list[SocialPost]] = None,
    ) -> dict[str, LLMFeatureSet]:
        """Extract features for multiple symbols."""
        tasks = []
        for sym in symbols:
            tasks.append(self.extract_all(
                sym,
                news_data.get(sym) if news_data else None,
                earnings_data.get(sym) if earnings_data else None,
                social_data.get(sym) if social_data else None,
            ))
        
        results = await asyncio.gather(*tasks)
        return dict(zip(symbols, results))
    
    def get_feature_matrix(self, feature_sets: list[LLMFeatureSet]) -> tuple[np.ndarray, list[str], list[str]]:
        """Get feature matrix for ML training."""
        if not feature_sets:
            return np.array([]), [], []
        
        # Use first feature set's names as reference
        names = feature_sets[0].feature_names
        symbols = [fs.symbol for fs in feature_sets]
        matrix = np.stack([fs.feature_vector for fs in feature_sets])
        
        return matrix, names, symbols


# Example usage and testing
async def demo():
    """Demo the pipeline."""
    # Setup LLM pool (multi-provider failover + quota tracking)
    llm = create_llm_pool()
    
    pipeline = LLMFeaturePipeline(llm)
    
    # Mock data
    news = [
        NewsArticle(
            title="Apple Beats Q4 Earnings",
            content="Apple reported strong Q4 results with EPS of $1.46 vs $1.39 estimate...",
            url="https://example.com",
            source="Reuters",
            published_at=datetime.utcnow(),
            symbols=["AAPL"],
        )
    ]
    
    earnings = EarningsData(
        symbol="AAPL",
        period="2024-Q4",
        reported_date=datetime.utcnow(),
        eps_actual=1.46,
        eps_estimate=1.39,
        revenue_actual=89.5e9,
        revenue_estimate=88.8e9,
        guidance="Raised Q1 guidance to $1.50 EPS",
    )
    
    social = [
        SocialPost(
            platform="twitter",
            author="trader_john",
            content="AAPL breaking out! Earnings beat was huge, guidance raise very bullish 🚀",
            timestamp=datetime.utcnow(),
            symbol="AAPL",
            engagement={"likes": 100, "retweets": 50},
            followers=10000,
            verified=False,
        )
    ]
    
    features = await pipeline.extract_all("AAPL", news, earnings, social)
    
    print(f"Symbol: {features.symbol}")
    print(f"Combined Sentiment: {features.combined_sentiment:.3f}")
    print(f"Combined Confidence: {features.combined_confidence:.3f}")
    print(f"Signal: {features.signal_direction} (strength: {features.signal_strength:.3f})")
    print(f"\nFeature Vector Shape: {features.feature_vector.shape}")
    print(f"Feature Names: {features.feature_names[:10]}...")


if __name__ == "__main__":
    import asyncio
    asyncio.run(demo())