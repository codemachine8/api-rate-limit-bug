"""
Test async race condition issues
Category: async_race_condition (Expected fix rate: 55-65%)

These tests fail due to:
- Missing locks on shared state
- Non-atomic operations in concurrent code
- Race conditions between check and update

Imports from: L4 (integrations), L3 (handlers), L2 (services), L1 (core)
"""
import pytest
import asyncio
import threading
import time
import random

# Level 1 imports
from src.core.base import BaseComponent, EventEmitter

# Level 2 imports
from src.services.cache import AsyncCache, LRUCache
from src.services.queue import AsyncQueue, Message

# Level 3 imports
from src.handlers.api_handler import ApiHandler, Request, HttpMethod, RateLimiter, WebSocketHandler
from src.handlers.event_handler import EventBus, Event, EventType

# Level 4 imports
from src.integrations.external_api import ExternalApiClient, ExternalApiConfig
from src.integrations.database_client import Connection, ConnectionPool


class TestAsyncCacheRaces:
    """Race conditions in async cache operations"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        AsyncCache.reset_instance()
        yield
        AsyncCache.reset_instance()
    
    @pytest.mark.asyncio
    async def test_concurrent_cache_updates(self):
        """
        FLAKY: Concurrent updates to same key cause inconsistent state
        FIX: Add locking around cache operations
        """
        cache = AsyncCache.get_instance()
        
        async def update_cache(value):
            # Read-modify-write without lock
            current = await cache.get("counter") or 0
            await asyncio.sleep(0.001)  # Simulate processing
            await cache.set("counter", current + value)
        
        # Run concurrent updates
        await asyncio.gather(*[update_cache(1) for _ in range(10)])
        
        final_value = await cache.get("counter")
        # FLAKY: Without proper locking, updates are lost
        assert final_value == 10, f"Expected 10, got {final_value}"
    
    @pytest.mark.asyncio
    async def test_cache_get_or_set_thundering_herd(self):
        """
        FLAKY: Multiple tasks call factory function simultaneously
        FIX: Add locking in get_or_set to prevent thundering herd
        """
        cache = AsyncCache.get_instance()
        call_count = {"value": 0}
        
        async def expensive_factory():
            call_count["value"] += 1  # FLAKY: race condition
            await asyncio.sleep(0.05)
            return "computed_value"
        
        # Launch concurrent requests for same key
        results = await asyncio.gather(*[
            cache.get_or_set("expensive_key", expensive_factory)
            for _ in range(5)
        ])
        
        # All should get same value
        assert all(r == "computed_value" for r in results)
        # FLAKY: Factory should only be called once with proper locking
        assert call_count["value"] == 1, f"Factory called {call_count['value']} times"
    
    @pytest.mark.asyncio
    async def test_concurrent_cache_delete_and_read(self):
        """
        FLAKY: Delete during read causes inconsistent state
        FIX: Use atomic operations or proper synchronization
        """
        cache = AsyncCache.get_instance()
        await cache.set("target", "initial_value")
        
        errors = []
        
        async def reader():
            for _ in range(10):
                try:
                    value = await cache.get("target")
                    if value is not None and value != "initial_value":
                        errors.append(f"Unexpected value: {value}")
                except Exception as e:
                    errors.append(str(e))
                await asyncio.sleep(0.001)
        
        async def deleter():
            await asyncio.sleep(0.005)
            await cache.delete("target")
        
        await asyncio.gather(reader(), deleter())
        
        # FLAKY: Race between read and delete can cause errors
        assert len(errors) == 0, f"Race condition errors: {errors}"


class TestQueueRaces:
    """Race conditions in async queue operations"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        AsyncQueue.clear_instances()
        yield
        AsyncQueue.clear_instances()
    
    @pytest.mark.asyncio
    async def test_concurrent_queue_operations(self):
        """
        FLAKY: Concurrent put/get causes message count mismatch
        FIX: Ensure atomic operations in queue
        """
        queue = AsyncQueue("test_queue", max_size=100)
        
        produced = []
        consumed = []
        
        async def producer():
            for i in range(10):
                msg = Message(payload=f"message_{i}")
                await queue.put(msg)
                produced.append(msg.id)
                await asyncio.sleep(0.001)
        
        async def consumer():
            for _ in range(10):
                msg = await queue.get(timeout=1.0)
                if msg:
                    consumed.append(msg.id)
                    await queue.ack(msg.id)
                await asyncio.sleep(0.001)
        
        await asyncio.gather(producer(), consumer())
        
        # FLAKY: Race between produce and consume
        assert len(produced) == 10, f"Produced {len(produced)} messages"
        assert len(consumed) == 10, f"Consumed {len(consumed)} messages"
    
    @pytest.mark.asyncio
    async def test_message_acknowledgment_race(self):
        """
        FLAKY: Double ack or ack after timeout causes errors
        FIX: Track acknowledgment state properly
        """
        queue = AsyncQueue("ack_test", max_size=10)
        msg = Message(payload="test")
        await queue.put(msg)
        
        received = await queue.get(timeout=1.0)
        
        # First ack should succeed
        result1 = await queue.ack(received.id)
        # Second ack should fail gracefully
        result2 = await queue.ack(received.id)
        
        # FLAKY: Second ack behavior is undefined
        assert result1 == True
        assert result2 == False, "Double ack should return False"


