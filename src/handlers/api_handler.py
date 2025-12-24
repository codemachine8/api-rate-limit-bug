"""
Level 3: API Handler
Handles API requests with caching and rate limiting.
Imports from: services.cache (Level 2), core (Level 1)
"""
import asyncio
import time
import random
import hashlib
from typing import Any, Optional, Dict, List, Callable
from dataclasses import dataclass, field
from enum import Enum

# Level 1 imports
from ..core.base import Result, CircuitBreaker, RetryPolicy, EventEmitter
from ..core.config import Config, FeatureFlags, Environment

# Level 2 imports
from ..services.cache import AsyncCache, CacheManager, LRUCache


class HttpMethod(Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"


@dataclass
class Request:
    """HTTP request representation"""
    method: HttpMethod
    path: str
    headers: Dict[str, str] = field(default_factory=dict)
    query_params: Dict[str, str] = field(default_factory=dict)
    body: Any = None
    client_ip: str = "127.0.0.1"
    request_id: str = field(default_factory=lambda: hashlib.md5(str(time.time()).encode()).hexdigest()[:8])
    timestamp: float = field(default_factory=time.time)


@dataclass
class Response:
    """HTTP response representation"""
    status_code: int
    body: Any = None
    headers: Dict[str, str] = field(default_factory=dict)
    latency_ms: float = 0.0


class RateLimiter:
    """Token bucket rate limiter"""
    
    _limiters: Dict[str, 'RateLimiter'] = {}  # Class-level - FLAKY: shared state
    
    def __init__(self, rate: float = 100.0, burst: int = 10):
        self.rate = rate  # tokens per second
        self.burst = burst
        self._tokens = float(burst)
        self._last_update = time.time()
        self._lock = asyncio.Lock()
    
    @classmethod
    def get_for_key(cls, key: str, rate: float = 100.0) -> 'RateLimiter':
        """Get or create a rate limiter for a key"""
        if key not in cls._limiters:
            cls._limiters[key] = RateLimiter(rate)
        return cls._limiters[key]
    
    @classmethod
    def clear_all(cls):
        cls._limiters.clear()
    
    async def acquire(self, tokens: int = 1) -> bool:
        """Try to acquire tokens"""
        async with self._lock:
            now = time.time()
            elapsed = now - self._last_update
            self._last_update = now
            
            # Add tokens based on elapsed time
            self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
            
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False
    
    @property
    def available_tokens(self) -> float:
        return self._tokens


class RequestValidator:
    """Validate incoming requests"""
    
    def __init__(self):
        self._rules: List[Callable[[Request], Optional[str]]] = []
    
    def add_rule(self, rule: Callable[[Request], Optional[str]]) -> None:
        """Add a validation rule (returns error message or None)"""
        self._rules.append(rule)
    
    def validate(self, request: Request) -> List[str]:
        """Validate request against all rules"""
        errors = []
        for rule in self._rules:
            error = rule(request)
            if error:
                errors.append(error)
        return errors
    
    @staticmethod
    def require_auth(request: Request) -> Optional[str]:
        """Require authentication header"""
        if "Authorization" not in request.headers:
            return "Missing Authorization header"
        return None
    
    @staticmethod
    def require_content_type(request: Request) -> Optional[str]:
        """Require Content-Type for POST/PUT"""
        if request.method in (HttpMethod.POST, HttpMethod.PUT):
            if "Content-Type" not in request.headers:
                return "Missing Content-Type header"
        return None


class ApiHandler:
    """Main API request handler"""
    
    _instance: Optional['ApiHandler'] = None  # Singleton - FLAKY
    _request_count: int = 0  # Class-level counter - FLAKY: not thread-safe
    
    def __init__(self):
        self._routes: Dict[str, Dict[HttpMethod, Callable]] = {}
        self._middleware: List[Callable] = []
        self._cache = AsyncCache.get_instance()
        self._events = EventEmitter()
        self._circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
        self._validator = RequestValidator()
        self._initialized = False
    
    @classmethod
    def get_instance(cls) -> 'ApiHandler':
        if cls._instance is None:
            cls._instance = ApiHandler()
        return cls._instance
    
    @classmethod
    def reset_instance(cls):
        cls._instance = None
        cls._request_count = 0
    
    def initialize(self) -> bool:
        """Initialize the handler"""
        if self._initialized:
            return True
        
        # Add default validation rules
        self._validator.add_rule(RequestValidator.require_content_type)
        
        self._initialized = True
        return True
    
    def register_route(self, path: str, method: HttpMethod, handler: Callable) -> None:
        """Register a route handler"""
        if path not in self._routes:
            self._routes[path] = {}
        self._routes[path][method] = handler
    
    def add_middleware(self, middleware: Callable) -> None:
        """Add middleware to the chain"""
        self._middleware.append(middleware)
    
    async def handle(self, request: Request) -> Response:
        """Handle an incoming request"""
        start_time = time.time()
        ApiHandler._request_count += 1  # FLAKY: race condition
        
        try:
            # Check circuit breaker
            if not self._circuit_breaker.can_execute():
                return Response(
                    status_code=503,
                    body={"error": "Service temporarily unavailable"},
                    latency_ms=(time.time() - start_time) * 1000
                )
            
            # Rate limiting
            limiter = RateLimiter.get_for_key(request.client_ip)
            if not await limiter.acquire():
                return Response(
                    status_code=429,
                    body={"error": "Rate limit exceeded"},
                    latency_ms=(time.time() - start_time) * 1000
                )
            
            # Validate request
            errors = self._validator.validate(request)
            if errors:
                return Response(
                    status_code=400,
                    body={"errors": errors},
                    latency_ms=(time.time() - start_time) * 1000
                )
            
            # Run middleware
            for middleware in self._middleware:
                result = await middleware(request) if asyncio.iscoroutinefunction(middleware) else middleware(request)
                if isinstance(result, Response):
                    return result
            
            # Find route handler
            route_handlers = self._routes.get(request.path)
            if route_handlers is None:
                return Response(status_code=404, body={"error": "Not found"})
            
            handler = route_handlers.get(request.method)
            if handler is None:
                return Response(status_code=405, body={"error": "Method not allowed"})
            
            # Check cache for GET requests
            if request.method == HttpMethod.GET:
                cache_key = f"{request.path}:{str(request.query_params)}"
                cached = await self._cache.get(cache_key)
                if cached is not None:
                    return Response(
                        status_code=200,
                        body=cached,
                        headers={"X-Cache": "HIT"},
                        latency_ms=(time.time() - start_time) * 1000
                    )
            
            # Execute handler
            if asyncio.iscoroutinefunction(handler):
                result = await handler(request)
            else:
                result = handler(request)
            
            # Cache successful GET responses
            if request.method == HttpMethod.GET and isinstance(result, dict):
                cache_key = f"{request.path}:{str(request.query_params)}"
                await self._cache.set(cache_key, result, ttl=60.0)
            
            self._circuit_breaker.record_success()
            
            return Response(
                status_code=200,
                body=result,
                latency_ms=(time.time() - start_time) * 1000
            )
            
        except Exception as e:
            self._circuit_breaker.record_failure()
            self._events.emit("error", request, e)
            
            return Response(
                status_code=500,
                body={"error": str(e)},
                latency_ms=(time.time() - start_time) * 1000
            )
    
    @property
    def request_count(self) -> int:
        return ApiHandler._request_count
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_requests": self._request_count,
            "routes": len(self._routes),
            "circuit_breaker_state": self._circuit_breaker.state,
        }


