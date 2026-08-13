#!/usr/bin/env python3
"""
Feature Importance & Auto-Alpha Generation.

Components:
1. FeatureImportance — permutation importance, correlation analysis
2. AutoAlphaGenerator — genetic algorithm to evolve new alpha factors
3. AlphaMutator — mutate/cross alpha expressions

Design:
    fi = FeatureImportance(df, target_col="returns_5d")
    rankings = fi.compute_importance(alphas)
    gen = AutoAlphaGenerator(alpha_library)
    new_alphas = gen.evolve(n_generations=10, population_size=50)
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field

import numpy as np


# ── Feature Importance ───────────────────────────────────────


@dataclass
class ImportanceResult:
    name: str
    importance: float  # 0-1 normalized
    ic_with_target: float
    correlation_with_others: dict = field(default_factory=dict)
    rank: int = 0


class FeatureImportance:
    """
    Feature importance via:
    1. Permutation importance (model-agnostic)
    2. Rank IC with target variable
    3. Correlation matrix for redundancy detection
    """

    def __init__(self, df=None, target_col: str = "returns_5d"):
        self.df = df
        self.target_col = target_col

    def compute_importance(
        self,
        alpha_values: dict[str, np.ndarray],
        target: np.ndarray | None = None,
        n_repeats: int = 10,
    ) -> list[ImportanceResult]:
        """Compute feature importance via permutation + IC."""
        if target is None and self.df is not None:
            close = self.df["close"].values
            target = np.diff(close) / close[:-1]
            target = np.concatenate([[0], target])

        if target is None:
            return []

        results = []
        from scipy import stats as sp_stats

        for name, values in alpha_values.items():
            valid = ~(np.isnan(values) | np.isnan(target))
            if valid.sum() < 20:
                continue

            # IC with target
            ic, _ = sp_stats.spearmanr(values[valid], target[valid])
            ic = float(ic) if not np.isnan(ic) else 0

            # Permutation importance
            base_score = abs(ic)
            perm_scores = []
            for _ in range(n_repeats):
                perm = np.random.permutation(values[valid])
                perm_ic, _ = sp_stats.spearmanr(perm, target[valid])
                perm_scores.append(abs(perm_ic) if not np.isnan(perm_ic) else 0)
            importance = base_score - np.mean(perm_scores)

            results.append(
                ImportanceResult(
                    name=name,
                    importance=max(importance, 0),
                    ic_with_target=ic,
                )
            )

        # Normalize
        max_imp = max((r.importance for r in results), default=1) or 1
        for r in results:
            r.importance /= max_imp

        results.sort(key=lambda r: r.importance, reverse=True)
        for i, r in enumerate(results):
            r.rank = i + 1

        return results

    def detect_redundancy(
        self, alpha_values: dict[str, np.ndarray], threshold: float = 0.8
    ) -> list[tuple[str, str, float]]:
        """Find pairs of highly correlated alphas (redundant)."""
        from scipy import stats as sp_stats

        pairs = []
        names = list(alpha_values.keys())
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                v1, v2 = alpha_values[names[i]], alpha_values[names[j]]
                valid = ~(np.isnan(v1) | np.isnan(v2))
                if valid.sum() > 10:
                    corr, _ = sp_stats.spearmanr(v1[valid], v2[valid])
                    if not np.isnan(corr) and abs(corr) > threshold:
                        pairs.append((names[i], names[j], round(float(corr), 3)))
        return sorted(pairs, key=lambda x: abs(x[2]), reverse=True)


# ── Auto-Alpha Generator ────────────────────────────────────


@dataclass
class AlphaExpression:
    """A tree-based alpha expression."""

    op: str  # "ema", "sma", "roc", "rank", "add", "mul", "sub", "div", "ts_mean", "ts_std", "data"
    children: list = field(default_factory=list)
    param: float = 0.0
    name: str = ""


class AlphaGenerator:
    """
    Generates new alpha factors via genetic programming.

    Expression tree: operations on OHLCV data that produce a signal.
    Fitness = abs(IC) with forward returns, penalizing complexity.
    """

    LEAF_OPS = ["data:close", "data:open", "data:high", "data:low", "data:volume"]
    UNARY_OPS = [
        "ema_5",
        "ema_10",
        "ema_20",
        "sma_5",
        "sma_10",
        "sma_20",
        "ts_rank_10",
        "ts_rank_20",
        "ts_std_10",
        "ts_std_20",
        "log",
        "abs",
        "sign",
        "neg",
        "delta_1",
        "delta_5",
    ]
    BINARY_OPS = ["add", "sub", "mul", "div", "ts_corr_10", "ts_corr_20"]

    def __init__(self, max_depth: int = 4):
        self.max_depth = max_depth

    def random_tree(self, depth: int = 0) -> AlphaExpression:
        if depth >= self.max_depth or (depth > 1 and random.random() < 0.3):
            return AlphaExpression(op=random.choice(self.LEAF_OPS))
        if random.random() < 0.5:
            child = self.random_tree(depth + 1)
            return AlphaExpression(op=random.choice(self.UNARY_OPS), children=[child])
        left = self.random_tree(depth + 1)
        right = self.random_tree(depth + 1)
        return AlphaExpression(
            op=random.choice(self.BINARY_OPS), children=[left, right]
        )

    def evaluate(
        self, tree: AlphaExpression, data: dict[str, np.ndarray]
    ) -> np.ndarray:
        """Evaluate expression tree on data."""
        op = tree.op
        if op.startswith("data:"):
            key = op.split(":")[1]
            return data.get(key, np.zeros(len(data.get("close", []))))

        if len(tree.children) == 1:
            child = self.evaluate(tree.children[0], data)
            return self._apply_unary(op, child)
        elif len(tree.children) == 2:
            left = self.evaluate(tree.children[0], data)
            right = self.evaluate(tree.children[1], data)
            return self._apply_binary(op, left, right)
        return np.zeros(len(data.get("close", [])))

    def _apply_unary(self, op: str, x: np.ndarray) -> np.ndarray:
        if op.startswith("ema_"):
            period = int(op.split("_")[1])
            return self._ema(x, period)
        elif op.startswith("sma_"):
            period = int(op.split("_")[1])
            return self._sma(x, period)
        elif op.startswith("ts_rank_"):
            period = int(op.split("_")[2])
            return self._ts_rank(x, period)
        elif op.startswith("ts_std_"):
            period = int(op.split("_")[2])
            return self._ts_std(x, period)
        elif op == "log":
            return np.log(np.abs(x) + 1e-9)
        elif op == "abs":
            return np.abs(x)
        elif op == "sign":
            return np.sign(x)
        elif op == "neg":
            return -x
        elif op.startswith("delta_"):
            period = int(op.split("_")[1])
            return np.diff(x, period, prepend=np.nan)
        return x

    def _apply_binary(self, op: str, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        if op == "add":
            return x + y
        elif op == "sub":
            return x - y
        elif op == "mul":
            return x * y
        elif op == "div":
            return x / (y + 1e-9)
        elif op.startswith("ts_corr_"):
            period = int(op.split("_")[2])
            return self._ts_corr(x, y, period)
        return x

    @staticmethod
    def _ema(x: np.ndarray, period: int) -> np.ndarray:
        alpha = 2 / (period + 1)
        out = np.zeros_like(x, dtype=float)
        out[0] = x[0]
        for i in range(1, len(x)):
            out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
        return out

    @staticmethod
    def _sma(x: np.ndarray, period: int) -> np.ndarray:
        out = np.zeros_like(x, dtype=float)
        for i in range(len(x)):
            start = max(0, i - period + 1)
            out[i] = np.mean(x[start : i + 1])
        return out

    @staticmethod
    def _ts_rank(x: np.ndarray, period: int) -> np.ndarray:
        out = np.zeros_like(x, dtype=float)
        for i in range(len(x)):
            start = max(0, i - period + 1)
            window = x[start : i + 1]
            out[i] = np.mean(window <= x[i])
        return out

    @staticmethod
    def _ts_std(x: np.ndarray, period: int) -> np.ndarray:
        out = np.zeros_like(x, dtype=float)
        for i in range(len(x)):
            start = max(0, i - period + 1)
            out[i] = np.std(x[start : i + 1])
        return out

    @staticmethod
    def _ts_corr(x: np.ndarray, y: np.ndarray, period: int) -> np.ndarray:
        out = np.zeros_like(x, dtype=float)
        for i in range(len(x)):
            start = max(0, i - period + 1)
            xi, yi = x[start : i + 1], y[start : i + 1]
            if len(xi) > 2 and np.std(xi) > 0 and np.std(yi) > 0:
                from scipy import stats as sp_stats

                corr, _ = sp_stats.spearmanr(xi, yi)
                out[i] = corr if not np.isnan(corr) else 0
        return out

    def tree_to_str(self, tree: AlphaExpression, depth: int = 0) -> str:
        """Convert tree to human-readable string."""
        if not tree.children:
            return tree.op
        if len(tree.children) == 1:
            return f"{tree.op}({self.tree_to_str(tree.children[0], depth + 1)})"
        return f"{tree.op}({self.tree_to_str(tree.children[0], depth + 1)}, {self.tree_to_str(tree.children[1], depth + 1)})"

    def crossover(
        self, parent1: AlphaExpression, parent2: AlphaExpression
    ) -> AlphaExpression:
        """Subtree crossover."""
        child = copy.deepcopy(parent1)
        # Randomly select a node in child and replace with subtree from parent2
        nodes = self._collect_nodes(child)
        if nodes:
            target = random.choice(nodes)
            donor_subtree = random.choice(self._collect_nodes(parent2))
            # Find parent of target and replace
            self._replace_random(child, donor_subtree)
        return child

    def mutate(self, tree: AlphaExpression) -> AlphaExpression:
        """Random mutation: replace a random node."""
        mutant = copy.deepcopy(tree)
        nodes = self._collect_nodes(mutant)
        if nodes:
            target = random.choice(nodes)
            target.op = random.choice(self.LEAF_OPS + self.UNARY_OPS)
            target.children = (
                [] if target.op.startswith("data:") else [self.random_tree(1)]
            )
        return mutant

    def _collect_nodes(self, tree: AlphaExpression) -> list[AlphaExpression]:
        nodes = [tree]
        for child in tree.children:
            nodes.extend(self._collect_nodes(child))
        return nodes

    def _replace_random(self, tree: AlphaExpression, replacement: AlphaExpression):
        """Replace a random subtree."""
        if tree.children and random.random() < 0.5:
            idx = random.randint(0, len(tree.children) - 1)
            tree.children[idx] = replacement
        else:
            tree.op = replacement.op
            tree.children = replacement.children


class AutoAlphaGenerator:
    """
    Genetic algorithm to evolve alpha factors.

    Population: list of expression trees
    Fitness: abs(IC with forward returns) - complexity penalty
    Selection: tournament
    Operations: crossover, mutation, reproduction
    """

    def __init__(self, max_depth: int = 4, complexity_penalty: float = 0.01):
        self.gen = AlphaGenerator(max_depth=max_depth)
        self.complexity_penalty = complexity_penalty

    def evolve(
        self,
        data: dict[str, np.ndarray],
        target: np.ndarray,
        population_size: int = 30,
        n_generations: int = 10,
        tournament_size: int = 5,
    ) -> list[dict]:
        """
        Run genetic evolution. Returns sorted list of best alphas.
        Each: {"tree": AlphaExpression, "fitness": float, "ic": float, "expression": str}
        """
        from scipy import stats as sp_stats

        # Initialize population
        population = [self.gen.random_tree() for _ in range(population_size)]

        history = []
        for gen in range(n_generations):
            # Evaluate fitness
            scored = []
            for tree in population:
                try:
                    values = self.gen.evaluate(tree, data)
                    valid = ~(np.isnan(values) | np.isnan(target))
                    if valid.sum() < 20:
                        continue
                    ic, _ = sp_stats.spearmanr(values[valid], target[valid])
                    ic = abs(float(ic)) if not np.isnan(ic) else 0
                    complexity = self._tree_depth(tree)
                    fitness = ic - self.complexity_penalty * complexity
                    scored.append(
                        {
                            "tree": tree,
                            "fitness": fitness,
                            "ic": ic,
                            "expression": self.gen.tree_to_str(tree),
                        }
                    )
                except Exception:
                    continue

            scored.sort(key=lambda x: x["fitness"], reverse=True)
            if scored:
                best = scored[0]
                history.append(
                    {
                        "generation": gen,
                        "best_fitness": best["fitness"],
                        "best_ic": best["ic"],
                        "best_expr": best["expression"],
                        "population_viable": len(scored),
                    }
                )

            if len(scored) < 2:
                break

            # Selection + reproduction
            new_pop = [
                copy.deepcopy(scored[i]["tree"]) for i in range(min(5, len(scored)))
            ]  # elitism
            while len(new_pop) < population_size:
                r = random.random()
                if r < 0.6:  # crossover
                    p1 = self._tournament(scored, tournament_size)
                    p2 = self._tournament(scored, tournament_size)
                    child = self.gen.crossover(p1["tree"], p2["tree"])
                    new_pop.append(child)
                elif r < 0.9:  # mutation
                    parent = self._tournament(scored, tournament_size)
                    child = self.gen.mutate(parent["tree"])
                    new_pop.append(child)
                else:  # random
                    new_pop.append(self.gen.random_tree())

            population = new_pop[:population_size]

        # Final evaluation
        final = []
        for tree in population:
            try:
                values = self.gen.evaluate(tree, data)
                valid = ~(np.isnan(values) | np.isnan(target))
                if valid.sum() < 20:
                    continue
                ic, _ = sp_stats.spearmanr(values[valid], target[valid])
                ic = abs(float(ic)) if not np.isnan(ic) else 0
                final.append(
                    {
                        "tree": tree,
                        "ic": ic,
                        "expression": self.gen.tree_to_str(tree),
                    }
                )
            except Exception:
                continue
        final.sort(key=lambda x: x["ic"], reverse=True)
        return final[:10]

    def _tournament(self, scored: list[dict], size: int) -> dict:
        contestants = random.sample(scored, min(size, len(scored)))
        return max(contestants, key=lambda x: x["fitness"])

    def _tree_depth(self, tree: AlphaExpression) -> int:
        if not tree.children:
            return 0
        return 1 + max(self._tree_depth(c) for c in tree.children)


if __name__ == "__main__":
    import pandas as pd

    print("=" * 60)
    print("FEATURE IMPORTANCE & AUTO-ALPHA — DEMO")
    print("=" * 60)

    # Synthetic data
    np.random.seed(42)
    n = 500
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame(
        {
            "open": close + np.random.randn(n) * 0.1,
            "high": close + abs(np.random.randn(n) * 0.3),
            "low": close - abs(np.random.randn(n) * 0.3),
            "close": close,
            "volume": np.random.exponential(1000, n),
        }
    )

    # Feature Importance
    from src.trading_agent.alpha_research.pipeline import _make_library

    lib = _make_library()
    target = np.diff(close) / close[:-1]
    target = np.concatenate([[0], target])
    alpha_values = {}
    for info in lib.list_alphas()[:15]:
        try:
            vals = lib.compute(info["name"], df)
            alpha_values[info["name"]] = (
                vals.values if hasattr(vals, "values") else np.array(vals)
            )
        except Exception:
            continue

    fi = FeatureImportance()
    rankings = fi.compute_importance(alpha_values, target=target)
    print(f"\nFeature Importance ({len(rankings)} features):")
    for r in rankings[:10]:
        print(
            f"  #{r.rank:2d} {r.name:30s} imp={r.importance:.3f} IC={r.ic_with_target:+.4f}"
        )

    redundancy = fi.detect_redundancy(alpha_values, threshold=0.8)
    if redundancy:
        print("\nRedundant pairs (>0.8 correlation):")
        for n1, n2, corr in redundancy[:5]:
            print(f"  {n1} ↔ {n2}: {corr}")

    # Auto-Alpha
    print("\nAuto-Alpha Generation (20 gen, pop=20)...")
    data = {col: df[col].values for col in ["open", "high", "low", "close", "volume"]}
    gen = AutoAlphaGenerator(max_depth=3)
    new_alphas = gen.evolve(data, target, population_size=20, n_generations=20)
    print("\nTop discovered alphas:")
    for a in new_alphas[:5]:
        print(f"  IC={a['ic']:.4f}  {a['expression']}")
