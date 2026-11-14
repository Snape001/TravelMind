from langchain_core.language_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek

from travelmind.config import Settings
from travelmind.schemas.agents import RunBudget


def create_chat_model(settings: Settings, budget: RunBudget) -> BaseChatModel:
    if not settings.deepseek_api_key or not settings.deepseek_api_key.get_secret_value():
        raise ValueError("DEEPSEEK_API_KEY is required")
    return ChatDeepSeek(
        api_key=settings.deepseek_api_key,
        base_url=str(settings.deepseek_base_url).rstrip("/"),
        model=settings.deepseek_model,
        timeout=budget.timeout_seconds,
        max_retries=budget.max_attempts - 1,
        max_tokens=budget.max_output_tokens,
    )
