"""Validate the configured model using the provider's read-only models endpoint."""

import sys

import httpx

from travelmind.config import get_settings


def main() -> int:
    settings = get_settings()
    if not settings.deepseek_api_key or not settings.deepseek_api_key.get_secret_value():
        print("Set DEEPSEEK_API_KEY in the backend environment.", file=sys.stderr)
        return 1
    try:
        response = httpx.get(
            f"{str(settings.deepseek_base_url).rstrip('/')}/models",
            headers={"Authorization": f"Bearer {settings.deepseek_api_key.get_secret_value()}"},
            timeout=settings.dependency_timeout_seconds,
        )
        response.raise_for_status()
        model_ids = {model["id"] for model in response.json()["data"]}
    except httpx.HTTPStatusError as exc:
        print(f"DeepSeek models request returned HTTP {exc.response.status_code}.", file=sys.stderr)
        return 1
    except (httpx.RequestError, KeyError, TypeError, ValueError):
        print("Could not read the DeepSeek model list.", file=sys.stderr)
        return 1
    if settings.deepseek_model not in model_ids:
        print(f"Configured model is absent from the model list: {settings.deepseek_model}")
        return 1
    print(f"DeepSeek model available: {settings.deepseek_model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
