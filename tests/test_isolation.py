"""
Test isolation issues
Category: test_isolation (Expected fix rate: 40-50%)

These tests fail due to:
- Tests that depend on other tests running first
- Shared fixtures with side effects
- Test order sensitivity
- Module-level setup not properly isolated

Imports from: L5 (app), L4 (integrations), L3 (handlers), L2 (services), L1 (core)
"""
import pytest
import asyncio
import time
import os

# Level 1 imports
from src.core.base import BaseComponent, CircuitBreaker, RetryPolicy
from src.core.config import Config, Environment

# Level 2 imports
from src.services.cache import LRUCache, AsyncCache, CacheManager
from src.services.queue import SimpleQueue, Message, QueueManager

# Level 3 imports
from src.handlers.api_handler import ApiHandler, Request, HttpMethod, Response
from src.handlers.event_handler import EventBus, Event, EventType

# Level 4 imports
from src.integrations.database_client import Repository, ConnectionPool, DatabaseClient

# Level 5 imports  
from src.app.main import Application, AppConfig, create_app


# Module-level state that causes isolation issues
_module_counter = 0
_module_initialized = False
_module_data = {}


def module_setup():
    """Module setup that should run once but affects all tests"""
    global _module_initialized, _module_counter
    _module_initialized = True
    _module_counter += 1


class TestOrderDependentSetup:
    """Tests that depend on execution order for setup"""
    
    def test_a_initialize_system(self):
        """
        First test - initializes system state
        Other tests depend on this running first
        """
        global _module_data
        _module_data["initialized"] = True
        _module_data["timestamp"] = time.time()
        _module_data["items"] = ["item1", "item2"]
        
        assert _module_data["initialized"]
    
    def test_b_requires_initialization(self):
        """
        FLAKY: Depends on test_a_initialize_system running first
        FIX: Each test should initialize its own state
        """
        # FLAKY: Will fail if test_a didn't run or ran after this
        assert _module_data.get("initialized") == True, \
            "System not initialized - test_a must run first"
        assert "timestamp" in _module_data
    
    def test_c_uses_initialized_items(self):
        """
        FLAKY: Depends on items being set by test_a
        FIX: Initialize items independently
        """
        # FLAKY: Assumes test_a populated this
        items = _module_data.get("items", [])
        assert len(items) == 2, f"Expected 2 items, got {len(items)}"
    
    def test_z_cleanup(self):
        """Cleanup test that other tests might depend on NOT running"""
        global _module_data
        _module_data.clear()


class TestDependentApiSetup:
    """Tests with API setup dependencies"""
    
    _shared_handler = None
    _routes_registered = False
    
    def test_01_setup_api_handler(self):
        """First test sets up shared handler"""
        TestDependentApiSetup._shared_handler = ApiHandler.get_instance()
        TestDependentApiSetup._shared_handler.initialize()
        
        # Register routes
        TestDependentApiSetup._shared_handler.register_route(
            "/users", HttpMethod.GET, lambda r: {"users": []}
        )
        TestDependentApiSetup._routes_registered = True
        
        assert TestDependentApiSetup._shared_handler is not None
    
    def test_02_uses_registered_route(self):
        """
        FLAKY: Depends on test_01 registering routes
        FIX: Register routes in each test or use fixture
        """
        handler = TestDependentApiSetup._shared_handler
        
        # FLAKY: Handler might be None if test_01 didn't run
        assert handler is not None, "Handler not initialized"
        assert TestDependentApiSetup._routes_registered, "Routes not registered"
        
        request = Request(method=HttpMethod.GET, path="/users")
        response = asyncio.run(handler.handle(request))
        
        assert response.status_code == 200
    
    def test_03_checks_route_count(self):
        """
        FLAKY: Depends on routes being registered by previous tests
        FIX: Independent route setup
        """
        handler = TestDependentApiSetup._shared_handler
        
        # FLAKY: Route count depends on test_01
        route_count = len(handler._routes) if handler else 0
        assert route_count > 0, "No routes registered"


