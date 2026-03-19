"""AI provider abstraction layer.

Provides a unified interface for multiple AI providers (Anthropic, OpenAI, Ollama).
"""

import requests
from flask import current_app


class AIServiceError(Exception):
    """Raised when an AI provider request fails."""
    pass


def get_ai_response(prompt: str, system: str = None) -> str:
    """Get a response from the configured AI provider.

    Args:
        prompt: The user prompt to send to the AI.
        system: Optional system prompt for context/instructions.

    Returns:
        The AI's response text.

    Raises:
        AIServiceError: If the provider is unsupported or the request fails.
    """
    config = current_app.config
    provider = config.get("AI_PROVIDER", "").lower()
    api_key = config.get("AI_API_KEY")
    model = config.get("AI_MODEL")
    base_url = config.get("AI_BASE_URL")

    if provider == "anthropic":
        return _call_anthropic(api_key, model, prompt, system)
    elif provider == "openai":
        return _call_openai(api_key, model, base_url, prompt, system)
    elif provider == "openai-compatible":
        if not base_url:
            raise AIServiceError("AI_BASE_URL is required for openai-compatible provider")
        return _call_openai(api_key, model, base_url, prompt, system)
    elif provider == "ollama":
        return _call_ollama(base_url, model, prompt, system)
    else:
        raise AIServiceError(f"Unsupported AI provider: {provider}")


def _call_anthropic(api_key: str, model: str, prompt: str, system: str = None) -> str:
    """Call the Anthropic API."""
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        kwargs = {
            "model": model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system is not None:
            kwargs["system"] = system

        response = client.messages.create(**kwargs)
        return response.content[0].text
    except Exception as e:
        raise AIServiceError(str(e))


def _call_openai(api_key: str, model: str, base_url: str, prompt: str, system: str = None) -> str:
    """Call the OpenAI API (or compatible endpoint)."""
    try:
        import openai

        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url

        client = openai.OpenAI(**kwargs)

        messages = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        return response.choices[0].message.content
    except Exception as e:
        raise AIServiceError(str(e))


def _call_ollama(base_url: str, model: str, prompt: str, system: str = None) -> str:
    """Call the Ollama API."""
    if not base_url:
        raise AIServiceError("AI_BASE_URL is required for Ollama provider")

    try:
        url = base_url.rstrip("/") + "/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }
        if system is not None:
            payload["system"] = system

        response = requests.post(url, json=payload)
        response.raise_for_status()
        return response.json()["response"]
    except Exception as e:
        raise AIServiceError(str(e))
