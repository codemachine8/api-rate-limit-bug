"""
Test state pollution issues
Category: state_pollution (Expected fix rate: 35-45%)

These tests fail due to:
- Class-level variables that persist between tests
- Singleton instances not reset
- Global state modifications
- Module-level caches not cleared

Imports from: L5 (app), L4 (integrations), L3 (handlers), L2 (services), L1 (core)
"""
import pytest
import asyncio
import time

# Level 1 imports
from src.core.base import BaseComponent, EventEmitter
from src.core.config import Config, FeatureFlags, Environment, Secrets

# Level 2 imports
from src.services.cache import CacheManager, AsyncCache, LRUCache
from src.services.queue import QueueManager, DeadLetterQueue, AsyncQueue

# Level 3 imports
from src.handlers.api_handler import ApiHandler, RateLimiter, WebSocketHandler
from src.handlers.event_handler import EventBus, EventStore, EventProcessor, SagaOrchestrator

# Level 4 imports
from src.integrations.external_api import ExternalApiClient, WebhookDispatcher, ThirdPartyIntegration
from src.integrations.database_client import DatabaseClient, ConnectionPool, Connection, UnitOfWork

# Level 5 imports
from src.app.main import Application, HealthCheck, MetricsCollector, Worker


class TestSingletonPollution:
    """Tests affected by singleton state pollution"""
    
    def test_config_starts_fresh(self):
        """
        FLAKY: Config singleton retains values from previous tests
        FIX: Add Config.reset_instance() in fixture
        """
        config = Config.get_instance()
        
        # FLAKY: Previous test may have set this
        assert config.get("custom_setting") is None, \
            "Config should not have custom_setting initially"
        
        config.set("custom_setting", "test_value")
    
    def test_config_has_no_custom_setting(self):
        """
        FLAKY: Depends on test order - previous test set custom_setting
        FIX: Reset singleton between tests
        """
        config = Config.get_instance()
        
        # FLAKY: This will fail if test_config_starts_fresh ran first
        value = config.get("custom_setting")
        assert value is None, f"Expected None, got {value}"
    
    def test_api_handler_request_count_starts_at_zero(self):
        """
        FLAKY: ApiHandler._request_count persists between tests
        FIX: Reset class-level counter in fixture
        """
        handler = ApiHandler.get_instance()
        
        # FLAKY: Count accumulates across tests
        assert handler.request_count == 0, \
            f"Request count should be 0, got {handler.request_count}"
    
    def test_api_handler_makes_requests(self):
        """Sets up state that pollutes other tests"""
        handler = ApiHandler.get_instance()
        handler.initialize()
        
        # This increments the class-level counter
        ApiHandler._request_count = 5
        
        assert handler.request_count == 5


class TestFeatureFlagPollution:
    """Tests affected by feature flag state"""
    
    def test_feature_flag_disabled_by_default(self):
        """
        FLAKY: Feature flags persist from previous tests
        FIX: Call FeatureFlags.clear_all() in fixture
        """
        # FLAKY: Previous test may have enabled this
        assert not FeatureFlags.is_enabled("premium_feature"), \
            "premium_feature should be disabled by default"
    
    def test_enable_feature_flag(self):
        """Enables a feature flag, polluting state"""
        FeatureFlags.enable("premium_feature")
        assert FeatureFlags.is_enabled("premium_feature")
    
    def test_another_feature_flag_check(self):
        """
        FLAKY: Depends on whether test_enable_feature_flag ran
        FIX: Reset feature flags between tests
        """
        # FLAKY: State depends on test order
        enabled = FeatureFlags.is_enabled("premium_feature")
        assert enabled == False, "Feature should be disabled in fresh state"


class TestEnvironmentPollution:
    """Tests affected by environment state"""
    
    def test_environment_defaults_to_development(self):
        """
        FLAKY: Environment._current persists between tests
        FIX: Call Environment.reset() in fixture
        """
        # FLAKY: Previous test may have set environment
        env = Environment.get()
        assert env == Environment.DEVELOPMENT, \
            f"Expected development, got {env}"
    
    def test_set_production_environment(self):
        """Sets environment to production, polluting state"""
        Environment.set(Environment.PRODUCTION)
        assert Environment.is_production()
    
    def test_environment_is_not_production(self):
        """
        FLAKY: Depends on test order
        FIX: Reset environment state
        """
        # FLAKY: Will fail if test_set_production_environment ran first
        assert not Environment.is_production(), \
            "Should not be in production by default"


