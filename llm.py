"""Multi-provider LLM layer: Gemini, Groq and OpenRouter behind one function.

Every task has an ordered chain of models across providers. If a model fails
(rate limit, overload, timeout, cut-off or unusable output) the next one in the
chain takes over, and the failed model is put on a short cooldown so parallel
jobs skip it instead of hitting the same wall.

All three providers speak the OpenAI chat-completions format, so one small
HTTP client covers them.
"""

import base64
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

logger = logging.getLogger("naturalsketch.llm")


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_env: str
    headers: dict = field(default_factory=dict)

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.key_env)


PROVIDERS = {
    "gemini": Provider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "groq": Provider("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": Provider(
        "openrouter",
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        {"HTTP-Referer": os.environ.get("NS_APP_ORIGIN", "http://localhost:8000"), "X-Title": "NaturalSketch"},
    ),
}


@dataclass(frozen=True)
class Model:
    provider: str
    name: str
    vision: bool = False
    max_output: int = 16000
    params: tuple = ()  # extra request fields as (key, value) pairs

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.name}"


GEMINI_FLASH = Model("gemini", "gemini-3.5-flash", True, 32000, (("reasoning_effort", "low"),))
GEMINI_LATEST = Model("gemini", "gemini-flash-latest", True, 32000, (("reasoning_effort", "low"),))
GEMINI_LITE = Model("gemini", "gemini-flash-lite-latest", True, 32000)
GROQ_OSS = Model("groq", "openai/gpt-oss-120b", False, 6000, (("reasoning_effort", "low"),))
GROQ_QWEN = Model("groq", "qwen/qwen3.8-27b", False, 6000)
# OpenRouter's router picks whichever free model is available, so it survives free models coming and going.
OPENROUTER_FREE = Model("openrouter", "openrouter/free", True, 16000)

# Groq is the fastest but allows only ~8k tokens/minute on the free tier, so it leads the
# small JSON tasks; Gemini leads the big vision and code tasks.
CHAINS = {
    "vision": [GEMINI_FLASH, GEMINI_LATEST, OPENROUTER_FREE, GEMINI_LITE],
    "plan": [GROQ_OSS, GEMINI_FLASH, OPENROUTER_FREE, GROQ_QWEN, GEMINI_LITE],
    "code": [GEMINI_FLASH, OPENROUTER_FREE, GEMINI_LATEST, GEMINI_LITE, GROQ_OSS, GROQ_QWEN],
    "chat": [GROQ_OSS, GROQ_QWEN, GEMINI_FLASH, OPENROUTER_FREE, GEMINI_LITE],
}
TIMEOUTS = {"vision": 150, "plan": 120, "code": 240, "chat": 90}

_cooldowns: dict[str, float] = {}
_cooldown_lock = threading.Lock()


class LLMError(Exception):
    """Every model in the chain failed."""


