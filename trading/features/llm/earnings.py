"""Earnings feature extraction using LLM."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from trading.llm.client import LLMClient
from trading.llm.pool import LLMPool

# LLM backend: LLMClient (đơn) hoặc LLMPool (multi-provider failover)
LLMBackend = LLMClient | LLMPool

logger = logging.getLogger(__name__)


@dataclass
class EarningsData:
    """Raw earnings data."""
    symbol: str
    period: str  # e.g., "2024-Q1"
    reported_date: datetime
    eps_actual: Optional[float] = None
    eps_estimate: Optional[float] = None
    revenue_actual: Optional[float] = None
    revenue_estimate: Optional[float] = None
    guidance: str = ""
    transcript: str = ""
    press_release: str = ""


@dataclass
class EarningsFeatures:
    """Extracted earnings features."""
    symbol: str
    period: str
    timestamp: datetime
    
    # Surprise metrics
    eps_surprise: float = 0.0  # (actual - estimate) / |estimate|
    revenue_surprise: float = 0.0
    eps_surprise_pct: float = 0.0
    revenue_surprise_pct: float = 0.0
    
    # Guidance
    guidance_raised: bool = False
    guidance_lowered: bool = False
    guidance_inline: bool = False
    next_quarter_eps_guidance: Optional[float] = None
    next_quarter_revenue_guidance: Optional[float] = None
    full_year_eps_guidance: Optional[float] = None
    full_year_revenue_guidance: Optional[float] = None
    
    # Sentiment
    management_tone: float = 0.0  # -1 to 1
    sentiment_confidence: float = 0.0
    key_topics: list[str] = None
    
    # Risk factors
    risk_factors: list[str] = None
    growth_outlook: str = "neutral"  # bullish, neutral, bearish
    margin_outlook: str = "neutral"
    
    # Price reaction
    expected_move: float = 0.0  # expected % move
    implied_volatility: float = 0.0
    
    def __post_init__(self):
        if self.key_topics is None:
            self.key_topics = []
        if self.risk_factors is None:
            self.risk_factors = []


class EarningsFeatureExtractor:
    """Extract trading features from earnings data using LLM."""
    
    SYSTEM_PROMPT = """You are an equity analyst analyzing earnings reports. Extract structured trading signals.

