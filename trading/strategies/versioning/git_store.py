"""Git-based version store for strategies."""

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from trading.strategies.versioning.registry import StrategyVersion, StrategyRegistry

logger = logging.getLogger(__name__)


@dataclass
class GitCommitInfo:
    """Information about a git commit."""
    hash: str
    message: str
    author: str
    timestamp: datetime
    files: list[str]


class GitVersionStore:
    """Git-backed version store for strategy code and metadata."""
    
    def __init__(self, repo_path: str = "./strategies_repo"):
        self.repo_path = Path(repo_path)
        self.repo_path.mkdir(parents=True, exist_ok=True)
        self._init_repo()
    
    def _init_repo(self) -> None:
        """Initialize git repository."""
        if not (self.repo_path / ".git").exists():
            self._run_git("init")
            self._run_git("config", "user.name", "Trading System")
            self._run_git("config", "user.email", "trading@system.local")
            
            # Create initial commit
            readme = self.repo_path / "README.md"
            readme.write_text("# Strategy Repository\n\nAuto-generated strategy versions.\n")
            self._run_git("add", "README.md")
            self._run_git("commit", "-m", "Initial commit")
    
    def _run_git(self, *args: str) -> str:
        """Run git command and return output."""
        result = subprocess.run(
            ["git"] + list(args),
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Git command failed: {' '.join(args)}\n{result.stderr}")
        return result.stdout.strip()
    
    def save_version(self, version: StrategyVersion) -> str:
        """Save strategy version to git."""
        name = version.metadata.name
        ver = version.metadata.version
        
        # Create directory structure
        strategy_dir = self.repo_path / name
        strategy_dir.mkdir(parents=True, exist_ok=True)
        
        # Save source code
        source_file = strategy_dir / f"{name}.py"
        source_file.write_text(version.source_code)
        
        # Save metadata
        meta_file = strategy_dir / f"{name}_v{ver}_meta.json"
        meta_data = {
            "metadata": {
                "name": version.metadata.name,
                "version": version.metadata.version,
                "author": version.metadata.author,
                "description": version.metadata.description,
                "asset_class": version.metadata.asset_class.value,
                "risk_profile": version.metadata.risk_profile.value,
                "timeframes": version.metadata.timeframes,
                "symbols": version.metadata.symbols,
                "params_schema": version.metadata.params_schema,
                "backtest_hash": version.metadata.backtest_hash,
                "backtest_period": version.metadata.backtest_period,
                "backtest_metrics": version.metadata.backtest_metrics,
                "created_at": version.metadata.created_at.isoformat(),
                "updated_at": version.metadata.updated_at.isoformat(),
                "tags": version.metadata.tags,
                "dependencies": version.metadata.dependencies,
            },
            "source_hash": version.source_hash,
            "abi_hash": version.abi_hash,
            "is_active": version.is_active,
            "is_deprecated": version.is_deprecated,
            "deployed_at": version.deployed_at.isoformat() if version.deployed_at else None,
            "retired_at": version.retired_at.isoformat() if version.retired_at else None,
            "deployment_hash": version.deployment_hash,
        }
        meta_file.write_text(json.dumps(meta_data, indent=2))
        
        # Save ABI
        abi_file = strategy_dir / f"{name}_v{ver}_abi.json"
        # We'd need the actual ABI object, but we can reconstruct from metadata
        abi_data = {
            "name": version.metadata.name,
            "version": version.metadata.version,
            "params": [],
            "methods": [],
            "signals": [],
            "required_data": [],
            "hash": version.abi_hash,
        }
        abi_file.write_text(json.dumps(abi_data, indent=2))
        
        # Commit
        self._run_git("add", ".")
        commit_msg = f"strategy: {name} v{ver} ({version.source_hash[:8]})"
        self._run_git("commit", "-m", commit_msg)
        
        commit_hash = self._run_git("rev-parse", "HEAD")
        logger.info(f"Saved {name} v{ver} to git: {commit_hash[:8]}")
        
        return commit_hash
    
    def load_version(self, name: str, version: str) -> Optional[StrategyVersion]:
        """Load strategy version from git."""
        strategy_dir = self.repo_path / name
        meta_file = strategy_dir / f"{name}_v{version}_meta.json"
        source_file = strategy_dir / f"{name}.py"
        
        if not meta_file.exists() or not source_file.exists():
            return None
        
        meta_data = json.loads(meta_file.read_text())
        source_code = source_file.read_text()
        
        # Reconstruct metadata
        from trading.strategies.versioning.registry import StrategyMetadata, RiskProfile, AssetClass
        metadata = StrategyMetadata(
            name=meta_data["metadata"]["name"],
            version=meta_data["metadata"]["version"],
            author=meta_data["metadata"]["author"],
            description=meta_data["metadata"]["description"],
            asset_class=AssetClass(meta_data["metadata"]["asset_class"]),
            risk_profile=RiskProfile(meta_data["metadata"]["risk_profile"]),
            timeframes=meta_data["metadata"]["timeframes"],
            symbols=meta_data["metadata"]["symbols"],
            params_schema=meta_data["metadata"]["params_schema"],
            backtest_hash=meta_data["metadata"]["backtest_hash"],
            backtest_period=meta_data["metadata"]["backtest_period"],
            backtest_metrics=meta_data["metadata"]["backtest_metrics"],
            created_at=datetime.fromisoformat(meta_data["metadata"]["created_at"]),
            updated_at=datetime.fromisoformat(meta_data["metadata"]["updated_at"]),
            tags=meta_data["metadata"]["tags"],
            dependencies=meta_data["metadata"]["dependencies"],
        )
        
        version_obj = StrategyVersion(
            metadata=metadata,
            source_code=source_code,
            source_hash=meta_data["source_hash"],
            abi_hash=meta_data["abi_hash"],
            is_active=meta_data["is_active"],
            is_deprecated=meta_data["is_deprecated"],
            deployed_at=datetime.fromisoformat(meta_data["deployed_at"]) if meta_data["deployed_at"] else None,
            retired_at=datetime.fromisoformat(meta_data["retired_at"]) if meta_data["retired_at"] else None,
            deployment_hash=meta_data["deployment_hash"],
        )
        
        return version_obj
    
    def list_versions(self, name: str) -> list[str]:
        """List all versions of a strategy."""
        strategy_dir = self.repo_path / name
        if not strategy_dir.exists():
            return []
        
        versions = []
        for meta_file in strategy_dir.glob(f"{name}_v*_meta.json"):
            # Extract version from filename
            ver = meta_file.stem.replace(f"{name}_v", "").replace("_meta", "")
            versions.append(ver)
        
        return sorted(versions)
    
    def get_history(self, name: str, limit: int = 50) -> list[GitCommitInfo]:
        """Get git history for a strategy."""
        strategy_dir = self.repo_path / name
        if not strategy_dir.exists():
            return []
        
        log_output = self._run_git(
            "log", "--oneline", "-n", str(limit), "--", str(strategy_dir)
        )
        
        history = []
        for line in log_output.split("\n"):
            if not line:
                continue
            parts = line.split(" ", 1)
            commit_hash = parts[0]
            message = parts[1] if len(parts) > 1 else ""
            
            # Get detailed info
            show = self._run_git("show", "--no-patch", "--format=%an|%ai", commit_hash)
            author, timestamp_str = show.split("|")
            timestamp = datetime.fromisoformat(timestamp_str.replace(" ", "T"))
            
            # Get files changed
            files_output = self._run_git("show", "--name-only", "--format=", commit_hash)
            files = [f for f in files_output.split("\n") if f and name in f]
            
            history.append(GitCommitInfo(
                hash=commit_hash,
                message=message,
                author=author,
                timestamp=timestamp,
                files=files,
            ))
        
        return history
    
    def rollback(self, name: str, version: str) -> str:
        """Rollback strategy to a specific version."""
        strategy_dir = self.repo_path / name
        if not strategy_dir.exists():
            raise ValueError(f"Strategy not found: {name}")
        
        # Find commit for version
        history = self.get_history(name, limit=100)
        target_commit = None
        for commit in history:
            if f"v{version}" in commit.message:
                target_commit = commit.hash
                break
        
        if not target_commit:
            raise ValueError(f"Version not found in history: {version}")
        
        # Checkout that commit for the strategy directory
        self._run_git("checkout", target_commit, "--", str(strategy_dir))
        
        # Commit the rollback
        self._run_git("add", ".")
        rollback_msg = f"rollback: {name} to v{version} ({target_commit[:8]})"
        self._run_git("commit", "-m", rollback_msg)
        
        new_commit = self._run_git("rev-parse", "HEAD")
        logger.info(f"Rolled back {name} to v{version}: {new_commit[:8]}")
        
        return new_commit
    
    def tag_release(self, name: str, version: str, tag: Optional[str] = None) -> str:
        """Tag a version as release."""
        tag = tag or f"{name}/v{version}"
        self._run_git("tag", "-a", tag, "-m", f"Release {name} v{version}")
        logger.info(f"Tagged {name} v{version} as {tag}")
        return tag
    
    def list_tags(self) -> list[str]:
        """List all release tags."""
        output = self._run_git("tag", "-l")
        return [t for t in output.split("\n") if t]
    
    def diff_versions(self, name: str, version1: str, version2: str) -> str:
        """Diff two versions of a strategy."""
        strategy_dir = self.repo_path / name
        
        # Find commits
        history = self.get_history(name, limit=200)
        commit1 = commit2 = None
        for commit in history:
            if f"v{version1}" in commit.message:
                commit1 = commit.hash
            if f"v{version2}" in commit.message:
                commit2 = commit.hash
        
        if not commit1 or not commit2:
            raise ValueError("One or both versions not found in history")
        
        return self._run_git("diff", commit1, commit2, "--", str(strategy_dir))
    
    def sync_with_registry(self, registry: StrategyRegistry) -> dict:
        """Sync git store with registry."""
        results = {"synced": [], "errors": []}
        
        for name in registry.list_strategies():
            versions = registry.list_versions(name)
            for version in versions:
                try:
                    existing = self.load_version(name, version.metadata.version)
                    if not existing or existing.source_hash != version.source_hash:
                        self.save_version(version)
                        results["synced"].append(f"{name} v{version.metadata.version}")
                except Exception as e:
                    results["errors"].append(f"{name} v{version.metadata.version}: {e}")
        
        return results