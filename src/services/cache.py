"""
Level 2: Cache service
Provides caching functionality with TTL support.
Imports from: core.base, core.config (Level 1)
"""
import asyncio
import threading
import time
import random
from typing import Any, Optional, Dict, List, Tuple
from collections import OrderedDict

# Level 1 imports
from ..core.base import BaseComponent, Result, EventEmitter
from ..core.config import Config, FeatureFlags


class CacheEntry:
    """Single cache entry with metadata"""
    
    def __init__(self, key: str, value: Any, ttl: Optional[float] = None):
        self.key = key
        self.value = value
        self.created_at = time.time()
        self.ttl = ttl
        self.hit_count = 0
        self.last_accessed = self.created_at
    
    @property
    def is_expired(self) -> bool:
        if self.ttl is None:
            return False
        return time.time() - self.created_at > self.ttl
    
    def touch(self):
        """Update last accessed time"""
        self.last_accessed = time.time()
        self.hit_count += 1


class LRUCache:
    """Least Recently Used cache implementation"""
    
    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
    
    def get(self, key: str) -> Optional[Any]:
        """Get a value from cache"""
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            
            entry = self._cache[key]
            
            # Check expiration
            if entry.is_expired:
                del self._cache[key]
                self._misses += 1
                return None
            
            # Move to end (most recently used)
            self._cache.move_to_end(key)
            entry.touch()
            self._hits += 1
            return entry.value
    
    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Set a value in cache"""
        with self._lock:
            # Remove oldest if at capacity
            while len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
            
            self._cache[key] = CacheEntry(key, value, ttl)
    
    def delete(self, key: str) -> bool:
        """Delete a key from cache"""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False
    
    def clear(self) -> None:
        """Clear all cache entries"""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0
    
    @property
    def hit_rate(self) -> float:
        """Calculate cache hit rate"""
        total = self._hits + self._misses
        if total == 0:
            return 0.0
        return self._hits / total
    
    @property
    def size(self) -> int:
        """Get current cache size"""
        return len(self._cache)


class AsyncCache:
    """Async-safe cache with race condition potential"""
    
    _instance: Optional['AsyncCache'] = None  # Singleton - FLAKY: shared state
    
    def __init__(self, default_ttl: float = 300.0):
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = asyncio.Lock()
        self._default_ttl = default_ttl
        self._pending_sets: Dict[str, asyncio.Event] = {}  # FLAKY: race condition prone
    
    @classmethod
    def get_instance(cls) -> 'AsyncCache':
        if cls._instance is None:
            cls._instance = AsyncCache()
        return cls._instance
    
    @classmethod
    def reset_instance(cls):
        cls._instance = None
    
    async def get(self, key: str) -> Optional[Any]:
        """Get a value from cache"""
        # FLAKY: No lock on read - can read stale data
        if key not in self._cache:
            return None
        
        entry = self._cache[key]
        if entry.is_expired:
            # FLAKY: Race condition - another task might be setting this key
            if key in self._cache:
                del self._cache[key]
            return None
        
        entry.touch()
        return entry.value
    
    async def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Set a value in cache"""
        # FLAKY: Race condition between check and set
        actual_ttl = ttl if ttl is not None else self._default_ttl
        
        # Simulate network/processing delay
        await asyncio.sleep(random.uniform(0.001, 0.005))
        
        self._cache[key] = CacheEntry(key, value, actual_ttl)
    
    async def get_or_set(self, key: str, factory: callable, ttl: Optional[float] = None) -> Any:
        """Get from cache or set using factory function"""
        # FLAKY: Thundering herd problem - multiple requests can call factory
        value = await self.get(key)
        if value is not None:
            return value
        
        # No locking - multiple tasks might compute the same value
        if asyncio.iscoroutinefunction(factory):
            value = await factory()
        else:
            value = factory()
        
        await self.set(key, value, ttl)
        return value
    
    async def delete(self, key: str) -> bool:
        """Delete a key from cache"""
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False
    
    async def clear(self) -> None:
        """Clear all cache entries"""
        async with self._lock:
            self._cache.clear()
    
    @property
    def size(self) -> int:
        return len(self._cache)