Output JSON with:
{
  "eps_surprise": float,  // (actual - estimate) / abs(estimate)
  "revenue_surprise": float,
  "guidance": {
    "raised": boolean,
    "lowered": boolean,
    "inline": boolean,
    "next_q_eps": float or null,
    "next_q_revenue": float or null,
    "fy_eps": float or null,
    "fy_revenue": float or null
  },
  "management_tone": float,  // -1 to 1
  "sentiment_confidence": float,  // 0 to 1
  "key_topics": [string],  // e.g., ["margin_expansion", "guidance_raise", "new_product", "cost_cuts"]
  "risk_factors": [string],  // e.g., ["macro_headwinds", "competition", "supply_chain"]
  "growth_outlook": "bullish|neutral|bearish",
  "margin_outlook": "bullish|neutral|bearish",
  "expected_move": float,  // expected % price move
  "implied_volatility": float,
  "summary": string  // 2-3 sentence summary
}"""
    
    def __init__(self, llm_client: LLMBackend):
        self.llm = llm_client
    
    async def extract(self, earnings: EarningsData) -> EarningsFeatures:
        """Extract features from earnings data."""
        # Build prompt
        prompt_parts = [
            f"Symbol: {earnings.symbol}",
            f"Period: {earnings.period}",
            f"Reported: {earnings.reported_date}",
        ]
        
        if earnings.eps_actual is not None:
            prompt_parts.append(f"EPS Actual: {earnings.eps_actual}")
        if earnings.eps_estimate is not None:
            prompt_parts.append(f"EPS Estimate: {earnings.eps_estimate}")
        if earnings.revenue_actual is not None:
            prompt_parts.append(f"Revenue Actual: {earnings.revenue_actual:,.0f}")
        if earnings.revenue_estimate is not None:
            prompt_parts.append(f"Revenue Estimate: {earnings.revenue_estimate:,.0f}")
        
        if earnings.guidance:
            prompt_parts.append(f"Guidance: {earnings.guidance[:2000]}")
        if earnings.transcript:
            prompt_parts.append(f"Transcript (excerpt): {earnings.transcript[:3000]}")
        if earnings.press_release:
            prompt_parts.append(f"Press Release: {earnings.press_release[:2000]}")
        
        prompt = "\n".join(prompt_parts)
        
        # Call LLM
        response = await self.llm.chat([
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ], temperature=0.1, max_tokens=1500)
        
        return self._parse_response(response, earnings)
    
    def _parse_response(self, response: str, earnings: EarningsData) -> EarningsFeatures:
        """Parse LLM response."""
        try:
            # Extract JSON
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(response[start:end])
            else:
                data = {}
        except Exception:
            logger.warning("Failed to parse earnings LLM response")
            data = {}
        
        # Calculate surprises if not in response
        eps_surprise = data.get("eps_surprise", 0)
        if eps_surprise == 0 and earnings.eps_actual is not None and earnings.eps_estimate:
            eps_surprise = (earnings.eps_actual - earnings.eps_estimate) / abs(earnings.eps_estimate)
        
        revenue_surprise = data.get("revenue_surprise", 0)
        if revenue_surprise == 0 and earnings.revenue_actual is not None and earnings.revenue_estimate:
            revenue_surprise = (earnings.revenue_actual - earnings.revenue_estimate) / abs(earnings.revenue_estimate)
        
        guidance = data.get("guidance", {})
        
        return EarningsFeatures(
            symbol=earnings.symbol,
            period=earnings.period,
            timestamp=datetime.utcnow(),
            eps_surprise=eps_surprise,
            revenue_surprise=revenue_surprise,
            eps_surprise_pct=eps_surprise * 100,
            revenue_surprise_pct=revenue_surprise * 100,
            guidance_raised=guidance.get("raised", False),
            guidance_lowered=guidance.get("lowered", False),
            guidance_inline=guidance.get("inline", False),
            next_quarter_eps_guidance=guidance.get("next_q_eps"),
            next_quarter_revenue_guidance=guidance.get("next_q_revenue"),
            full_year_eps_guidance=guidance.get("fy_eps"),
            full_year_revenue_guidance=guidance.get("fy_revenue"),
            management_tone=data.get("management_tone", 0),
            sentiment_confidence=data.get("sentiment_confidence", 0),
            key_topics=data.get("key_topics", []),
            risk_factors=data.get("risk_factors", []),
            growth_outlook=data.get("growth_outlook", "neutral"),
            margin_outlook=data.get("margin_outlook", "neutral"),
            expected_move=data.get("expected_move", 0),
            implied_volatility=data.get("implied_volatility", 0),
        )


class EarningsCalendarTracker:
    """Track upcoming earnings and prepare features."""
    
    def __init__(self, extractor: EarningsFeatureExtractor):
        self.extractor = extractor
        self.upcoming: dict[str, EarningsData] = {}
        self.historical: dict[str, list[EarningsFeatures]] = {}
    
    def add_upcoming(self, earnings: EarningsData) -> None:
        """Add upcoming earnings."""
        self.upcoming[earnings.symbol] = earnings
    
    def get_upcoming(self, days: int = 7) -> list[EarningsData]:
        """Get earnings in next N days."""
        cutoff = datetime.utcnow() + timedelta(days=days)
        return [
            e for e in self.upcoming.values()
            if e.reported_date <= cutoff
        ]
    
    async def process_reported(self, earnings: EarningsData) -> EarningsFeatures:
        """Process reported earnings."""
        features = await self.extractor.extract(earnings)
        
        # Store historical
        if earnings.symbol not in self.historical:
            self.historical[earnings.symbol] = []
        self.historical[earnings.symbol].append(features)
        
        # Remove from upcoming
        self.upcoming.pop(earnings.symbol, None)
        
        return features
    
    def get_history(self, symbol: str, periods: int = 4) -> list[EarningsFeatures]:
        """Get historical earnings features."""
        return self.historical.get(symbol, [])[-periods:]
    
    def get_surprise_streak(self, symbol: str) -> dict:
        """Get earnings surprise streak."""
        history = self.get_history(symbol, 8)
        if not history:
            return {"streak": 0, "direction": "none", "avg_surprise": 0}
        
        surprises = [h.eps_surprise for h in history]
        positive = sum(1 for s in surprises if s > 0.01)
        negative = sum(1 for s in surprises if s < -0.01)
        
        # Current streak
        streak = 0
        direction = "none"
        for s in reversed(surprises):
            if streak == 0:
                if s > 0.01:
                    direction = "positive"
                    streak = 1
                elif s < -0.01:
                    direction = "negative"
                    streak = 1
            elif direction == "positive" and s > 0.01:
                streak += 1
            elif direction == "negative" and s < -0.01:
                streak += 1
            else:
                break
        
        return {
            "streak": streak,
            "direction": direction,
            "avg_surprise": sum(surprises) / len(surprises),
            "beat_rate": positive / len(surprises),
            "recent_surprises": surprises[-4:],
        }


from datetime import timedelta


__all__ = [
    "EarningsFeatureExtractor",
    "EarningsData",
    "EarningsFeatures",
    "EarningsCalendarTracker",
]