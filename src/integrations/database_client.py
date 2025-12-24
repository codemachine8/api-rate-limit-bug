"""
Level 4: Database Client
Provides database connectivity with connection pooling and transactions.
Imports from: handlers (Level 3), services (Level 2), core (Level 1)
"""
import asyncio
import threading
import time
import random
from typing import Any, Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
from enum import Enum
import uuid

# Level 1 imports
from ..core.base import BaseComponent, Result, CircuitBreaker, EventEmitter
from ..core.config import Config, Environment

# Level 2 imports
from ..services.cache import AsyncCache, LRUCache
from ..services.queue import Message, DeadLetterQueue

# Level 3 imports
from ..handlers.event_handler import Event, EventType, EventBus, EventStore


class IsolationLevel(Enum):
    READ_UNCOMMITTED = "READ_UNCOMMITTED"
    READ_COMMITTED = "READ_COMMITTED"
    REPEATABLE_READ = "REPEATABLE_READ"
    SERIALIZABLE = "SERIALIZABLE"


@dataclass
class QueryResult:
    """Database query result"""
    rows: List[Dict[str, Any]] = field(default_factory=list)
    affected_rows: int = 0
    last_insert_id: Optional[int] = None
    execution_time_ms: float = 0.0


class Connection:
    """Database connection wrapper"""
    
    _connection_count: int = 0  # Class-level - FLAKY: shared state
    
    def __init__(self, connection_id: str, pool: 'ConnectionPool'):
        self.id = connection_id
        self._pool = pool
        self._in_transaction = False
        self._transaction_start: Optional[float] = None
        self._queries_executed = 0
        self._created_at = time.time()
        self._last_used = self._created_at
        
        Connection._connection_count += 1  # FLAKY: race condition
    
    @classmethod
    def get_connection_count(cls) -> int:
        return cls._connection_count
    
    @classmethod
    def reset_count(cls):
        cls._connection_count = 0
    
    async def execute(self, query: str, params: Optional[Tuple] = None) -> QueryResult:
        """Execute a query"""
        start = time.time()
        self._queries_executed += 1
        self._last_used = time.time()
        
        # Simulate query execution
        await asyncio.sleep(random.uniform(0.001, 0.01))
        
        # FLAKY: Random slow queries
        if random.random() < 0.05:
            await asyncio.sleep(random.uniform(0.1, 0.5))
        
        # FLAKY: Random query failures
        if random.random() < 0.02:
            raise Exception("Query execution failed: deadlock detected")
        
        return QueryResult(
            rows=[{"id": 1, "data": "test"}],
            affected_rows=1,
            execution_time_ms=(time.time() - start) * 1000
        )
    
    async def begin_transaction(self, isolation_level: IsolationLevel = IsolationLevel.READ_COMMITTED) -> None:
        """Begin a transaction"""
        if self._in_transaction:
            raise Exception("Transaction already in progress")
        
        self._in_transaction = True
        self._transaction_start = time.time()
    
    async def commit(self) -> None:
        """Commit the transaction"""
        if not self._in_transaction:
            raise Exception("No transaction in progress")
        
        # FLAKY: Random commit failures
        if random.random() < 0.01:
            raise Exception("Commit failed: connection lost")
        
        self._in_transaction = False
        self._transaction_start = None
    
    async def rollback(self) -> None:
        """Rollback the transaction"""
        if not self._in_transaction:
            return  # FLAKY: Silent no-op instead of error
        
        self._in_transaction = False
        self._transaction_start = None
    
    async def release(self) -> None:
        """Release connection back to pool"""
        if self._in_transaction:
            await self.rollback()  # FLAKY: Implicit rollback
        await self._pool.release(self)
    
    @property
    def is_idle(self) -> bool:
        return not self._in_transaction
    
    @property
    def age_seconds(self) -> float:
        return time.time() - self._created_at