class ProviderError(Exception):
    def __init__(self, message: str, status: int = 0, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def chain_for(task: str) -> list[Model]:
    """The model chain for a task; NS_CHAIN_<TASK>="provider:model,..." in .env overrides it."""
    override = os.environ.get(f"NS_CHAIN_{task.upper()}")
    if not override:
        return CHAINS[task]
    models = []
    for item in override.split(","):
        provider, _, name = item.strip().partition(":")
        if provider in PROVIDERS and name:
            models.append(Model(provider, name, vision=provider != "groq"))
    return models or CHAINS[task]


def _on_cooldown(model: Model) -> bool:
    with _cooldown_lock:
        return _cooldowns.get(model.label, 0) > time.time()


def _cool_down(model: Model, seconds: float) -> None:
    with _cooldown_lock:
        _cooldowns[model.label] = time.time() + seconds


def _describe(error: Exception) -> str:
    if isinstance(error, ProviderError):
        return {
            429: "rate limited",
            503: "overloaded",
            502: "provider error",
            504: "timed out",
            401: "bad API key",
            403: "access denied",
        }.get(error.status, str(error))
    if isinstance(error, httpx.TimeoutException):
        return "timed out"
    return str(error) or type(error).__name__


def _data_url(image: tuple[bytes, str]) -> str:
    data, mime = image
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _call(model: Model, system: str, prompt: str, images, json_mode: bool, max_tokens: int, timeout: float):
    provider = PROVIDERS[model.provider]
    if images and model.vision:
        content = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": _data_url(image)}} for image in images
        ]
    elif images:
        content = prompt + "\n\n(The reference images could not be attached for this model; rely on the written description.)"
    else:
        content = prompt

    body = {
        "model": model.name,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "max_tokens": min(max_tokens, model.max_output),
        "temperature": 0.5,
        **dict(model.params),
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    response = httpx.post(
        f"{provider.base_url}/chat/completions",
        headers={"Authorization": f"Bearer {provider.api_key}", **provider.headers},
        json=body,
        timeout=timeout,
    )
    try:
        data = response.json()
    except ValueError:
        raise ProviderError(f"HTTP {response.status_code}", response.status_code) from None
    if isinstance(data, list):  # Gemini wraps some errors in a list
        data = data[0] if data else {}

    error = data.get("error")
    if response.status_code >= 400 or error:
        status = response.status_code if response.status_code >= 400 else int((error or {}).get("code") or 502)
        retry = response.headers.get("retry-after")
        message = (error or {}).get("message") if isinstance(error, dict) else str(error)
        raise ProviderError(message or f"HTTP {status}", status, float(retry) if retry and retry.isdigit() else None)

    choice = (data.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content") or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if choice.get("finish_reason") == "length":
        raise ProviderError("output was cut off")
    if not text:
        raise ProviderError("empty response")
    # OpenRouter reports which free model its router actually used.
    used = data.get("model") or model.name
    return text, used if model.provider != "openrouter" else f"{model.name} → {used}"


def complete(
    task: str,
    system: str,
    prompt: str,
    images: list[tuple[bytes, str]] | None = None,
    json_mode: bool = False,
    max_tokens: int = 8000,
    validate: Callable[[str], object] | None = None,
    on_fallback: Callable[[str, str], None] | None = None,
):
    """Run a task through its model chain. Returns (result, "provider:model").

    `validate` turns the raw text into the result and raises to reject it, which
    moves on to the next model. `on_fallback(failed_label, reason)` reports switches.
    """
    chain = [model for model in chain_for(task) if PROVIDERS[model.provider].api_key]
    if not chain:
        raise LLMError("No AI provider is configured. Add API keys to .env.")
    if images:  # models that can see the images go first
        chain = [m for m in chain if m.vision] + [m for m in chain if not m.vision]
    # Models cooling down after a failure go last instead of being dropped.
    chain = [m for m in chain if not _on_cooldown(m)] + [m for m in chain if _on_cooldown(m)]

    errors = []
    for model in chain:
        try:
            text, used = _call(model, system, prompt, images, json_mode, max_tokens, TIMEOUTS[task])
            result = validate(text) if validate else text
            return result, f"{model.provider}:{used}"
        except Exception as error:
            reason = _describe(error)
            status = getattr(error, "status", 0)
            if status in (401, 403):
                _cool_down(model, 3600)
            elif status in (429, 502, 503, 504) or isinstance(error, httpx.TransportError):
                _cool_down(model, min(getattr(error, "retry_after", None) or 45, 180))
            logger.warning("%s failed on %s: %s", task, model.label, reason)
            errors.append(f"{model.label}: {reason}")
            if on_fallback:
                on_fallback(model.label, reason)
    raise LLMError("All AI providers failed: " + "; ".join(errors))


def parse_json(text: str) -> dict:
    """Parse a JSON object out of a model reply, tolerating code fences and chatter."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the reply")
    value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("reply is not a JSON object")
    return value


def status() -> dict:
    now = time.time()
    with _cooldown_lock:
        cooling = {label: round(until - now) for label, until in _cooldowns.items() if until > now}
    return {
        "providers": {name: bool(p.api_key) for name, p in PROVIDERS.items()},
        "chains": {task: [m.label for m in chain_for(task)] for task in CHAINS},
        "cooling_down": cooling,
    }
