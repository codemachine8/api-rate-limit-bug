"""
Level 1: Core configuration module
Manages application configuration with environment variable support.
"""
import os
import json
import random
from typing import Any, Optional, Dict
from pathlib import Path


class ConfigError(Exception):
    """Configuration error"""
    pass


class Config:
    """Application configuration manager"""
    
    _instance: Optional['Config'] = None  # Singleton - FLAKY: shared state
    _loaded: bool = False
    
    # Default configuration values
    DEFAULTS = {
        "debug": False,
        "log_level": "INFO",
        "max_connections": 100,
        "timeout_seconds": 30,
        "retry_attempts": 3,
        "cache_ttl": 300,
        "batch_size": 50,
        "rate_limit": 1000,
    }
    
    def __init__(self):
        self._values: Dict[str, Any] = {}
        self._env_prefix = "APP_"
        self._config_file: Optional[Path] = None
    
    @classmethod
    def get_instance(cls) -> 'Config':
        """Get singleton instance"""
        if cls._instance is None:
            cls._instance = Config()
        return cls._instance
    
    @classmethod
    def reset_instance(cls):
        """Reset singleton instance"""
        cls._instance = None
        cls._loaded = False
    
    def load_from_env(self) -> None:
        """Load configuration from environment variables"""
        for key, default in self.DEFAULTS.items():
            env_key = f"{self._env_prefix}{key.upper()}"
            env_value = os.environ.get(env_key)
            
            if env_value is not None:
                # Parse based on default type
                if isinstance(default, bool):
                    self._values[key] = env_value.lower() in ('true', '1', 'yes')
                elif isinstance(default, int):
                    self._values[key] = int(env_value)
                elif isinstance(default, float):
                    self._values[key] = float(env_value)
                else:
                    self._values[key] = env_value
            else:
                self._values[key] = default
        
        Config._loaded = True
    
    def load_from_file(self, path: Path) -> None:
        """Load configuration from JSON file"""
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}")
        
        with open(path) as f:
            file_config = json.load(f)
        
        for key, value in file_config.items():
            self._values[key] = value
        
        self._config_file = path
        Config._loaded = True
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value"""
        return self._values.get(key, self.DEFAULTS.get(key, default))
    
    def set(self, key: str, value: Any) -> None:
        """Set a configuration value"""
        self._values[key] = value
    
    def is_loaded(self) -> bool:
        """Check if configuration has been loaded"""
        return Config._loaded
    
    def to_dict(self) -> Dict[str, Any]:
        """Export configuration as dictionary"""
        result = dict(self.DEFAULTS)
        result.update(self._values)
        return result


class FeatureFlags:
    """Feature flag management"""
    
    _flags: Dict[str, bool] = {}  # Class-level - FLAKY: shared state
    _rollout_percentages: Dict[str, int] = {}  # Class-level
    
    @classmethod
    def enable(cls, flag: str) -> None:
        """Enable a feature flag"""
        cls._flags[flag] = True
    
    @classmethod
    def disable(cls, flag: str) -> None:
        """Disable a feature flag"""
        cls._flags[flag] = False
    
    @classmethod
    def is_enabled(cls, flag: str, default: bool = False) -> bool:
        """Check if a feature flag is enabled"""
        return cls._flags.get(flag, default)
    
    @classmethod
    def set_rollout(cls, flag: str, percentage: int) -> None:
        """Set rollout percentage (0-100)"""
        cls._rollout_percentages[flag] = max(0, min(100, percentage))
    
    @classmethod
    def is_enabled_for_user(cls, flag: str, user_id: str) -> bool:
        """Check if flag is enabled for a specific user (based on rollout)"""
        if flag not in cls._rollout_percentages:
            return cls.is_enabled(flag)
        
        # FLAKY: Hash-based rollout can be non-deterministic with certain user IDs
        percentage = cls._rollout_percentages[flag]
        user_hash = hash(user_id) % 100
        return user_hash < percentage
    
    @classmethod
    def clear_all(cls) -> None:
        """Clear all feature flags"""
        cls._flags.clear()
        cls._rollout_percentages.clear()


class Environment:
    """Environment detection and utilities"""
    
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TESTING = "testing"
    
    _current: Optional[str] = None  # Class-level - FLAKY: shared state
    
    @classmethod
    def detect(cls) -> str:
        """Detect current environment from env vars"""
        # FLAKY: Depends on environment variables being set
        env = os.environ.get("APP_ENV", os.environ.get("ENVIRONMENT", ""))
        
        if env.lower() in ("prod", "production"):
            cls._current = cls.PRODUCTION
        elif env.lower() in ("staging", "stage"):
            cls._current = cls.STAGING
        elif env.lower() in ("test", "testing"):
            cls._current = cls.TESTING
        else:
            cls._current = cls.DEVELOPMENT
        
        return cls._current
    
    @classmethod
    def get(cls) -> str:
        """Get current environment"""
        if cls._current is None:
            return cls.detect()
        return cls._current
    
    @classmethod
    def set(cls, env: str) -> None:
        """Manually set environment"""
        cls._current = env
    
    @classmethod
    def reset(cls) -> None:
        """Reset environment detection"""
        cls._current = None
    
    @classmethod
    def is_production(cls) -> bool:
        return cls.get() == cls.PRODUCTION
    
    @classmethod
    def is_development(cls) -> bool:
        return cls.get() == cls.DEVELOPMENT
    
    @classmethod
    def is_testing(cls) -> bool:
        return cls.get() == cls.TESTING


class Secrets:
    """Secrets management (mock implementation)"""
    
    _secrets: Dict[str, str] = {}  # Class-level - FLAKY: shared state
    _loaded: bool = False
    
    @classmethod
    def load(cls, source: str = "env") -> None:
        """Load secrets from a source"""
        if source == "env":
            # Load from environment variables with SECRET_ prefix
            for key, value in os.environ.items():
                if key.startswith("SECRET_"):
                    secret_name = key[7:].lower()  # Remove prefix
                    cls._secrets[secret_name] = value
        
        cls._loaded = True
    
    @classmethod
    def get(cls, name: str) -> Optional[str]:
        """Get a secret value"""
        if not cls._loaded:
            cls.load()
        return cls._secrets.get(name)
    
    @classmethod
    def set(cls, name: str, value: str) -> None:
        """Set a secret (for testing)"""
        cls._secrets[name] = value
    
    @classmethod
    def clear(cls) -> None:
        """Clear all secrets"""
        cls._secrets.clear()
        cls._loaded = False
