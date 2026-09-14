"""Model-independent, offline-only embedding runtime for SGAR retrieval."""

from __future__ import annotations

import json
import hashlib
import os
import threading
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

import numpy as np
from pydantic import Field, field_validator, model_validator

from .pipeline_control import FrozenContract, canonical_sha256
from .retrieval_lifecycle import (
    LocalEmbeddingIdentity,
    RetrievalLifecycleError,
    build_local_embedding_identity,
)


EMBEDDING_RUNTIME_PROTOCOL = "sgar-embedding-runtime-config-v1"
EMBEDDING_RUNTIME_IDENTITY_PROTOCOL = "sgar-embedding-runtime-identity-v2"
INDEX_META_PROTOCOL = "sgar-index-meta-v2"
CAPABILITY_QUERY_INSTRUCTION = (
    "Given an S-GAR subtask, retrieve resources whose declared capabilities "
    "and problem space can perform it."
)
CONSTRAINT_QUERY_INSTRUCTION = (
    "Given an S-GAR subtask contract, retrieve resources whose declared input, "
    "output, interaction, and runtime constraints are compatible."
)
QueryRole = Literal["capability", "constraint", "raw"]


class EmbeddingRuntimeConfigV1(FrozenContract):
    """Host-free identity for one embedding/index candidate."""

    protocol: Literal[EMBEDDING_RUNTIME_PROTOCOL] = EMBEDDING_RUNTIME_PROTOCOL
    candidate_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    revision: str | None = None
    family: Literal["bge", "bge_icl", "qwen3"]
    release_eligibility: Literal[
        "historical_only", "eligible", "feasibility_only", "reference_only"
    ] = "eligible"
    official_source: Literal[True] = True
    output_dimension: int = Field(gt=0)
    capability_query_instruction: str = Field(min_length=1)
    constraint_query_instruction: str = Field(min_length=1)
    raw_query_instruction: str = Field(min_length=1)
    document_instruction: str = ""
    pooling: Literal["sentence_transformers", "last_token"]
    normalize: Literal[True] = True
    max_tokens: int = Field(gt=0, le=32768)
    dtype: Literal["float32", "float16", "bfloat16", "auto"] = "auto"
    quantization: Literal["none", "int8"] = "none"
    int8_threshold: float = 6.0
    int8_fp32_cpu_offload: bool = False
    device_map: str | dict[str, Any] | None = None
    attention_implementation: Literal["sdpa", "eager"] = "sdpa"
    local_files_only: Literal[True] = True
    configuration_sha256: str = ""

    @field_validator("revision")
    @classmethod
    def _revision_is_commit(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip().lower()
        if len(normalized) != 40 or any(ch not in "0123456789abcdef" for ch in normalized):
            raise ValueError("embedding_revision_invalid")
        return normalized

    @model_validator(mode="after")
    def _seal(self) -> "EmbeddingRuntimeConfigV1":
        if self.quantization == "none" and self.int8_fp32_cpu_offload:
            raise ValueError("int8_offload_requires_int8_quantization")
        if self.quantization == "int8" and self.family != "qwen3":
            raise ValueError("int8_quantization_requires_qwen3")
        if self.family == "bge" and self.quantization != "none":
            raise ValueError("bge_quantization_not_supported")
        if self.family == "qwen3" and self.pooling != "last_token":
            raise ValueError("qwen3_requires_last_token_pooling")
        if self.family == "bge_icl" and self.pooling != "last_token":
            raise ValueError("bge_icl_requires_last_token_pooling")
        if self.release_eligibility == "historical_only" and self.family != "bge":
            raise ValueError("historical_embedding_candidate_family_invalid")
        projection = self.model_dump(mode="python", exclude={"configuration_sha256"})
        expected = canonical_sha256(projection)
        if self.configuration_sha256 and self.configuration_sha256 != expected:
            raise ValueError("embedding_configuration_sha256_mismatch")
        object.__setattr__(self, "configuration_sha256", expected)
        return self

    def instruction_for(self, role: QueryRole) -> str:
        return {
            "capability": self.capability_query_instruction,
            "constraint": self.constraint_query_instruction,
            "raw": self.raw_query_instruction,
        }[role]


class EmbeddingRuntimeIdentityV2(FrozenContract):
    """Host-free identity for exact model bytes, encoding, and package runtime."""

    protocol: Literal[EMBEDDING_RUNTIME_IDENTITY_PROTOCOL] = (
        EMBEDDING_RUNTIME_IDENTITY_PROTOCOL
    )
    candidate_id: str = Field(min_length=1)
    model_repo_id: str = Field(min_length=1)
    model_revision: str
    file_collection_sha256: str
    tokenizer_sha256: str
    capability_instruction_sha256: str
    constraint_instruction_sha256: str
    document_instruction_sha256: str
    pooling: str = Field(min_length=1)
    normalization: Literal[True] = True
    output_dimension: int = Field(gt=0)
    dtype: str = Field(min_length=1)
    quantization: str = Field(min_length=1)
    max_input_tokens: int = Field(gt=0)
    runtime_package_versions: dict[str, str]
    identity_sha256: str = ""

    @model_validator(mode="after")
    def _seal(self) -> "EmbeddingRuntimeIdentityV2":
        for field_name in (
            "model_revision",
            "file_collection_sha256",
            "tokenizer_sha256",
            "capability_instruction_sha256",
            "constraint_instruction_sha256",
            "document_instruction_sha256",
        ):
            value = str(getattr(self, field_name)).strip().lower()
            if len(value) not in {40, 64} or any(
                character not in "0123456789abcdef" for character in value
            ):
                raise ValueError(f"{field_name}_invalid")
            if field_name != "model_revision" and len(value) != 64:
                raise ValueError(f"{field_name}_invalid")
            if field_name == "model_revision" and len(value) != 40:
                raise ValueError("model_revision_invalid")
            object.__setattr__(self, field_name, value)
        projection = self.model_dump(mode="python", exclude={"identity_sha256"})
        expected = canonical_sha256(projection)
        if self.identity_sha256 and self.identity_sha256 != expected:
            raise ValueError("embedding_runtime_identity_sha256_mismatch")
        object.__setattr__(self, "identity_sha256", expected)
        return self


class IndexMetaV2(FrozenContract):
    """Portable identity of one independently built capability/constraint index."""

    protocol: Literal[INDEX_META_PROTOCOL] = INDEX_META_PROTOCOL
    embedding_runtime_identity: EmbeddingRuntimeIdentityV2
    resource_manifest_sha256: str
    resource_id_order: tuple[str, ...]
    resource_id_order_sha256: str
    capability_vectors_sha256: str
    constraint_vectors_sha256: str
    faiss_kind: Literal["IndexFlatIP"] = "IndexFlatIP"
    faiss_dimension: int = Field(gt=0)
    vector_count: int = Field(gt=0)
    index_meta_sha256: str = ""

    @model_validator(mode="after")
    def _seal(self) -> "IndexMetaV2":
        if self.faiss_dimension != self.embedding_runtime_identity.output_dimension:
            raise ValueError("index_meta_embedding_dimension_mismatch")
        if self.vector_count != len(self.resource_id_order):
            raise ValueError("index_meta_vector_count_mismatch")
        if self.resource_id_order_sha256 != canonical_sha256(self.resource_id_order):
            raise ValueError("index_meta_resource_order_sha256_mismatch")
        for field_name in (
            "resource_manifest_sha256",
            "resource_id_order_sha256",
            "capability_vectors_sha256",
            "constraint_vectors_sha256",
        ):
            value = str(getattr(self, field_name)).strip().lower()
            if len(value) != 64 or any(
                character not in "0123456789abcdef" for character in value
            ):
                raise ValueError(f"{field_name}_invalid")
        projection = self.model_dump(mode="python", exclude={"index_meta_sha256"})
        expected = canonical_sha256(projection)
        if self.index_meta_sha256 and self.index_meta_sha256 != expected:
            raise ValueError("index_meta_sha256_mismatch")
        object.__setattr__(self, "index_meta_sha256", expected)
        return self


def _tree_subset_sha256(root: Path, names: Sequence[str]) -> str:
    digest = hashlib.sha256()
    matched = [
        path
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix())
        if path.is_file() and any(token in path.name.lower() for token in names)
    ]
    if not matched:
        raise RetrievalLifecycleError("embedding_tokenizer_files_missing")
    for path in matched:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def embedding_runtime_identity_v2(
    config: EmbeddingRuntimeConfigV1,
    local_identity: LocalEmbeddingIdentity,
    snapshot: Path,
) -> EmbeddingRuntimeIdentityV2:
    packages: dict[str, str] = {}
    for package in (
        "torch",
        "transformers",
        "sentence-transformers",
        "accelerate",
        "bitsandbytes",
        "faiss-cpu",
    ):
        try:
            packages[package] = importlib_metadata.version(package)
        except importlib_metadata.PackageNotFoundError:
            packages[package] = "not-installed"
    return EmbeddingRuntimeIdentityV2(
        candidate_id=config.candidate_id,
        model_repo_id=config.model_id,
        model_revision=str(local_identity.embedding_model_revision),
        file_collection_sha256=local_identity.snapshot_content_sha256,
        tokenizer_sha256=_tree_subset_sha256(
            snapshot,
            ("tokenizer", "vocab", "merges", "special_tokens"),
        ),
        capability_instruction_sha256=hashlib.sha256(
            config.capability_query_instruction.encode("utf-8")
        ).hexdigest(),
        constraint_instruction_sha256=hashlib.sha256(
            config.constraint_query_instruction.encode("utf-8")
        ).hexdigest(),
        document_instruction_sha256=hashlib.sha256(
            config.document_instruction.encode("utf-8")
        ).hexdigest(),
        pooling=config.pooling,
        output_dimension=config.output_dimension,
        dtype=config.dtype,
        quantization=config.quantization,
        max_input_tokens=config.max_tokens,
        runtime_package_versions=packages,
    )