class TestEventBusRaces:
    """Race conditions in event bus operations"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        EventBus.reset_instance()
        yield
        EventBus.reset_instance()
    
    @pytest.mark.asyncio
    async def test_concurrent_event_publishing(self):
        """
        FLAKY: Concurrent event publishing causes lost events
        FIX: Use thread-safe counter in event bus
        """
        bus = EventBus.get_instance()
        received_events = {"count": 0}
        
        async def handler(event):
            received_events["count"] += 1  # FLAKY: race condition
        
        bus.subscribe(EventType.USER_CREATED, handler)
        
        # Publish events concurrently
        events = [
            Event(type=EventType.USER_CREATED, payload={"id": i})
            for i in range(20)
        ]
        
        await asyncio.gather(*[bus.publish(e) for e in events])
        
        # FLAKY: Counter increment isn't atomic
        assert received_events["count"] == 20, f"Received {received_events['count']} events"
    
    @pytest.mark.asyncio
    async def test_subscribe_unsubscribe_race(self):
        """
        FLAKY: Subscribe/unsubscribe during event delivery causes errors
        FIX: Use copy of subscriber list during delivery
        """
        bus = EventBus.get_instance()
        subscription_id = None
        
        async def handler(event):
            # Unsubscribe during handling
            if subscription_id:
                bus.unsubscribe(subscription_id)
        
        subscription_id = bus.subscribe(EventType.USER_CREATED, handler)
        
        event = Event(type=EventType.USER_CREATED)
        
        # Should not raise even if handler unsubscribes
        count = await bus.publish(event)
        
        # FLAKY: Unsubscribe during delivery may cause issues
        assert count >= 0  # Should complete without error


class TestApiHandlerRaces:
    """Race conditions in API handler"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        ApiHandler.reset_instance()
        RateLimiter.clear_all()
        yield
        ApiHandler.reset_instance()
    
    @pytest.mark.asyncio
    async def test_concurrent_request_counting(self):
        """
        FLAKY: Concurrent requests cause incorrect count
        FIX: Use atomic counter for request counting
        """
        handler = ApiHandler.get_instance()
        handler.initialize()
        handler.register_route("/test", HttpMethod.GET, lambda r: {"ok": True})
        
        initial_count = handler.request_count
        
        # Send concurrent requests
        requests = [
            Request(method=HttpMethod.GET, path="/test")
            for _ in range(20)
        ]
        
        await asyncio.gather(*[handler.handle(r) for r in requests])
        
        final_count = handler.request_count
        
        # FLAKY: _request_count += 1 is not atomic
        assert final_count == initial_count + 20, \
            f"Expected {initial_count + 20}, got {final_count}"
    
    @pytest.mark.asyncio
    async def test_rate_limiter_under_load(self):
        """
        FLAKY: Rate limiter token count becomes inconsistent under load
        FIX: Use atomic operations for token management
        """
        limiter = RateLimiter(rate=100.0, burst=10)
        
        results = []
        
        async def try_acquire():
            result = await limiter.acquire()
            results.append(result)
        
        # Concurrent acquisition attempts
        await asyncio.gather(*[try_acquire() for _ in range(20)])
        
        successes = sum(1 for r in results if r)
        
        # FLAKY: Should be exactly burst size (10) but race conditions may vary
        assert successes == 10, f"Expected 10 successes, got {successes}"


