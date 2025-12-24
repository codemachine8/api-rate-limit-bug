"""
Pytest configuration and fixtures.

NOTE: This conftest intentionally does NOT reset state between tests
to create realistic flaky test scenarios. The AI fix generator should
recognize that proper fixtures/cleanup are needed.
"""
import pytest
import asyncio
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture(scope="session")
def event_loop_policy():
    """Use default event loop policy"""
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
def event_loop():
    """Create event loop for async tests"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# NOTE: The following fixtures are intentionally commented out
# to create state pollution scenarios. A proper fix would be to
# uncomment and use these fixtures.

# @pytest.fixture(autouse=True)
# def reset_singletons():
#     """Reset all singletons between tests"""
#     from src.core.config import Config, FeatureFlags, Environment, Secrets
#     from src.services.cache import CacheManager, AsyncCache
#     from src.services.queue import QueueManager, DeadLetterQueue, AsyncQueue
#     from src.handlers.api_handler import ApiHandler, RateLimiter, WebSocketHandler
#     from src.handlers.event_handler import EventBus, EventStore, EventProcessor, SagaOrchestrator
#     from src.integrations.external_api import ExternalApiClient, WebhookDispatcher, ThirdPartyIntegration
#     from src.integrations.database_client import DatabaseClient, ConnectionPool, Connection, UnitOfWork
#     from src.app.main import Application, HealthCheck, MetricsCollector, Worker
#     from src.core.base import BaseComponent
#     
#     yield
#     
#     # Reset all singletons and class-level state
#     Config.reset_instance()
#     FeatureFlags.clear_all()
#     Environment.reset()
#     Secrets.clear()
#     CacheManager.reset_default()
#     AsyncCache.reset_instance()
#     QueueManager.reset_instance()
#     DeadLetterQueue.reset_global()
#     AsyncQueue.clear_instances()
#     ApiHandler.reset_instance()
#     RateLimiter.clear_all()
#     WebSocketHandler.clear_connections()
#     EventBus.reset_instance()
#     EventStore.clear()
#     EventProcessor.clear_processors()
#     SagaOrchestrator.clear_sagas()
#     ExternalApiClient.clear_clients()
#     WebhookDispatcher.clear_dispatchers()
#     ThirdPartyIntegration.clear_integrations()
#     DatabaseClient.reset_instance()
#     ConnectionPool.clear_pools()
#     Connection.reset_count()
#     UnitOfWork.clear_active()
#     Application.reset_instance()
#     HealthCheck.clear()
#     MetricsCollector.clear()
#     Worker.clear_workers()
#     BaseComponent.clear_registry()


# Markers for test categories
def pytest_configure(config):
    """Register custom markers"""
    config.addinivalue_line(
        "markers", "timing: tests with timing dependencies"
    )
    config.addinivalue_line(
        "markers", "async_race: tests with async race conditions"
    )
    config.addinivalue_line(
        "markers", "state_pollution: tests affected by shared state"
    )
    config.addinivalue_line(
        "markers", "isolation: tests with isolation issues"
    )
    config.addinivalue_line(
        "markers", "cleanup: tests with resource cleanup issues"
    )
    config.addinivalue_line(
        "markers", "nondeterministic: tests with random behavior"
    )
    config.addinivalue_line(
        "markers", "environment: tests dependent on environment"
    )
