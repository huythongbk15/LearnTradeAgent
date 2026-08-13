"""Meta-learning for fast strategy adaptation (MAML/Reptile)."""

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
import numpy as np


@dataclass
class MetaLearningConfig:
    """Configuration for meta-learning."""

    # MAML
    meta_lr: float = 0.01
    inner_lr: float = 0.1
    inner_steps: int = 5
    meta_batch_size: int = 4

    # Reptile
    reptile_lr: float = 0.1
    reptile_steps: int = 10
    reptile_meta_lr: float = 0.1

    # General
    first_order: bool = True  # First-order approximation


class Task(ABC):
    """Abstract task for meta-learning."""

    @abstractmethod
    def sample_data(self, n: int) -> tuple[np.ndarray, np.ndarray]:
        """Sample n data points for this task."""
        pass

    @abstractmethod
    def evaluate(self, params: dict, X: np.ndarray, y: np.ndarray) -> float:
        """Evaluate parameters on task data."""
        pass

    @abstractmethod
    def gradient(self, params: dict, X: np.ndarray, y: np.ndarray) -> dict:
        """Compute gradient of loss w.r.t params."""
        pass


class MetaLearner(ABC):
    """Base meta-learner."""

    def __init__(self, config: MetaLearningConfig):
        self.config = config
        self.meta_params: dict = {}

    @abstractmethod
    def meta_train(self, tasks: list[Task], steps: int) -> dict:
        """Meta-train on distribution of tasks."""
        pass

    @abstractmethod
    def adapt(self, task: Task, n_samples: int = 10) -> dict:
        """Fast adaptation to new task."""
        pass


class MAML(MetaLearner):
    """Model-Agnostic Meta-Learning (MAML)."""

    def __init__(self, config: MetaLearningConfig, initial_params: dict):
        super().__init__(config)
        self.meta_params = initial_params.copy()

    def _inner_update(
        self, task: Task, params: dict, X: np.ndarray, y: np.ndarray
    ) -> dict:
        """Single inner gradient step."""
        grad = task.gradient(params, X, y)
        updated = {}
        for key in params:
            updated[key] = params[key] - self.config.inner_lr * grad.get(key, 0)
        return updated

    def _inner_loop(self, task: Task, X: np.ndarray, y: np.ndarray) -> dict:
        """Run inner loop adaptation."""
        params = self.meta_params.copy()
        for _ in range(self.config.inner_steps):
            params = self._inner_update(task, params, X, y)
        return params

    def meta_train(self, tasks: list[Task], steps: int) -> dict:
        """MAML meta-training."""
        for step in range(steps):
            # Sample batch of tasks
            batch_tasks = random.sample(
                tasks, min(self.config.meta_batch_size, len(tasks))
            )

            meta_grads = {key: 0.0 for key in self.meta_params}

            for task in batch_tasks:
                # Sample support and query sets
                X_support, y_support = task.sample_data(20)
                X_query, y_query = task.sample_data(20)

                # Inner loop
                adapted_params = self._inner_loop(task, X_support, y_support)

                # Compute meta-gradient on query set
                if self.config.first_order:
                    # First-order: use adapted params gradient
                    query_grad = task.gradient(adapted_params, X_query, y_query)
                else:
                    # Second-order: would need Hessian-vector product
                    query_grad = task.gradient(adapted_params, X_query, y_query)

                # Accumulate meta-gradients
                for key in meta_grads:
                    meta_grads[key] += query_grad.get(key, 0)

            # Average and update meta-params
            for key in self.meta_params:
                self.meta_params[key] -= (
                    self.config.meta_lr * meta_grads[key] / len(batch_tasks)
                )

        return self.meta_params

    def adapt(self, task: Task, n_samples: int = 10) -> dict:
        """Fast adaptation to new task."""
        X, y = task.sample_data(n_samples)
        return self._inner_loop(task, X, y)


