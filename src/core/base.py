"""
Level 1: Core base module
Provides foundational classes used throughout the application.
"""
import threading
import time
import random
from typing import Any, Optional, Dict, List
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Result:
    """Generic result container"""
    success: bool
    data: Any = None
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    
    def is_ok(self) -> bool:
        return self.success and self.error is None


class BaseComponent:
    """Base class for all components with lifecycle management"""
    
    _instances: Dict[str, 'BaseComponent'] = {}  # Class-level registry - FLAKY: shared state
    _instance_count: int = 0  # Class-level counter - FLAKY: not reset between tests
    
    def __init__(self, name: str):
        self.name = name
        self._initialized = False
        self._start_time: Optional[float] = None
        self._lock = threading.Lock()
        
        # Register instance (shared state issue)
        BaseComponent._instances[name] = self
        BaseComponent._instance_count += 1
    
    def initialize(self) -> bool:
        """Initialize the component"""
        if self._initialized:
            return True
        
        # Simulate initialization delay - FLAKY: timing-dependent
        time.sleep(random.uniform(0.001, 0.01))
        self._initialized = True
        self._start_time = time.time()
        return True
    
    def shutdown(self) -> bool:
        """Shutdown the component"""
        if not self._initialized:
            return False
        self._initialized = False
        return True
    
    @classmethod
    def get_instance(cls, name: str) -> Optional['BaseComponent']:
        """Get a registered instance by name"""
        return cls._instances.get(name)
    
    @classmethod
    def get_instance_count(cls) -> int:
        """Get total number of instances created"""
        return cls._instance_count
    
    @classmethod
    def clear_registry(cls):
        """Clear the instance registry"""
        cls._instances.clear()
        cls._instance_count = 0
    
    @property
    def uptime(self) -> float:
        """Get component uptime in seconds"""
        if self._start_time is None:
            return 0.0
        return time.time() - self._start_time


class CircuitBreaker:
    """Circuit breaker pattern implementation"""
    
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"
    
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = self.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = threading.Lock()
    
    @property
    def state(self) -> str:
        with self._lock:
            if self._state == self.OPEN:
                # Check if recovery timeout has passed
                if self._last_failure_time and \
                   time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._state = self.HALF_OPEN
            return self._state
    
    def record_success(self):
        """Record a successful call"""
        with self._lock:
            self._failure_count = 0
            self._state = self.CLOSED
    
    def record_failure(self):
        """Record a failed call"""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._failure_count >= self.failure_threshold:
                self._state = self.OPEN
    
    def can_execute(self) -> bool:
        """Check if a call can be executed"""
        return self.state != self.OPEN


class RetryPolicy:
    """Configurable retry policy"""
    
    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 0.1,
        max_delay: float = 10.0,
        exponential_base: float = 2.0,
        jitter: bool = True
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
    
    def get_delay(self, attempt: int) -> float:
        """Calculate delay for a given attempt number"""
        delay = self.base_delay * (self.exponential_base ** attempt)
        delay = min(delay, self.max_delay)
        
        if self.jitter:
            # Add random jitter - FLAKY: non-deterministic
            delay *= random.uniform(0.5, 1.5)
        
        return delay
    
    def should_retry(self, attempt: int) -> bool:
        """Check if another retry should be attempted"""
        return attempt < self.max_retries


class EventEmitter:
    """Simple event emitter for pub/sub pattern"""
    
    def __init__(self):
        self._listeners: Dict[str, List[callable]] = {}
        self._lock = threading.Lock()
    
    def on(self, event: str, callback: callable):
        """Register an event listener"""
        with self._lock:
            if event not in self._listeners:
                self._listeners[event] = []
            self._listeners[event].append(callback)
    
    def off(self, event: str, callback: callable):
        """Remove an event listener"""
        with self._lock:
            if event in self._listeners:
                self._listeners[event] = [
                    cb for cb in self._listeners[event] if cb != callback
                ]
    
    def emit(self, event: str, *args, **kwargs):
        """Emit an event to all listeners"""
        listeners = []
        with self._lock:
            if event in self._listeners:
                listeners = self._listeners[event].copy()
        
        for callback in listeners:
            # FLAKY: No error handling, callbacks may fail
            callback(*args, **kwargs)
    
    def clear(self):
        """Clear all listeners"""
        with self._lock:
            self._listeners.clear()
