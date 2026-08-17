"""Content-addressed feature artifacts with explicit research provenance."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd


FEATURE_FRAMEWORK_VERSION = "feature-artifact-v2"


class FeatureStoreError(RuntimeError):
    pass


def _validate_sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value.lower()):
        raise ValueError(f"{label} must be a 64-character SHA-256 hex digest")


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def dataframe_schema_hash(frame: pd.DataFrame) -> str:
    return canonical_sha256(
        {
            "columns": [str(column) for column in frame.columns],
            "dtypes": [str(dtype) for dtype in frame.dtypes],
            "index_name": str(frame.index.name),
            "index_dtype": str(frame.index.dtype),
        }
    )


def dataframe_manifest_sha(frame: pd.DataFrame) -> str:
    hashed = pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes()
    digest = hashlib.sha256()
    digest.update(hashed)
    digest.update(dataframe_schema_hash(frame).encode("ascii"))
    return digest.hexdigest()


def feature_code_sha(feature: Callable[..., Any] | str) -> str:
    if callable(feature):
        try:
            source = inspect.getsource(feature)
        except (OSError, TypeError):
            source = f"{feature.__module__}.{feature.__qualname__}"
    else:
        source = str(feature)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FeatureArtifact:
    artifact_id: str
    feature_name: str
    feature_code_sha: str
    params_hash: str
    input_data_manifest_sha: str
    schema_hash: str
    symbol_or_universe: str
    timeframe: str
    feature_framework_version: str
    storage_format: str
    relative_path: str
    content_sha: str
    row_count: int
    schema_metadata: dict[str, Any]
    created_at: datetime
    provenance_status: str

    @classmethod
    def identity(
        cls,
        *,
        feature_name: str,
        feature_code_sha: str,
        params_hash: str,
        input_data_manifest_sha: str,
        schema_hash: str,
        symbol_or_universe: str,
        timeframe: str,
        feature_framework_version: str,
    ) -> str:
        return canonical_sha256(
            {
                "feature_name": feature_name,
                "feature_code_sha": feature_code_sha,
                "params_hash": params_hash,
                "input_data_manifest_sha": input_data_manifest_sha,
                "schema_hash": schema_hash,
                "symbol_or_universe": symbol_or_universe,
                "timeframe": timeframe,
                "feature_framework_version": feature_framework_version,
            }
        )


class FeatureStore:
    """Append-only artifact store; manifest determines symmetric read format."""

    def __init__(self, base_path: str | Path = "features") -> None:
        self.base = Path(base_path)
        self.base.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, pd.DataFrame] = {}

    @staticmethod
    def _safe_component(value: str) -> str:
        cleaned = value.replace("\\", "__").replace("/", "__").strip()
        if not cleaned or cleaned in {".", ".."}:
            raise ValueError("feature path component must be non-empty and safe")
        return cleaned

    def _artifact_dir(self, symbol: str, feature_name: str) -> Path:
        return self.base / self._safe_component(symbol) / self._safe_component(feature_name)

    @staticmethod
    def _params_hash(params: dict[str, Any] | None) -> str:
        return canonical_sha256(params or {})

    @staticmethod
    def _schema_metadata(frame: pd.DataFrame) -> dict[str, Any]:
        return {
            "columns": [str(column) for column in frame.columns],
            "dtypes": {str(column): str(dtype) for column, dtype in frame.dtypes.items()},
            "index_name": frame.index.name,
            "index_dtype": str(frame.index.dtype),
            "index_freq": str(getattr(frame.index, "freqstr", "") or ""),
        }

    @staticmethod
    def _manifest_from_path(path: Path) -> FeatureArtifact:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["created_at"] = datetime.fromisoformat(payload["created_at"])
            return FeatureArtifact(**payload)
        except Exception as exc:
            raise FeatureStoreError(f"invalid feature artifact manifest {path}: {exc}") from exc

    def put(
        self,
        symbol: str,
        alpha_name: str,
        frame: pd.DataFrame,
        params: dict[str, Any] | None = None,
        *,
        feature_code_hash: str | None = None,
        feature_callable: Callable[..., Any] | None = None,
        input_data_manifest_sha: str | None = None,
        schema_hash: str | None = None,
        timeframe: str = "unknown",
        feature_framework_version: str = FEATURE_FRAMEWORK_VERSION,
    ) -> str:
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("frame must be a pandas DataFrame")
        verified_code = feature_code_hash is not None or feature_callable is not None
        verified_input = input_data_manifest_sha is not None
        code_hash = feature_code_hash or feature_code_sha(feature_callable or alpha_name)
        data_hash = input_data_manifest_sha or dataframe_manifest_sha(frame)
        actual_schema_hash = dataframe_schema_hash(frame)
        if schema_hash is not None and schema_hash != actual_schema_hash:
            raise ValueError("schema_hash does not match the feature frame schema")
        output_schema_hash = actual_schema_hash
        _validate_sha256(code_hash, "feature_code_hash")
        _validate_sha256(data_hash, "input_data_manifest_sha")
        if not timeframe.strip() or not feature_framework_version.strip():
            raise ValueError("timeframe and feature_framework_version must be non-empty")
        params_digest = self._params_hash(params)
        artifact_id = FeatureArtifact.identity(
            feature_name=alpha_name,
            feature_code_sha=code_hash,
            params_hash=params_digest,
            input_data_manifest_sha=data_hash,
            schema_hash=output_schema_hash,
            symbol_or_universe=symbol,
            timeframe=timeframe,
            feature_framework_version=feature_framework_version,
        )
        directory = self._artifact_dir(symbol, alpha_name)
        directory.mkdir(parents=True, exist_ok=True)
        manifest_path = directory / f"{artifact_id}.artifact.json"
        if manifest_path.exists():
            artifact = self._manifest_from_path(manifest_path)
            self._cache[artifact_id] = self._read_artifact(artifact, directory)
            return artifact_id

        storage_format = "parquet"
        relative_path = f"{artifact_id}.parquet"
        destination = directory / relative_path
        temporary: Path | None = None
        try:
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{artifact_id}.", suffix=".parquet", dir=directory
            )
            os.close(descriptor)
            temporary = Path(temp_name)
            try:
                frame.to_parquet(temporary, index=True)
            except Exception:
                temporary.unlink(missing_ok=True)
                descriptor, temp_name = tempfile.mkstemp(
                    prefix=f".{artifact_id}.", suffix=".csv", dir=directory
                )
                os.close(descriptor)
                temporary = Path(temp_name)
                storage_format = "csv"
                relative_path = f"{artifact_id}.csv"
                destination = directory / relative_path
                frame.to_csv(temporary, index=True)
            os.replace(temporary, destination)
            temporary = None
            content_sha = hashlib.sha256(destination.read_bytes()).hexdigest()
            artifact = FeatureArtifact(
                artifact_id=artifact_id,
                feature_name=alpha_name,
                feature_code_sha=code_hash,
                params_hash=params_digest,
                input_data_manifest_sha=data_hash,
                schema_hash=output_schema_hash,
                symbol_or_universe=symbol,
                timeframe=timeframe,
                feature_framework_version=feature_framework_version,
                storage_format=storage_format,
                relative_path=relative_path,
                content_sha=content_sha,
                row_count=len(frame),
                schema_metadata=self._schema_metadata(frame),
                created_at=datetime.now(UTC),
                provenance_status=(
                    "VERIFIED" if verified_code and verified_input else "DERIVED_FALLBACK"
                ),
            )
            manifest_tmp = manifest_path.with_suffix(".json.tmp")
            manifest_tmp.write_text(
                json.dumps(asdict(artifact), default=str, sort_keys=True, indent=2),
                encoding="utf-8",
            )
            os.replace(manifest_tmp, manifest_path)
            self._cache[artifact_id] = frame.copy()
            return artifact_id
        except Exception as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            evidence = directory / f"{artifact_id}.storage-error.json"
            evidence.write_text(
                json.dumps(
                    {
                        "artifact_id": artifact_id,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "created_at": datetime.now(UTC).isoformat(),
                    },
                    sort_keys=True,
                    indent=2,
                ),
                encoding="utf-8",
            )
            raise FeatureStoreError(
                f"failed to persist feature artifact {artifact_id}; evidence={evidence}"
            ) from exc

    def get(
        self,
        symbol: str,
        alpha_name: str,
        params: dict[str, Any] | None = None,
        *,
        artifact_id: str | None = None,
        feature_code_hash: str | None = None,
        input_data_manifest_sha: str | None = None,
        schema_hash: str | None = None,
        timeframe: str | None = None,
        feature_framework_version: str | None = None,
    ) -> pd.DataFrame | None:
        directory = self._artifact_dir(symbol, alpha_name)
        if not directory.exists():
            return None
        candidates = [
            self._manifest_from_path(path)
            for path in sorted(directory.glob("*.artifact.json"))
        ]
        params_digest = self._params_hash(params)
        candidates = [artifact for artifact in candidates if artifact.params_hash == params_digest]
        filters = {
            "artifact_id": artifact_id,
            "feature_code_sha": feature_code_hash,
            "input_data_manifest_sha": input_data_manifest_sha,
            "schema_hash": schema_hash,
            "timeframe": timeframe,
            "feature_framework_version": feature_framework_version,
        }
        for key, expected in filters.items():
            if expected is not None:
                candidates = [
                    artifact for artifact in candidates if getattr(artifact, key) == expected
                ]
        if not candidates:
            return None
        if len(candidates) > 1:
            raise FeatureStoreError(
                "ambiguous feature lookup; supply artifact_id or complete provenance"
            )
        artifact = candidates[0]
        if artifact.artifact_id in self._cache:
            return self._cache[artifact.artifact_id].copy()
        frame = self._read_artifact(artifact, directory)
        self._cache[artifact.artifact_id] = frame
        return frame.copy()

    def _read_artifact(self, artifact: FeatureArtifact, directory: Path) -> pd.DataFrame:
        path = directory / artifact.relative_path
        if not path.exists():
            raise FeatureStoreError(f"feature payload missing: {path}")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != artifact.content_sha:
            raise FeatureStoreError(f"feature payload integrity mismatch: {path}")
        try:
            if artifact.storage_format == "parquet":
                frame = pd.read_parquet(path)
            elif artifact.storage_format == "csv":
                frame = pd.read_csv(path, index_col=0)
                for column, dtype in artifact.schema_metadata["dtypes"].items():
                    if dtype.startswith("datetime"):
                        frame[column] = pd.to_datetime(frame[column])
                    else:
                        frame[column] = frame[column].astype(dtype)
                index_dtype = artifact.schema_metadata["index_dtype"]
                if index_dtype.startswith("datetime"):
                    frame.index = pd.to_datetime(frame.index)
                    index_freq = artifact.schema_metadata.get("index_freq")
                    if index_freq:
                        frame.index = pd.DatetimeIndex(frame.index, freq=index_freq)
                elif index_dtype.startswith("int") or index_dtype.startswith("uint"):
                    frame.index = frame.index.astype(index_dtype)
                elif index_dtype.startswith("float"):
                    frame.index = frame.index.astype(index_dtype)
                frame.index.name = artifact.schema_metadata["index_name"]
            else:
                raise FeatureStoreError(
                    f"unsupported feature storage format: {artifact.storage_format}"
                )
        except FeatureStoreError:
            raise
        except Exception as exc:
            raise FeatureStoreError(f"failed to read feature payload {path}: {exc}") from exc
        if len(frame) != artifact.row_count:
            raise FeatureStoreError(f"feature row-count mismatch: {path}")
        if dataframe_schema_hash(frame) != artifact.schema_hash:
            raise FeatureStoreError(f"feature schema mismatch: {path}")
        return frame

    def list_alphas(self, symbol: str) -> list[str]:
        symbol_path = self.base / self._safe_component(symbol)
        if not symbol_path.exists():
            return []
        return sorted(path.name for path in symbol_path.iterdir() if path.is_dir())

    def versions(self, symbol: str, alpha_name: str) -> list[str]:
        directory = self._artifact_dir(symbol, alpha_name)
        if not directory.exists():
            return []
        return sorted(path.name.removesuffix(".artifact.json") for path in directory.glob("*.artifact.json"))
