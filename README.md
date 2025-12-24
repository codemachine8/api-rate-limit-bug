# UnfoldCI Flaky Test Evaluation Repository v2

A comprehensive evaluation repository for testing AI-powered flaky test detection and automated fixing capabilities. This repository contains **intentionally flaky tests** across 7 categories with deep import chains (5 levels) to simulate real-world codebases.

## 🎯 Purpose

This repository is designed to evaluate:
1. **Detection accuracy** - Can the system identify which tests are flaky?
2. **Root cause analysis** - Can the AI correctly categorize the flakiness?
3. **Fix quality** - Are the generated fixes correct and comprehensive?
4. **Context gathering** - Can the system pull relevant code from imports to understand the problem?
  
## 📁 Repository Structure

```
flaky-eval-v2/
├── src/
│   ├── core/           # Level 1: Base classes, config, utilities
│   │   ├── base.py     # BaseComponent, CircuitBreaker, RetryPolicy, EventEmitter
│   │   └── config.py   # Config, FeatureFlags, Environment, Secrets
│   │
│   ├── services/       # Level 2: Business services (imports from L1)
│   │   ├── cache.py    # LRUCache, AsyncCache, DistributedCacheSimulator
│   │   └── queue.py    # SimpleQueue, AsyncQueue, DeadLetterQueue
│   │
│   ├── handlers/       # Level 3: Request/event handlers (imports from L1, L2)
│   │   ├── api_handler.py    # ApiHandler, RateLimiter, WebSocketHandler
│   │   └── event_handler.py  # EventBus, EventProcessor, SagaOrchestrator
│   │
│   ├── integrations/   # Level 4: External integrations (imports from L1, L2, L3)
│   │   ├── external_api.py   # ExternalApiClient, WebhookDispatcher
│   │   └── database_client.py # ConnectionPool, Repository, UnitOfWork
│   │
│   └── app/            # Level 5: Main application (imports from all levels)
│       └── main.py     # Application, HealthCheck, MetricsCollector
│
└── tests/
    ├── test_timing_dependency.py     # ~15 tests
    ├── test_async_race.py            # ~15 tests
    ├── test_state_pollution.py       # ~25 tests
    ├── test_isolation.py             # ~15 tests
    ├── test_resource_cleanup.py      # ~20 tests
    ├── test_environment_dependent.py # ~15 tests
    └── test_non_deterministic.py     # ~15 tests
```

## 🏷️ Flaky Test Categories

### 1. Timing Dependency (`timing_dependency`)
**Expected Fix Rate: 70-80%**

Tests that fail due to:
- Hardcoded timeouts that don't account for system variability
- Sleep durations that race with async operations
- Latency assertions that are too strict

**Example Fix Pattern:**
```python
# Before (flaky)
assert response_time < 50  # Too tight

# After (fixed)
assert response_time < 200  # Allow for variability
```

### 2. Async Race Conditions (`async_race_condition`)
**Expected Fix Rate: 55-65%**

Tests that fail due to:
- Missing locks on shared state
- Non-atomic operations in concurrent code
- Race conditions between check and update

**Example Fix Pattern:**
```python
# Before (flaky)
counter["value"] += 1  # Race condition

# After (fixed)
async with lock:
    counter["value"] += 1
```

### 3. State Pollution (`state_pollution`)
**Expected Fix Rate: 40-50%**

Tests that fail due to:
- Class-level variables not reset between tests
- Singleton instances persisting across tests
- Global state modifications

**Example Fix Pattern:**
```python
# Before (flaky - no cleanup)
def test_something():
    Config.get_instance().set("key", "value")

# After (fixed - proper fixture)
@pytest.fixture(autouse=True)
def reset_config():
    yield
    Config.reset_instance()
```

### 4. Test Isolation (`test_isolation`)
**Expected Fix Rate: 45-55%**

Tests that fail when run in different order:
- Dependencies on other tests running first
- Shared file system state
- Database state assumptions

