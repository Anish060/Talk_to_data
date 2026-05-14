import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

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
