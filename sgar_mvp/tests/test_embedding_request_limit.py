"""The embedding cap must authorize immediately before network transport."""

import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sgar_mvp.src.embedding_runtime import (
    EmbeddingRequestLimitError,
    _OpenAICompatibleEmbeddingEncoder,
    embedding_request_budget,
)


class _Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class EmbeddingRequestLimitTest(unittest.TestCase):
    def test_second_transport_is_blocked_before_send(self):
        config = SimpleNamespace(
            api_key_environment_variable="SGAR_TEST_EMBEDDING_KEY",
            api_model_id="test-embedding-model",
            api_endpoint_url="https://embedding.invalid/v1/embeddings",
            output_dimension=2,
        )
        encoder = object.__new__(_OpenAICompatibleEmbeddingEncoder)
        encoder.config = config
        response = _Response(
            {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}
        )
        with patch.dict(os.environ, {"SGAR_TEST_EMBEDDING_KEY": "secret"}), patch(
            "sgar_mvp.src.embedding_runtime.urllib.request.urlopen",
            return_value=response,
        ) as transport:
            with embedding_request_budget(1) as budget:
                with self.assertRaises(EmbeddingRequestLimitError):
                    encoder.encode(
                        ["first", "second"],
                        batch_size=1,
                        show_progress_bar=False,
                        normalize_embeddings=False,
                    )
                summary = budget.summary()

        self.assertEqual(transport.call_count, 1)
        self.assertEqual(summary["started_request_count"], 1)
        self.assertEqual(summary["blocked_request_count"], 1)
        self.assertEqual(summary["input_count"], 1)

    def test_unbound_budget_preserves_default_transport(self):
        config = SimpleNamespace(
            api_key_environment_variable="SGAR_TEST_EMBEDDING_KEY",
            api_model_id="test-embedding-model",
            api_endpoint_url="https://embedding.invalid/v1/embeddings",
            output_dimension=2,
        )
        encoder = object.__new__(_OpenAICompatibleEmbeddingEncoder)
        encoder.config = config
        response = _Response(
            {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}
        )
        with patch.dict(os.environ, {"SGAR_TEST_EMBEDDING_KEY": "secret"}), patch(
            "sgar_mvp.src.embedding_runtime.urllib.request.urlopen",
            return_value=response,
        ) as transport:
            encoder.encode(
                ["first", "second"],
                batch_size=1,
                show_progress_bar=False,
                normalize_embeddings=False,
            )
        self.assertEqual(transport.call_count, 2)


if __name__ == "__main__":
    unittest.main()
