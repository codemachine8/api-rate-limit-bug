"""
Level 5: Main Application
Main application entry point integrating all components.
Imports from: integrations (L4), handlers (L3), services (L2), core (L1)
"""
import asyncio
import time
import random
from typing import Any, Optional, Dict, List
from dataclasses import dataclass, field
import signal

# Level 1 imports
from ..core.base import BaseComponent, Result, EventEmitter
from ..core.config import Config, Environment, FeatureFlags, Secrets

# Level 2 imports
from ..services.cache import CacheManager, AsyncCache
from ..services.queue import QueueManager, SimpleQueue, AsyncQueue, Message

# Level 3 imports
from ..handlers.api_handler import ApiHandler, HttpMethod, Request, Response, RateLimiter
from ..handlers.event_handler import EventBus, EventProcessor, Event, EventType, EventStore

# Level 4 imports
from ..integrations.external_api import ExternalApiClient, ExternalApiConfig, WebhookDispatcher
from ..integrations.database_client import DatabaseClient, ConnectionPool, Repository


@dataclass
class AppConfig:
    """Application configuration"""
    name: str = "FlakyCIApp"
    version: str = "1.0.0"
    debug: bool = False
    workers: int = 4
    port: int = 8080
    db_pool_size: int = 10
    cache_size: int = 1000
    queue_size: int = 5000


class HealthCheck:
    """Health check for the application"""
    
    _checks: Dict[str, callable] = {}  # Class-level - FLAKY
    _last_results: Dict[str, bool] = {}  # Class-level - FLAKY
    
    @classmethod
    def register(cls, name: str, check: callable) -> None:
        """Register a health check"""
        cls._checks[name] = check
    
    @classmethod
    async def run_all(cls) -> Dict[str, bool]:
        """Run all health checks"""
        results = {}
        
        for name, check in cls._checks.items():
            try:
                if asyncio.iscoroutinefunction(check):
                    result = await check()
                else:
                    result = check()
                results[name] = bool(result)
            except Exception:
                results[name] = False
        
        cls._last_results = results  # FLAKY: race condition
        return results
    
    @classmethod
    def is_healthy(cls) -> bool:
        """Check if all components are healthy"""
        if not cls._last_results:
            return False
        return all(cls._last_results.values())
    
    @classmethod
    def clear(cls):
        cls._checks.clear()
        cls._last_results.clear()


class MetricsCollector:
    """Collect application metrics"""
    
    _metrics: Dict[str, List[float]] = {}  # Class-level - FLAKY: unbounded
    _counters: Dict[str, int] = {}  # Class-level - FLAKY
    
    @classmethod
    def record(cls, name: str, value: float) -> None:
        """Record a metric value"""
        if name not in cls._metrics:
            cls._metrics[name] = []
        cls._metrics[name].append(value)  # FLAKY: unbounded growth
    
    @classmethod
    def increment(cls, name: str, amount: int = 1) -> None:
        """Increment a counter"""
        if name not in cls._counters:
            cls._counters[name] = 0
        cls._counters[name] += amount  # FLAKY: race condition
    
    @classmethod
    def get_average(cls, name: str) -> float:
        """Get average of a metric"""
        values = cls._metrics.get(name, [])
        if not values:
            return 0.0
        return sum(values) / len(values)
    
    @classmethod
    def get_counter(cls, name: str) -> int:
        """Get counter value"""
        return cls._counters.get(name, 0)
    
    @classmethod
    def get_all(cls) -> Dict[str, Any]:
        """Get all metrics"""
        return {
            "metrics": {k: cls.get_average(k) for k in cls._metrics},
            "counters": dict(cls._counters),
        }
    
    @classmethod
    def clear(cls):
        cls._metrics.clear()
        cls._counters.clear()


