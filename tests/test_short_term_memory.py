import unittest

from memory.short_term import ShortTermMemory


class FailingRedisClient:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.store = {}
        self.deleted_keys = []
        self.last_ttl = None

    def ping(self):
        return True

    def setex(self, key, ttl, value):
        if self.fail_on == "setex":
            raise RuntimeError("redis setex unavailable")
        self.last_ttl = ttl
        self.store[key] = (ttl, value)

    def get(self, key):
        if self.fail_on == "get":
            raise RuntimeError("redis get unavailable")
        item = self.store.get(key)
        return item[1] if item else None

    def delete(self, key):
        if self.fail_on == "delete":
            raise RuntimeError("redis delete unavailable")
        self.deleted_keys.append(key)
        self.store.pop(key, None)


class ShortTermMemoryRedisFallbackTests(unittest.TestCase):
    def setUp(self):
        ShortTermMemory._instance = None

    def tearDown(self):
        ShortTermMemory._instance = None

    def test_redis_runtime_save_failure_falls_back_to_memory(self):
        memory = ShortTermMemory(storage_type="memory")
        memory.storage_type = "redis"
        memory.redis_client = FailingRedisClient(fail_on="setex")

        memory.add_message("session-a", "user", "hello")

        self.assertEqual(memory.storage_type, "memory")
        self.assertEqual(memory.redis_client, None)
        self.assertEqual(
            memory.get_recent_messages("session-a", limit=10)[0]["content"],
            "hello",
        )

    def test_redis_runtime_load_failure_uses_cached_session(self):
        memory = ShortTermMemory(storage_type="memory")
        memory.storage_type = "redis"
        memory.redis_client = FailingRedisClient(fail_on="get")
        history = memory.create_session("session-b")
        history.add_message("assistant", "cached reply")
        memory.sessions["session-b"] = history

        loaded = memory.get_session("session-b")

        self.assertIsNotNone(loaded)
        self.assertEqual(memory.storage_type, "memory")
        self.assertEqual(loaded.messages[-1]["content"], "cached reply")

    def test_session_ids_remain_isolated_in_memory_fallback(self):
        memory = ShortTermMemory(storage_type="memory")

        memory.add_message("session-1", "user", "one")
        memory.add_message("session-2", "user", "two")

        self.assertEqual(
            [msg["content"] for msg in memory.get_recent_messages("session-1", limit=10)],
            ["one"],
        )
        self.assertEqual(
            [msg["content"] for msg in memory.get_recent_messages("session-2", limit=10)],
            ["two"],
        )


class ShortTermMemoryRedisApiTests(unittest.TestCase):
    def setUp(self):
        ShortTermMemory._instance = None

    def tearDown(self):
        ShortTermMemory._instance = None

    def test_ttl_is_seven_days(self):
        self.assertEqual(ShortTermMemory.REDIS_TTL_SECONDS, 7 * 24 * 3600)
        self.assertEqual(ShortTermMemory.REDIS_TTL_SECONDS, 604800)

    def test_redis_write_then_read_by_session_id(self):
        memory = ShortTermMemory(storage_type="memory")
        memory.storage_type = "redis"
        client = FailingRedisClient()
        memory.redis_client = client

        memory.add_message("session-a", "user", "hello from a")
        memory.add_message("session-b", "user", "hello from b")

        self.assertEqual(client.last_ttl, ShortTermMemory.REDIS_TTL_SECONDS)
        self.assertIn("session:session-a", client.store)
        self.assertIn("session:session-b", client.store)

        memory.sessions.clear()
        loaded_a = memory.get_session("session-a")
        loaded_b = memory.get_session("session-b")

        self.assertIsNotNone(loaded_a)
        self.assertIsNotNone(loaded_b)
        self.assertEqual(loaded_a.messages[-1]["content"], "hello from a")
        self.assertEqual(loaded_b.messages[-1]["content"], "hello from b")


class ShortTermMessageApiTests(unittest.TestCase):
    def setUp(self):
        ShortTermMemory._instance = None

    def tearDown(self):
        ShortTermMemory._instance = None

    def test_get_short_term_messages_returns_session_list(self):
        from webapi.reads import get_short_term_messages

        memory = ShortTermMemory(storage_type="memory")
        memory.add_message("sess-1", "user", "血压有点高")
        memory.add_message("sess-1", "assistant", "建议监测血压")
        memory.add_message("sess-1", "tool", "should-skip")
        memory.add_message("sess-2", "user", "other")

        class _Coord:
            short_term_memory = memory

        payload = get_short_term_messages("sess-1", coordinator=_Coord())
        self.assertEqual(payload["session_id"], "sess-1")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(
            [msg["content"] for msg in payload["messages"]],
            ["血压有点高", "建议监测血压"],
        )
        self.assertEqual([msg["role"] for msg in payload["messages"]], ["user", "assistant"])

    def test_missing_session_returns_empty_list(self):
        from webapi.reads import get_short_term_messages

        memory = ShortTermMemory(storage_type="memory")

        class _Coord:
            short_term_memory = memory

        payload = get_short_term_messages("missing", coordinator=_Coord())
        self.assertEqual(payload["messages"], [])
        self.assertEqual(payload["count"], 0)


if __name__ == "__main__":
    unittest.main()
