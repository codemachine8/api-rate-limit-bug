"""
Test resource cleanup issues
Category: resource_cleanup (Expected fix rate: 60-70%)

These tests fail due to:
- Unclosed connections/files
- Resources not released in finally blocks
- Missing cleanup in test teardown
- Context managers not properly used

Imports from: L5 (app), L4 (integrations), L3 (handlers), L2 (services), L1 (core)
"""
import pytest
import asyncio
import threading
import time
import tempfile
import os

# Level 1 imports
from src.core.base import BaseComponent, EventEmitter

# Level 2 imports
from src.services.cache import DistributedCacheSimulator, LRUCache
from src.services.queue import SimpleQueue, Message, MessageProcessor

# Level 3 imports
from src.handlers.api_handler import WebSocketHandler
from src.handlers.event_handler import EventProcessor, AsyncQueue

# Level 4 imports
from src.integrations.database_client import Connection, ConnectionPool, UnitOfWork

# Level 5 imports
from src.app.main import Worker


class TestConnectionCleanup:
    """Tests with database connection cleanup issues"""
    
    @pytest.mark.asyncio
    async def test_connection_not_released(self):
        """
        FLAKY: Connection acquired but not released
        FIX: Use try/finally or context manager
        """
        pool = ConnectionPool("cleanup_test", min_connections=2, max_connections=3)
        await pool.initialize()
        
        # Acquire connections without releasing
        conn1 = await pool.acquire()
        conn2 = await pool.acquire()
        
        # Third acquire should work with proper cleanup
        # FLAKY: Will timeout if previous connections not released
        try:
            conn3 = await asyncio.wait_for(pool.acquire(), timeout=0.5)
            await conn3.release()
        except asyncio.TimeoutError:
            pytest.fail("Connection pool exhausted - previous connections not released")
        
        # Cleanup (but damage is done for other tests)
        await conn1.release()
        await conn2.release()
    
    @pytest.mark.asyncio
    async def test_connection_leak_in_exception(self):
        """
        FLAKY: Connection not released when exception occurs
        FIX: Use try/finally to ensure release
        """
        pool = ConnectionPool("leak_test", min_connections=1, max_connections=2)
        await pool.initialize()
        
        initial_available = pool.available_count
        
        # This simulates code that doesn't handle exceptions properly
        conn = await pool.acquire()
        try:
            # Simulate error during operation
            raise ValueError("Simulated error")
        except ValueError:
            pass  # Exception caught but connection not released!
        
        # FLAKY: Connection leaked
        final_available = pool.available_count
        assert final_available == initial_available, \
            f"Connection leaked: {initial_available} -> {final_available}"
    
    @pytest.mark.asyncio
    async def test_transaction_not_rolled_back(self):
        """
        FLAKY: Transaction started but not committed/rolled back
        FIX: Use context manager or ensure rollback in finally
        """
        pool = ConnectionPool("tx_test", min_connections=1, max_connections=2)
        await pool.initialize()
        
        conn = await pool.acquire()
        await conn.begin_transaction()
        
        # Simulate operation that fails
        try:
            # Do some work...
            raise RuntimeError("Operation failed")
        except RuntimeError:
            pass  # Transaction still open!
        
        # FLAKY: Transaction still open, connection in bad state
        assert not conn._in_transaction, "Transaction not cleaned up"
        
        await conn.release()