class Reptile(MetaLearner):
    """Reptile: First-order meta-learning via weight interpolation."""

    def __init__(self, config: MetaLearningConfig, initial_params: dict):
        super().__init__(config)
        self.meta_params = initial_params.copy()

    def meta_train(self, tasks: list[Task], steps: int) -> dict:
        """Reptile meta-training."""
        for step in range(steps):
            # Sample batch of tasks
            batch_tasks = random.sample(
                tasks, min(self.config.meta_batch_size, len(tasks))
            )

            for task in batch_tasks:
                # Sample data for this task
                X, y = task.sample_data(20)

                # Start from meta-params
                params = self.meta_params.copy()

                # Inner loop (standard SGD)
                for _ in range(self.config.reptile_steps):
                    grad = task.gradient(params, X, y)
                    for key in params:
                        params[key] -= self.config.inner_lr * grad.get(key, 0)

                # Reptile update: interpolate towards adapted params
                for key in self.meta_params:
                    self.meta_params[key] += self.config.reptile_meta_lr * (
                        params[key] - self.meta_params[key]
                    )

        return self.meta_params

    def adapt(self, task: Task, n_samples: int = 10) -> dict:
        """Fast adaptation to new task."""
        X, y = task.sample_data(n_samples)
        params = self.meta_params.copy()

        for _ in range(self.config.inner_steps):
            grad = task.gradient(params, X, y)
            for key in params:
                params[key] -= self.config.inner_lr * grad.get(key, 0)

        return params


class MetaSGD(MetaLearner):
    """Meta-SGD: Meta-learning the learning rate."""

    def __init__(
        self,
        config: MetaLearningConfig,
        initial_params: dict,
        initial_lr: Optional[dict] = None,
    ):
        super().__init__(config)
        self.meta_params = initial_params.copy()
        self.meta_lr_params = initial_lr or {
            key: config.inner_lr for key in initial_params
        }

    def meta_train(self, tasks: list[Task], steps: int) -> dict:
        """Meta-SGD meta-training."""
        for step in range(steps):
            batch_tasks = random.sample(
                tasks, min(self.config.meta_batch_size, len(tasks))
            )

            meta_param_grads = {key: 0.0 for key in self.meta_params}
            meta_lr_grads = {key: 0.0 for key in self.meta_lr_params}

            for task in batch_tasks:
                X_support, y_support = task.sample_data(20)
                X_query, y_query = task.sample_data(20)

                # Inner loop with per-parameter learning rates
                params = self.meta_params.copy()
                for _ in range(self.config.inner_steps):
                    grad = task.gradient(params, X_support, y_support)
                    for key in params:
                        params[key] -= self.meta_lr_params[key] * grad.get(key, 0)

                # Meta-gradients
                query_grad = task.gradient(params, X_query, y_query)

                for key in self.meta_params:
                    # Gradient w.r.t meta-parameters
                    meta_param_grads[key] += query_grad.get(key, 0)
                    # Gradient w.r.t learning rates (chain rule)
                    support_grad = task.gradient(self.meta_params, X_support, y_support)
                    meta_lr_grads[key] += -query_grad.get(key, 0) * support_grad.get(
                        key, 0
                    )

            # Update meta-parameters and learning rates
            for key in self.meta_params:
                self.meta_params[key] -= (
                    self.config.meta_lr * meta_param_grads[key] / len(batch_tasks)
                )
                self.meta_lr_params[key] -= (
                    self.config.meta_lr * meta_lr_grads[key] / len(batch_tasks)
                )

        return self.meta_params

    def adapt(self, task: Task, n_samples: int = 10) -> dict:
        """Fast adaptation."""
        X, y = task.sample_data(n_samples)
        params = self.meta_params.copy()

        for _ in range(self.config.inner_steps):
            grad = task.gradient(params, X, y)
            for key in params:
                params[key] -= self.meta_lr_params[key] * grad.get(key, 0)

        return params


class ANIL(MetaLearner):
    """Almost No Inner Loop (ANIL) - only adapt last layer."""

    def __init__(
        self, config: MetaLearningConfig, initial_params: dict, head_keys: list[str]
    ):
        super().__init__(config)
        self.meta_params = initial_params.copy()
        self.head_keys = set(head_keys)
        self.body_keys = set(initial_params.keys()) - self.head_keys

    def meta_train(self, tasks: list[Task], steps: int) -> dict:
        """ANIL meta-training (only adapt head)."""
        for step in range(steps):
            batch_tasks = random.sample(
                tasks, min(self.config.meta_batch_size, len(tasks))
            )

            meta_grads = {key: 0.0 for key in self.meta_params}

            for task in batch_tasks:
                X_support, y_support = task.sample_data(20)
                X_query, y_query = task.sample_data(20)

                # Only adapt head
                params = {k: self.meta_params[k] for k in self.head_keys}
                for _ in range(self.config.inner_steps):
                    grad = task.gradient(params, X_support, y_support)
                    for key in params:
                        params[key] -= self.config.inner_lr * grad.get(key, 0)

                # Combine with frozen body
                adapted = self.meta_params.copy()
                adapted.update(params)

                # Meta-gradient
                query_grad = task.gradient(adapted, X_query, y_query)
                for key in meta_grads:
                    meta_grads[key] += query_grad.get(key, 0)

            for key in self.meta_params:
                self.meta_params[key] -= (
                    self.config.meta_lr * meta_grads[key] / len(batch_tasks)
                )

        return self.meta_params

    def adapt(self, task: Task, n_samples: int = 10) -> dict:
        """Fast adaptation (head only)."""
        X, y = task.sample_data(n_samples)
        params = {k: self.meta_params[k] for k in self.head_keys}

        for _ in range(self.config.inner_steps):
            grad = task.gradient(params, X, y)
            for key in params:
                params[key] -= self.config.inner_lr * grad.get(key, 0)

        adapted = self.meta_params.copy()
        adapted.update(params)
        return adapted