class BatchHandler:
    """Handle batch API requests"""
    
    def __init__(self, api_handler: ApiHandler, max_batch_size: int = 100):
        self._api_handler = api_handler
        self._max_batch_size = max_batch_size
        self._batch_count = 0  # FLAKY: not thread-safe
    
    async def handle_batch(self, requests: List[Request]) -> List[Response]:
        """Process a batch of requests"""
        if len(requests) > self._max_batch_size:
            return [Response(status_code=400, body={"error": "Batch too large"})]
        
        self._batch_count += 1  # FLAKY: race condition
        
        # Process concurrently - FLAKY: order not guaranteed
        tasks = [self._api_handler.handle(req) for req in requests]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Convert exceptions to error responses
        result = []
        for resp in responses:
            if isinstance(resp, Exception):
                result.append(Response(status_code=500, body={"error": str(resp)}))
            else:
                result.append(resp)
        
        return result
    
    async def handle_sequential(self, requests: List[Request]) -> List[Response]:
        """Process requests sequentially (maintains order)"""
        responses = []
        for request in requests:
            # FLAKY: Timing between requests is not controlled
            response = await self._api_handler.handle(request)
            responses.append(response)
        return responses


class WebSocketHandler:
    """WebSocket connection handler"""
    
    _connections: Dict[str, 'WebSocketHandler'] = {}  # Class-level - FLAKY
    _message_count: int = 0  # Class-level - FLAKY
    
    def __init__(self, connection_id: str):
        self.connection_id = connection_id
        self._connected = False
        self._messages_received = 0
        self._messages_sent = 0
        self._last_ping: Optional[float] = None
        self._events = EventEmitter()
        
        WebSocketHandler._connections[connection_id] = self
    
    @classmethod
    def get_connection(cls, connection_id: str) -> Optional['WebSocketHandler']:
        return cls._connections.get(connection_id)
    
    @classmethod
    def get_all_connections(cls) -> List['WebSocketHandler']:
        return list(cls._connections.values())
    
    @classmethod
    def clear_connections(cls):
        cls._connections.clear()
        cls._message_count = 0
    
    async def connect(self) -> bool:
        """Establish connection"""
        # FLAKY: Random connection failures
        if random.random() < 0.02:
            return False
        
        self._connected = True
        self._events.emit("connected", self)
        return True
    
    async def disconnect(self) -> None:
        """Close connection"""
        self._connected = False
        if self.connection_id in WebSocketHandler._connections:
            del WebSocketHandler._connections[self.connection_id]
        self._events.emit("disconnected", self)
    
    async def send(self, message: Any) -> bool:
        """Send a message"""
        if not self._connected:
            return False
        
        # FLAKY: Random send failures
        if random.random() < 0.01:
            return False
        
        # Simulate network latency
        await asyncio.sleep(random.uniform(0.001, 0.01))
        
        self._messages_sent += 1
        WebSocketHandler._message_count += 1  # FLAKY: race condition
        return True
    
    async def receive(self, timeout: float = 30.0) -> Optional[Any]:
        """Receive a message"""
        if not self._connected:
            return None
        
        # Simulate waiting for message
        try:
            await asyncio.sleep(random.uniform(0.01, 0.05))
            self._messages_received += 1
            return {"type": "message", "data": "test"}
        except asyncio.TimeoutError:
            return None
    
    async def ping(self) -> bool:
        """Send a ping"""
        if not self._connected:
            return False
        
        self._last_ping = time.time()
        return True
    
    @property
    def is_connected(self) -> bool:
        return self._connected
    
    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "connected": self._connected,
            "messages_sent": self._messages_sent,
            "messages_received": self._messages_received,
        }
