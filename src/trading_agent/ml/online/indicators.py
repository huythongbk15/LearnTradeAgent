"""Online learning indicators using River/Crema-style algorithms."""

import math
from abc import ABC, abstractmethod
from collections import deque
from typing import Optional


class OnlineIndicator(ABC):
    """Base class for online (streaming) indicators."""

    @abstractmethod
    def update(self, value: float) -> float:
        """Update indicator with new value, return current value."""
        pass

    @abstractmethod
    def reset(self) -> None:
        """Reset indicator state."""
        pass

    @property
    @abstractmethod
    def is_ready(self) -> bool:
        """Whether indicator has enough data."""
        pass


class OnlineEMA(OnlineIndicator):
    """Exponential Moving Average (streaming)."""

    def __init__(self, period: int, alpha: Optional[float] = None):
        self.period = period
        self.alpha = alpha or (2.0 / (period + 1))
        self.value: Optional[float] = None
        self._initialized = False

    def update(self, value: float) -> float:
        if not self._initialized:
            self.value = value
            self._initialized = True
        else:
            self.value = self.alpha * value + (1 - self.alpha) * self.value
        return self.value

    def reset(self) -> None:
        self.value = None
        self._initialized = False

    @property
    def is_ready(self) -> bool:
        return self._initialized


class OnlineSMA(OnlineIndicator):
    """Simple Moving Average (streaming)."""

    def __init__(self, period: int):
        self.period = period
        self.values = deque(maxlen=period)
        self._sum = 0.0

    def update(self, value: float) -> float:
        self.values.append(value)
        self._sum += value
        if len(self.values) > self.period:
            self._sum -= self.values[0]
        return self._sum / len(self.values) if self.values else 0.0

    def reset(self) -> None:
        self.values.clear()
        self._sum = 0.0

    @property
    def is_ready(self) -> bool:
        return len(self.values) >= self.period

    @property
    def value(self) -> Optional[float]:
        """Current SMA value (None until first update)."""
        if not self.values:
            return None
        return self._sum / len(self.values)


class OnlineRSI(OnlineIndicator):
    """Relative Strength Index (streaming)."""

    def __init__(self, period: int = 14):
        self.period = period
        self.gains = deque(maxlen=period)
        self.losses = deque(maxlen=period)
        self.prev_close: Optional[float] = None
        self._avg_gain: Optional[float] = None
        self._avg_loss: Optional[float] = None

    def update(self, close: float) -> float:
        if self.prev_close is None:
            self.prev_close = close
            return 50.0  # Neutral

        change = close - self.prev_close
        gain = max(change, 0)
        loss = max(-change, 0)

        self.gains.append(gain)
        self.losses.append(loss)

        if len(self.gains) < self.period:
            self.prev_close = close
            return 50.0

        # Wilder's smoothing
        if self._avg_gain is None:
            self._avg_gain = sum(self.gains) / self.period
            self._avg_loss = sum(self.losses) / self.period
        else:
            self._avg_gain = (self._avg_gain * (self.period - 1) + gain) / self.period
            self._avg_loss = (self._avg_loss * (self.period - 1) + loss) / self.period

        self.prev_close = close

        if self._avg_loss == 0:
            return 100.0

        rs = self._avg_gain / self._avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def reset(self) -> None:
        self.gains.clear()
        self.losses.clear()
        self.prev_close = None
        self._avg_gain = None
        self._avg_loss = None

    @property
    def is_ready(self) -> bool:
        return len(self.gains) >= self.period