class ConnectionPool(BaseComponent):
    """Database connection pool"""
    
    _pools: Dict[str, 'ConnectionPool'] = {}  # Class-level - FLAKY
    
    def __init__(
        self,
        name: str,
        min_connections: int = 5,
        max_connections: int = 20,
        max_idle_time: float = 300.0
    ):
        super().__init__(name)
        self._min_connections = min_connections
        self._max_connections = max_connections
        self._max_idle_time = max_idle_time
        self._available: List[Connection] = []
        self._in_use: Dict[str, Connection] = {}
        self._lock = asyncio.Lock()
        self._total_connections_created = 0
        self._wait_count = 0  # FLAKY: not thread-safe
        
        ConnectionPool._pools[name] = self
    
    @classmethod
    def get_pool(cls, name: str) -> Optional['ConnectionPool']:
        return cls._pools.get(name)
    
    @classmethod
    def clear_pools(cls):
        cls._pools.clear()
    
    async def initialize(self) -> bool:
        """Initialize the pool with minimum connections"""
        if not super().initialize():
            return False
        
        for _ in range(self._min_connections):
            conn = await self._create_connection()
            self._available.append(conn)
        
        return True
    
    async def _create_connection(self) -> Connection:
        """Create a new connection"""
        self._total_connections_created += 1
        conn_id = f"conn_{self._total_connections_created}"
        
        # Simulate connection establishment
        await asyncio.sleep(random.uniform(0.01, 0.05))
        
        # FLAKY: Random connection failures
        if random.random() < 0.02:
            raise Exception("Failed to establish connection")
        
        return Connection(conn_id, self)
    
    async def acquire(self, timeout: float = 30.0) -> Connection:
        """Acquire a connection from the pool"""
        deadline = time.time() + timeout
        
        while time.time() < deadline:
            async with self._lock:
                # Try to get an available connection
                if self._available:
                    conn = self._available.pop()
                    self._in_use[conn.id] = conn
                    return conn
                
                # Create new connection if under limit
                total = len(self._available) + len(self._in_use)
                if total < self._max_connections:
                    conn = await self._create_connection()
                    self._in_use[conn.id] = conn
                    return conn
            
            # Wait and retry
            self._wait_count += 1  # FLAKY: race condition
            await asyncio.sleep(0.1)
        
        raise TimeoutError("Failed to acquire connection")
    
    async def release(self, connection: Connection) -> None:
        """Release a connection back to the pool"""
        async with self._lock:
            if connection.id in self._in_use:
                del self._in_use[connection.id]
                
                # Check if connection is still healthy
                if connection.age_seconds < self._max_idle_time:
                    self._available.append(connection)
                # else: connection discarded - FLAKY: may lose connections
    
    @asynccontextmanager
    async def connection(self):
        """Context manager for acquiring a connection"""
        conn = await self.acquire()
        try:
            yield conn
        finally:
            await conn.release()
    
    @property
    def available_count(self) -> int:
        return len(self._available)
    
    @property
    def in_use_count(self) -> int:
        return len(self._in_use)
    
    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "available": self.available_count,
            "in_use": self.in_use_count,
            "total_created": self._total_connections_created,
            "wait_count": self._wait_count,
        }


