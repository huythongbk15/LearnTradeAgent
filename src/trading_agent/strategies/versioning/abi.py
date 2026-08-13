"""ABI (Application Binary Interface) verification for strategies."""

import hashlib
import inspect
import json
from dataclasses import dataclass, asdict
from typing import Any, get_type_hints
from typing import Optional, get_origin, get_args


@dataclass
class ParameterSpec:
    """Parameter specification."""

    name: str
    type: str
    default: Any = None
    required: bool = True
    description: str = ""
    constraints: dict = None


@dataclass
class MethodSpec:
    """Method specification."""

    name: str
    params: list[ParameterSpec]
    return_type: str
    is_async: bool = False


@dataclass
class StrategyABI:
    """Strategy Application Binary Interface."""

    name: str
    version: str
    params: list[ParameterSpec]
    methods: list[MethodSpec]
    signals: list[str]  # signal types this strategy emits
    required_data: list[str]  # data types this strategy needs
    hash: str = ""

    def __post_init__(self):
        if not self.hash:
            self.hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """Compute deterministic hash of ABI."""
        data = {
            "name": self.name,
            "version": self.version,
            "params": [asdict(p) for p in self.params],
            "methods": [asdict(m) for m in self.methods],
            "signals": self.signals,
            "required_data": self.required_data,
        }
        content = json.dumps(data, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    @classmethod
    def from_strategy(cls, strategy_class: type) -> "StrategyABI":
        """Extract ABI from strategy class."""
        # Get constructor params
        sig = inspect.signature(strategy_class.__init__)
        type_hints = get_type_hints(strategy_class.__init__)

        params = []
        for name, param in sig.parameters.items():
            if name == "self":
                continue

            param_type = type_hints.get(name, Any)
            type_str = cls._type_to_string(param_type)

            default = (
                param.default if param.default != inspect.Parameter.empty else None
            )
            required = param.default == inspect.Parameter.empty

            params.append(
                ParameterSpec(
                    name=name,
                    type=type_str,
                    default=default,
                    required=required,
                )
            )

        # Get methods
        methods = []
        for name, method in inspect.getmembers(
            strategy_class, predicate=inspect.isfunction
        ):
            if name.startswith("_"):
                continue

            method_sig = inspect.signature(method)
            method_hints = get_type_hints(method)

            method_params = []
            for pname, p in method_sig.parameters.items():
                if pname == "self":
                    continue
                ptype = method_hints.get(pname, Any)
                method_params.append(
                    ParameterSpec(
                        name=pname,
                        type=cls._type_to_string(ptype),
                        default=p.default
                        if p.default != inspect.Parameter.empty
                        else None,
                        required=p.default == inspect.Parameter.empty,
                    )
                )

            return_type = method_hints.get("return", Any)
            methods.append(
                MethodSpec(
                    name=name,
                    params=method_params,
                    return_type=cls._type_to_string(return_type),
                    is_async=inspect.iscoroutinefunction(method),
                )
            )

        # Detect signals and required data
        signals = cls._detect_signals(strategy_class)
        required_data = cls._detect_required_data(strategy_class)

        return cls(
            name=strategy_class.__name__,
            version=getattr(strategy_class, "__version__", "1.0.0"),
            params=params,
            methods=methods,
            signals=signals,
            required_data=required_data,
        )

    @staticmethod
    def _type_to_string(t: Any) -> str:
        """Convert type to string representation."""
        if t is Any:
            return "Any"
        origin = get_origin(t)
        args = get_args(t)

        if origin is None:
            return getattr(t, "__name__", str(t))

        if origin is list:
            return f"list[{StrategyABI._type_to_string(args[0])}]"
        elif origin is dict:
            return f"dict[{StrategyABI._type_to_string(args[0])}, {StrategyABI._type_to_string(args[1])}]"
        elif origin is Optional:
            return f"Optional[{StrategyABI._type_to_string(args[0])}]"
        elif hasattr(origin, "__name__"):
            return f"{origin.__name__}[{', '.join(StrategyABI._type_to_string(a) for a in args)}]"

        return str(t)

    @staticmethod
    def _detect_signals(strategy_class: type) -> list[str]:
        """Detect signal types emitted by strategy."""
        signals = set()
        for name, method in inspect.getmembers(
            strategy_class, predicate=inspect.isfunction
        ):
            if "signal" in name.lower() or "emit" in name.lower():
                sig = inspect.signature(method)
                hints = get_type_hints(method)
                for pname, p in sig.parameters.items():
                    if pname == "self":
                        continue
                    ptype = hints.get(pname, Any)
                    if "signal" in ptype.__name__.lower() or "Signal" in str(ptype):
                        signals.add(pname)
        return list(signals) or ["buy", "sell", "hold"]

    @staticmethod
    def _detect_required_data(strategy_class: type) -> list[str]:
        """Detect data types required by strategy."""
        data_types = set()
        for name, method in inspect.getmembers(
            strategy_class, predicate=inspect.isfunction
        ):
            sig = inspect.signature(method)
            hints = get_type_hints(method)
            for pname, p in sig.parameters.items():
                if pname == "self":
                    continue
                ptype = hints.get(pname, Any)
                type_str = str(ptype).lower()
                if any(
                    kw in type_str
                    for kw in ["candle", "ohlcv", "tick", "orderbook", "trade"]
                ):
                    data_types.add(pname)
        return list(data_types) or ["candles"]


class ABIVerifier:
    """Verify ABI compatibility between strategy versions."""

    @staticmethod
    def verify(old_abi: StrategyABI, new_abi: StrategyABI) -> dict:
        """Verify compatibility between two ABIs."""
        result = {
            "compatible": True,
            "breaking_changes": [],
            "warnings": [],
            "added_params": [],
            "removed_params": [],
            "changed_params": [],
            "added_methods": [],
            "removed_methods": [],
            "changed_methods": [],
        }

        # Check params
        old_params = {p.name: p for p in old_abi.params}
        new_params = {p.name: p for p in new_abi.params}

        for name in old_params:
            if name not in new_params:
                result["removed_params"].append(name)
                result["breaking_changes"].append(f"Removed required parameter: {name}")
                result["compatible"] = False
            else:
                old_p = old_params[name]
                new_p = new_params[name]
                if old_p.type != new_p.type:
                    result["changed_params"].append(name)
                    result["warnings"].append(
                        f"Parameter {name} type changed: {old_p.type} -> {new_p.type}"
                    )
                if old_p.required and not new_p.required:
                    result["warnings"].append(f"Parameter {name} became optional")
                if not old_p.required and new_p.required:
                    result["breaking_changes"].append(
                        f"Parameter {name} became required"
                    )
                    result["compatible"] = False

        for name in new_params:
            if name not in old_params:
                if new_params[name].required:
                    result["breaking_changes"].append(
                        f"Added required parameter: {name}"
                    )
                    result["compatible"] = False
                else:
                    result["added_params"].append(name)

        # Check methods
        old_methods = {m.name: m for m in old_abi.methods}
        new_methods = {m.name: m for m in new_abi.methods}

        for name in old_methods:
            if name not in new_methods:
                result["removed_methods"].append(name)
                result["breaking_changes"].append(f"Removed method: {name}")
                result["compatible"] = False
            else:
                old_m = old_methods[name]
                new_m = new_methods[name]
                if old_m.return_type != new_m.return_type:
                    result["changed_methods"].append(name)
                    result["warnings"].append(f"Method {name} return type changed")
                if len(old_m.params) != len(new_m.params):
                    result["changed_methods"].append(name)
                    result["warnings"].append(f"Method {name} parameter count changed")

        for name in new_methods:
            if name not in old_methods:
                result["added_methods"].append(name)

        # Check signals
        old_signals = set(old_abi.signals)
        new_signals = set(new_abi.signals)

        removed_signals = old_signals - new_signals
        if removed_signals:
            result["warnings"].append(f"Removed signals: {removed_signals}")

        # Check required data
        old_data = set(old_abi.required_data)
        new_data = set(new_abi.required_data)

        removed_data = old_data - new_data
        if removed_data:
            result["warnings"].append(f"Removed required data: {removed_data}")

        return result

    @staticmethod
    def can_upgrade(old_abi: StrategyABI, new_abi: StrategyABI) -> bool:
        """Check if safe to upgrade without breaking existing deployments."""
        result = ABIVerifier.verify(old_abi, new_abi)
        return result["compatible"]

    @staticmethod
    def generate_migration(old_abi: StrategyABI, new_abi: StrategyABI) -> dict:
        """Generate migration guide for breaking changes."""
        result = ABIVerifier.verify(old_abi, new_abi)
        return {
            "migration_required": not result["compatible"],
            "steps": [
                f"Update parameter {p} (type change)" for p in result["changed_params"]
            ]
            + [f"Add required parameter {p}" for p in result["removed_params"]]
            + [f"Remove method {m} calls" for m in result["removed_methods"]],
        }
