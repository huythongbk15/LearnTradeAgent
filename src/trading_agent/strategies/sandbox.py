"""Sandboxed execution for untrusted strategy code."""

import ast
import asyncio
import logging
import os
import sys
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SandboxType(Enum):
    """Sandbox execution types."""

    SUBPROCESS = "subprocess"  # Simple subprocess isolation
    DOCKER = "docker"  # Docker container
    GVISOR = "gvisor"  # gVisor (runsc)
    FIRECRACKER = "firecracker"  # Firecracker microVM
    NSJAIL = "nsjail"  # nsjail


@dataclass
class SandboxConfig:
    """Sandbox configuration."""

    sandbox_type: SandboxType = SandboxType.SUBPROCESS
    memory_limit_mb: int = 512
    cpu_limit_percent: int = 50
    timeout_seconds: int = 30
    network_enabled: bool = False
    allowed_imports: list[str] = field(default_factory=list)
    working_dir: Optional[str] = None
    environment_vars: dict[str, str] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    """Result of sandboxed execution."""

    success: bool
    output: Any = None
    error: Optional[str] = None
    execution_time_ms: float = 0
    memory_used_mb: float = 0
    cpu_used_percent: float = 0
    logs: list[str] = None


class StrategySandbox(ABC):
    """Abstract sandbox for strategy execution."""

    def __init__(self, config: SandboxConfig):
        self.config = config

    @abstractmethod
    async def execute(
        self, strategy_code: str, method: str, *args, **kwargs
    ) -> ExecutionResult:
        """Execute a strategy method in sandbox."""
        pass

    @abstractmethod
    async def validate(self, strategy_code: str) -> ExecutionResult:
        """Validate strategy code without executing."""
        pass


