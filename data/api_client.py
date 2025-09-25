"""
Resilient API Client - Exponential backoff and caching for external APIs

This module provides a robust API client with:
- Exponential backoff retry logic for transient failures
- In-memory caching with TTL to reduce API load
- Graceful handling of network timeouts and errors
- Configurable retry parameters for different use cases

Used throughout the trading system for:
- Price data fetching with fallbacks
- Liquidity and volume data with resilience
- Token information with caching
- Any external API calls requiring reliability

Key Features:
- Automatic retry on RequestException with exponential backoff
- 60-second TTL caching to reduce redundant API calls
- Graceful failure handling without crashing the trading loop
- Configurable retry count and delay parameters
"""

import time
import random
import requests
from typing import Optional, Callable, Any
from functools import wraps

class APIClient:
    """Resilient API client with exponential backoff"""
    
    def __init__(self, base_delay=1.0, max_delay=60.0, max_retries=5):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.request_cache = {}
        self.cache_ttl = 5  # 5 seconds cache
    
    def exponential_backoff(self, attempt: int, base_delay: float = None) -> float:
        """Calculate exponential backoff delay with jitter"""
        if base_delay is None:
            base_delay = self.base_delay
        
        delay = min(base_delay * (2 ** attempt), self.max_delay)
        # Add jitter (±25%)
        jitter = delay * 0.25 * (random.random() * 2 - 1)
        return max(0.1, delay + jitter)
    
    def resilient_request(self, func: Callable, *args, cache_key: str = None, **kwargs) -> Optional[Any]:
        """Execute function with exponential backoff and caching"""
        
        # Check cache first
        if cache_key:
            current_time = time.time()
            if cache_key in self.request_cache:
                cached_data = self.request_cache[cache_key]
                if current_time - cached_data['timestamp'] < self.cache_ttl:
                    return cached_data['data']
        
        last_exception = None
        
        for attempt in range(self.max_retries):
            try:
                result = func(*args, **kwargs)
                
                # Cache successful result
                if cache_key and result is not None:
                    self.request_cache[cache_key] = {
                        'data': result,
                        'timestamp': time.time()
                    }
                
                return result
                
            except requests.exceptions.Timeout as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    delay = self.exponential_backoff(attempt)
                    from monitoring.logger import sniper_logger
                    sniper_logger.log_warning("API timeout", extra={
                        'attempt': attempt + 1, 'max_retries': self.max_retries, 'delay_seconds': delay
                    })
                    time.sleep(delay)
                    continue
                    
            except requests.exceptions.ConnectionError as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    delay = self.exponential_backoff(attempt, base_delay=2.0)  # Longer delay for connection errors
                    print(f"⚠️ Connection error, retrying in {delay:.1f}s (attempt {attempt + 1}/{self.max_retries})")
                    time.sleep(delay)
                    continue
                    
            except requests.exceptions.HTTPError as e:
                last_exception = e
                status_code = getattr(e.response, 'status_code', None)
                
                # Don't retry client errors (4xx)
                if status_code and 400 <= status_code < 500:
                    from monitoring.logger import sniper_logger
                    sniper_logger.log_error("Client error", extra={
                        'status_code': status_code, 'error': str(e)
                    })
                    break
                
                # Retry server errors (5xx) with backoff
                if attempt < self.max_retries - 1:
                    delay = self.exponential_backoff(attempt)
                    from monitoring.logger import sniper_logger
                    sniper_logger.log_warning("Server error", extra={
                        'status_code': status_code, 'attempt': attempt + 1, 'max_retries': self.max_retries, 'delay_seconds': delay
                    })
                    time.sleep(delay)
                    continue
                    
            except Exception as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    delay = self.exponential_backoff(attempt)
                    from monitoring.logger import sniper_logger
                    sniper_logger.log_warning("API error", extra={
                        'error_type': type(e).__name__, 'error': str(e), 'attempt': attempt + 1, 
                        'max_retries': self.max_retries, 'delay_seconds': delay
                    })
                    time.sleep(delay)
                    continue
        
        # All retries failed
        from monitoring.logger import sniper_logger
        sniper_logger.log_error("API call failed after retries", extra={
            'max_retries': self.max_retries, 'last_exception': str(last_exception)
        })
        
        # Return cached data if available as fallback
        if cache_key and cache_key in self.request_cache:
            cached_data = self.request_cache[cache_key]
            cache_age = time.time() - cached_data['timestamp']
            sniper_logger.log_info("Using stale cached data", extra={
                'cache_key': cache_key, 'cache_age_seconds': cache_age
            })
            return cached_data['data']
        
        return None

def resilient_api_call(cache_key: str = None, max_retries: int = 5):
    """Decorator for resilient API calls"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            client = APIClient(max_retries=max_retries)
            return client.resilient_request(func, *args, cache_key=cache_key, **kwargs)
        return wrapper
    return decorator

# Global API client instance
api_client = APIClient()
