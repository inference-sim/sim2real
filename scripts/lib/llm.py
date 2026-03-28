"""LiteLLM-compatible HTTP client. Uses OPENAI_API_KEY + OPENAI_BASE_URL."""
import os
import concurrent.futures
import requests

MODELS = [
    "Azure/gpt-4o",
    "GCP/gemini-2.5-flash",
    "aws/claude-opus-4-6",
]


class LLMError(Exception):
    pass


def _get_endpoint() -> tuple[str, str]:
    """Returns (api_key, base_url). Raises LLMError if not configured."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")
    if not api_key:
        # Fallback to ANTHROPIC_AUTH_TOKEN (same as review.sh)
        api_key = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        base_url = os.environ.get("ANTHROPIC_BASE_URL", base_url)
    if not api_key:
        raise LLMError(
            "No API key found. Set OPENAI_API_KEY + OPENAI_BASE_URL, "
            "or ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL."
        )
    return api_key, base_url.rstrip("/")


def call_model(model: str, messages: list[dict],
               timeout: int = 300) -> str:
    """Call a single model. Returns response text. Raises LLMError on failure."""
    api_key, base_url = _get_endpoint()
    url = f"{base_url}/v1/chat/completions"
    payload = {"model": model, "messages": messages}
    try:
        resp = requests.post(
            url, json=payload,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            timeout=timeout,
        )
    except requests.Timeout:
        raise LLMError(f"{model}: request timed out after {timeout}s")
    except requests.ConnectionError as e:
        raise LLMError(f"{model}: connection error — {e}")
    if resp.status_code != 200:
        raise LLMError(f"{model}: HTTP {resp.status_code} — {resp.text[:200]}")
    try:
        return resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"{model}: unexpected response shape — {e}: {resp.text[:200]}")


def call_models_parallel(models: list[str], messages: list[dict],
                         timeout: int = 300) -> dict[str, "str | LLMError"]:
    """Call multiple models in parallel. Returns {model: response_text | LLMError}."""
    results: dict = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(models)) as ex:
        futures = {ex.submit(call_model, m, messages, timeout): m for m in models}
        for future in concurrent.futures.as_completed(futures):
            model = futures[future]
            try:
                results[model] = future.result()
            except LLMError as e:
                results[model] = e
    return results