class Application(BaseComponent):
    """Main application class"""
    
    _instance: Optional['Application'] = None  # Singleton - FLAKY
    _startup_time: Optional[float] = None  # Class-level - FLAKY
    
    def __init__(self, config: AppConfig):
        super().__init__(config.name)
        self.config = config
        self._running = False
        self._api_handler: Optional[ApiHandler] = None
        self._event_bus: Optional[EventBus] = None
        self._db_client: Optional[DatabaseClient] = None
        self._cache_manager: Optional[CacheManager] = None
        self._queue_manager: Optional[QueueManager] = None
        self._event_processor: Optional[EventProcessor] = None
        self._shutdown_handlers: List[callable] = []
        
        Application._instance = self
    
    @classmethod
    def get_instance(cls) -> Optional['Application']:
        return cls._instance
    
    @classmethod
    def reset_instance(cls):
        cls._instance = None
        cls._startup_time = None
    
    async def initialize(self) -> bool:
        """Initialize all application components"""
        if not super().initialize():
            return False
        
        Application._startup_time = time.time()
        
        try:
            # Initialize configuration
            config = Config.get_instance()
            config.load_from_env()
            
            # Detect environment
            Environment.detect()
            
            # Load secrets
            Secrets.load()
            
            # Initialize cache
            self._cache_manager = CacheManager("app_cache")
            self._cache_manager.initialize()
            
            # Initialize database
            self._db_client = DatabaseClient.get_instance()
            await self._db_client.connect(
                min_connections=5,
                max_connections=self.config.db_pool_size
            )
            
            # Initialize queue
            self._queue_manager = QueueManager.get_instance()
            event_queue = self._queue_manager.create_async_queue("events", self.config.queue_size)
            
            # Initialize event bus
            self._event_bus = EventBus.get_instance()
            
            # Initialize event processor
            self._event_processor = EventProcessor("main_processor", event_queue)
            
            # Initialize API handler
            self._api_handler = ApiHandler.get_instance()
            self._api_handler.initialize()
            self._setup_routes()
            
            # Register health checks
            self._setup_health_checks()
            
            # Subscribe to events
            self._setup_event_handlers()
            
            return True
            
        except Exception as e:
            # FLAKY: Partial initialization state
            return False
    
    def _setup_routes(self) -> None:
        """Setup API routes"""
        if self._api_handler is None:
            return
        
        # Health endpoint
        self._api_handler.register_route("/health", HttpMethod.GET, self._handle_health)
        
        # Metrics endpoint
        self._api_handler.register_route("/metrics", HttpMethod.GET, self._handle_metrics)
        
        # API endpoints
        self._api_handler.register_route("/api/users", HttpMethod.GET, self._handle_get_users)
        self._api_handler.register_route("/api/users", HttpMethod.POST, self._handle_create_user)
    
    async def _handle_health(self, request: Request) -> Dict:
        """Handle health check request"""
        results = await HealthCheck.run_all()
        return {
            "status": "healthy" if HealthCheck.is_healthy() else "unhealthy",
            "checks": results,
            "uptime": time.time() - (Application._startup_time or time.time()),
        }
    
    async def _handle_metrics(self, request: Request) -> Dict:
        """Handle metrics request"""
        MetricsCollector.increment("metrics_requests")
        return MetricsCollector.get_all()
    
    async def _handle_get_users(self, request: Request) -> Dict:
        """Handle get users request"""
        MetricsCollector.increment("api_requests")
        
        # Simulate database query
        start = time.time()
        await asyncio.sleep(random.uniform(0.01, 0.05))
        MetricsCollector.record("query_time_ms", (time.time() - start) * 1000)
        
        return {"users": [{"id": 1, "name": "Test User"}]}
    
    async def _handle_create_user(self, request: Request) -> Dict:
        """Handle create user request"""
        MetricsCollector.increment("api_requests")
        MetricsCollector.increment("users_created")
        
        # Emit event
        await self._event_bus.publish(Event(
            type=EventType.USER_CREATED,
            payload=request.body or {},
            source="api",
        ))
        
        return {"id": random.randint(1, 1000), "status": "created"}
    
    def _setup_health_checks(self) -> None:
        """Setup health checks"""
        HealthCheck.register("database", lambda: self._db_client is not None)
        HealthCheck.register("cache", lambda: self._cache_manager is not None)
        HealthCheck.register("api", lambda: self._api_handler is not None)
    
    def _setup_event_handlers(self) -> None:
        """Setup event handlers"""
        if self._event_bus is None:
            return
        
        self._event_bus.subscribe(
            EventType.USER_CREATED,
            self._on_user_created
        )
        
        self._event_bus.subscribe(
            EventType.SYSTEM_ERROR,
            self._on_system_error
        )
    
    async def _on_user_created(self, event: Event) -> None:
        """Handle user created event"""
        MetricsCollector.increment("events_processed")
        EventStore.append(event)
    
    async def _on_system_error(self, event: Event) -> None:
        """Handle system error event"""
        MetricsCollector.increment("system_errors")
        EventStore.append(event)
    
    async def start(self) -> None:
        """Start the application"""
        if self._running:
            return
        
        self._running = True
        
        # Start event processor
        if self._event_processor:
            await self._event_processor.start()
        
        # Start health check loop
        asyncio.create_task(self._health_check_loop())
    
    async def _health_check_loop(self) -> None:
        """Periodic health check"""
        while self._running:
            await HealthCheck.run_all()
            await asyncio.sleep(30)  # FLAKY: Fixed interval
    
    async def stop(self, timeout: float = 30.0) -> None:
        """Stop the application"""
        self._running = False
        
        # Run shutdown handlers
        for handler in self._shutdown_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await asyncio.wait_for(handler(), timeout=5.0)
                else:
                    handler()
            except Exception:
                pass  # FLAKY: Silent failure
        
        # Stop event processor
        if self._event_processor:
            await self._event_processor.stop(timeout=10.0)
        
        # Stop queue manager
        if self._queue_manager:
            self._queue_manager.stop_all()
    
    def on_shutdown(self, handler: callable) -> None:
        """Register a shutdown handler"""
        self._shutdown_handlers.append(handler)
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def uptime(self) -> float:
        if Application._startup_time is None:
            return 0.0
        return time.time() - Application._startup_time


