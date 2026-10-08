"""Shared builders for provider tests."""

from app.contracts import CanonicalAction, ObservedItem, ProvenanceFlag, SemanticContext
from app.semantic.base import build_semantic_context

FAKE_KEY = "sk-test-SECRET-0123456789"

FINDINGS_JSON = (
    '{"intent_alignment": "ALIGNED", "injection_suspected": false, '
    '"ambiguity": "LOW", "rationale": "Fits the task."}'
)


def make_ctx(user_task="do the thing", observed=(), flags=(), **args) -> SemanticContext:
    action = CanonicalAction(
        tool_id="t.act",
        args=args or {"a": 1},
        justification="because",
        agent_id="agent",
        session_id="s1",
        user_task=user_task,
    )
    return build_semantic_context(action, "desc", list(observed), list(flags))


def observed(item_id="o1", label="WEBSITE", text="page text") -> ObservedItem:
    return ObservedItem(item_id=item_id, label=label, text=text)


def flag(arg="a", label="WEBSITE", item_id="o1") -> ProvenanceFlag:
    return ProvenanceFlag(arg=arg, origin_label=label, item_id=item_id)


def live_env(**over) -> dict:
    env = {
        "QUALCOMM_API_KEY": FAKE_KEY,
        "QUALCOMM_BASE_URL": "https://example.invalid",
        "QUALCOMM_MODEL": "test-model",
        "SENTINEL_PROMPT_VERSION": "v1",
    }
    env.update(over)
    return env
