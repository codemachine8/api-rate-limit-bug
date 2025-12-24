"""
Level 3: Event Handler
Handles asynchronous events with queue integration.
Imports from: services.queue (Level 2), core (Level 1)
"""
import asyncio
import threading
import time
import random
from typing import Any, Optional, Dict, List, Callable, Set
from dataclasses import dataclass, field
from enum import Enum
import uuid

# Level 1 imports
from ..core.base import BaseComponent, Result, EventEmitter, CircuitBreaker
from ..core.config import Config, FeatureFlags

# Level 2 imports
from ..services.queue import Message, AsyncQueue, QueueManager, DeadLetterQueue


class EventType(Enum):
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_DELETED = "user.deleted"
    ORDER_PLACED = "order.placed"
    ORDER_COMPLETED = "order.completed"
    ORDER_CANCELLED = "order.cancelled"
    PAYMENT_RECEIVED = "payment.received"
    PAYMENT_FAILED = "payment.failed"
    NOTIFICATION_SENT = "notification.sent"
    SYSTEM_ERROR = "system.error"


@dataclass
class Event:
    """Domain event representation"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: EventType = EventType.SYSTEM_ERROR
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    source: str = "unknown"
    correlation_id: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3


class EventSubscription:
    """Represents a subscription to an event type"""
    
    def __init__(
        self,
        event_type: EventType,
        handler: Callable,
        filter_fn: Optional[Callable[[Event], bool]] = None
    ):
        self.id = str(uuid.uuid4())
        self.event_type = event_type
        self.handler = handler
        self.filter_fn = filter_fn
        self.created_at = time.time()
        self.invocation_count = 0
        self.error_count = 0
    
    async def invoke(self, event: Event) -> bool:
        """Invoke the handler for an event"""
        if self.filter_fn and not self.filter_fn(event):
            return True  # Filtered out, consider success
        
        try:
            self.invocation_count += 1
            if asyncio.iscoroutinefunction(self.handler):
                await self.handler(event)
            else:
                self.handler(event)
            return True
        except Exception:
            self.error_count += 1
            return False


class EventBus:
    """Central event bus for pub/sub"""
    
    _instance: Optional['EventBus'] = None  # Singleton - FLAKY
    _total_events: int = 0  # Class-level - FLAKY
    
    def __init__(self):
        self._subscriptions: Dict[EventType, List[EventSubscription]] = {}
        self._pending_events: List[Event] = []  # FLAKY: unbounded growth
        self._lock = asyncio.Lock()
        self._processing = False
    
    @classmethod
    def get_instance(cls) -> 'EventBus':
        if cls._instance is None:
            cls._instance = EventBus()
        return cls._instance
    
    @classmethod
    def reset_instance(cls):
        cls._instance = None
        cls._total_events = 0
    
    def subscribe(
        self,
        event_type: EventType,
        handler: Callable,
        filter_fn: Optional[Callable[[Event], bool]] = None
    ) -> str:
        """Subscribe to an event type"""
        subscription = EventSubscription(event_type, handler, filter_fn)
        
        if event_type not in self._subscriptions:
            self._subscriptions[event_type] = []
        self._subscriptions[event_type].append(subscription)
        
        return subscription.id
    
    def unsubscribe(self, subscription_id: str) -> bool:
        """Remove a subscription"""
        for event_type, subs in self._subscriptions.items():
            for sub in subs:
                if sub.id == subscription_id:
                    subs.remove(sub)
                    return True
        return False
    
    async def publish(self, event: Event) -> int:
        """Publish an event and return number of handlers invoked"""
        EventBus._total_events += 1  # FLAKY: race condition
        
        subscriptions = self._subscriptions.get(event.type, [])
        if not subscriptions:
            return 0
        
        # FLAKY: No ordering guarantee
        invoked = 0
        for sub in subscriptions:
            success = await sub.invoke(event)
            if success:
                invoked += 1
        
        return invoked
    
    async def publish_batch(self, events: List[Event]) -> Dict[str, int]:
        """Publish multiple events"""
        results = {}
        
        # FLAKY: Concurrent publishing can cause ordering issues
        tasks = [self.publish(event) for event in events]
        counts = await asyncio.gather(*tasks)
        
        for event, count in zip(events, counts):
            results[event.id] = count
        
        return results
    
    def get_subscription_count(self, event_type: EventType) -> int:
        """Get number of subscriptions for an event type"""
        return len(self._subscriptions.get(event_type, []))
    
    @property
    def total_subscriptions(self) -> int:
        return sum(len(subs) for subs in self._subscriptions.values())
    
    def clear(self) -> None:
        """Clear all subscriptions"""
        self._subscriptions.clear()


class EventProcessor(BaseComponent):
    """Process events from queue"""
    
    _processors: Dict[str, 'EventProcessor'] = {}  # Class-level - FLAKY
    
    def __init__(self, name: str, queue: AsyncQueue):
        super().__init__(name)
        self._queue = queue
        self._event_bus = EventBus.get_instance()
        self._running = False
        self._processed_count = 0
        self._error_count = 0
        self._task: Optional[asyncio.Task] = None
        
        EventProcessor._processors[name] = self
    
    @classmethod
    def get_processor(cls, name: str) -> Optional['EventProcessor']:
        return cls._processors.get(name)
    
    @classmethod
    def clear_processors(cls):
        cls._processors.clear()
    
    async def start(self) -> None:
        """Start processing events"""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._process_loop())
    
    async def stop(self, timeout: float = 5.0) -> None:
        """Stop processing"""
        self._running = False
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=timeout)
            except asyncio.TimeoutError:
                self._task.cancel()
                # FLAKY: Task might not cancel cleanly
            self._task = None
    
    async def _process_loop(self) -> None:
        """Main processing loop"""
        while self._running:
            try:
                message = await self._queue.get(timeout=0.1)
                if message is None:
                    continue
                
                event = self._deserialize_event(message)
                if event:
                    await self._process_event(event, message.id)
                
            except Exception as e:
                self._error_count += 1
                # FLAKY: Error not properly handled
    
    def _deserialize_event(self, message: Message) -> Optional[Event]:
        """Convert message to event"""
        try:
            payload = message.payload
            if isinstance(payload, Event):
                return payload
            elif isinstance(payload, dict):
                return Event(
                    type=EventType(payload.get("type", "system.error")),
                    payload=payload.get("data", {}),
                    source=payload.get("source", "unknown"),
                )
            return None
        except Exception:
            return None
    
    async def _process_event(self, event: Event, message_id: str) -> None:
        """Process a single event"""
        try:
            handlers_invoked = await self._event_bus.publish(event)
            
            if handlers_invoked > 0:
                await self._queue.ack(message_id)
                self._processed_count += 1
            else:
                # No handlers, might be a configuration issue
                await self._queue.nack(message_id, requeue=False)
                
        except Exception as e:
            self._error_count += 1
            
            if event.retry_count < event.max_retries:
                event.retry_count += 1
                await self._queue.nack(message_id, requeue=True)
            else:
                await self._queue.nack(message_id, requeue=False)
                DeadLetterQueue.get_global().add(
                    Message(payload=event),
                    str(e)
                )
    
    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "processed": self._processed_count,
            "errors": self._error_count,
        }


class SagaOrchestrator:
    """Orchestrate multi-step event-driven workflows"""
    
    _sagas: Dict[str, Dict[str, Any]] = {}  # Class-level - FLAKY
    
    def __init__(self, name: str):
        self.name = name
        self._steps: List[Dict[str, Any]] = []
        self._compensations: List[Callable] = []
        self._event_bus = EventBus.get_instance()
    
    def add_step(
        self,
        name: str,
        execute: Callable,
        compensate: Optional[Callable] = None
    ) -> 'SagaOrchestrator':
        """Add a step to the saga"""
        self._steps.append({
            "name": name,
            "execute": execute,
            "compensate": compensate,
        })
        return self
    
    async def execute(self, context: Dict[str, Any]) -> Result:
        """Execute the saga"""
        saga_id = str(uuid.uuid4())
        SagaOrchestrator._sagas[saga_id] = {
            "status": "running",
            "completed_steps": [],
            "context": context,
        }
        
        completed = []
        
        try:
            for step in self._steps:
                # Execute step
                if asyncio.iscoroutinefunction(step["execute"]):
                    result = await step["execute"](context)
                else:
                    result = step["execute"](context)
                
                if not result:
                    raise Exception(f"Step {step['name']} failed")
                
                completed.append(step)
                SagaOrchestrator._sagas[saga_id]["completed_steps"].append(step["name"])
                
                # FLAKY: No timeout between steps
                await asyncio.sleep(0)  # Yield control
            
            SagaOrchestrator._sagas[saga_id]["status"] = "completed"
            return Result(success=True, data={"saga_id": saga_id})
            
        except Exception as e:
            # Compensate in reverse order
            SagaOrchestrator._sagas[saga_id]["status"] = "compensating"
            
            for step in reversed(completed):
                if step["compensate"]:
                    try:
                        if asyncio.iscoroutinefunction(step["compensate"]):
                            await step["compensate"](context)
                        else:
                            step["compensate"](context)
                    except Exception:
                        # FLAKY: Compensation failure not handled properly
                        pass
            
            SagaOrchestrator._sagas[saga_id]["status"] = "compensated"
            return Result(success=False, error=str(e))
    
    @classmethod
    def get_saga_status(cls, saga_id: str) -> Optional[Dict[str, Any]]:
        return cls._sagas.get(saga_id)
    
    @classmethod
    def clear_sagas(cls):
        cls._sagas.clear()


class EventStore:
    """Simple event store for event sourcing"""
    
    _events: List[Event] = []  # Class-level - FLAKY: shared state
    _snapshots: Dict[str, Dict[str, Any]] = {}  # Class-level
    
    @classmethod
    def append(cls, event: Event) -> None:
        """Append an event to the store"""
        cls._events.append(event)
    
    @classmethod
    def get_events(
        cls,
        event_type: Optional[EventType] = None,
        since: Optional[float] = None,
        limit: int = 100
    ) -> List[Event]:
        """Get events with optional filtering"""
        events = cls._events
        
        if event_type:
            events = [e for e in events if e.type == event_type]
        
        if since:
            events = [e for e in events if e.timestamp > since]
        
        return events[-limit:]
    
    @classmethod
    def get_by_correlation_id(cls, correlation_id: str) -> List[Event]:
        """Get all events with a correlation ID"""
        return [e for e in cls._events if e.correlation_id == correlation_id]
    
    @classmethod
    def save_snapshot(cls, aggregate_id: str, state: Dict[str, Any]) -> None:
        """Save an aggregate snapshot"""
        cls._snapshots[aggregate_id] = {
            "state": state,
            "timestamp": time.time(),
        }
    
    @classmethod
    def get_snapshot(cls, aggregate_id: str) -> Optional[Dict[str, Any]]:
        """Get an aggregate snapshot"""
        return cls._snapshots.get(aggregate_id)
    
    @classmethod
    def clear(cls) -> None:
        """Clear all events and snapshots"""
        cls._events.clear()
        cls._snapshots.clear()
    
    @classmethod
    def count(cls) -> int:
        return len(cls._events)