class Repository:
    """Base repository with common database operations"""
    
    def __init__(self, table_name: str, pool: ConnectionPool):
        self.table_name = table_name
        self._pool = pool
        self._cache = LRUCache(max_size=100)
        self._query_count = 0  # FLAKY: not thread-safe
    
    async def find_by_id(self, id: int, use_cache: bool = True) -> Optional[Dict]:
        """Find a record by ID"""
        cache_key = f"{self.table_name}:{id}"
        
        if use_cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
        
        self._query_count += 1  # FLAKY: race condition
        
        async with self._pool.connection() as conn:
            result = await conn.execute(
                f"SELECT * FROM {self.table_name} WHERE id = ?",
                (id,)
            )
            
            if result.rows:
                record = result.rows[0]
                self._cache.set(cache_key, record)
                return record
        
        return None
    
    async def find_all(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        """Find all records with pagination"""
        self._query_count += 1
        
        async with self._pool.connection() as conn:
            result = await conn.execute(
                f"SELECT * FROM {self.table_name} LIMIT ? OFFSET ?",
                (limit, offset)
            )
            return result.rows
    
    async def create(self, data: Dict) -> int:
        """Create a new record"""
        self._query_count += 1
        
        async with self._pool.connection() as conn:
            await conn.begin_transaction()
            try:
                result = await conn.execute(
                    f"INSERT INTO {self.table_name} ...",
                    tuple(data.values())
                )
                await conn.commit()
                
                # Emit event
                await EventBus.get_instance().publish(Event(
                    type=EventType.USER_CREATED,
                    payload={"table": self.table_name, "id": result.last_insert_id},
                ))
                
                return result.last_insert_id or 0
            except Exception:
                await conn.rollback()
                raise
    
    async def update(self, id: int, data: Dict) -> bool:
        """Update a record"""
        self._query_count += 1
        
        # Invalidate cache - FLAKY: race condition with concurrent reads
        cache_key = f"{self.table_name}:{id}"
        self._cache.delete(cache_key)
        
        async with self._pool.connection() as conn:
            result = await conn.execute(
                f"UPDATE {self.table_name} SET ... WHERE id = ?",
                (*data.values(), id)
            )
            return result.affected_rows > 0
    
    async def delete(self, id: int) -> bool:
        """Delete a record"""
        self._query_count += 1
        
        # Invalidate cache
        cache_key = f"{self.table_name}:{id}"
        self._cache.delete(cache_key)
        
        async with self._pool.connection() as conn:
            result = await conn.execute(
                f"DELETE FROM {self.table_name} WHERE id = ?",
                (id,)
            )
            return result.affected_rows > 0
    
    def clear_cache(self) -> None:
        """Clear the repository cache"""
        self._cache.clear()


class UnitOfWork:
    """Unit of Work pattern for transaction management"""
    
    _active_units: Dict[str, 'UnitOfWork'] = {}  # Class-level - FLAKY
    
    def __init__(self, pool: ConnectionPool):
        self.id = str(uuid.uuid4())
        self._pool = pool
        self._connection: Optional[Connection] = None
        self._committed = False
        self._rolled_back = False
        self._operations: List[Dict] = []  # FLAKY: unbounded growth
        
        UnitOfWork._active_units[self.id] = self
    
    @classmethod
    def get_active(cls, unit_id: str) -> Optional['UnitOfWork']:
        return cls._active_units.get(unit_id)
    
    @classmethod
    def get_active_count(cls) -> int:
        return len(cls._active_units)
    
    @classmethod
    def clear_active(cls):
        cls._active_units.clear()
    
    async def begin(self, isolation_level: IsolationLevel = IsolationLevel.READ_COMMITTED) -> None:
        """Begin the unit of work"""
        self._connection = await self._pool.acquire()
        await self._connection.begin_transaction(isolation_level)
    
    async def execute(self, query: str, params: Optional[Tuple] = None) -> QueryResult:
        """Execute a query within the unit of work"""
        if self._connection is None:
            raise Exception("Unit of work not started")
        
        if self._committed or self._rolled_back:
            raise Exception("Unit of work already completed")
        
        self._operations.append({"query": query, "params": params})
        return await self._connection.execute(query, params)
    
    async def commit(self) -> None:
        """Commit the unit of work"""
        if self._connection is None:
            raise Exception("Unit of work not started")
        
        await self._connection.commit()
        self._committed = True
        await self._cleanup()
    
    async def rollback(self) -> None:
        """Rollback the unit of work"""
        if self._connection is None:
            return
        
        await self._connection.rollback()
        self._rolled_back = True
        await self._cleanup()
    
    async def _cleanup(self) -> None:
        """Cleanup resources"""
        if self._connection:
            await self._connection.release()
            self._connection = None
        
        if self.id in UnitOfWork._active_units:
            del UnitOfWork._active_units[self.id]
    
    async def __aenter__(self):
        await self.begin()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            await self.rollback()
        elif not self._committed:
            await self.commit()


class DatabaseClient(BaseComponent):
    """High-level database client"""
    
    _instance: Optional['DatabaseClient'] = None  # Singleton - FLAKY
    _query_log: List[Dict] = []  # Class-level - FLAKY: unbounded, shared
    
    def __init__(self, pool_name: str = "default"):
        super().__init__(f"db_client_{pool_name}")
        self._pool: Optional[ConnectionPool] = None
        self._repositories: Dict[str, Repository] = {}
        self._event_bus = EventBus.get_instance()
    
    @classmethod
    def get_instance(cls) -> 'DatabaseClient':
        if cls._instance is None:
            cls._instance = DatabaseClient()
        return cls._instance
    
    @classmethod
    def reset_instance(cls):
        cls._instance = None
        cls._query_log.clear()
    
    async def connect(
        self,
        min_connections: int = 5,
        max_connections: int = 20
    ) -> bool:
        """Connect to the database"""
        self._pool = ConnectionPool(
            self.name,
            min_connections=min_connections,
            max_connections=max_connections
        )
        return await self._pool.initialize()
    
    def get_repository(self, table_name: str) -> Repository:
        """Get or create a repository for a table"""
        if self._pool is None:
            raise Exception("Database not connected")
        
        if table_name not in self._repositories:
            self._repositories[table_name] = Repository(table_name, self._pool)
        
        return self._repositories[table_name]
    
    def create_unit_of_work(self) -> UnitOfWork:
        """Create a new unit of work"""
        if self._pool is None:
            raise Exception("Database not connected")
        return UnitOfWork(self._pool)
    
    async def execute_raw(self, query: str, params: Optional[Tuple] = None) -> QueryResult:
        """Execute a raw query"""
        if self._pool is None:
            raise Exception("Database not connected")
        
        # Log query - FLAKY: unbounded growth
        DatabaseClient._query_log.append({
            "query": query,
            "params": params,
            "timestamp": time.time(),
        })
        
        async with self._pool.connection() as conn:
            return await conn.execute(query, params)
    
    @property
    def pool_stats(self) -> Dict[str, Any]:
        if self._pool is None:
            return {}
        return self._pool.stats
    
    @classmethod
    def get_query_log(cls, limit: int = 100) -> List[Dict]:
        return cls._query_log[-limit:]
