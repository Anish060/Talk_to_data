import os
# pyrefly: ignore [missing-import]
import redis

class RedisClient:
    def __init__(self):
        self.url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
        self.client = None
        self.is_connected = False
        try:
            # Connect with a short timeout to prevent slow bootups if offline
            self.client = redis.Redis.from_url(self.url, socket_timeout=1.5)
            # Ping to verify active connection
            self.client.ping()
            self.is_connected = True
            
            # Print a high-visibility terminal header indicating active cache
            print("\n" + "=" * 70)
            print("[CACHE SYSTEM] 🟢 Redis cache active (redis://127.0.0.1:6379/0)")
            print("=" * 70 + "\n")
        except Exception:
            self.is_connected = False
            # Print a high-visibility terminal header indicating in-memory fallback
            print("\n" + "=" * 70)
            print("[CACHE SYSTEM] ⚠️ Redis offline. Falling back to local in-memory caching!")
            print("=" * 70 + "\n")

    def get(self, key: str):
        if not self.is_connected or not self.client:
            return None
        try:
            val = self.client.get(key)
            if val is not None:
                # Decode bytes to string
                return val.decode("utf-8") if isinstance(val, bytes) else val
            return None
        except Exception:
            return None

    def setex(self, key: str, ttl: int, value: str):
        if not self.is_connected or not self.client:
            return
        try:
            self.client.setex(key, ttl, value)
        except Exception:
            pass

    def delete(self, key: str):
        if not self.is_connected or not self.client:
            return
        try:
            self.client.delete(key)
        except Exception:
            pass

    def clear_pattern(self, pattern: str):
        """Find keys matching a pattern and delete them all."""
        if not self.is_connected or not self.client:
            return
        try:
            keys = self.client.keys(pattern)
            if keys:
                self.client.delete(*keys)
        except Exception:
            pass

# Create a single shared connection instance for the entire lifecycle
redis_client = RedisClient()
