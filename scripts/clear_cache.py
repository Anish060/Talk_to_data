import os
import sys

# Add project root to PYTHONPATH
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(project_root)

from app.db.redis_client import redis_client

def clear_cache():
    if not redis_client.is_connected:
        print("Redis is not connected or offline. No cache to clear.")
        return
        
    print("Clearing Redis Cache...")
    
    # 1. Clear catalogue metadata cache
    redis_client.clear_pattern("catalogue:*")
    print("✓ Cleared 'catalogue:*' keys")
    
    # 2. Clear verified query results cache
    redis_client.clear_pattern("verified_query:*")
    print("✓ Cleared 'verified_query:*' keys")
    
    print("\nRedis Cache fully cleared successfully!")

if __name__ == "__main__":
    clear_cache()