class TestCacheManagerPollution:
    """Tests affected by cache state pollution"""
    
    def test_cache_manager_empty_initially(self):
        """
        FLAKY: CacheManager._default_manager persists
        FIX: Reset default manager in fixture
        """
        manager = CacheManager.get_default()
        stats = manager.get_stats()
        
        # FLAKY: Previous tests may have added items
        assert stats["local_size"] == 0, \
            f"Cache should be empty, has {stats['local_size']} items"
    
    def test_cache_manager_stores_data(self):
        """Adds data to cache manager"""
        manager = CacheManager.get_default()
        manager.set("pollution_key", "pollution_value")
        
        assert manager.get("pollution_key") == "pollution_value"
    
    def test_cache_manager_fresh_state(self):
        """
        FLAKY: Cache retains data from previous test
        FIX: Clear cache between tests
        """
        manager = CacheManager.get_default()
        
        # FLAKY: This key exists if previous test ran
        value = manager.get("pollution_key")
        assert value is None, f"Expected None, got {value}"


class TestEventBusPollution:
    """Tests affected by event bus state"""
    
    def test_event_bus_has_no_subscriptions(self):
        """
        FLAKY: EventBus subscriptions persist
        FIX: Clear event bus in fixture
        """
        bus = EventBus.get_instance()
        
        # FLAKY: Previous tests may have added subscriptions
        assert bus.total_subscriptions == 0, \
            f"Expected 0 subscriptions, got {bus.total_subscriptions}"
    
    def test_subscribe_to_events(self):
        """Subscribes to events, polluting state"""
        bus = EventBus.get_instance()
        
        from src.handlers.event_handler import EventType
        bus.subscribe(EventType.USER_CREATED, lambda e: None)
        bus.subscribe(EventType.USER_UPDATED, lambda e: None)
        
        assert bus.total_subscriptions == 2
    
    def test_event_bus_subscription_count(self):
        """
        FLAKY: Subscription count depends on previous tests
        FIX: Reset event bus singleton
        """
        bus = EventBus.get_instance()
        
        # FLAKY: Count varies based on test order
        count = bus.total_subscriptions
        assert count == 0, f"Expected 0, got {count}"


class TestEventStorePollution:
    """Tests affected by event store state"""
    
    def test_event_store_empty(self):
        """
        FLAKY: EventStore._events is class-level list
        FIX: Clear event store in fixture
        """
        count = EventStore.count()
        
        # FLAKY: Previous tests may have appended events
        assert count == 0, f"Event store should be empty, has {count} events"
    
    def test_append_events(self):
        """Appends events, polluting state"""
        from src.handlers.event_handler import Event, EventType
        
        for i in range(5):
            EventStore.append(Event(type=EventType.USER_CREATED))
        
        assert EventStore.count() == 5
    
    def test_event_store_count_is_zero(self):
        """
        FLAKY: Event count depends on previous tests
        FIX: Clear class-level list
        """
        # FLAKY: This will fail if test_append_events ran
        assert EventStore.count() == 0


class TestDeadLetterQueuePollution:
    """Tests affected by DLQ state"""
    
    def test_dlq_empty_initially(self):
        """
        FLAKY: DeadLetterQueue._global_dlq persists
        FIX: Reset global DLQ in fixture
        """
        dlq = DeadLetterQueue.get_global()
        
        # FLAKY: Previous tests may have added dead letters
        assert dlq.size == 0, f"DLQ should be empty, has {dlq.size} items"
    
    def test_add_to_dlq(self):
        """Adds to DLQ, polluting state"""
        from src.services.queue import Message
        
        dlq = DeadLetterQueue.get_global()
        dlq.add(Message(payload="failed"), "Test error")
        
        assert dlq.size == 1
    
    def test_dlq_is_empty(self):
        """
        FLAKY: DLQ retains items from previous tests
        FIX: Clear DLQ between tests
        """
        dlq = DeadLetterQueue.get_global()
        
        # FLAKY: Size depends on test order
        assert dlq.size == 0, f"Expected 0, got {dlq.size}"


class TestConnectionPollution:
    """Tests affected by connection tracking state"""
    
    def test_connection_count_starts_at_zero(self):
        """
        FLAKY: Connection._connection_count is class-level
        FIX: Reset count in fixture
        """
        count = Connection.get_connection_count()
        
        # FLAKY: Previous tests created connections
        assert count == 0, f"Connection count should be 0, got {count}"
    
    def test_create_connections(self):
        """Creates connections, incrementing class counter"""
        # Simulate connection creation
        Connection._connection_count = 10
        
        assert Connection.get_connection_count() == 10
    
    def test_fresh_connection_count(self):
        """
        FLAKY: Counter persists from previous test
        FIX: Reset class-level counter
        """
        count = Connection.get_connection_count()
        
        # FLAKY: Will be 10 if test_create_connections ran
        assert count == 0, f"Expected 0, got {count}"


