import os
import hashlib
import json
from openai import OpenAI
from dotenv import load_dotenv
from app.db.redis_client import redis_client

load_dotenv()

# Local fallback cache variable for LLM completions
_local_llm_cache = {}

class LLMClient:
    def __init__(self, provider=None):
        self.provider = provider or os.getenv("LLM_PROVIDER", "openai")
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        self.model = os.getenv("LLM_MODEL", "gpt-4o")

        if self.provider == "openai":
            self.client = OpenAI(api_key=self.api_key)
        elif self.provider == "ollama":
            self.client = OpenAI(base_url=self.base_url, api_key="ollama") # Ollama uses OpenAI compatible API
        else:
            raise ValueError(f"Unsupported provider: {provider}")

    def chat(self, messages, temperature=0):
        """Chat completion call wrapped with dynamic Redis and local cache fallback."""
        # 1. Generate unique request key from model, message payload, and temperature
        msg_str = json.dumps(messages, sort_keys=True)
        key_content = f"{self.model}:{msg_str}:{temperature}"
        msg_hash = hashlib.sha256(key_content.encode('utf-8')).hexdigest()

        # 2. Redis Caching Path
        if redis_client.is_connected:
            cache_key = f"llm:chat:{msg_hash}"
            cached = redis_client.get(cache_key)
            if cached:
                # Return cached content directly in <1ms
                return cached
            
            content = self._call_completion_api(messages, temperature)
            # Store in Redis for 24 hours
            redis_client.setex(cache_key, 86400, content)
            return content

        # 3. Local Fallback Cache Path
        if msg_hash in _local_llm_cache:
            return _local_llm_cache[msg_hash]
            
        content = self._call_completion_api(messages, temperature)
        _local_llm_cache[msg_hash] = content
        return content

    def _call_completion_api(self, messages, temperature):
        """Direct call to the downstream OpenAI/Ollama completion endpoint."""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature
        )
        return response.choices[0].message.content

    def extract_json(self, prompt, system_prompt="You are a helpful assistant that returns only valid JSON."):
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        response = self.chat(messages)
        # Simple cleanup if the LLM adds markdown blocks
        if "```json" in response:
            response = response.split("```json")[1].split("```")[0].strip()
        elif "```" in response:
            response = response.split("```")[1].split("```")[0].strip()
        return response

if __name__ == "__main__":
    # Quick test if key is present
    try:
        client = LLMClient()
        print("LLM Client initialized.")
    except Exception as e:
        print(f"LLM Client init failed: {e}")