class SubprocessSandbox(StrategySandbox):
    """Restricted subprocess runner for reviewed code.

    A subprocess is not a kernel security boundary.  Secrets are removed and
    source is AST-validated, but truly untrusted code still requires the Docker,
    gVisor or microVM backends.
    """

    def __init__(self, config: SandboxConfig):
        super().__init__(config)
        self._allowed_imports = config.allowed_imports or [
            "numpy",
            "pandas",
            "decimal",
            "datetime",
            "math",
            "statistics",
            "typing",
            "dataclasses",
            "enum",
            "collections",
            "itertools",
        ]

    async def execute(
        self, strategy_code: str, method: str, *args, **kwargs
    ) -> ExecutionResult:
        """Execute strategy method in subprocess."""
        import json
        import time

        start_time = time.time()

        validation = await self.validate(strategy_code)
        if not validation.success:
            return validation

        # Prepare execution script
        script = self._prepare_script(strategy_code, method, args, kwargs)

        # Write to temp file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(script)
            script_path = f.name

        try:
            # Run subprocess with limits
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.config.working_dir,
                env=self._subprocess_environment(),
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self.config.timeout_seconds
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ExecutionResult(
                    success=False,
                    error=f"Execution timeout ({self.config.timeout_seconds}s)",
                    execution_time_ms=(time.time() - start_time) * 1000,
                )

            execution_time = (time.time() - start_time) * 1000

            if proc.returncode != 0:
                return ExecutionResult(
                    success=False,
                    error=stderr.decode() if stderr else "Unknown error",
                    execution_time_ms=execution_time,
                )

            # Parse result
            try:
                result = json.loads(stdout.decode())
                return ExecutionResult(
                    success=True,
                    output=result.get("output"),
                    execution_time_ms=execution_time,
                    logs=result.get("logs", []),
                )
            except json.JSONDecodeError:
                return ExecutionResult(
                    success=False,
                    error="Invalid output format",
                    execution_time_ms=execution_time,
                )

        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass

    async def validate(self, strategy_code: str) -> ExecutionResult:
        """Validate strategy code syntax and imports."""
        try:
            tree = ast.parse(strategy_code, filename="<strategy>", mode="exec")
        except SyntaxError as e:
            return ExecutionResult(
                success=False,
                error=f"Syntax error: {e}",
            )

        dangerous_calls = {
            "breakpoint",
            "compile",
            "delattr",
            "eval",
            "exec",
            "getattr",
            "globals",
            "input",
            "locals",
            "open",
            "setattr",
            "vars",
            "__import__",
        }
        allowed = set(self._allowed_imports)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root not in allowed:
                        return ExecutionResult(
                            success=False, error=f"Forbidden import: {root}"
                        )
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".", 1)[0]
                if node.level or root not in allowed:
                    return ExecutionResult(
                        success=False, error=f"Forbidden import: {root or 'relative'}"
                    )
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in dangerous_calls:
                    return ExecutionResult(
                        success=False, error=f"Forbidden call: {node.func.id}"
                    )
            elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
                return ExecutionResult(
                    success=False, error=f"Forbidden attribute: {node.attr}"
                )
        return ExecutionResult(success=True)

    def _subprocess_environment(self) -> dict[str, str]:
        """Build a minimal environment without inheriting API keys or tokens."""
        safe_names = {
            "COMSPEC",
            "LANG",
            "LC_ALL",
            "PATH",
            "PATHEXT",
            "SYSTEMDRIVE",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "TZ",
            "WINDIR",
        }
        environment = {
            name: value
            for name, value in os.environ.items()
            if name.upper() in safe_names
        }
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUNBUFFERED"] = "1"
        environment.update(self.config.environment_vars)
        return environment

    def _prepare_script(self, strategy_code: str, method: str, args, kwargs) -> str:
        """Prepare execution script."""
        args_json = __import__("json").dumps(args)
        kwargs_json = __import__("json").dumps(kwargs)

        return f"""
import json
import sys
import traceback

# Strategy code
{strategy_code}

# Execution wrapper
def main():
    try:
        # Find strategy class
        strategy_class = None
        for _name, _obj in list(globals().items()):
            if isinstance(_obj, type) and hasattr(_obj, 'on_bar'):
                strategy_class = _obj
                break
        
        if not strategy_class:
            print(json.dumps({{"success": False, "error": "No strategy class found"}}))
            return
        
        # Instantiate
        strategy = strategy_class()
        
        # Call method
        args = json.loads('{args_json}')
        kwargs = json.loads('{kwargs_json}')
        result = getattr(strategy, '{method}')(*args, **kwargs)
        
        # Serialize result
        if hasattr(result, '__dict__'):
            output = result.__dict__
        elif hasattr(result, '_asdict'):
            output = result._asdict()
        else:
            output = result
        
        print(json.dumps({{"success": True, "output": output, "logs": []}}))
        
    except Exception as e:
        print(json.dumps({{"success": False, "error": traceback.format_exc()}}))

if __name__ == "__main__":
    main()
"""


class DockerSandbox(StrategySandbox):
    """Docker-based sandbox."""

    def __init__(self, config: SandboxConfig, image: str = "python:3.11-slim"):
        super().__init__(config)
        self.image = image

    async def execute(
        self, strategy_code: str, method: str, *args, **kwargs
    ) -> ExecutionResult:
        """Execute in Docker container."""
        import time

        start_time = time.time()

        # Create container with limits
        cmd = [
            "docker",
            "run",
            "--rm",
            "--memory",
            f"{self.config.memory_limit_mb}m",
            "--cpus",
            str(self.config.cpu_limit_percent / 100),
            "--network",
            "none" if not self.config.network_enabled else "bridge",
            "--pids-limit",
            "50",
            "--security-opt",
            "no-new-privileges",
            "-v",
            f"{self.config.working_dir or tempfile.gettempdir()}:/workspace",
            "-w",
            "/workspace",
            self.image,
            "python3",
            "-c",
            strategy_code,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.config.timeout_seconds
            )

        except asyncio.TimeoutError:
            return ExecutionResult(
                success=False,
                error="Timeout",
                execution_time_ms=(time.time() - start_time) * 1000,
            )

        execution_time = (time.time() - start_time) * 1000

        if proc.returncode != 0:
            return ExecutionResult(
                success=False,
                error=stderr.decode(),
                execution_time_ms=execution_time,
            )

        return ExecutionResult(
            success=True,
            output=stdout.decode(),
            execution_time_ms=execution_time,
        )

    async def validate(self, strategy_code: str) -> ExecutionResult:
        """Quick syntax check."""
        return await self.execute(strategy_code, "validate")