# Example task for strategy parameter adaptation
class StrategyParameterTask(Task):
    """Task for adapting strategy parameters to market regime."""

    def __init__(self, data: np.ndarray, target_metric: str = "sharpe"):
        """
        Args:
            data: Historical price data (n_samples, n_features)
            target_metric: Metric to optimize
        """
        self.data = data
        self.target_metric = target_metric
        self.param_bounds = {
            "ema_fast": (5, 30),
            "ema_slow": (20, 60),
            "rsi_period": (7, 21),
            "bb_period": (15, 30),
            "bb_std": (1.5, 3.0),
        }

    def sample_data(self, n: int) -> tuple[np.ndarray, np.ndarray]:
        """Sample n windows of data."""
        max_start = len(self.data) - n
        if max_start <= 0:
            start = 0
        else:
            start = random.randint(0, max_start)
        X = self.data[start : start + n]
        y = np.ones(n)  # Dummy target (optimization target)
        return X, y

    def evaluate(self, params: dict, X: np.ndarray, y: np.ndarray) -> float:
        """Evaluate strategy params on data (simplified)."""
        # This would run a backtest in practice
        # Return negative Sharpe (minimization)
        return -1.0

    def gradient(self, params: dict, X: np.ndarray, y: np.ndarray) -> dict:
        """Finite difference gradient."""
        eps = 1e-4
        grad = {}
        base_loss = self.evaluate(params, X, y)

        for key in params:
            if key not in self.param_bounds:
                grad[key] = 0
                continue

            params_plus = params.copy()
            params_plus[key] += eps
            loss_plus = self.evaluate(params_plus, X, y)

            grad[key] = (loss_plus - base_loss) / eps

        return grad


class MetaStrategyAdapter:
    """High-level interface for meta-learning strategy adaptation."""

    def __init__(
        self, algorithm: str = "maml", config: Optional[MetaLearningConfig] = None
    ):
        self.config = config or MetaLearningConfig()
        self.algorithm = algorithm
        self.learner: Optional[MetaLearner] = None
        self.param_names = ["ema_fast", "ema_slow", "rsi_period", "bb_period", "bb_std"]

    def train(self, market_data: dict[str, np.ndarray], steps: int = 100) -> dict:
        """
        Meta-train on multiple market regimes.

        Args:
            market_data: Dict of regime_name -> price_data
            steps: Meta-training steps
        """
        # Create tasks for each regime
        tasks = [StrategyParameterTask(data) for data in market_data.values()]

        # Initial parameters
        initial_params = {
            "ema_fast": 12.0,
            "ema_slow": 26.0,
            "rsi_period": 14.0,
            "bb_period": 20.0,
            "bb_std": 2.0,
        }

        # Create learner
        if self.algorithm == "maml":
            self.learner = MAML(self.config, initial_params)
        elif self.algorithm == "reptile":
            self.learner = Reptile(self.config, initial_params)
        elif self.algorithm == "metasgd":
            self.learner = MetaSGD(self.config, initial_params)
        elif self.algorithm == "anil":
            self.learner = ANIL(
                self.config, initial_params, head_keys=["ema_fast", "ema_slow"]
            )
        else:
            raise ValueError(f"Unknown algorithm: {self.algorithm}")

        # Meta-train
        self.learner.meta_train(tasks, steps)

        return self.learner.meta_params

    def adapt_to_regime(self, regime_data: np.ndarray, n_samples: int = 20) -> dict:
        """Adapt to new market regime."""
        if self.learner is None:
            raise RuntimeError("Must train first")

        task = StrategyParameterTask(regime_data)
        adapted = self.learner.adapt(task, n_samples)
        return adapted

    def get_meta_params(self) -> dict:
        """Get meta-learned initialization."""
        if self.learner is None:
            return {}
        return self.learner.meta_params
