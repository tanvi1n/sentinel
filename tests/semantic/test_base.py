"""Semantic base: interface, skipped_result, context builder, canonical key."""

import ast
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path

import pytest

from app.semantic.models import (
    ContentLabel,
    ProviderResult,
    SemanticContext,
    SemanticFindings,
    SemanticFlaggedArg,
    SemanticObservedItem,
    SemanticSource,
    SemanticStatus,
)
from app.semantic.base import (
    MAX_OBSERVED_CHARS,
    SemanticReasoner,
    build_semantic_context as _build,
    canonical_context_json,
    canonical_json,
    replay_key,
    skipped_result,
)

BASE_PY = Path(__file__).resolve().parents[2] / "app" / "semantic" / "base.py"


# ---- helpers ----------------------------------------------------------------


@dataclass
class Act:
    """What the old tests called a canonical action (test-only shim)."""

    tool_id: str = "t.act"
    args: dict = field(default_factory=lambda: {"b": 2, "a": {"y": 1, "x": [1, 2]}})
    justification: str = "because"
    user_task: str = "do the thing"


def action(**kw) -> Act:
    return Act(**kw)


def build_semantic_context(act, tool_description, observed_content=(), provenance_flags=()):
    return _build(
        tool_id=act.tool_id,
        tool_description=tool_description,
        arguments=act.args,
        user_task=act.user_task,
        justification=act.justification,
        observed_content=observed_content,
        flagged_args=provenance_flags,
    )


def item(item_id="o1", label="TOOL_OUTPUT", text="hello") -> SemanticObservedItem:
    return SemanticObservedItem(item_id=item_id, label=ContentLabel(label), text=text)


def flag(arg="dest", label="EXTERNAL", item_id="o1") -> SemanticFlaggedArg:
    return SemanticFlaggedArg(arg=arg, origin_label=ContentLabel(label), item_id=item_id)


def ctx(**kw) -> SemanticContext:
    return build_semantic_context(action(), "desc", **kw)


def valid_result() -> ProviderResult:
    return ProviderResult(
        status=SemanticStatus.VALID,
        source=SemanticSource.MOCK,
        findings=SemanticFindings(
            intent_alignment="ALIGNED",
            injection_suspected=False,
            ambiguity="LOW",
            rationale="[MOCK] ok",
        ),
    )


# ---- SemanticReasoner interface ---------------------------------------------


def test_reasoner_is_abstract():
    with pytest.raises(TypeError):
        SemanticReasoner()


def test_analyze_returns_provider_result():
    class Ok(SemanticReasoner):
        def _analyze(self, context):
            return valid_result()

    assert Ok().analyze(ctx()).status is SemanticStatus.VALID


def test_analyze_never_raises_and_maps_failures():
    class Boom(SemanticReasoner):
        def _analyze(self, context):
            raise RuntimeError("secret-key-123 leaked in message")

    class Slow(SemanticReasoner):
        def _analyze(self, context):
            raise TimeoutError("took too long")

    r = Boom().analyze(ctx())
    assert r.status is SemanticStatus.UNAVAILABLE
    assert r.source is SemanticSource.NONE and r.findings is None
    assert r.error == "RuntimeError"
    assert "secret-key-123" not in r.model_dump_json()

    t = Slow().analyze(ctx())
    assert t.status is SemanticStatus.TIMEOUT and t.source is SemanticSource.NONE


def test_analyze_wraps_non_result_return_as_invalid():
    class Bad(SemanticReasoner):
        def _analyze(self, context):
            return {"status": "VALID"}

    r = Bad().analyze(ctx())
    assert isinstance(r, ProviderResult)
    assert r.status is SemanticStatus.INVALID and r.source is SemanticSource.NONE


def test_analyze_cannot_be_overridden():
    with pytest.raises(TypeError):

        class Sneaky(SemanticReasoner):
            def _analyze(self, context):
                return valid_result()

            def analyze(self, context):
                raise RuntimeError("escapes")


# ---- skipped_result ---------------------------------------------------------


def test_skipped_result():
    r = skipped_result()
    assert isinstance(r, ProviderResult)
    assert r.status is SemanticStatus.SKIPPED
    assert r.source is SemanticSource.NONE
    assert r.findings is None and r.error is None and r.provider is None
    assert skipped_result() == r


# ---- build_semantic_context -------------------------------------------------


def test_context_fields_from_trusted_inputs():
    c = build_semantic_context(action(), "tool desc")
    assert isinstance(c, SemanticContext)
    assert (c.tool_id, c.tool_description) == ("t.act", "tool desc")
    assert c.user_task == "do the thing" and c.justification == "because"
    assert c.canonical_args == {"b": 2, "a": {"y": 1, "x": [1, 2]}}
    assert c.observed_content == [] and c.flagged_args == []


def test_context_does_not_alias_action_args():
    a = action()
    c = build_semantic_context(a, "d")
    c.canonical_args["a"]["y"] = 999
    assert a.args["a"]["y"] == 1