class TestWebSocketRaces:
    """Race conditions in WebSocket handling"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        WebSocketHandler.clear_connections()
        yield
        WebSocketHandler.clear_connections()
    
    @pytest.mark.asyncio
    async def test_concurrent_message_counting(self):
        """
        FLAKY: Concurrent sends cause incorrect message count
        FIX: Use atomic counter for message tracking
        """
        ws = WebSocketHandler("test_conn")
        await ws.connect()
        
        initial_count = WebSocketHandler._message_count
        
        # Send messages concurrently
        results = await asyncio.gather(*[
            ws.send({"msg": i}) for i in range(10)
        ])
        
        # Check all sends succeeded
        assert all(results), "Some sends failed"
        
        # FLAKY: _message_count += 1 is not atomic
        expected = initial_count + 10
        assert WebSocketHandler._message_count == expected, \
            f"Expected {expected}, got {WebSocketHandler._message_count}"
    
    @pytest.mark.asyncio
    async def test_connect_disconnect_race(self):
        """
        FLAKY: Concurrent connect/disconnect causes inconsistent state
        FIX: Use proper state machine for connection lifecycle
        """
        ws = WebSocketHandler("race_conn")
        
        async def connect_then_send():
            await ws.connect()
            return await ws.send({"test": True})
        
        async def disconnect():
            await asyncio.sleep(0.001)
            await ws.disconnect()
        
        # Race between send and disconnect
        results = await asyncio.gather(
            connect_then_send(),
            disconnect(),
            return_exceptions=True
        )
        
        # FLAKY: Result depends on timing
        assert ws.is_connected == False  # Should end disconnected


class TestDatabaseRaces:
    """Race conditions in database operations"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        Connection.reset_count()
        ConnectionPool.clear_pools()
        yield
        ConnectionPool.clear_pools()
    
    @pytest.mark.asyncio
    async def test_connection_count_accuracy(self):
        """
        FLAKY: Concurrent connection creation causes incorrect count
        FIX: Use atomic counter for connection tracking
        """
        pool = ConnectionPool("test", min_connections=0, max_connections=20)
        await pool.initialize()
        
        initial = Connection.get_connection_count()
        
        async def acquire_and_release():
            conn = await pool.acquire()
            await asyncio.sleep(0.01)
            await conn.release()
        
        # Concurrent connection acquisitions
        await asyncio.gather(*[acquire_and_release() for _ in range(10)])
        
        # FLAKY: Connection._connection_count += 1 is not atomic
        created = Connection.get_connection_count() - initial
        assert created <= 10, f"Created more connections than expected: {created}"


class TestThreadSafetyRaces:
    """Race conditions in thread-based code"""
    
    def test_shared_counter_increment(self):
        """
        FLAKY: Non-atomic counter increment in threads
        FIX: Use threading.Lock or atomic counter
        """
        counter = {"value": 0}
        
        def increment():
            for _ in range(100):
                # FLAKY: Read-modify-write race
                current = counter["value"]
                time.sleep(0.0001)  # Increase chance of race
                counter["value"] = current + 1
        
        threads = [threading.Thread(target=increment) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # FLAKY: Without locking, some increments are lost
        assert counter["value"] == 500, f"Expected 500, got {counter['value']}"
    
    def test_lru_cache_concurrent_access(self):
        """
        FLAKY: LRU cache under concurrent access loses items
        FIX: Cache already has lock, but test exposes timing issues
        """
        cache = LRUCache(max_size=50)
        errors = []
        
        def writer():
            for i in range(100):
                cache.set(f"key_{i}", f"value_{i}")
                time.sleep(0.0001)
        
        def reader():
            for i in range(100):
                value = cache.get(f"key_{i % 50}")
                # FLAKY: Key may not exist yet or may have been evicted
                if value is None and i > 50:
                    errors.append(f"key_{i % 50} unexpectedly None")
        
        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # FLAKY: Reader may see inconsistent state
        assert len(errors) == 0, f"Errors: {errors[:5]}"