class TestDatabaseSetupDependency:
    """Tests with database setup dependencies"""
    
    _pool = None
    _repository = None
    
    @pytest.mark.asyncio
    async def test_01_initialize_database(self):
        """First test initializes database connection"""
        TestDatabaseSetupDependency._pool = ConnectionPool(
            "test_iso_pool", 
            min_connections=2, 
            max_connections=5
        )
        await TestDatabaseSetupDependency._pool.initialize()
        
        assert TestDatabaseSetupDependency._pool.available_count >= 2
    
    @pytest.mark.asyncio
    async def test_02_create_repository(self):
        """
        FLAKY: Depends on test_01 creating pool
        FIX: Create pool in fixture or each test
        """
        pool = TestDatabaseSetupDependency._pool
        
        # FLAKY: Pool might not exist
        assert pool is not None, "Database pool not initialized"
        
        TestDatabaseSetupDependency._repository = Repository("users", pool)
        assert TestDatabaseSetupDependency._repository is not None
    
    @pytest.mark.asyncio
    async def test_03_use_repository(self):
        """
        FLAKY: Depends on both test_01 and test_02
        FIX: Independent setup
        """
        repo = TestDatabaseSetupDependency._repository
        
        # FLAKY: Repository might not exist
        assert repo is not None, "Repository not created"
        
        # Would fail if pool wasn't initialized
        result = await repo.find_by_id(1)


class TestCacheWarmupDependency:
    """Tests that depend on cache being warmed up"""
    
    _cache = None
    _warmed_up = False
    
    def test_01_warmup_cache(self):
        """Warms up cache with test data"""
        TestCacheWarmupDependency._cache = LRUCache(max_size=100)
        
        for i in range(50):
            TestCacheWarmupDependency._cache.set(f"key_{i}", f"value_{i}")
        
        TestCacheWarmupDependency._warmed_up = True
        assert TestCacheWarmupDependency._cache.size == 50
    
    def test_02_cache_hit_rate(self):
        """
        FLAKY: Depends on cache being warmed up
        FIX: Warm up cache in each test
        """
        cache = TestCacheWarmupDependency._cache
        
        # FLAKY: Cache might not be warmed up
        assert TestCacheWarmupDependency._warmed_up, "Cache not warmed up"
        
        hits = 0
        for i in range(50):
            if cache.get(f"key_{i}") is not None:
                hits += 1
        
        hit_rate = hits / 50
        assert hit_rate > 0.9, f"Low hit rate: {hit_rate}"
    
    def test_03_expects_specific_cache_size(self):
        """
        FLAKY: Expects exact cache state from warmup
        FIX: Set up cache state independently
        """
        cache = TestCacheWarmupDependency._cache
        
        # FLAKY: Size depends on test_01
        assert cache is not None, "Cache not initialized"
        assert cache.size == 50, f"Expected 50 items, got {cache.size}"


class TestEventSubscriptionOrder:
    """Tests dependent on event subscription order"""
    
    _received_events = []
    
    def test_01_subscribe_handlers(self):
        """Sets up event subscriptions"""
        bus = EventBus.get_instance()
        bus.clear()  # Start fresh
        
        TestEventSubscriptionOrder._received_events = []
        
        def handler(event):
            TestEventSubscriptionOrder._received_events.append(event)
        
        bus.subscribe(EventType.USER_CREATED, handler)
        
        assert bus.total_subscriptions == 1
    
    def test_02_publish_events(self):
        """
        FLAKY: Depends on subscriptions from test_01
        FIX: Subscribe in each test
        """
        bus = EventBus.get_instance()
        
        # FLAKY: Handler might not be subscribed
        event = Event(type=EventType.USER_CREATED, payload={"id": 1})
        count = asyncio.run(bus.publish(event))
        
        assert count == 1, f"Expected 1 handler, got {count}"
    
    def test_03_check_received_events(self):
        """
        FLAKY: Depends on test_02 publishing events
        FIX: Publish events in same test that checks them
        """
        # FLAKY: Events list depends on test_02
        events = TestEventSubscriptionOrder._received_events
        
        assert len(events) > 0, "No events received"


class TestQueueProcessingOrder:
    """Tests dependent on queue processing order"""
    
    _queue = None
    _processed = []
    
    def test_01_setup_queue(self):
        """Sets up queue with messages"""
        TestQueueProcessingOrder._queue = SimpleQueue(max_size=100)
        TestQueueProcessingOrder._processed = []
        
        for i in range(5):
            msg = Message(payload=f"message_{i}")
            TestQueueProcessingOrder._queue.put(msg)
        
        assert TestQueueProcessingOrder._queue.size == 5
    
    def test_02_process_messages(self):
        """
        FLAKY: Depends on test_01 setting up queue
        FIX: Setup queue independently
        """
        queue = TestQueueProcessingOrder._queue
        
        # FLAKY: Queue might not exist
        assert queue is not None, "Queue not initialized"
        
        while not queue.is_empty:
            msg = queue.get(timeout=0.1)
            if msg:
                TestQueueProcessingOrder._processed.append(msg.payload)
                queue.task_done()
    
    def test_03_verify_processing(self):
        """
        FLAKY: Depends on test_02 processing messages
        FIX: Process and verify in same test
        """
        processed = TestQueueProcessingOrder._processed
        
        # FLAKY: List depends on test_02
        assert len(processed) == 5, f"Expected 5 processed, got {len(processed)}"


