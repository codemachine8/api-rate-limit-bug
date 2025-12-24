"""
Level 4: External API Integration
Provides integration with external APIs with retry and caching.
Imports from: handlers (Level 3), services (Level 2), core (Level 1)
"""
import asyncio
import time
import random
import hashlib
from typing import Any, Optional, Dict, List
from dataclasses import dataclass, field
from enum import Enum

# Level 1 imports
from ..core.base import Result, CircuitBreaker, RetryPolicy
from ..core.config import Config, Environment, Secrets

# Level 2 imports
from ..services.cache import AsyncCache, CacheManager
from ..services.queue import Message, AsyncQueue

# Level 3 imports
from ..handlers.api_handler import Request, Response, HttpMethod, RateLimiter
from ..handlers.event_handler import Event, EventType, EventBus


class ExternalServiceError(Exception):
    """Error from external service"""
    def __init__(self, message: str, status_code: int = 500, retry_after: Optional[float] = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class AuthMethod(Enum):
    API_KEY = "api_key"
    BEARER_TOKEN = "bearer_token"
    BASIC_AUTH = "basic_auth"
    OAUTH2 = "oauth2"


@dataclass
class ExternalApiConfig:
    """Configuration for an external API"""
    name: str
    base_url: str
    auth_method: AuthMethod = AuthMethod.API_KEY
    timeout: float = 30.0
    max_retries: int = 3
    rate_limit: float = 100.0  # requests per second
    cache_ttl: float = 60.0


class ExternalApiClient:
    """Client for calling external APIs"""
    
    _clients: Dict[str, 'ExternalApiClient'] = {}  # Class-level - FLAKY
    _total_requests: int = 0  # Class-level - FLAKY: race condition
    
    def __init__(self, config: ExternalApiConfig):
        self.config = config
        self._cache = AsyncCache.get_instance()
        self._circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
        self._retry_policy = RetryPolicy(max_retries=config.max_retries)
        self._rate_limiter = RateLimiter(rate=config.rate_limit)
        self._request_count = 0
        self._error_count = 0
        self._last_request_time: Optional[float] = None
        
        ExternalApiClient._clients[config.name] = self
    
    @classmethod
    def get_client(cls, name: str) -> Optional['ExternalApiClient']:
        return cls._clients.get(name)
    
    @classmethod
    def clear_clients(cls):
        cls._clients.clear()
        cls._total_requests = 0
    
    async def _get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers"""
        headers = {}
        
        if self.config.auth_method == AuthMethod.API_KEY:
            api_key = Secrets.get(f"{self.config.name}_api_key")
            if api_key:
                headers["X-API-Key"] = api_key
        elif self.config.auth_method == AuthMethod.BEARER_TOKEN:
            token = Secrets.get(f"{self.config.name}_token")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        
        return headers
    
    async def request(
        self,
        method: HttpMethod,
        path: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        use_cache: bool = True
    ) -> Result:
        """Make a request to the external API"""
        ExternalApiClient._total_requests += 1  # FLAKY: race condition
        self._request_count += 1
        self._last_request_time = time.time()
        
        # Check circuit breaker
        if not self._circuit_breaker.can_execute():
            return Result(success=False, error="Circuit breaker open")
        
        # Rate limiting
        if not await self._rate_limiter.acquire():
            return Result(success=False, error="Rate limit exceeded")
        
        # Check cache for GET requests
        cache_key = None
        if method == HttpMethod.GET and use_cache:
            cache_key = self._get_cache_key(path, params)
            cached = await self._cache.get(cache_key)
            if cached is not None:
                return Result(success=True, data=cached)
        
        # Execute with retries
        attempt = 0
        last_error = None
        
        while self._retry_policy.should_retry(attempt):
            try:
                response = await self._execute_request(method, path, data, params)
                
                self._circuit_breaker.record_success()
                
                # Cache successful GET responses
                if cache_key and response:
                    await self._cache.set(cache_key, response, ttl=self.config.cache_ttl)
                
                return Result(success=True, data=response)
                
            except ExternalServiceError as e:
                last_error = e
                self._error_count += 1
                
                if e.status_code >= 500:
                    # Server error, retry
                    delay = e.retry_after or self._retry_policy.get_delay(attempt)
                    await asyncio.sleep(delay)
                    attempt += 1
                elif e.status_code == 429:
                    # Rate limited by external service
                    delay = e.retry_after or 1.0
                    await asyncio.sleep(delay)
                    attempt += 1
                else:
                    # Client error, don't retry
                    break
                    
            except Exception as e:
                last_error = e
                self._error_count += 1
                self._circuit_breaker.record_failure()
                
                delay = self._retry_policy.get_delay(attempt)
                await asyncio.sleep(delay)
                attempt += 1
        
        self._circuit_breaker.record_failure()
        return Result(success=False, error=str(last_error))
    
    async def _execute_request(
        self,
        method: HttpMethod,
        path: str,
        data: Optional[Dict],
        params: Optional[Dict]
    ) -> Dict:
        """Execute the actual HTTP request (simulated)"""
        # Simulate network latency
        latency = random.uniform(0.01, 0.1)
        
        # FLAKY: Random latency spikes
        if random.random() < 0.05:
            latency += random.uniform(0.5, 2.0)
        
        await asyncio.sleep(latency)
        
        # FLAKY: Random failures
        if random.random() < 0.03:
            raise ExternalServiceError("Service temporarily unavailable", status_code=503)
        
        if random.random() < 0.02:
            raise ExternalServiceError("Too many requests", status_code=429, retry_after=1.0)
        
        # Simulate response
        return {
            "status": "ok",
            "path": path,
            "method": method.value,
            "timestamp": time.time(),
        }
    
    def _get_cache_key(self, path: str, params: Optional[Dict]) -> str:
        """Generate cache key for a request"""
        key_data = f"{self.config.name}:{path}:{str(params or {})}"
        return hashlib.md5(key_data.encode()).hexdigest()
    
    async def get(self, path: str, params: Optional[Dict] = None) -> Result:
        """Make a GET request"""
        return await self.request(HttpMethod.GET, path, params=params)
    
    async def post(self, path: str, data: Dict) -> Result:
        """Make a POST request"""
        return await self.request(HttpMethod.POST, path, data=data, use_cache=False)
    
    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "requests": self._request_count,
            "errors": self._error_count,
            "circuit_breaker_state": self._circuit_breaker.state,
            "cache_enabled": True,
        }


class WebhookDispatcher:
    """Dispatch webhooks to external services"""
    
    _dispatchers: Dict[str, 'WebhookDispatcher'] = {}  # Class-level - FLAKY
    _total_dispatched: int = 0  # Class-level - FLAKY
    
    def __init__(self, name: str):
        self.name = name
        self._endpoints: List[str] = []
        self._retry_policy = RetryPolicy(max_retries=5)
        self._queue: Optional[AsyncQueue] = None
        self._dispatch_count = 0
        self._failure_count = 0
        
        WebhookDispatcher._dispatchers[name] = self
    
    @classmethod
    def get_dispatcher(cls, name: str) -> Optional['WebhookDispatcher']:
        return cls._dispatchers.get(name)
    
    @classmethod
    def clear_dispatchers(cls):
        cls._dispatchers.clear()
        cls._total_dispatched = 0
    
    def add_endpoint(self, url: str) -> None:
        """Add a webhook endpoint"""
        self._endpoints.append(url)
    
    def set_queue(self, queue: AsyncQueue) -> None:
        """Set queue for async dispatch"""
        self._queue = queue
    
    async def dispatch(self, event: Event, async_mode: bool = True) -> Result:
        """Dispatch an event to all endpoints"""
        if not self._endpoints:
            return Result(success=False, error="No endpoints configured")
        
        if async_mode and self._queue:
            # Queue for async processing
            message = Message(payload={
                "event": event,
                "endpoints": self._endpoints.copy(),
            })
            await self._queue.put(message)
            return Result(success=True, data={"queued": True})
        
        # Synchronous dispatch
        return await self._dispatch_to_all(event)
    
    async def _dispatch_to_all(self, event: Event) -> Result:
        """Dispatch to all endpoints"""
        WebhookDispatcher._total_dispatched += 1  # FLAKY: race condition
        self._dispatch_count += 1
        
        results = []
        
        # FLAKY: Concurrent dispatch to all endpoints
        tasks = [self._dispatch_to_endpoint(url, event) for url in self._endpoints]
        endpoint_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        successes = sum(1 for r in endpoint_results if isinstance(r, bool) and r)
        failures = len(endpoint_results) - successes
        
        if failures > 0:
            self._failure_count += failures
        
        return Result(
            success=failures == 0,
            data={"successes": successes, "failures": failures}
        )
    
    async def _dispatch_to_endpoint(self, url: str, event: Event) -> bool:
        """Dispatch to a single endpoint with retry"""
        attempt = 0
        
        while self._retry_policy.should_retry(attempt):
            try:
                await self._send_webhook(url, event)
                return True
            except Exception:
                delay = self._retry_policy.get_delay(attempt)
                await asyncio.sleep(delay)
                attempt += 1
        
        return False
    
    async def _send_webhook(self, url: str, event: Event) -> None:
        """Send webhook (simulated)"""
        # Simulate network latency
        await asyncio.sleep(random.uniform(0.01, 0.05))
        
        # FLAKY: Random failures
        if random.random() < 0.05:
            raise ConnectionError(f"Failed to connect to {url}")
    
    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "endpoints": len(self._endpoints),
            "dispatched": self._dispatch_count,
            "failures": self._failure_count,
        }


class ThirdPartyIntegration:
    """Integration with a specific third-party service"""
    
    _integrations: Dict[str, 'ThirdPartyIntegration'] = {}  # Class-level - FLAKY
    
    def __init__(self, name: str, api_client: ExternalApiClient):
        self.name = name
        self._client = api_client
        self._event_bus = EventBus.get_instance()
        self._enabled = True
        self._last_sync: Optional[float] = None
        self._sync_errors: List[Dict] = []  # FLAKY: unbounded growth
        
        ThirdPartyIntegration._integrations[name] = self
    
    @classmethod
    def get_integration(cls, name: str) -> Optional['ThirdPartyIntegration']:
        return cls._integrations.get(name)
    
    @classmethod
    def clear_integrations(cls):
        cls._integrations.clear()
    
    async def sync_data(self, data_type: str, params: Optional[Dict] = None) -> Result:
        """Sync data from third-party service"""
        if not self._enabled:
            return Result(success=False, error="Integration disabled")
        
        result = await self._client.get(f"/sync/{data_type}", params=params)
        
        if result.success:
            self._last_sync = time.time()
            
            # Emit sync event
            await self._event_bus.publish(Event(
                type=EventType.SYSTEM_ERROR,  # Would be a custom type in real app
                payload={"integration": self.name, "data_type": data_type},
                source=self.name,
            ))
        else:
            self._sync_errors.append({
                "timestamp": time.time(),
                "error": result.error,
            })
        
        return result
    
    async def push_data(self, data_type: str, data: Dict) -> Result:
        """Push data to third-party service"""
        if not self._enabled:
            return Result(success=False, error="Integration disabled")
        
        return await self._client.post(f"/data/{data_type}", data=data)
    
    def enable(self) -> None:
        self._enabled = True
    
    def disable(self) -> None:
        self._enabled = False
    
    @property
    def is_healthy(self) -> bool:
        """Check if integration is healthy"""
        if not self._enabled:
            return False
        
        # Check recent sync errors
        recent_errors = [
            e for e in self._sync_errors
            if time.time() - e["timestamp"] < 300  # Last 5 minutes
        ]
        
        return len(recent_errors) < 5
    
    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "healthy": self.is_healthy,
            "last_sync": self._last_sync,
            "error_count": len(self._sync_errors),
        }