class DistributedCacheSimulator:
    """Simulates a distributed cache with network latency and failures"""
    
    def __init__(
        self,
        latency_ms: Tuple[float, float] = (1.0, 10.0),
        failure_rate: float = 0.02,
        timeout: float = 5.0
    ):
        self._cache: Dict[str, CacheEntry] = {}
        self._latency_range = latency_ms
        self._failure_rate = failure_rate
        self._timeout = timeout
        self._lock = threading.Lock()
        self._connected = True
        self._request_count = 0  # FLAKY: not thread-safe
    
    def _simulate_network(self) -> None:
        """Simulate network latency and potential failure"""
        # FLAKY: Random failures
        if random.random() < self._failure_rate:
            raise ConnectionError("Cache connection failed")
        
        # FLAKY: Variable latency
        latency = random.uniform(*self._latency_range) / 1000.0
        time.sleep(latency)
    
    def get(self, key: str) -> Optional[Any]:
        """Get a value with simulated network"""
        self._request_count += 1  # FLAKY: race condition
        
        if not self._connected:
            raise ConnectionError("Cache not connected")
        
        self._simulate_network()
        
        with self._lock:
            if key not in self._cache:
                return None
            
            entry = self._cache[key]
            if entry.is_expired:
                del self._cache[key]
                return None
            
            return entry.value
    
    def set(self, key: str, value: Any, ttl: float = 300.0) -> bool:
        """Set a value with simulated network"""
        self._request_count += 1  # FLAKY: race condition
        
        if not self._connected:
            raise ConnectionError("Cache not connected")
        
        self._simulate_network()
        
        with self._lock:
            self._cache[key] = CacheEntry(key, value, ttl)
            return True
    
    def disconnect(self) -> None:
        """Simulate disconnection"""
        self._connected = False
    
    def connect(self) -> None:
        """Simulate reconnection"""
        self._connected = True
    
    def clear(self) -> None:
        """Clear all entries"""
        with self._lock:
            self._cache.clear()
            self._request_count = 0
    
    @property
    def request_count(self) -> int:
        return self._request_count


class CacheManager(BaseComponent):
    """High-level cache manager integrating multiple cache strategies"""
    
    _default_manager: Optional['CacheManager'] = None  # Class-level - FLAKY
    
    def __init__(self, name: str = "cache_manager"):
        super().__init__(name)
        self._local_cache = LRUCache(max_size=Config.get_instance().get("cache_size", 1000))
        self._distributed_cache: Optional[DistributedCacheSimulator] = None
        self._events = EventEmitter()
        self._use_distributed = FeatureFlags.is_enabled("distributed_cache")
    
    @classmethod
    def get_default(cls) -> 'CacheManager':
        if cls._default_manager is None:
            cls._default_manager = CacheManager("default")
            cls._default_manager.initialize()
        return cls._default_manager
    
    @classmethod
    def reset_default(cls):
        cls._default_manager = None
    
    def initialize(self) -> bool:
        """Initialize cache manager"""
        if not super().initialize():
            return False
        
        if self._use_distributed:
            self._distributed_cache = DistributedCacheSimulator()
        
        self._events.emit("initialized", self)
        return True
    
    def get(self, key: str) -> Optional[Any]:
        """Get from cache with fallback"""
        # Try local first
        value = self._local_cache.get(key)
        if value is not None:
            return value
        
        # Try distributed if available
        if self._distributed_cache:
            try:
                value = self._distributed_cache.get(key)
                if value is not None:
                    # Populate local cache
                    self._local_cache.set(key, value)
                    return value
            except ConnectionError:
                self._events.emit("distributed_error", key)
        
        return None
    
    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> bool:
        """Set in cache"""
        self._local_cache.set(key, value, ttl)
        
        if self._distributed_cache:
            try:
                self._distributed_cache.set(key, value, ttl or 300.0)
            except ConnectionError:
                self._events.emit("distributed_error", key)
        
        return True
    
    def invalidate(self, key: str) -> None:
        """Invalidate a cache key"""
        self._local_cache.delete(key)
        if self._distributed_cache:
            try:
                # FLAKY: distributed delete might fail silently
                self._distributed_cache.get(key)  # Simulate the network call
            except ConnectionError:
                pass
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        return {
            "local_size": self._local_cache.size,
            "local_hit_rate": self._local_cache.hit_rate,
            "distributed_enabled": self._distributed_cache is not None,
            "distributed_requests": self._distributed_cache.request_count if self._distributed_cache else 0,
        }
