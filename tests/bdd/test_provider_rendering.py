"""Executable acceptance tests for provider rendering policy."""

from importlib import import_module

from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../features/provider_rendering.feature")


@given("a canonical rights-cleared segment")
def rights_cleared_segment(context):
    context.values.update(text="one point six terabit", rights=True)


@given("a canonical segment whose voice consent is revoked")
def revoked_segment(context):
    context.values.update(text="one point six terabit", rights=False)


@given(parsers.parse('the "{provider}" renderer is eligible'))
def eligible_renderer(context, provider):
    context.values.setdefault("eligible", set()).add(provider)


@given(parsers.parse('the "{provider}" renderer is transiently unavailable'))
def unavailable_renderer(context, provider):
    context.values.setdefault("unavailable", set()).add(provider)


def _render(context, provider):
    try:
        rendering = import_module("poddown.rendering")
    except ModuleNotFoundError as error:
        raise AssertionError("Provider rendering policy is not implemented") from error
    context.values["result"] = rendering.request_candidate(
        text=context.values["text"],
        rights_valid=context.values["rights"],
        provider=provider,
        eligible=context.values.get("eligible", set()),
        unavailable=context.values.get("unavailable", set()),
    )


@when(parsers.parse('one candidate is requested from "{provider}"'))
def request_candidate(context, provider):
    _render(context, provider)


@when(parsers.parse('rendering falls back to "{provider}"'))
def fallback_candidate(context, provider):
    _render(context, provider)


@then("the provider receives the immutable expected-spoken text")
def immutable_text(context):
    assert context.values["result"].submitted_text == context.values["text"]


@then("the candidate records provider request, model, usage, cost, and checksum")
def complete_metadata(context):
    candidate = context.values["result"]
    assert all(
        (candidate.request_id, candidate.model, candidate.usage, candidate.checksum)
    )
    assert candidate.cost >= 0


@then("rendering fails before any provider call")
def blocked_before_provider(context):
    assert context.values["result"].provider_calls == 0
    assert context.values["result"].accepted is False


@then("a distinct provider candidate is recorded")
def distinct_candidate(context):
    assert context.values["result"].provider == "openai"
    assert context.values["result"].candidate_id


@then("all critical-token and audio gates remain required")
def gates_remain(context):
    assert context.values["result"].required_gates == {
        "audio_quality",
        "critical_tokens",
    }
