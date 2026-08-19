"""
LLM Client — unified interface for multiple providers.

Supports Qwen, DeepSeek (via OpenAI-compatible API), OpenAI, Anthropic, Gemini.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from app.core.config import LLMProvider

logger = logging.getLogger(__name__)


class LLMClient:
    """Unified LLM client for trade analysis."""

    def __init__(
        self,
        provider: LLMProvider,
        api_key: str,
        model: str,
        base_url: str = "",
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def analyze(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """
        Send prompts to LLM and get trade analysis.

        Returns dict with: action, confidence, reason, entry, sl, tp (optional)
        """
        if self.provider in (LLMProvider.QWEN, LLMProvider.DEEPSEEK, LLMProvider.OPENAI):
            return await self._analyze_openai_compatible(system_prompt, user_prompt)
        elif self.provider == LLMProvider.ANTHROPIC:
            return await self._analyze_anthropic(system_prompt, user_prompt)
        elif self.provider == LLMProvider.GEMINI:
            return await self._analyze_gemini(system_prompt, user_prompt)
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

    async def _analyze_openai_compatible(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """Use OpenAI-compatible API (works for Qwen, DeepSeek, OpenAI)."""
        from openai import AsyncOpenAI

        kwargs: Dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url

        client = AsyncOpenAI(**kwargs)

        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content or "{}"
        return json.loads(content)

    async def _analyze_anthropic(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """Use Anthropic Claude API."""
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=self.api_key)

        response = await client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        content = response.content[0].text
        # Try to extract JSON from response
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Try to find JSON block in response
            import re
            match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
            if match:
                return json.loads(match.group())
            return {"action": "HOLD", "confidence": 0.5, "reason": f"Parse error: {content[:200]}"}

    async def _analyze_gemini(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """Use Google Gemini API."""
        import google.generativeai as genai

        genai.configure(api_key=self.api_key)
        model = genai.GenerativeModel(self.model)

        full_prompt = f"{system_prompt}\n\n{user_prompt}"
        response = await model.generate_content_async(full_prompt)

        content = response.text
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            import re
            match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
            if match:
                return json.loads(match.group())
            return {"action": "HOLD", "confidence": 0.5, "reason": f"Parse error: {content[:200]}"}


# Provider defaults
PROVIDER_DEFAULTS = {
    LLMProvider.QWEN: {
        "model": "qwen-max",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    LLMProvider.DEEPSEEK: {
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
    },
    LLMProvider.OPENAI: {
        "model": "gpt-4o",
        "base_url": "",
    },
    LLMProvider.ANTHROPIC: {
        "model": "claude-3-5-sonnet-20241022",
        "base_url": "",
    },
    LLMProvider.GEMINI: {
        "model": "gemini-1.5-pro",
        "base_url": "",
    },
}


def create_client(
    provider: LLMProvider,
    api_key: str,
    model: str = "",
    base_url: str = "",
    temperature: float = 0.1,
    max_tokens: int = 2048,
) -> LLMClient:
    """Create an LLM client with provider-specific defaults."""
    defaults = PROVIDER_DEFAULTS.get(provider, {})
    return LLMClient(
        provider=provider,
        api_key=api_key,
        model=model or defaults.get("model", "gpt-4o"),
        base_url=base_url or defaults.get("base_url", ""),
        temperature=temperature,
        max_tokens=max_tokens,
    )
