"""Allowlisted, hash-verified canonical strategy registry (STR-0107).

The registry is the *only* way runtime code may obtain a strategy instance:

- **Allowlist**: every strategy must be explicitly registered with a
  :class:`StrategyDescriptor`; there is no dynamic import path.
- **Hash-verified**: at registration time the registry hashes the source
  file that defines the factory (or its wrapped class) and refuses any
  descriptor whose ``code_sha`` does not match — a tampered or stale
  artifact is blocked before it can run (S1 exit gate).
- **Environment gate**: descriptors flagged ``research_only`` (e.g. legacy
  adapters without proven parity, STR-0103) are only resolvable in the
  RESEARCH environment.
"""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from typing import Callable

from trading_agent.authority.config import Environment
from trading_agent.strategies.canonical.descriptor import StrategyDescriptor
from trading_agent.research.forecast import ForecastStrategy


class RegistryIntegrityError(RuntimeError):
    """Raised when code_sha / contract binding fails verification."""


class UnknownStrategyError(KeyError):
    """Raised when a strategy_id is not on the allowlist."""


@dataclass(frozen=True)
class RegistryEntry:
    descriptor: StrategyDescriptor
    factory: Callable[[], ForecastStrategy]

    def build(self) -> ForecastStrategy:
        instance = self.factory()
        if not callable(getattr(instance, "forecast", None)):
            raise RegistryIntegrityError(
                f"factory for {self.descriptor.strategy_id!r} did not produce "
                "a ForecastStrategy"
            )
        return instance


def _source_code_sha(
    factory: Callable[[], ForecastStrategy],
    code_source: object | None = None,
) -> str:
    """sha256 of the source file defining *code_source* (or the factory)."""
    target = code_source if code_source is not None else factory
    if inspect.ismodule(target):
        path = getattr(target, "__file__", None)
    elif isinstance(target, type):
        path = inspect.getsourcefile(target)
    elif callable(target):
        fn = getattr(target, "__code__", None)
        if fn is None and hasattr(target, "func"):
            inner = target.func
            fn = getattr(inner, "__code__", None)
            target = inner
        if fn is None:
            raise RegistryIntegrityError("factory must be a plain function")
        path = inspect.getsourcefile(fn)
    else:
        raise RegistryIntegrityError("code_source must be a module, class or function")
    if not path:
        raise RegistryIntegrityError("cannot locate source file for verification")
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class CanonicalStrategyRegistry:
    """Fail-closed allowlist of canonical strategies."""

    def __init__(self) -> None:
        self._entries: dict[str, RegistryEntry] = {}

    # ── Registration ────────────────────────────────────────────────────
    def register(
        self,
        descriptor: StrategyDescriptor,
        factory: Callable[[], ForecastStrategy],
        *,
        verify_code_hash: bool = True,
        code_source: object | None = None,
    ) -> None:
        """Register one strategy.

        ``code_source`` optionally points at the module/class/function whose
        source file the descriptor's ``code_sha`` describes — use it when the
        factory lives in a different file than the strategy implementation.
        Defaults to hashing the factory's own source file.
        """
        if not callable(factory):
            raise TypeError("factory must be callable")
        existing = self._entries.get(descriptor.strategy_id)
        if existing is not None:
            if existing.descriptor.descriptor_id == descriptor.descriptor_id:
                return  # idempotent re-registration of identical content
            raise RegistryIntegrityError(
                f"strategy_id {descriptor.strategy_id!r} already registered "
                f"with different content ({existing.descriptor.descriptor_id} "
                f"!= {descriptor.descriptor_id})"
            )
        if verify_code_hash:
            actual = _source_code_sha(factory, code_source)
            if actual != descriptor.code_sha:
                raise RegistryIntegrityError(
                    f"code_sha mismatch for {descriptor.strategy_id!r}: "
                    f"descriptor claims {descriptor.code_sha[:12]}…, module "
                    f"hash is {actual[:12]}… — refusing to load"
                )
        self._entries[descriptor.strategy_id] = RegistryEntry(
            descriptor=descriptor, factory=factory
        )

    # ── Resolution ──────────────────────────────────────────────────────
    def get(
        self, strategy_id: str, *, environment: Environment
    ) -> tuple[StrategyDescriptor, ForecastStrategy]:
        entry = self._entries.get(strategy_id)
        if entry is None:
            raise UnknownStrategyError(
                f"{strategy_id!r} is not on the allowlist; available: "
                f"{sorted(self._entries)}"
            )
        if entry.descriptor.research_only and environment is not Environment.RESEARCH:
            raise RegistryIntegrityError(
                f"strategy {strategy_id!r} is research_only and blocked in "
                f"environment {environment.value}"
            )
        return entry.descriptor, entry.build()

    def has(self, strategy_id: str) -> bool:
        return strategy_id in self._entries

    def list_ids(self) -> list[str]:
        return sorted(self._entries)

    def describe(self, strategy_id: str) -> StrategyDescriptor:
        entry = self._entries.get(strategy_id)
        if entry is None:
            raise UnknownStrategyError(strategy_id)
        return entry.descriptor
