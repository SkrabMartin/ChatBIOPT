from dataclasses import dataclass

from app.ai.gateway.base import ChatMessage
from app.ai.prompts.research_assistant import SYSTEM_PROMPT
from app.core.errors import AppError
from app.models import Message

# No tokenizer is shared across providers, so the budget is estimated conservatively. English prose averages
# ~4 characters per token; 3 leaves headroom for code, identifiers and non-English text.
CHARS_PER_TOKEN = 3


class ContextTooLongError(AppError):
    status_code = 422
    code = "context_too_long"


@dataclass(frozen=True)
class BuiltContext:
    messages: list[ChatMessage]
    history_message_count: int  # conversation messages included (excludes the system prompt)


def input_budget_chars(context_window: int, max_output_tokens: int) -> int:
    return (context_window - max_output_tokens) * CHARS_PER_TOKEN


def build_context(
    history: list[Message],
    *,
    context_window: int,
    max_output_tokens: int,
    system_prompt: str = SYSTEM_PROMPT,
    latest_user_content: str | None = None,
) -> BuiltContext:
    """System prompt + the most recent user/assistant messages that fit the model's window.

    Oldest messages are dropped first; the latest message is always kept or the build fails explicitly
    rather than sending a silently truncated question. `latest_user_content` replaces the text of the latest
    message (grounded runs put the question together with its evidence there).
    """
    budget = input_budget_chars(context_window, max_output_tokens) - len(system_prompt)
    selected: list[ChatMessage] = []
    for message in reversed(history):
        if message.role not in ("user", "assistant"):
            continue
        content = latest_user_content if (not selected and latest_user_content is not None) else message.content
        cost = len(content)
        if cost > budget:
            if not selected:
                raise ContextTooLongError("The latest message is too long for this model's context window")
            break
        budget -= cost
        selected.append(ChatMessage(role="user" if message.role == "user" else "assistant", content=content))
    selected.reverse()
    return BuiltContext(messages=[ChatMessage("system", system_prompt), *selected], history_message_count=len(selected))
