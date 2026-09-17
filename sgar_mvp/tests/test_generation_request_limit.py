"""Both production transports must authorize in the same ledger before send."""
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from sgar_mvp.src.model_accounting import (
    ModelPrice, ModelPricingCatalog, ModelCostPolicy, RunCostLedger, RequestCountBudgetError,
)
from sgar_mvp.src.model_transport import send_chat_completion, async_send_chat_completion


class GenerationRequestLimitTest(unittest.IsolatedAsyncioTestCase):
    async def test_shared_sync_async_limit_blocks_request_25(self):
        await self.check_limit(24, 24)

    async def test_default_has_no_new_request_count_limit(self):
        await self.check_limit(None, 26)

    async def check_limit(self, limit, successful):
        price = ModelPrice(resource_id="test-model", api_model_id="test-api", provider="test",
                           input_per_m="1", output_per_m="1", cache_per_m="0",
                           source_manifest_sha256="a" * 64)
        catalog = ModelPricingCatalog([price], resource_pool_sha256="a" * 64)
        reply = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2))
        sync = Mock(return_value=reply)
        asynchronous = AsyncMock(return_value=reply)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=sync)))
        async_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=asynchronous)))
        with tempfile.TemporaryDirectory() as directory:
            ledger = RunCostLedger(catalog=catalog, policy=ModelCostPolicy(), output_dir=directory,
                                   max_generation_requests=limit)
            context = ledger.new_operation(stage="planner_decompose", model_resource_id="test-model")
            for i in range(successful):
                if i % 2:
                    await async_send_chat_completion(async_client, ledger=ledger, context=context, model="test-api", messages=[])
                else:
                    send_chat_completion(client, ledger=ledger, context=context, model="test-api", messages=[])
            if limit is not None:
                with self.assertRaises(RequestCountBudgetError):
                    send_chat_completion(client, ledger=ledger, context=context, model="test-api", messages=[])
                with self.assertRaises(RequestCountBudgetError):
                    await async_send_chat_completion(async_client, ledger=ledger, context=context, model="test-api", messages=[])
            self.assertEqual(sync.call_count + asynchronous.await_count, successful)
            self.assertEqual(ledger.summary()["started_call_count"], successful)
            ledger.close()
