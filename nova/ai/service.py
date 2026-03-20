"""AI provider abstraction layer.

Provides a unified interface for multiple AI providers (Anthropic, OpenAI, Ollama).
"""

import requests
from flask import current_app


class AIServiceError(Exception):
    """Raised when an AI provider request fails."""
    pass


def get_ai_response(prompt: str, system: str = None, stream: bool = False) -> str | object:
    """Get a response from the configured AI provider.

    Args:
        prompt: The user prompt to send to the AI.
        system: Optional system prompt for context/instructions.
        stream: If True, returns a generator yielding text chunks.

    Returns:
        If stream=False: The AI's response text.
        If stream=True: A generator yielding text chunks.

    Raises:
        AIServiceError: If the provider is unsupported or the request fails.
    """
    config = current_app.config
    provider = config.get("AI_PROVIDER", "").lower()
    api_key = config.get("AI_API_KEY")
    model = config.get("AI_MODEL")
    base_url = config.get("AI_BASE_URL")

    if provider == "anthropic":
        if stream:
            return _stream_anthropic(api_key, model, prompt, system)
        return _call_anthropic(api_key, model, prompt, system)
    elif provider == "openai":
        if stream:
            return _stream_openai(api_key, model, base_url, prompt, system)
        return _call_openai(api_key, model, base_url, prompt, system)
    elif provider == "openai-compatible":
        if not base_url:
            raise AIServiceError("AI_BASE_URL is required for openai-compatible provider")
        if stream:
            return _stream_openai(api_key, model, base_url, prompt, system)
        return _call_openai(api_key, model, base_url, prompt, system)
    elif provider == "ollama":
        if stream:
            return _stream_ollama(base_url, model, prompt, system)
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


def _stream_anthropic(api_key: str, model: str, prompt: str, system: str = None):
    """Stream response from the Anthropic API, yielding text chunks."""
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        kwargs = {
            "model": model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }
        if system is not None:
            kwargs["system"] = system

        with client.messages.create(**kwargs) as stream:
            for event in stream:
                if event.type == "content_block_delta":
                    if hasattr(event.delta, "text"):
                        yield event.delta.text
    except Exception as e:
        raise AIServiceError(str(e))


def _stream_openai(api_key: str, model: str, base_url: str, prompt: str, system: str = None):
    """Stream response from the OpenAI API (or compatible endpoint), yielding text chunks."""
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

        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except Exception as e:
        raise AIServiceError(str(e))


def _stream_ollama(base_url: str, model: str, prompt: str, system: str = None):
    """Stream response from the Ollama API, yielding text chunks."""
    if not base_url:
        raise AIServiceError("AI_BASE_URL is required for Ollama provider")

    try:
        url = base_url.rstrip("/") + "/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": True,
        }
        if system is not None:
            payload["system"] = system

        response = requests.post(url, json=payload, stream=True)
        response.raise_for_status()

        for line in response.iter_lines():
            if line:
                import json
                chunk = json.loads(line)
                if "response" in chunk:
                    yield chunk["response"]
    except Exception as e:
        raise AIServiceError(str(e))