class TestFileHandleCleanup:
    """Tests with file handle cleanup issues"""
    
    def test_file_not_closed(self):
        """
        FLAKY: File opened but not closed
        FIX: Use 'with' statement or close in finally
        """
        temp_path = tempfile.mktemp()
        
        # Bad practice: file not closed
        f = open(temp_path, 'w')
        f.write("test data")
        # f.close() missing!
        
        # FLAKY: File might still be locked
        try:
            with open(temp_path, 'r') as f2:
                content = f2.read()
            assert content == "test data"
        finally:
            f.close()  # Cleanup
            os.unlink(temp_path)
    
    def test_tempfile_not_cleaned(self):
        """
        FLAKY: Temp file created but not removed
        FIX: Use tempfile context manager or cleanup in finally
        """
        temp_files = []
        
        # Create temp files without cleanup
        for i in range(5):
            path = tempfile.mktemp(suffix=f"_test_{i}.txt")
            with open(path, 'w') as f:
                f.write(f"content {i}")
            temp_files.append(path)
        
        # FLAKY: Temp files left behind
        try:
            for path in temp_files:
                assert os.path.exists(path), f"Temp file missing: {path}"
        finally:
            # Cleanup
            for path in temp_files:
                if os.path.exists(path):
                    os.unlink(path)
    
    def test_directory_not_cleaned(self):
        """
        FLAKY: Temp directory created but not removed
        FIX: Use tempfile.TemporaryDirectory context manager
        """
        temp_dir = tempfile.mkdtemp(prefix="flaky_test_")
        
        # Create files in temp dir
        for i in range(3):
            path = os.path.join(temp_dir, f"file_{i}.txt")
            with open(path, 'w') as f:
                f.write(f"content {i}")
        
        # FLAKY: Directory and files left behind
        try:
            files = os.listdir(temp_dir)
            assert len(files) == 3
        finally:
            # Cleanup
            import shutil
            shutil.rmtree(temp_dir)


class TestThreadCleanup:
    """Tests with thread cleanup issues"""
    
    def test_thread_not_joined(self):
        """
        FLAKY: Thread started but not joined
        FIX: Always join threads or use daemon=True
        """
        result = {"value": None}
        
        def worker():
            time.sleep(0.1)
            result["value"] = "completed"
        
        thread = threading.Thread(target=worker)
        thread.start()
        # thread.join() missing!
        
        # FLAKY: Thread might not have completed
        time.sleep(0.05)  # Race condition
        assert result["value"] == "completed", \
            f"Expected 'completed', got {result['value']}"
        
        thread.join()  # Cleanup
    
    def test_daemon_thread_not_tracked(self):
        """
        FLAKY: Daemon thread might be killed before completion
        FIX: Track and wait for important work
        """
        results = []
        
        def background_task():
            for i in range(5):
                time.sleep(0.02)
                results.append(i)
        
        thread = threading.Thread(target=background_task, daemon=True)
        thread.start()
        
        # Don't wait for completion
        time.sleep(0.05)
        
        # FLAKY: Daemon thread might not have finished
        assert len(results) == 5, f"Expected 5 results, got {len(results)}"
    
    def test_thread_pool_not_shutdown(self):
        """
        FLAKY: Thread pool created but not shut down
        FIX: Use context manager or shutdown in finally
        """
        from concurrent.futures import ThreadPoolExecutor
        
        results = []
        
        executor = ThreadPoolExecutor(max_workers=3)
        
        # Submit work
        futures = [
            executor.submit(lambda x: results.append(x * 2), i)
            for i in range(5)
        ]
        
        # FLAKY: Not waiting for completion
        # executor.shutdown() missing!
        
        try:
            assert len(results) == 5, f"Expected 5 results, got {len(results)}"
        finally:
            executor.shutdown(wait=True)