class TestMetricsCollectorPollution:
    """Tests affected by metrics state - uses Level 5 imports"""
    
    def test_metrics_empty_initially(self):
        """
        FLAKY: MetricsCollector class-level dicts persist
        FIX: Clear metrics in fixture
        """
        metrics = MetricsCollector.get_all()
        
        # FLAKY: Previous tests may have recorded metrics
        assert len(metrics["counters"]) == 0, \
            f"Expected no counters, got {metrics['counters']}"
    
    def test_record_metrics(self):
        """Records metrics, polluting state"""
        MetricsCollector.increment("requests", 100)
        MetricsCollector.record("latency", 50.5)
        
        assert MetricsCollector.get_counter("requests") == 100
    
    def test_metrics_fresh_state(self):
        """
        FLAKY: Metrics persist from previous test
        FIX: Clear class-level dicts
        """
        count = MetricsCollector.get_counter("requests")
        
        # FLAKY: Will be 100 if test_record_metrics ran
        assert count == 0, f"Expected 0, got {count}"


class TestHealthCheckPollution:
    """Tests affected by health check registration"""
    
    def test_no_health_checks_registered(self):
        """
        FLAKY: HealthCheck._checks persists
        FIX: Clear checks in fixture
        """
        # Run checks to populate _last_results
        asyncio.run(HealthCheck.run_all())
        
        # FLAKY: Previous tests may have registered checks
        results = HealthCheck._last_results
        assert len(results) == 0, f"Expected no checks, got {len(results)}"
    
    def test_register_health_checks(self):
        """Registers checks, polluting state"""
        HealthCheck.register("db", lambda: True)
        HealthCheck.register("cache", lambda: True)
        
        results = asyncio.run(HealthCheck.run_all())
        assert len(results) == 2
    
    def test_health_check_count(self):
        """
        FLAKY: Registered checks persist
        FIX: Clear _checks dict
        """
        results = asyncio.run(HealthCheck.run_all())
        
        # FLAKY: Count depends on previous test
        assert len(results) == 0, f"Expected 0 checks, got {len(results)}"


class TestSagaPollution:
    """Tests affected by saga state"""
    
    def test_no_active_sagas(self):
        """
        FLAKY: SagaOrchestrator._sagas persists
        FIX: Clear sagas in fixture
        """
        status = SagaOrchestrator.get_saga_status("any_id")
        
        # FLAKY: Previous tests may have created sagas
        assert status is None
        assert len(SagaOrchestrator._sagas) == 0, \
            f"Expected no sagas, got {len(SagaOrchestrator._sagas)}"
    
    def test_create_saga(self):
        """Creates saga state"""
        SagaOrchestrator._sagas["test_saga"] = {"status": "completed"}
        
        status = SagaOrchestrator.get_saga_status("test_saga")
        assert status is not None
    
    def test_saga_state_clean(self):
        """
        FLAKY: Saga state persists
        FIX: Clear _sagas dict
        """
        saga_count = len(SagaOrchestrator._sagas)
        
        # FLAKY: Will be 1 if test_create_saga ran
        assert saga_count == 0, f"Expected 0 sagas, got {saga_count}"


class TestWebSocketPollution:
    """Tests affected by WebSocket connection state"""
    
    def test_no_active_connections(self):
        """
        FLAKY: WebSocketHandler._connections persists
        FIX: Clear connections in fixture
        """
        connections = WebSocketHandler.get_all_connections()
        
        # FLAKY: Previous tests may have created connections
        assert len(connections) == 0, \
            f"Expected no connections, got {len(connections)}"
    
    def test_create_websocket_connection(self):
        """Creates WebSocket connection, polluting state"""
        ws = WebSocketHandler("test_ws_pollution")
        asyncio.run(ws.connect())
        
        assert WebSocketHandler.get_connection("test_ws_pollution") is not None
    
    def test_connections_empty(self):
        """
        FLAKY: Connections dict retains entries
        FIX: Clear _connections class variable
        """
        connections = WebSocketHandler.get_all_connections()
        
        # FLAKY: Will have 1 if test_create_websocket_connection ran
        assert len(connections) == 0


class TestSecretsPollution:
    """Tests affected by secrets state"""
    
    def test_no_secrets_loaded(self):
        """
        FLAKY: Secrets._secrets persists
        FIX: Clear secrets in fixture
        """
        # FLAKY: Previous tests may have set secrets
        value = Secrets.get("api_key")
        assert value is None, f"Expected None, got {value}"
    
    def test_set_secrets(self):
        """Sets secrets, polluting state"""
        Secrets.set("api_key", "secret123")
        Secrets.set("db_password", "dbpass456")
        
        assert Secrets.get("api_key") == "secret123"
    
    def test_secrets_empty(self):
        """
        FLAKY: Secrets persist from previous test
        FIX: Clear _secrets dict
        """
        value = Secrets.get("api_key")
        
        # FLAKY: Will be "secret123" if test_set_secrets ran
        assert value is None, f"Expected None, got {value}"
