"""
Level 2: Queue service
Provides message queue functionality with async support.
Imports from: core.base, core.config (Level 1)
"""
import asyncio
import threading
import time
import random
import uuid
from typing import Any, Optional, Dict, List, Callable
from dataclasses import dataclass, field
from queue import Queue, Empty, Full
from collections import deque

# Level 1 imports
from ..core.base import BaseComponent, Result, RetryPolicy, CircuitBreaker
from ..core.config import Config, Environment


@dataclass
class Message:
    """Queue message container"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    payload: Any = None
    created_at: float = field(default_factory=time.time)
    attempts: int = 0
    max_attempts: int = 3
    delay_until: Optional[float] = None
    
    @property
    def is_delayed(self) -> bool:
        if self.delay_until is None:
            return False
        return time.time() < self.delay_until
    
    @property
    def can_retry(self) -> bool:
        return self.attempts < self.max_attempts


class SimpleQueue:
    """Thread-safe simple queue implementation"""
    
    def __init__(self, max_size: int = 1000):
        self._queue: Queue = Queue(maxsize=max_size)
        self._processed_count = 0  # FLAKY: not thread-safe
        self._error_count = 0  # FLAKY: not thread-safe
    
    def put(self, message: Message, timeout: Optional[float] = None) -> bool:
        """Put a message on the queue"""
        try:
            self._queue.put(message, timeout=timeout)
            return True
        except Full:
            return False
    
    def get(self, timeout: Optional[float] = None) -> Optional[Message]:
        """Get a message from the queue"""
        try:
            return self._queue.get(timeout=timeout)
        except Empty:
            return None
    
    def task_done(self) -> None:
        """Mark task as done"""
        self._queue.task_done()
        self._processed_count += 1  # FLAKY: race condition
    
    def mark_error(self) -> None:
        """Mark task as errored"""
        self._error_count += 1  # FLAKY: race condition
    
    @property
    def size(self) -> int:
        return self._queue.qsize()
    
    @property
    def is_empty(self) -> bool:
        return self._queue.empty()
    
    @property
    def stats(self) -> Dict[str, int]:
        return {
            "size": self.size,
            "processed": self._processed_count,
            "errors": self._error_count,
        }
    
    def clear(self) -> None:
        """Clear the queue"""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except Empty:
                break
        self._processed_count = 0
        self._error_count = 0


class AsyncQueue:
    """Async queue with priority support"""
    
    _instances: Dict[str, 'AsyncQueue'] = {}  # Class-level registry - FLAKY
    
    def __init__(self, name: str, max_size: int = 1000):
        self.name = name
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=max_size)
        self._processing: Dict[str, Message] = {}  # FLAKY: no cleanup on failure
        self._lock = asyncio.Lock()
        self._message_count = 0
        
        # Register instance
        AsyncQueue._instances[name] = self
    
    @classmethod
    def get_instance(cls, name: str) -> Optional['AsyncQueue']:
        return cls._instances.get(name)
    
    @classmethod
    def clear_instances(cls):
        cls._instances.clear()
    
    async def put(self, message: Message, priority: int = 5) -> bool:
        """Put a message with priority (lower = higher priority)"""
        try:
            # FLAKY: No timeout, can hang
            await self._queue.put((priority, self._message_count, message))
            self._message_count += 1
            return True
        except asyncio.QueueFull:
            return False
    
    async def get(self, timeout: Optional[float] = None) -> Optional[Message]:
        """Get the highest priority message"""
        try:
            if timeout:
                _, _, message = await asyncio.wait_for(
                    self._queue.get(), timeout=timeout
                )
            else:
                _, _, message = await self._queue.get()
            
            # Track processing - FLAKY: race condition
            self._processing[message.id] = message
            return message
        except asyncio.TimeoutError:
            return None
    
    async def ack(self, message_id: str) -> bool:
        """Acknowledge message processing"""
        # FLAKY: No lock protection
        if message_id in self._processing:
            del self._processing[message_id]
            return True
        return False
    
    async def nack(self, message_id: str, requeue: bool = True) -> bool:
        """Negative acknowledge - optionally requeue"""
        if message_id not in self._processing:
            return False
        
        message = self._processing.pop(message_id)
        
        if requeue and message.can_retry:
            message.attempts += 1
            # Add delay before retry - FLAKY: timing dependent
            message.delay_until = time.time() + (message.attempts * 0.5)
            await self.put(message, priority=10)  # Lower priority for retries
        
        return True
    
    @property
    def size(self) -> int:
        return self._queue.qsize()
    
    @property
    def processing_count(self) -> int:
        return len(self._processing)


class DeadLetterQueue:
    """Queue for failed messages"""
    
    _global_dlq: Optional['DeadLetterQueue'] = None  # Class-level - FLAKY
    
    def __init__(self, max_size: int = 10000):
        self._messages: deque = deque(maxlen=max_size)
        self._lock = threading.Lock()
    
    @classmethod
    def get_global(cls) -> 'DeadLetterQueue':
        if cls._global_dlq is None:
            cls._global_dlq = DeadLetterQueue()
        return cls._global_dlq
    
    @classmethod
    def reset_global(cls):
        cls._global_dlq = None
    
    def add(self, message: Message, error: str) -> None:
        """Add a failed message"""
        with self._lock:
            self._messages.append({
                "message": message,
                "error": error,
                "failed_at": time.time(),
            })
    
    def get_all(self) -> List[Dict]:
        """Get all dead letters"""
        with self._lock:
            return list(self._messages)
    
    def clear(self) -> None:
        """Clear dead letter queue"""
        with self._lock:
            self._messages.clear()
    
    @property
    def size(self) -> int:
        return len(self._messages)


class MessageProcessor(BaseComponent):
    """Process messages from queue with error handling"""
    
    def __init__(self, name: str, queue: SimpleQueue):
        super().__init__(name)
        self._queue = queue
        self._handlers: Dict[str, Callable] = {}
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._retry_policy = RetryPolicy()
        self._circuit_breaker = CircuitBreaker(failure_threshold=3)
    
    def register_handler(self, message_type: str, handler: Callable) -> None:
        """Register a handler for a message type"""
        self._handlers[message_type] = handler
    
    def start(self) -> None:
        """Start processing messages"""
        if self._running:
            return
        
        self._running = True
        self._worker_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._worker_thread.start()
    
    def stop(self, timeout: float = 5.0) -> None:
        """Stop processing"""
        self._running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=timeout)
            # FLAKY: Thread might not stop in time
            self._worker_thread = None
    
    def _process_loop(self) -> None:
        """Main processing loop"""
        while self._running:
            message = self._queue.get(timeout=0.1)
            if message is None:
                continue
            
            try:
                self._process_message(message)
                self._queue.task_done()
                self._circuit_breaker.record_success()
            except Exception as e:
                self._queue.mark_error()
                self._circuit_breaker.record_failure()
                
                if message.can_retry and self._circuit_breaker.can_execute():
                    message.attempts += 1
                    # FLAKY: Immediate retry without backoff
                    self._queue.put(message)
                else:
                    DeadLetterQueue.get_global().add(message, str(e))
    
    def _process_message(self, message: Message) -> None:
        """Process a single message"""
        # FLAKY: Assumes payload has 'type' field
        message_type = getattr(message.payload, 'type', 'default')
        
        handler = self._handlers.get(message_type)
        if handler is None:
            raise ValueError(f"No handler for message type: {message_type}")
        
        # FLAKY: No timeout on handler execution
        handler(message.payload)


class QueueManager(BaseComponent):
    """High-level queue management"""
    
    _instance: Optional['QueueManager'] = None  # Singleton - FLAKY
    
    def __init__(self):
        super().__init__("queue_manager")
        self._queues: Dict[str, SimpleQueue] = {}
        self._async_queues: Dict[str, AsyncQueue] = {}
        self._processors: Dict[str, MessageProcessor] = {}
    
    @classmethod
    def get_instance(cls) -> 'QueueManager':
        if cls._instance is None:
            cls._instance = QueueManager()
        return cls._instance
    
    @classmethod
    def reset_instance(cls):
        if cls._instance:
            for processor in cls._instance._processors.values():
                processor.stop()
        cls._instance = None
    
    def create_queue(self, name: str, max_size: int = 1000) -> SimpleQueue:
        """Create a new queue"""
        if name in self._queues:
            return self._queues[name]
        
        queue = SimpleQueue(max_size)
        self._queues[name] = queue
        return queue
    
    def get_queue(self, name: str) -> Optional[SimpleQueue]:
        """Get an existing queue"""
        return self._queues.get(name)
    
    def create_async_queue(self, name: str, max_size: int = 1000) -> AsyncQueue:
        """Create a new async queue"""
        if name in self._async_queues:
            return self._async_queues[name]
        
        queue = AsyncQueue(name, max_size)
        self._async_queues[name] = queue
        return queue
    
    def create_processor(self, queue_name: str) -> Optional[MessageProcessor]:
        """Create a processor for a queue"""
        queue = self.get_queue(queue_name)
        if queue is None:
            return None
        
        processor = MessageProcessor(f"{queue_name}_processor", queue)
        self._processors[queue_name] = processor
        return processor
    
    def start_all(self) -> None:
        """Start all processors"""
        for processor in self._processors.values():
            processor.start()
    
    def stop_all(self) -> None:
        """Stop all processors"""
        for processor in self._processors.values():
            processor.stop()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get stats for all queues"""
        return {
            "queues": {name: q.stats for name, q in self._queues.items()},
            "async_queues": {name: {"size": q.size} for name, q in self._async_queues.items()},
            "dead_letter_size": DeadLetterQueue.get_global().size,
        }
