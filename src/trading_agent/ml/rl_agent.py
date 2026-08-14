#!/usr/bin/env python3
"""
Reinforcement Learning Agent — DQN/PPO for trading.

Components:
1. TradingEnvironment — gym-compatible env for price series
2. DQNAgent — Deep Q-Network for discrete actions
3. PPOAgent — Proximal Policy Optimization for continuous actions
4. RLMetrics — training metrics tracking
5. Model save/load for deployment

Design:
    env = TradingEnvironment(df, window=60)
    agent = DQNAgent(state_dim=60*5, action_dim=3)  # buy/sell/hold
    agent.train(env, episodes=100)
    action = agent.predict(state)
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field

import numpy as np

# ── Trading Environment ───────────────────────────────────────


class TradingEnvironment:
    """
    Gym-compatible trading environment.

    State: window of OHLCV + indicators (normalized)
    Actions: 0=hold, 1=buy, 2=sell
    Reward: PnL-based with risk penalty
    """

    def __init__(
        self,
        df,
        window: int = 60,
        reward_scaling: float = 100,
        transaction_cost_bps: float = 10,
    ):
        self.window = window
        self.reward_scaling = reward_scaling
        self.txn_cost = transaction_cost_bps / 10_000

        # Precompute features
        self.features = self._build_features(df)
        self.prices = df["close"].values
        self.n_steps = len(self.features) - window
        self.reset()

    def _build_features(self, df) -> np.ndarray:
        """Build feature matrix: returns + volatility + volume + momentum."""
        close = df["close"].values.astype(float)
        vol = df["volume"].values.astype(float)

        rets = np.diff(close) / close[:-1]
        rets = np.concatenate([[0], rets])

        # Rolling features
        def rolling(arr, w):
            out = np.zeros_like(arr)
            for i in range(len(arr)):
                start = max(0, i - w + 1)
                out[i] = np.mean(arr[start : i + 1])
            return out

        vol_ma = rolling(vol, 20) / (rolling(vol, 5) + 1e-9)
        ret_ma5 = rolling(rets, 5)
        ret_ma20 = rolling(rets, 20)
        realized_vol = rolling(rets**2, 20) ** 0.5

        features = np.column_stack([rets, vol_ma, ret_ma5, ret_ma20, realized_vol])
        # Normalize
        for i in range(features.shape[1]):
            std = np.std(features[:, i])
            if std > 0:
                features[:, i] = (features[:, i] - np.mean(features[:, i])) / std
        return features

    def reset(self):
        self.step_idx = 0
        self.position = 0  # 0=flat, 1=long, -1=short
        self.entry_price = 0
        self.total_pnl = 0
        self.trades = []
        return self._get_state()

    def _get_state(self) -> np.ndarray:
        return self.features[self.step_idx : self.step_idx + self.window].flatten()

    def step(self, action: int) -> tuple:
        """Execute action, return (state, reward, done, info)."""
        assert 0 <= action <= 2, f"Invalid action: {action}"
        current_price = self.prices[self.step_idx + self.window]

        reward = 0
        info = {"action": action, "position": self.position}

        if action == 1 and self.position == 0:  # buy
            self.position = 1
            self.entry_price = current_price * (1 + self.txn_cost)
            self.trades.append(
                {"type": "buy", "price": self.entry_price, "step": self.step_idx}
            )
            info["trade"] = "buy"

        elif action == 2 and self.position == 0:  # sell (short)
            self.position = -1
            self.entry_price = current_price * (1 - self.txn_cost)
            self.trades.append(
                {"type": "sell", "price": self.entry_price, "step": self.step_idx}
            )
            info["trade"] = "short"

        elif action == 1 and self.position == -1:  # close short
            pnl = (self.entry_price - current_price) / self.entry_price - self.txn_cost
            reward = pnl * self.reward_scaling
            self.total_pnl += pnl
            info["pnl"] = pnl
            self.position = 0

        elif action == 2 and self.position == 1:  # close long
            pnl = (current_price - self.entry_price) / self.entry_price - self.txn_cost
            reward = pnl * self.reward_scaling
            self.total_pnl += pnl
            info["pnl"] = pnl
            self.position = 0

        elif self.position != 0:  # hold with position — unrealized PnL
            if self.position == 1:
                unrealized = (current_price - self.entry_price) / self.entry_price
            else:
                unrealized = (self.entry_price - current_price) / self.entry_price
            reward = unrealized * 0.01  # small reward for holding in right direction

        self.step_idx += 1
        done = self.step_idx >= self.n_steps - 1
        next_state = self._get_state() if not done else np.zeros(self.window * 5)
        return next_state, reward, done, info

    @property
    def state_dim(self) -> int:
        return self.window * 5

    @property
    def action_dim(self) -> int:
        return 3


# ── DQN Agent ────────────────────────────────────────────────


class DQNAgent:
    """
    Deep Q-Network agent for discrete actions.
    Pure numpy implementation (no PyTorch/TensorFlow dependency).
    Uses linear approximation for simplicity; swap for neural net in production.
    """

    def __init__(
        self,
        state_dim: int,
        action_dim: int = 3,
        lr: float = 0.001,
        gamma: float = 0.99,
        epsilon_start: float = 1.0,
        epsilon_end: float = 0.01,
        epsilon_decay: int = 500,
        memory_size: int = 10_000,
    ):
        self.state_dim = state_dim
        self.action_dim = action_dim
        self.lr = lr
        self.gamma = gamma
        self.epsilon = epsilon_start
        self.epsilon_end = epsilon_end
        self.epsilon_decay = epsilon_decay
        self.memory = deque(maxlen=memory_size)

        # Linear Q approximation: Q(s,a) = s @ W_a + b_a
        self.W = np.random.randn(state_dim, action_dim) * 0.01
        self.b = np.zeros(action_dim)
        self.steps = 0

    def predict(self, state: np.ndarray) -> int:
        q_values = state @ self.W + self.b
        return int(np.argmax(q_values))

    def act(self, state: np.ndarray) -> int:
        if random.random() < self.epsilon:
            return random.randint(0, self.action_dim - 1)
        return self.predict(state)

    def remember(self, state, action, reward, next_state, done):
        self.memory.append((state, action, reward, next_state, done))

    def train(self, batch_size: int = 32) -> float:
        if len(self.memory) < batch_size:
            return 0.0

        batch = random.sample(self.memory, batch_size)
        losses = []

        for state, action, reward, next_state, done in batch:
            current_q = state @ self.W + self.b
            target_q = current_q.copy()

            if done:
                target_q[action] = reward
            else:
                next_q = next_state @ self.W + self.b
                target_q[action] = reward + self.gamma * np.max(next_q)

            # Gradient descent on MSE
            error = target_q - current_q
            grad = np.outer(state, error)
            self.W += self.lr * grad
            self.b += self.lr * error
            losses.append(np.mean(error**2))

        self.steps += 1
        self.epsilon = max(
            self.epsilon_end, self.epsilon * math.exp(-1 / self.epsilon_decay)
        )
        return np.mean(losses)

    def save(self, path: str):
        np.savez(path, W=self.W, b=self.b)

    def load(self, path: str):
        data = np.load(path)
        self.W = data["W"]
        self.b = data["b"]


# ── PPO Agent (simplified) ──────────────────────────────────


class PPOAgent:
    """
    Simplified PPO agent for continuous action space.
    Uses linear policy with clipped objective.
    """

    def __init__(
        self,
        state_dim: int,
        action_dim: int = 3,
        lr: float = 0.001,
        gamma: float = 0.99,
        clip_epsilon: float = 0.2,
    ):
        self.state_dim = state_dim
        self.action_dim = action_dim
        self.lr = lr
        self.gamma = gamma
        self.clip_epsilon = clip_epsilon

        # Policy: softmax(state @ W_policy)
        self.W_policy = np.random.randn(state_dim, action_dim) * 0.01
        # Value: state @ W_value
        self.W_value = np.random.randn(state_dim, 1) * 0.01

    def get_action_probs(self, state: np.ndarray) -> np.ndarray:
        logits = state @ self.W_policy
        exp_logits = np.exp(logits - np.max(logits))
        return exp_logits / exp_logits.sum()

    def get_value(self, state: np.ndarray) -> float:
        result = state @ self.W_value
        return float(result.item())

    def act(self, state: np.ndarray) -> int:
        probs = self.get_action_probs(state)
        return int(np.random.choice(self.action_dim, p=probs))

    def train_episode(self, states, actions, rewards) -> dict:
        """One PPO update step on collected episode."""
        n = len(states)
        returns = np.zeros(n)
        G = 0
        for t in reversed(range(n)):
            G = rewards[t] + self.gamma * G
            returns[t] = G

        # Normalize returns
        returns = (returns - returns.mean()) / (returns.std() + 1e-8)

        # Compute advantages
        values = np.array([self.get_value(s) for s in states])
        advantages = returns - values

        # Policy gradient with clipping
        old_probs = np.array([self.get_action_probs(s) for s in states])
        new_probs = old_probs.copy()
        for i, (s, a) in enumerate(zip(states, actions)):
            probs = self.get_action_probs(s)
            ratio = probs[a] / (old_probs[i, a] + 1e-8)
            clipped = np.clip(ratio, 1 - self.clip_epsilon, 1 + self.clip_epsilon)
            loss = -min(ratio * advantages[i], clipped * advantages[i])
            # Gradient update
            grad = np.outer(s, new_probs[i])
            grad[:, a] -= s
            self.W_policy += self.lr * loss * grad

        # Value update
        for i, s in enumerate(states):
            pred = self.get_value(s)
            error = returns[i] - pred
            self.W_value += self.lr * error * s.reshape(-1, 1)

        return {
            "mean_return": float(np.mean(rewards)),
            "mean_advantage": float(np.mean(advantages)),
        }


# ── RL Metrics ───────────────────────────────────────────────


@dataclass
class RLMetrics:
    episode_rewards: list[float] = field(default_factory=list)
    episode_lengths: list[int] = field(default_factory=list)
    losses: list[float] = field(default_factory=list)
    epsilons: list[float] = field(default_factory=list)
    win_rates: list[float] = field(default_factory=list)
    total_trades: int = 0
    best_reward: float = float("-inf")

    def record_episode(
        self,
        reward: float,
        length: int,
        loss: float = 0,
        epsilon: float = 0,
        trades: int = 0,
    ):
        self.episode_rewards.append(reward)
        self.episode_lengths.append(length)
        self.losses.append(loss)
        self.epsilons.append(epsilon)
        self.total_trades += trades
        if reward > self.best_reward:
            self.best_reward = reward
        # Win rate (reward > 0)
        recent = self.episode_rewards[-100:]
        self.win_rates.append(sum(1 for r in recent if r > 0) / len(recent))

    def summary(self) -> dict:
        if not self.episode_rewards:
            return {"n_episodes": 0}
        return {
            "n_episodes": len(self.episode_rewards),
            "mean_reward": np.mean(self.episode_rewards),
            "std_reward": np.std(self.episode_rewards),
            "best_reward": self.best_reward,
            "mean_length": np.mean(self.episode_lengths),
            "current_win_rate": self.win_rates[-1] if self.win_rates else 0,
            "total_trades": self.total_trades,
            "current_epsilon": self.epsilons[-1] if self.epsilons else 0,
            "mean_loss": np.mean(self.losses) if self.losses else 0,
        }


if __name__ == "__main__":
    print("=" * 60)
    print("RL AGENT — DEMO")
    print("=" * 60)

    # Synthetic data
    np.random.seed(42)
    n = 2000
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    import pandas as pd

    df = pd.DataFrame(
        {
            "open": close + np.random.randn(n) * 0.1,
            "high": close + abs(np.random.randn(n) * 0.3),
            "low": close - abs(np.random.randn(n) * 0.3),
            "close": close,
            "volume": np.random.exponential(1000, n),
        }
    )

    env = TradingEnvironment(df, window=30)
    agent = DQNAgent(state_dim=env.state_dim, action_dim=env.action_dim)
    metrics = RLMetrics()

    print("\nTraining DQN for 50 episodes...")
    for ep in range(50):
        state = env.reset()
        total_reward = 0
        steps = 0
        while True:
            action = agent.act(state)
            next_state, reward, done, info = env.step(action)
            agent.remember(state, action, reward, next_state, done)
            state = next_state
            total_reward += reward
            steps += 1
            if done:
                break
        loss = agent.train(batch_size=32)
        metrics.record_episode(
            total_reward, steps, loss, agent.epsilon, len(env.trades)
        )

    print("\nTraining Complete:")
    s = metrics.summary()
    for k, v in s.items():
        print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")

    # Test
    state = env.reset()
    actions_taken = []
    while True:
        action = agent.predict(state)
        actions_taken.append(action)
        next_state, reward, done, info = env.step(action)
        state = next_state
        if done:
            break
    print(f"\nTest: {len(actions_taken)} steps, PnL={env.total_pnl:.4f}")
    print(
        f"Actions: buy={actions_taken.count(1)}, sell={actions_taken.count(2)}, hold={actions_taken.count(0)}"
    )