class TestAsyncResourceCleanup:
    """Tests with async resource cleanup issues"""
    
    @pytest.mark.asyncio
    async def test_async_task_not_cancelled(self):
        """
        FLAKY: Background task not cancelled on exit
        FIX: Track and cancel tasks in cleanup
        """
        results = []
        
        async def background_worker():
            for i in range(10):
                await asyncio.sleep(0.02)
                results.append(i)
        
        task = asyncio.create_task(background_worker())
        
        # Don't wait for completion
        await asyncio.sleep(0.05)
        
        # FLAKY: Task still running
        try:
            assert len(results) == 10, f"Expected 10, got {len(results)}"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    
    @pytest.mark.asyncio
    async def test_websocket_not_closed(self):
        """
        FLAKY: WebSocket connection not properly closed
        FIX: Use try/finally or async context manager
        """
        WebSocketHandler.clear_connections()
        
        ws = WebSocketHandler("cleanup_test_ws")
        await ws.connect()
        
        # Do some work
        await ws.send({"type": "test"})
        
        # FLAKY: Connection not closed
        # await ws.disconnect() missing!
        
        try:
            connections = WebSocketHandler.get_all_connections()
            assert len(connections) == 0, "WebSocket not cleaned up"
        finally:
            await ws.disconnect()
    
    @pytest.mark.asyncio
    async def test_event_processor_not_stopped(self):
        """
        FLAKY: Event processor not stopped
        FIX: Ensure stop() called in finally
        """
        EventProcessor.clear_processors()
        
        queue = AsyncQueue("processor_test", max_size=100)
        processor = EventProcessor("test_processor", queue)
        
        await processor.start()
        
        # Do some work
        await asyncio.sleep(0.1)
        
        # FLAKY: Processor still running
        # await processor.stop() missing!
        
        try:
            assert not processor._running, "Processor not stopped"
        finally:
            await processor.stop()


class TestCacheResourceCleanup:
    """Tests with cache resource cleanup issues"""
    
    def test_distributed_cache_not_disconnected(self):
        """
        FLAKY: Distributed cache connection not closed
        FIX: Call disconnect in cleanup
        """
        cache = DistributedCacheSimulator()
        cache.connect()
        
        # Use cache
        cache.set("key", "value")
        _ = cache.get("key")
        
        # FLAKY: Connection still open
        # cache.disconnect() missing!
        
        try:
            assert not cache._connected, "Cache not disconnected"
        finally:
            cache.disconnect()
    
    def test_cache_not_cleared_between_tests(self):
        """
        FLAKY: Cache data persists to next test
        FIX: Clear cache in teardown
        """
        cache = LRUCache(max_size=100)
        
        # Populate cache
        for i in range(50):
            cache.set(f"persistent_key_{i}", f"value_{i}")
        
        # FLAKY: No cleanup - data persists
        assert cache.size == 50
        
        # Other tests might see this data
        cache.clear()  # Explicit cleanup


class TestQueueResourceCleanup:
    """Tests with queue resource cleanup issues"""
    
    def test_message_processor_not_stopped(self):
        """
        FLAKY: Message processor thread not stopped
        FIX: Call stop() in cleanup
        """
        queue = SimpleQueue(max_size=100)
        processor = MessageProcessor("cleanup_test_processor", queue)
        
        # Add handler
        processor.register_handler("test", lambda x: None)
        processor.start()
        
        # Do some work
        queue.put(Message(payload={"type": "test"}))
        time.sleep(0.1)
        
        # FLAKY: Processor thread still running
        # processor.stop() missing!
        
        try:
            assert not processor._running, "Processor not stopped"
        finally:
            processor.stop()
    
    def test_queue_not_drained(self):
        """
        FLAKY: Queue has pending messages at end
        FIX: Drain queue in cleanup
        """
        queue = SimpleQueue(max_size=100)
        
        # Add messages
        for i in range(10):
            queue.put(Message(payload=f"message_{i}"))
        
        # Process some but not all
        for _ in range(5):
            queue.get(timeout=0.1)
            queue.task_done()
        
        # FLAKY: 5 messages still pending
        try:
            assert queue.is_empty, f"Queue not empty: {queue.size} items remaining"
        finally:
            queue.clear()