def load_embedding_release_config(path: Path) -> EmbeddingRuntimeConfigV1:
    """Load the one fixed embedding identity allowed on the production branch."""

    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if payload.get("protocol") != "sgar-embedding-release-config-v1":
        raise ValueError("embedding_release_config_protocol_invalid")
    config = EmbeddingRuntimeConfigV1.model_validate(payload.get("runtime"))
    if (
        config.candidate_id != "qwen3-embedding-0.6b-bf16-1024"
        or config.model_id != "Qwen/Qwen3-Embedding-0.6B"
        or config.family != "qwen3"
        or config.dtype != "bfloat16"
        or config.quantization != "none"
        or config.output_dimension != 1024
    ):
        raise ValueError("embedding_release_config_identity_invalid")
    return config


def embedding_config_from_metadata(
    metadata: Mapping[str, Any],
    *,
    fallback_model_id: str,
    fallback_query_prefix: str,
) -> EmbeddingRuntimeConfigV1:
    raw = metadata.get("embedding_runtime_config")
    if isinstance(raw, Mapping):
        return EmbeddingRuntimeConfigV1.model_validate(raw)
    del fallback_model_id, fallback_query_prefix
    raise RetrievalLifecycleError("embedding_runtime_config_missing_from_index")


def prepare_query_texts(
    texts: Sequence[str],
    config: EmbeddingRuntimeConfigV1,
    *,
    role: QueryRole,
) -> list[str]:
    instruction = config.instruction_for(role)
    if config.family == "qwen3":
        return [f"Instruct: {instruction}\nQuery: {text}" for text in texts]
    return [f"{instruction}{text}" for text in texts]