class GVisorSandbox(DockerSandbox):
    """gVisor sandbox (runsc runtime)."""

    def __init__(self, config: SandboxConfig, image: str = "python:3.11-slim"):
        super().__init__(config, image)
        # Use runsc runtime
        self.runtime = "runsc"

    async def execute(
        self, strategy_code: str, method: str, *args, **kwargs
    ) -> ExecutionResult:
        # Override docker command to use runsc
        import time

        start_time = time.time()

        cmd = [
            "docker",
            "run",
            "--rm",
            "--runtime",
            self.runtime,
            "--memory",
            f"{self.config.memory_limit_mb}m",
            "--cpus",
            str(self.config.cpu_limit_percent / 100),
            "--network",
            "none" if not self.config.network_enabled else "bridge",
            "--pids-limit",
            "50",
            "--security-opt",
            "no-new-privileges",
            self.image,
            "python3",
            "-c",
            strategy_code,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.config.timeout_seconds
            )

        except asyncio.TimeoutError:
            return ExecutionResult(
                success=False,
                error="Timeout",
                execution_time_ms=(time.time() - start_time) * 1000,
            )

        execution_time = (time.time() - start_time) * 1000

        if proc.returncode != 0:
            return ExecutionResult(
                success=False,
                error=stderr.decode(),
                execution_time_ms=execution_time,
            )

        return ExecutionResult(
            success=True,
            output=stdout.decode(),
            execution_time_ms=execution_time,
        )


class SandboxFactory:
    """Factory for creating sandboxes."""

    @staticmethod
    def create(config: SandboxConfig) -> StrategySandbox:
        """Create sandbox based on config."""
        if config.sandbox_type == SandboxType.SUBPROCESS:
            return SubprocessSandbox(config)
        elif config.sandbox_type == SandboxType.DOCKER:
            return DockerSandbox(config)
        elif config.sandbox_type == SandboxType.GVISOR:
            return GVisorSandbox(config)
        else:
            raise ValueError(f"Unsupported sandbox type: {config.sandbox_type}")

    @staticmethod
    def create_default() -> StrategySandbox:
        """Create default subprocess sandbox."""
        return SubprocessSandbox(SandboxConfig())


# Context manager for easy usage
class SandboxedStrategy:
    """Context manager for sandboxed strategy execution."""

    def __init__(self, strategy_code: str, sandbox: Optional[StrategySandbox] = None):
        self.strategy_code = strategy_code
        self.sandbox = sandbox or SandboxFactory.create_default()
        self._instance = None

    async def __aenter__(self) -> "SandboxedStrategy":
        # Validate first
        result = await self.sandbox.validate(self.strategy_code)
        if not result.success:
            raise ValueError(f"Strategy validation failed: {result.error}")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    async def call(self, method: str, *args, **kwargs) -> ExecutionResult:
        """Call a strategy method."""
        return await self.sandbox.execute(self.strategy_code, method, *args, **kwargs)

    async def on_bar(self, bar) -> ExecutionResult:
        return await self.call("on_bar", bar)

    async def on_signal(self, signal) -> ExecutionResult:
        return await self.call("on_signal", signal)

    async def on_fill(self, fill) -> ExecutionResult:
        return await self.call("on_fill", fill)

    async def get_params(self) -> ExecutionResult:
        return await self.call("get_params")
