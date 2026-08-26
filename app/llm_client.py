"""
LLM Client abstraction layer.
Supports: OpenAI, Qwen (DashScope), Claude (Anthropic), Gemini (Google), DeepSeek, and other OpenAI-compatible APIs.
"""

from __future__ import annotations

import os
import json
import logging
from typing import Optional, Dict, Any, List
from abc import ABC, abstractmethod
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

class LLMClient(ABC):
    """Abstract base class for LLM clients."""

    @abstractmethod
    async def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """Send chat completion request and return response text."""
        pass


class OpenAICompatibleClient(LLMClient):
    """Client for OpenAI and OpenAI-compatible APIs (Qwen, DeepSeek, etc.)."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        **kwargs
    ):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=api_key or "sk-placeholder", base_url=base_url)
        self.model = model
        self.default_kwargs = kwargs

    async def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        merged = {**self.default_kwargs, **kwargs}
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **merged
        )
        return response.choices[0].message.content


class AnthropicClient(LLMClient):
    """Client for Anthropic Claude API."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-5-sonnet-20241022",
        max_tokens: int = 4096,
        **kwargs
    ):
        from anthropic import AsyncAnthropic
        self.client = AsyncAnthropic(api_key=api_key or "sk-placeholder")
        self.model = model
        self.max_tokens = max_tokens
        self.default_kwargs = kwargs

    async def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        # Convert OpenAI format to Anthropic format
        system_msg = ""
        user_msgs = []
        for msg in messages:
            if msg["role"] == "system":
                system_msg = msg["content"]
            else:
                user_msgs.append(msg)

        merged = {**self.default_kwargs, **kwargs}

        response = await self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_msg if system_msg else None,
            messages=user_msgs,
            **merged
        )
        return response.content[0].text

class GeminiClient(LLMClient):
    """Client for Google Gemini API."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-1.5-flash",
        **kwargs
    ):
        import google.generativeai as genai
        if api_key:
            genai.configure(api_key=api_key)
        self.default_kwargs = kwargs

    async def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        # Convert OpenAI format to Gemini format
        # Gemini doesn't have separate system/user roles in the same way
        # We'll combine system message with first user message
        system_msg = ""
        user_content = []
        
        for msg in messages:
            if msg["role"] == "system":
                system_msg = msg["content"]
            elif msg["role"] == "user":
                user_content.append(msg["content"])
        
        # Combine system and user messages
        if system_msg:
            prompt = f"{system_msg}\n\n{user_content[-1]}"
        else:
            prompt = user_content[-1]
        
        merged = {**self.default_kwargs, **kwargs}
        response = await self.model.generate_content_async(prompt, **merged)
        return response.text


def create_llm_client(provider: Optional[str] = None, **kwargs) -> LLMClient:
    """
    Factory function to create LLM client based on provider.

    Provider can be set via:
    1. Explicit `provider` parameter
    2. LLM_PROVIDER env variable
    3. Auto-detect from base_url

    Supported providers:
    - "openai": OpenAI GPT models
    - "qwen" / "dashscope": Alibaba Qwen via DashScope
    - "deepseek": DeepSeek via OpenAI-compatible API
    - "anthropic" / "claude": Anthropic Claude models
    - "gemini" / "google": Google Gemini models
    - "openai_compatible": Generic OpenAI-compatible endpoint
    """
    provider = (provider or os.getenv("LLM_PROVIDER", "openai")).lower()

    if provider in ("openai",):
        return OpenAICompatibleClient(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            **kwargs
        )

    elif provider in ("qwen", "dashscope"):
        return OpenAICompatibleClient(
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            base_url=os.getenv("DASHSCOPE_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
            model=os.getenv("LLM_MODEL", "qwen3.7-flash"),
            **kwargs
        )

    elif provider in ("deepseek",):
        return OpenAICompatibleClient(
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            **kwargs
        )
    elif provider in ("anthropic", "claude"):
        return AnthropicClient(
            api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            model=os.getenv("LLM_MODEL", "claude-3-5-sonnet-20241022"),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            **kwargs
        )

    elif provider in ("gemini", "google"):
        return GeminiClient(
            api_key=os.getenv("GOOGLE_API_KEY", ""),
            model=os.getenv("LLM_MODEL", "gemini-1.5-flash"),
            **kwargs
        )

    elif provider in ("openai_compatible", "custom"):
        return OpenAICompatibleClient(
            api_key=os.getenv("CUSTOM_API_KEY", ""),
            base_url=os.getenv("CUSTOM_BASE_URL", ""),
            model=os.getenv("LLM_MODEL", ""),
            **kwargs
        )

    else:
        raise ValueError(f"Unknown LLM provider: {provider}")


class JSONResponseParser:
    """Parse JSON from LLM responses, handling markdown code blocks."""

    @staticmethod
    def parse(text: str) -> Dict[str, Any]:
        """Extract and parse JSON from response text."""
        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to extract from markdown code block
        import re
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find JSON object in text
        brace_start = text.find('{')
        brace_end = text.rfind('}')
        if brace_start != -1 and brace_end != -1:
            try:
                return json.loads(text[brace_start:brace_end + 1])
            except json.JSONDecodeError:
                pass

        raise ValueError(f"Could not parse JSON from response: {text[:200]}...")
