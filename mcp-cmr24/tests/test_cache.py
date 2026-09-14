import asyncio
import unittest

from reference_service import AsyncTTLCache


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_eviction(self):
        cache = AsyncTTLCache(maxsize=2, ttl=60)
        for key in "abc":
            await cache.get_or_create(key, lambda k=key: asyncio.sleep(0, result=k))
        self.assertEqual(cache.size, 2)
        self.assertNotIn("a", cache._values)

    async def test_ttl_expiration(self):
        cache = AsyncTTLCache(maxsize=2, ttl=0.01)
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            return calls

        self.assertEqual(await cache.get_or_create("x", factory), 1)
        await asyncio.sleep(0.02)
        self.assertEqual(await cache.get_or_create("x", factory), 2)

    async def test_concurrent_fill_is_single(self):
        cache = AsyncTTLCache(maxsize=2, ttl=60)
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return "value"

        values = await asyncio.gather(
            *(cache.get_or_create("x", factory) for _ in range(20))
        )
        self.assertEqual(calls, 1)
        self.assertEqual(values, ["value"] * 20)
