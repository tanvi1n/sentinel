"""Shared builders for provider tests."""

from app.semantic.base import build_semantic_context
from app.semantic.models import (
    ContentLabel,
    SemanticContext,
    SemanticFlaggedArg,
    SemanticObservedItem,
)

FAKE_KEY = "sk-test-SECRET-0123456789"

FINDINGS_JSON = (
    '{"intent_alignment": "ALIGNED", "injection_suspected": false, '
    '"ambiguity": "LOW", "rationale": "Fits the task."}'
)


def make_ctx(user_task="do the thing", observed=(), flags=(), **args) -> SemanticContext:
    return build_semantic_context(
        tool_id="t.act",
        tool_description="desc",
        arguments=args or {"a": 1},
        user_task=user_task,
        justification="because",
        observed_content=list(observed),
        flagged_args=list(flags),
    )


def observed(item_id="o1", label="EXTERNAL", text="page text") -> SemanticObservedItem:
    return SemanticObservedItem(item_id=item_id, label=ContentLabel(label), text=text)


def flag(arg="a", label="EXTERNAL", item_id="o1") -> SemanticFlaggedArg:
    return SemanticFlaggedArg(arg=arg, origin_label=ContentLabel(label), item_id=item_id)


def live_env(**over) -> dict:
    env = {
        "QUALCOMM_API_KEY": FAKE_KEY,
        "QUALCOMM_BASE_URL": "https://example.invalid",
        "QUALCOMM_MODEL": "test-model",
        "SENTINEL_PROMPT_VERSION": "v1",
    }
    env.update(over)
    return env