class TestApplicationLifecycle:
    """Tests dependent on application lifecycle"""
    
    _app = None
    _initialized = False
    
    @pytest.mark.asyncio
    async def test_01_create_application(self):
        """Creates and initializes application"""
        config = AppConfig(name="TestApp", debug=True)
        TestApplicationLifecycle._app = create_app(config)
        
        # Don't actually initialize (would require more setup)
        TestApplicationLifecycle._initialized = True
        
        assert TestApplicationLifecycle._app is not None
    
    @pytest.mark.asyncio
    async def test_02_application_config(self):
        """
        FLAKY: Depends on test_01 creating app
        FIX: Create app in each test
        """
        app = TestApplicationLifecycle._app
        
        # FLAKY: App might not exist
        assert app is not None, "Application not created"
        assert app.config.name == "TestApp"
    
    @pytest.mark.asyncio
    async def test_03_application_state(self):
        """
        FLAKY: Depends on application being created
        FIX: Independent app creation
        """
        # FLAKY: Checks state set by test_01
        assert TestApplicationLifecycle._initialized, \
            "Application not initialized"


class TestCircuitBreakerState:
    """Tests dependent on circuit breaker state"""
    
    _breaker = None
    
    def test_01_setup_circuit_breaker(self):
        """Sets up circuit breaker in closed state"""
        TestCircuitBreakerState._breaker = CircuitBreaker(
            failure_threshold=3,
            recovery_timeout=1.0
        )
        
        assert TestCircuitBreakerState._breaker.state == CircuitBreaker.CLOSED
    
    def test_02_trip_circuit_breaker(self):
        """
        FLAKY: Depends on test_01 creating breaker
        FIX: Create breaker in each test
        """
        breaker = TestCircuitBreakerState._breaker
        
        # FLAKY: Breaker might not exist
        assert breaker is not None, "Circuit breaker not created"
        
        # Trip the breaker
        for _ in range(3):
            breaker.record_failure()
        
        assert breaker.state == CircuitBreaker.OPEN
    
    def test_03_check_breaker_open(self):
        """
        FLAKY: Depends on test_02 tripping breaker
        FIX: Set up breaker state independently
        """
        breaker = TestCircuitBreakerState._breaker
        
        # FLAKY: State depends on test_02
        assert breaker is not None
        assert breaker.state == CircuitBreaker.OPEN, \
            f"Expected OPEN, got {breaker.state}"


class TestFileSystemDependency:
    """Tests with file system dependencies"""
    
    _test_file = "/tmp/flaky_test_isolation.txt"
    
    def test_01_create_test_file(self):
        """Creates test file"""
        with open(self._test_file, "w") as f:
            f.write("test data\n")
            f.write("line 2\n")
        
        assert os.path.exists(self._test_file)
    
    def test_02_read_test_file(self):
        """
        FLAKY: Depends on test_01 creating file
        FIX: Create file in each test or fixture
        """
        # FLAKY: File might not exist
        assert os.path.exists(self._test_file), \
            "Test file not created"
        
        with open(self._test_file) as f:
            lines = f.readlines()
        
        assert len(lines) == 2
    
    def test_03_file_content(self):
        """
        FLAKY: Depends on file existing with specific content
        FIX: Independent file creation
        """
        # FLAKY: File content depends on test_01
        with open(self._test_file) as f:
            content = f.read()
        
        assert "test data" in content
    
    def test_99_cleanup_file(self):
        """Cleanup that might affect other tests if it runs first"""
        if os.path.exists(self._test_file):
            os.remove(self._test_file)


class TestModuleCounterIsolation:
    """Tests affected by module-level counter"""
    
    def test_counter_at_zero(self):
        """
        FLAKY: Module counter might have been incremented
        FIX: Reset counter or don't use module-level state
        """
        global _module_counter
        
        # FLAKY: Counter might be > 0
        assert _module_counter == 0, \
            f"Counter should be 0, got {_module_counter}"
    
    def test_increment_counter(self):
        """Increments module counter"""
        global _module_counter
        _module_counter += 5
        
        assert _module_counter >= 5
    
    def test_counter_value(self):
        """
        FLAKY: Counter value depends on test order
        FIX: Don't share state between tests
        """
        global _module_counter
        
        # FLAKY: Value depends on which tests ran
        assert _module_counter == 0 or _module_counter == 5