class TestWorkerCleanup:
    """Tests with worker cleanup issues - uses Level 5 imports"""
    
    def test_worker_not_stopped(self):
        """
        FLAKY: Worker thread not stopped
        FIX: Call stop() in cleanup
        """
        Worker.clear_workers()
        
        queue = SimpleQueue(max_size=100)
        worker = Worker("cleanup_test_worker", queue)
        
        worker.start()
        
        # Add some work
        for i in range(5):
            queue.put(Message(payload=f"task_{i}"))
        
        time.sleep(0.2)  # Let worker process
        
        # FLAKY: Worker still running
        # worker.stop() missing!
        
        try:
            assert not worker._running, "Worker not stopped"
        finally:
            worker.stop()
    
    def test_worker_leaves_queue_items(self):
        """
        FLAKY: Worker stopped but queue not drained
        FIX: Drain queue before stopping
        """
        Worker.clear_workers()
        
        queue = SimpleQueue(max_size=100)
        worker = Worker("drain_test_worker", queue)
        
        # Add many items
        for i in range(20):
            queue.put(Message(payload=f"task_{i}"))
        
        worker.start()
        time.sleep(0.1)  # Process some
        worker.stop()
        
        # FLAKY: Queue might have remaining items
        remaining = queue.size
        try:
            assert remaining == 0, f"Queue has {remaining} remaining items"
        finally:
            queue.clear()


class TestUnitOfWorkCleanup:
    """Tests with unit of work cleanup issues"""
    
    @pytest.mark.asyncio
    async def test_unit_of_work_not_committed(self):
        """
        FLAKY: UoW started but not committed/rolled back
        FIX: Use async context manager
        """
        UnitOfWork.clear_active()
        
        pool = ConnectionPool("uow_test", min_connections=1, max_connections=2)
        await pool.initialize()
        
        uow = UnitOfWork(pool)
        await uow.begin()
        
        # Do work
        await uow.execute("SELECT 1")
        
        # FLAKY: UoW not completed
        # await uow.commit() or await uow.rollback() missing!
        
        try:
            assert UnitOfWork.get_active_count() == 0, \
                f"Active UoW count: {UnitOfWork.get_active_count()}"
        finally:
            await uow.rollback()
    
    @pytest.mark.asyncio
    async def test_active_uow_count(self):
        """
        FLAKY: Previous tests left active UoWs
        FIX: Clear active UoWs in setup
        """
        # FLAKY: Count polluted by previous tests
        count = UnitOfWork.get_active_count()
        assert count == 0, f"Expected 0 active UoWs, got {count}"


class TestEventEmitterCleanup:
    """Tests with event emitter cleanup issues"""
    
    def test_listeners_not_removed(self):
        """
        FLAKY: Event listeners not removed
        FIX: Clear listeners in cleanup
        """
        emitter = EventEmitter()
        
        received = []
        
        def handler(data):
            received.append(data)
        
        # Add listener
        emitter.on("test_event", handler)
        
        # Use it
        emitter.emit("test_event", "data1")
        
        # FLAKY: Listener still attached
        # emitter.off("test_event", handler) or emitter.clear() missing!
        
        try:
            # Check listener was removed
            assert len(emitter._listeners.get("test_event", [])) == 0, \
                "Listeners not cleaned up"
        finally:
            emitter.clear()
    
    def test_emitter_state_after_clear(self):
        """
        FLAKY: Emitter has lingering listeners from previous tests
        FIX: Clear in setup
        """
        emitter = EventEmitter()
        
        # FLAKY: Might have listeners from other tests if shared
        listener_count = len(emitter._listeners)
        assert listener_count == 0, \
            f"Expected 0 listeners, got {listener_count}"


class TestBaseComponentCleanup:
    """Tests with BaseComponent cleanup issues"""
    
    def test_component_not_shutdown(self):
        """
        FLAKY: Component initialized but not shut down
        FIX: Call shutdown() in cleanup
        """
        BaseComponent.clear_registry()
        
        component = BaseComponent("cleanup_test_component")
        component.initialize()
        
        assert component._initialized
        
        # FLAKY: Component still initialized
        # component.shutdown() missing!
        
        try:
            assert not component._initialized, "Component not shut down"
        finally:
            component.shutdown()
    
    def test_component_registry_cleanup(self):
        """
        FLAKY: Components left in registry
        FIX: Clear registry in cleanup
        """
        # Create several components
        for i in range(5):
            comp = BaseComponent(f"registry_test_{i}")
        
        # FLAKY: Components in registry
        try:
            count = BaseComponent.get_instance_count()
            assert count == 0, f"Registry has {count} components"
        finally:
            BaseComponent.clear_registry()