**Example Fix Pattern:**
```python
# Before (flaky - depends on order)
def test_second():
    assert get_data() == expected  # Assumes test_first ran

# After (fixed - self-contained)
def test_second():
    setup_test_data()  # Own setup
    assert get_data() == expected
```

### 5. Resource Cleanup (`resource_cleanup`)
**Expected Fix Rate: 50-60%**

Tests that fail due to:
- File handles not closed
- Database connections leaked
- Threads/tasks not properly terminated

**Example Fix Pattern:**
```python
# Before (flaky - resource leak)
def test_file_operation():
    f = open("test.txt")
    # No close!

# After (fixed)
def test_file_operation():
    with open("test.txt") as f:
        # Automatically closed
```

### 6. Environment Dependent (`environment_dependent`)
**Expected Fix Rate: 35-45%**

Tests that fail due to:
- Missing environment variables
- Platform-specific behavior
- Timezone/locale differences

**Example Fix Pattern:**
```python
# Before (flaky - assumes env var exists)
def test_api_key():
    key = os.environ["API_KEY"]

# After (fixed)
def test_api_key():
    key = os.environ.get("API_KEY", "test_key")
```

### 7. Non-Deterministic (`non_deterministic`)
**Expected Fix Rate: 30-40%**

Tests that fail due to:
- Random number generation without seeds
- Unordered collection comparisons
- Timestamp-based assertions

**Example Fix Pattern:**
```python
# Before (flaky - random without seed)
def test_random():
    assert random.choice([1,2,3]) == 2

# After (fixed)
def test_random():
    random.seed(42)  # Deterministic
    assert random.choice([1,2,3]) == 2
```

## 📊 Expected Results Summary

| Category | Test Count | Expected Detection | Expected Fix Rate |
|----------|------------|-------------------|-------------------|
| timing_dependency | ~15 | 95% | 70-80% |
| async_race_condition | ~15 | 85% | 55-65% |
| state_pollution | ~25 | 90% | 40-50% |
| test_isolation | ~15 | 80% | 45-55% |
| resource_cleanup | ~20 | 85% | 50-60% |
| environment_dependent | ~15 | 75% | 35-45% |
| non_deterministic | ~15 | 90% | 30-40% |
| **Total** | **~120** | **~85%** | **~50%** |

## 🔍 Import Depth Testing

Each test file imports from multiple levels to test the AI's ability to gather context:

```python
# Example from test_timing_dependency.py
from src.core.base import CircuitBreaker           # Level 1
from src.services.cache import AsyncCache          # Level 2
from src.handlers.api_handler import ApiHandler    # Level 3
from src.integrations.external_api import Client   # Level 4
```

The AI must:
1. Identify the failing test
2. Follow imports to find relevant source code
3. Understand how classes interact across levels
4. Generate fixes that account for the full context

## 🏃 Running Tests

```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests (many will fail - they're intentionally flaky!)
pytest tests/ -v

# Run with randomization (reveals more flakiness)
pytest tests/ --randomly-seed=random -v

# Run specific category
pytest tests/test_timing_dependency.py -v

# Run with JUnit output (for CI integration)
pytest tests/ --junitxml=test-results/junit.xml
```

## 📈 Evaluation Scoring

Each generated fix should be scored on a 5-point scale:

| Score | Description |
|-------|-------------|
| 5 | Perfect fix - addresses root cause correctly |
| 4 | Good fix - works but could be improved |
| 3 | Partial fix - reduces flakiness but doesn't eliminate |
| 2 | Minimal fix - only masks the symptom |
| 1 | Incorrect fix - doesn't address the issue |
| 0 | No fix generated or breaks tests |

## 🔧 Configuration

The repository uses these key configuration files:

- `pytest.ini` - Pytest configuration with asyncio mode
- `requirements.txt` - Python dependencies
- `.github/workflows/unfoldci-test.yml` - CI workflow with 10 parallel runs

## 📝 License

MIT License - Use freely for evaluation purposes.