class OnlineBollingerBands(OnlineIndicator):
    """Bollinger Bands (streaming)."""

    def __init__(self, period: int = 20, num_std: float = 2.0):
        self.period = period
        self.num_std = num_std
        self.sma = OnlineSMA(period)
        self.squared_sma = OnlineSMA(period)

    def update(self, value: float) -> tuple[float, float, float]:
        """Returns (middle, upper, lower)."""
        middle = self.sma.update(value)
        self.squared_sma.update(value * value)

        if not self.sma.is_ready:
            return middle, middle, middle

        variance = self.squared_sma.value - middle * middle
        std = math.sqrt(max(variance, 0))

        upper = middle + self.num_std * std
        lower = middle - self.num_std * std

        return middle, upper, lower

    def reset(self) -> None:
        self.sma.reset()
        self.squared_sma.reset()

    @property
    def is_ready(self) -> bool:
        return self.sma.is_ready

    @property
    def value(self) -> tuple[float, float, float]:
        if self.sma.value is None:
            return 0.0, 0.0, 0.0
        middle = self.sma.value
        if not self.is_ready:
            return middle, middle, middle
        variance = self.squared_sma.value - middle * middle
        std = math.sqrt(max(variance, 0))
        upper = middle + self.num_std * std
        lower = middle - self.num_std * std
        return middle, upper, lower


class OnlineMACD(OnlineIndicator):
    """MACD (streaming)."""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self.fast_ema = OnlineEMA(fast)
        self.slow_ema = OnlineEMA(slow)
        self.signal_ema = OnlineEMA(signal)
        self.macd_value: Optional[float] = None
        self.signal_value: Optional[float] = None
        self.histogram: Optional[float] = None

    def update(self, value: float) -> tuple[float, float, float]:
        """Returns (macd, signal, histogram)."""
        fast_val = self.fast_ema.update(value)
        slow_val = self.slow_ema.update(value)

        self.macd_value = fast_val - slow_val
        self.signal_value = self.signal_ema.update(self.macd_value)
        self.histogram = self.macd_value - self.signal_value

        return self.macd_value, self.signal_value, self.histogram

    def reset(self) -> None:
        self.fast_ema.reset()
        self.slow_ema.reset()
        self.signal_ema.reset()
        self.macd_value = None
        self.signal_value = None
        self.histogram = None

    @property
    def is_ready(self) -> bool:
        return (
            self.fast_ema.is_ready
            and self.slow_ema.is_ready
            and self.signal_ema.is_ready
        )

    @property
    def value(self) -> tuple[float, float, float]:
        return (
            self.macd_value or 0.0,
            self.signal_value or 0.0,
            self.histogram or 0.0,
        )


class OnlineATR(OnlineIndicator):
    """Average True Range (streaming)."""

    def __init__(self, period: int = 14):
        self.period = period
        self.tr_values = deque(maxlen=period)
        self.prev_high: Optional[float] = None
        self.prev_low: Optional[float] = None
        self.prev_close: Optional[float] = None
        self._avg_tr: Optional[float] = None

    def update(self, high: float, low: float, close: float) -> float:
        if self.prev_close is None:
            self.prev_high = high
            self.prev_low = low
            self.prev_close = close
            return 0.0

        # True Range
        tr1 = high - low
        tr2 = abs(high - self.prev_close)
        tr3 = abs(low - self.prev_close)
        tr = max(tr1, tr2, tr3)

        self.tr_values.append(tr)

        if len(self.tr_values) < self.period:
            self.prev_high = high
            self.prev_low = low
            self.prev_close = close
            return 0.0

        # Wilder's smoothing
        if self._avg_tr is None:
            self._avg_tr = sum(self.tr_values) / self.period
        else:
            self._avg_tr = (self._avg_tr * (self.period - 1) + tr) / self.period

        self.prev_high = high
        self.prev_low = low
        self.prev_close = close

        return self._avg_tr

    def reset(self) -> None:
        self.tr_values.clear()
        self.prev_high = None
        self.prev_low = None
        self.prev_close = None
        self._avg_tr = None

    @property
    def is_ready(self) -> bool:
        return len(self.tr_values) >= self.period

    @property
    def value(self) -> float:
        return self._avg_tr or 0.0