def prepare_document_texts(
    texts: Sequence[str],
    config: EmbeddingRuntimeConfigV1,
) -> list[str]:
    if not config.document_instruction:
        return [str(text) for text in texts]
    return [f"{config.document_instruction}{text}" for text in texts]


def _normalize_and_truncate(vectors: Any, dimension: int) -> np.ndarray:
    matrix = np.asarray(vectors, dtype="float32")
    if matrix.ndim != 2 or matrix.shape[0] == 0:
        raise RetrievalLifecycleError("embedding_output_matrix_invalid")
    if matrix.shape[1] < dimension:
        raise RetrievalLifecycleError("embedding_output_dimension_too_small")
    matrix = matrix[:, :dimension]
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if not np.isfinite(matrix).all() or not np.isfinite(norms).all() or np.any(norms <= 0):
        raise RetrievalLifecycleError("embedding_output_non_finite")
    return np.asarray(matrix / norms, dtype="float32")


class _Qwen3NativeEncoder:
    """Official Transformers Qwen3 path with last-token pooling.

    SentenceTransformers 5.3 unconditionally calls ``.to()`` after loading a
    device-mapped model. That is invalid for int8 layers dispatched through
    Accelerate and can leave meta tensors. This adapter uses the model-card
    pooling algorithm directly while preserving the same tokenizer/model
    bytes and output semantics.
    """

    def __init__(
        self,
        snapshot: Path,
        *,
        model_kwargs: Mapping[str, Any],
        max_tokens: int,
    ) -> None:
        import torch
        from transformers import AutoModel, AutoTokenizer

        self._torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(snapshot),
            local_files_only=True,
            padding_side="left",
        )
        self.model = AutoModel.from_pretrained(
            str(snapshot),
            local_files_only=True,
            **dict(model_kwargs),
        )
        if not getattr(self.model, "hf_device_map", None):
            device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
            self.model.to(device)
        self.model.eval()
        self.max_seq_length = max_tokens
        self._input_device = self._resolve_input_device()

    def _resolve_input_device(self) -> Any:
        torch = self._torch
        device_map = getattr(self.model, "hf_device_map", None)
        if isinstance(device_map, Mapping):
            for key, raw_device in device_map.items():
                if not str(key).endswith(("embed_tokens", "embeddings")):
                    continue
                if isinstance(raw_device, int):
                    return torch.device(f"cuda:{raw_device}")
                if str(raw_device).startswith("cuda"):
                    return torch.device(str(raw_device))
            for raw_device in device_map.values():
                if isinstance(raw_device, int):
                    return torch.device(f"cuda:{raw_device}")
                if str(raw_device).startswith("cuda"):
                    return torch.device(str(raw_device))
        for parameter in self.model.parameters():
            if parameter.device.type != "meta":
                return parameter.device
        raise RetrievalLifecycleError("qwen_embedding_input_device_unresolved")

    def _last_token_pool(self, last_hidden_states: Any, attention_mask: Any) -> Any:
        left_padding = bool(
            attention_mask[:, -1].sum().item() == attention_mask.shape[0]
        )
        if left_padding:
            return last_hidden_states[:, -1]
        sequence_lengths = (attention_mask.sum(dim=1) - 1).to(
            last_hidden_states.device
        )
        batch_size = last_hidden_states.shape[0]
        return last_hidden_states[
            self._torch.arange(batch_size, device=last_hidden_states.device),
            sequence_lengths,
        ]

    def encode(
        self,
        values: Sequence[str],
        *,
        batch_size: int,
        show_progress_bar: bool,
        normalize_embeddings: bool,
    ) -> np.ndarray:
        del show_progress_bar, normalize_embeddings
        outputs: list[np.ndarray] = []
        torch = self._torch
        for offset in range(0, len(values), batch_size):
            batch_values = list(values[offset : offset + batch_size])
            encoded = self.tokenizer(
                batch_values,
                padding=True,
                truncation=False,
                return_tensors="pt",
            )
            encoded = {key: value.to(self._input_device) for key, value in encoded.items()}
            with torch.inference_mode():
                model_output = self.model(**encoded)
                pooled = self._last_token_pool(
                    model_output.last_hidden_state,
                    encoded["attention_mask"],
                )
            outputs.append(pooled.detach().float().cpu().numpy())
        return np.concatenate(outputs, axis=0)