class Worker(BaseComponent):
    """Background worker for processing tasks"""
    
    _workers: Dict[str, 'Worker'] = {}  # Class-level - FLAKY
    _total_tasks: int = 0  # Class-level - FLAKY
    
    def __init__(self, name: str, queue: SimpleQueue):
        super().__init__(name)
        self._queue = queue
        self._running = False
        self._processed = 0
        self._errors = 0
        self._thread = None
        
        Worker._workers[name] = self
    
    @classmethod
    def get_worker(cls, name: str) -> Optional['Worker']:
        return cls._workers.get(name)
    
    @classmethod
    def clear_workers(cls):
        cls._workers.clear()
        cls._total_tasks = 0
    
    def start(self) -> None:
        """Start the worker"""
        if self._running:
            return
        
        self._running = True
        import threading
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
    
    def stop(self, timeout: float = 5.0) -> None:
        """Stop the worker"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=timeout)
            # FLAKY: Thread might not stop
            self._thread = None
    
    def _run(self) -> None:
        """Worker loop"""
        while self._running:
            message = self._queue.get(timeout=0.1)
            if message is None:
                continue
            
            try:
                self._process(message)
                self._processed += 1
                Worker._total_tasks += 1  # FLAKY: race condition
                self._queue.task_done()
            except Exception:
                self._errors += 1
                self._queue.mark_error()
    
    def _process(self, message: Message) -> None:
        """Process a message"""
        # Simulate work
        time.sleep(random.uniform(0.01, 0.1))
        
        # FLAKY: Random failures
        if random.random() < 0.05:
            raise Exception("Processing failed")
    
    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "processed": self._processed,
            "errors": self._errors,
        }


def create_app(config: Optional[AppConfig] = None) -> Application:
    """Factory function to create application"""
    if config is None:
        config = AppConfig()
    
    return Application(config)