class OnlineVWAP(OnlineIndicator):
    """Volume Weighted Average Price (streaming)."""

    def __init__(self):
        self.total_pv = 0.0  # price * volume
        self.total_volume = 0.0

    def update(self, price: float, volume: float) -> float:
        self.total_pv += price * volume
        self.total_volume += volume
        return self.total_pv / self.total_volume if self.total_volume > 0 else price

    def reset(self) -> None:
        self.total_pv = 0.0
        self.total_volume = 0.0

    @property
    def is_ready(self) -> bool:
        return self.total_volume > 0

    @property
    def value(self) -> float:
        return self.total_pv / self.total_volume if self.total_volume > 0 else 0.0


class OnlineStandardDeviation(OnlineIndicator):
    """Standard deviation (streaming, Welford's algorithm)."""

    def __init__(self, period: Optional[int] = None):
        self.period = period
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0  # Sum of squared differences
        self.values = deque(maxlen=period) if period else None

    def update(self, value: float) -> float:
        if self.values is not None:
            # Sliding window - remove old value
            if len(self.values) == self.period:
                old = self.values.popleft()
                self.count -= 1
                delta = old - self.mean
                self.mean -= delta / self.count if self.count > 0 else 0
                self.m2 -= delta * (old - self.mean)

        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (value - self.mean)

        if self.values is not None:
            self.values.append(value)

        if self.count < 2:
            return 0.0

        variance = self.m2 / (self.count - 1)
        return math.sqrt(variance)

    def reset(self) -> None:
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0
        if self.values:
            self.values.clear()

    @property
    def is_ready(self) -> bool:
        return (
            self.count >= 2 if self.period is None else len(self.values) >= self.period
        )

    @property
    def value(self) -> float:
        if self.count < 2:
            return 0.0
        return math.sqrt(self.m2 / (self.count - 1))


class OnlineCorrelation(OnlineIndicator):
    """Pearson correlation between two streams (streaming)."""

    def __init__(self, period: Optional[int] = None):
        self.period = period
        self.count = 0
        self.mean_x = 0.0
        self.mean_y = 0.0
        self.cov = 0.0
        self.var_x = 0.0
        self.var_y = 0.0

        self.values_x = deque(maxlen=period) if period else None
        self.values_y = deque(maxlen=period) if period else None

    def update(self, x: float, y: float) -> float:
        if self.values_x is not None:
            if len(self.values_x) == self.period:
                old_x = self.values_x.popleft()
                old_y = self.values_y.popleft()
                self.count -= 1
                if self.count > 0:
                    dx = old_x - self.mean_x
                    dy = old_y - self.mean_y
                    self.mean_x -= dx / self.count
                    self.mean_y -= dy / self.count
                    self.cov -= dx * (old_y - self.mean_y)
                    self.var_x -= dx * (old_x - self.mean_x)
                    self.var_y -= dy * (old_y - self.mean_y)

        self.count += 1
        dx = x - self.mean_x
        dy = y - self.mean_y
        self.mean_x += dx / self.count
        self.mean_y += dy / self.count
        self.cov += dx * (y - self.mean_y)
        self.var_x += dx * (x - self.mean_x)
        self.var_y += dy * (y - self.mean_y)

        if self.values_x is not None:
            self.values_x.append(x)
            self.values_y.append(y)

        if self.count < 2 or self.var_x <= 0 or self.var_y <= 0:
            return 0.0

        return self.cov / math.sqrt(self.var_x * self.var_y)

    def reset(self) -> None:
        self.count = 0
        self.mean_x = 0.0
        self.mean_y = 0.0
        self.cov = 0.0
        self.var_x = 0.0
        self.var_y = 0.0
        if self.values_x:
            self.values_x.clear()
            self.values_y.clear()

    @property
    def is_ready(self) -> bool:
        return self.count >= 2

    @property
    def value(self) -> float:
        if self.count < 2 or self.var_x <= 0 or self.var_y <= 0:
            return 0.0
        return self.cov / math.sqrt(self.var_x * self.var_y)