class LocalEmbeddingEncoder:
    """One process-level, re-entrant encoder loaded from a pinned local snapshot."""

    def __init__(
        self,
        config: EmbeddingRuntimeConfigV1,
        *,
        encoder_factory: Any | None = None,
    ) -> None:
        self.config = config
        self._encoder_factory = encoder_factory
        self._lock = threading.RLock()
        self._encoder: Any | None = None
        self._identity: LocalEmbeddingIdentity | None = None
        self._snapshot: Path | None = None

    @property
    def encoder(self) -> Any | None:
        return self._encoder

    @encoder.setter
    def encoder(self, value: Any | None) -> None:
        self._encoder = value

    def identity(self) -> LocalEmbeddingIdentity:
        with self._lock:
            if self._identity is None:
                identity, snapshot = build_local_embedding_identity(
                    model_id=self.config.model_id,
                    revision=self.config.revision,
                    index_dimension=self.config.output_dimension,
                    encoding_configuration=self.config.model_dump(mode="json"),
                )
                self._identity = identity
                self._snapshot = snapshot
            return self._identity

    def runtime_identity_v2(self) -> EmbeddingRuntimeIdentityV2:
        with self._lock:
            local_identity = self.identity()
            assert self._snapshot is not None
            return embedding_runtime_identity_v2(
                self.config,
                local_identity,
                self._snapshot,
            )

    def _model_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "attn_implementation": self.config.attention_implementation,
        }
        if self.config.dtype != "auto":
            import torch

            dtype_key = "dtype" if self.config.family == "qwen3" else "torch_dtype"
            kwargs[dtype_key] = {
                "float32": torch.float32,
                "float16": torch.float16,
                "bfloat16": torch.bfloat16,
            }[self.config.dtype]
        if self.config.quantization == "int8":
            from transformers import BitsAndBytesConfig

            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_8bit=True,
                llm_int8_threshold=self.config.int8_threshold,
                llm_int8_enable_fp32_cpu_offload=self.config.int8_fp32_cpu_offload,
            )
            kwargs["device_map"] = self.config.device_map or "auto"
        elif self.config.family == "qwen3" and self.config.device_map is not None:
            kwargs["device_map"] = self.config.device_map
        if "device_map" in kwargs:
            offload_root = Path(
                os.environ.get("SGAR_EMBEDDING_OFFLOAD_DIR", "D:/sgar_embedding_runtime")
            ).expanduser().resolve()
            offload_root.mkdir(parents=True, exist_ok=True)
            kwargs["offload_folder"] = str(offload_root)
        return kwargs

    def load(self) -> Any:
        with self._lock:
            if self._encoder is not None:
                return self._encoder
            self.identity()
            assert self._snapshot is not None
            factory = self._encoder_factory
            if factory is None and self.config.family in {"qwen3", "bge_icl"}:
                encoder = _Qwen3NativeEncoder(
                    self._snapshot,
                    model_kwargs=self._model_kwargs(),
                    max_tokens=self.config.max_tokens,
                )
                self._encoder = encoder
                return encoder
            if factory is None:
                from sentence_transformers import SentenceTransformer

                factory = SentenceTransformer
            constructor_kwargs: dict[str, Any] = {
                "local_files_only": True,
                "truncate_dim": self.config.output_dimension,
            }
            model_kwargs = self._model_kwargs()
            if model_kwargs:
                constructor_kwargs["model_kwargs"] = model_kwargs
            try:
                encoder = factory(str(self._snapshot), **constructor_kwargs)
            except TypeError:
                if self._encoder_factory is None:
                    raise
                encoder = factory(str(self._snapshot), local_files_only=True)
            if hasattr(encoder, "max_seq_length"):
                encoder.max_seq_length = self.config.max_tokens
            self._encoder = encoder
            return encoder

    def _assert_token_lengths(self, texts: Sequence[str]) -> None:
        encoder = self.load()
        tokenizer = getattr(encoder, "tokenizer", None)
        if tokenizer is None:
            return
        encoded = tokenizer(
            list(texts),
            add_special_tokens=True,
            padding=False,
            truncation=False,
        )
        input_ids = encoded.get("input_ids") if isinstance(encoded, Mapping) else None
        if input_ids is None:
            raise RetrievalLifecycleError("embedding_tokenizer_output_invalid")
        for index, token_ids in enumerate(input_ids):
            if len(token_ids) > self.config.max_tokens:
                raise RetrievalLifecycleError(
                    f"embedding_input_token_limit_exceeded:{index}:{len(token_ids)}"
                )

    def assert_token_lengths(self, texts: Sequence[str]) -> None:
        """Public fail-closed preflight used before accepting a Profiler output."""

        with self._lock:
            self._assert_token_lengths(texts)

    def _encode(self, prepared_texts: Sequence[str], *, batch_size: int) -> np.ndarray:
        if not prepared_texts:
            return np.empty((0, self.config.output_dimension), dtype="float32")
        # One process owns one model instance.  Serialize inference so that callers
        # may issue concurrent retrieval requests without concurrently mutating a
        # shared CUDA model or allocating duplicate execution workspaces.
        with self._lock:
            self._assert_token_lengths(prepared_texts)
            encoder = self.load()
            vectors = encoder.encode(
                list(prepared_texts),
                batch_size=batch_size,
                show_progress_bar=False,
                normalize_embeddings=False,
            )
            return _normalize_and_truncate(vectors, self.config.output_dimension)

    def encode_queries(
        self,
        texts: Sequence[str],
        *,
        role: QueryRole,
        batch_size: int = 16,
    ) -> np.ndarray:
        return self._encode(
            prepare_query_texts(texts, self.config, role=role),
            batch_size=batch_size,
        )

    def encode_documents(
        self,
        texts: Sequence[str],
        *,
        batch_size: int = 16,
    ) -> np.ndarray:
        return self._encode(
            prepare_document_texts(texts, self.config),
            batch_size=batch_size,
        )

    def close(self) -> None:
        with self._lock:
            self._encoder = None
            self._identity = None
            self._snapshot = None


__all__ = [
    "CAPABILITY_QUERY_INSTRUCTION",
    "CONSTRAINT_QUERY_INSTRUCTION",
    "EMBEDDING_RUNTIME_PROTOCOL",
    "EMBEDDING_RUNTIME_IDENTITY_PROTOCOL",
    "INDEX_META_PROTOCOL",
    "EmbeddingRuntimeConfigV1",
    "EmbeddingRuntimeIdentityV2",
    "IndexMetaV2",
    "LocalEmbeddingEncoder",
    "embedding_config_from_metadata",
    "embedding_runtime_identity_v2",
    "load_embedding_release_config",
    "prepare_document_texts",
    "prepare_query_texts",
]