def test_provenance_preserved():
    c = ctx(
        observed_content=[item("o1", "EXTERNAL", "page"), item("o2", "TOOL_OUTPUT", "out")],
        provenance_flags=[flag("dest", "EXTERNAL", "o1")],
    )
    assert [(i.item_id, i.label.value, i.text) for i in c.observed_content] == [
        ("o1", "EXTERNAL", "page"),
        ("o2", "TOOL_OUTPUT", "out"),
    ]
    assert len(c.flagged_args) == 1
    f = c.flagged_args[0]
    assert (f.arg, f.origin_label.value, f.item_id) == ("dest", "EXTERNAL", "o1")


def test_observed_text_truncated():
    long = "x" * (MAX_OBSERVED_CHARS + 500)
    c = ctx(observed_content=[item(text=long), item("o2", text="short")])
    assert [len(i.text) for i in c.observed_content] == [MAX_OBSERVED_CHARS, 5]


def test_lists_sorted_for_stable_order():
    a = ctx(
        observed_content=[item("o2"), item("o1")],
        provenance_flags=[flag("z"), flag("a")],
    )
    b = ctx(
        observed_content=[item("o1"), item("o2")],
        provenance_flags=[flag("a"), flag("z")],
    )
    assert [i.item_id for i in a.observed_content] == ["o1", "o2"]
    assert [f.arg for f in a.flagged_args] == ["a", "z"]
    assert a == b


def test_context_carries_nothing_beyond_contract():
    c = ctx(observed_content=[item()])
    assert set(c.model_dump()) == set(SemanticContext.model_fields)


def test_builder_takes_no_agent_session_or_verdict_inputs():
    import inspect

    params = set(inspect.signature(_build).parameters)
    assert params == {
        "tool_id", "tool_description", "arguments", "user_task", "justification",
        "observed_content", "flagged_args",
    }


# ---- canonical serialization and replay key ---------------------------------


def test_canonical_json_ignores_key_order_and_whitespace():
    assert canonical_json({"b": 1, "a": [1, {"d": 1, "c": 2}]}) == (
        '{"a":[1,{"c":2,"d":1}],"b":1}'
    )
    assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})


def test_canonical_json_keeps_unicode_and_rejects_nan():
    assert canonical_json({"k": "é"}) == '{"k":"é"}'
    with pytest.raises(ValueError):
        canonical_json({"k": float("nan")})


def test_equivalent_contexts_same_canonical_text_and_key():
    a = build_semantic_context(action(args={"a": 1, "b": 2}), "d")
    b = build_semantic_context(action(args={"b": 2, "a": 1}), "d")
    assert canonical_context_json(a) == canonical_context_json(b)
    assert replay_key(a, "v1") == replay_key(b, "v1")


def test_key_is_stable_sha256_hex():
    c = ctx()
    k = replay_key(c, "v1")
    assert k == replay_key(c, "v1")
    assert len(k) == 64 and int(k, 16) >= 0
    expected = hashlib.sha256(
        canonical_json(
            {"prompt_version": "v1", "context": json.loads(canonical_context_json(c))}
        ).encode("utf-8")
    ).hexdigest()
    assert k == expected


@pytest.mark.parametrize(
    "mutate",
    [
        lambda: build_semantic_context(action(tool_id="t.other"), "desc"),
        lambda: build_semantic_context(action(), "other desc"),
        lambda: build_semantic_context(action(args={"a": 1}), "desc"),
        lambda: build_semantic_context(action(user_task="different"), "desc"),
        lambda: build_semantic_context(action(justification="other"), "desc"),
        lambda: ctx(observed_content=[item()]),
        lambda: ctx(provenance_flags=[flag()]),
    ],
)
def test_meaningful_change_changes_key(mutate):
    assert replay_key(mutate(), "v1") != replay_key(ctx(), "v1")


def test_prompt_version_changes_key():
    c = ctx()
    assert replay_key(c, "v1") != replay_key(c, "v2")


def test_observed_label_changes_key():
    a = ctx(observed_content=[item(label="TOOL_OUTPUT")])
    b = ctx(observed_content=[item(label="EXTERNAL")])
    assert replay_key(a, "v1") != replay_key(b, "v1")


def test_flag_origin_changes_key():
    a = ctx(provenance_flags=[flag(label="EXTERNAL")])
    b = ctx(provenance_flags=[flag(label="TOOL_OUTPUT")])
    assert replay_key(a, "v1") != replay_key(b, "v1")


def test_truncation_beyond_limit_does_not_change_key():
    base = "x" * MAX_OBSERVED_CHARS
    a = ctx(observed_content=[item(text=base + "AAA")])
    b = ctx(observed_content=[item(text=base + "BBB")])
    assert replay_key(a, "v1") == replay_key(b, "v1")


# ---- genericity and layering ------------------------------------------------


def test_base_has_no_domain_or_tool_vocabulary():
    src = BASE_PY.read_text(encoding="utf-8").lower()
    banned = [
        "email", "calendar", "payment", "website", "invoice", "inbox",
        "transfer", "delete", "web.fetch", "file.", "email.", "payment.",
    ]
    for word in banned:
        assert word not in src, f"base.py mentions {word!r}"


def test_base_imports_only_the_semantic_models_and_stdlib():
    tree = ast.parse(BASE_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        elif isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        for m in mods:
            if m == "app" or m.startswith("app."):
                assert m == "app.semantic.models", f"base.py must not import {m}"
