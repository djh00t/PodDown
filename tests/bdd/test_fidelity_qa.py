"""Executable acceptance tests for critical-token fidelity."""

from importlib import import_module

from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../features/fidelity_qa.feature")


@given(parsers.parse('a canonical segment containing the critical token "{text}"'))
def critical_token(context, text):
    context.values["expected"] = text


@given(parsers.parse('its candidate transcript contains "{text}"'))
def transcript(context, text):
    context.values["actual"] = text


@when("candidate fidelity is evaluated")
def evaluate(context):
    try:
        fidelity = import_module("poddown.qa.fidelity")
    except ModuleNotFoundError as error:
        raise AssertionError("Critical-token fidelity is not implemented") from error
    context.values["result"] = fidelity.evaluate_critical_tokens(
        expected=(context.values["expected"],),
        transcript=context.values["actual"],
    )


@then("the candidate fails the critical-token gate")
def candidate_fails(context):
    assert context.values["result"].passed is False


@then("only its segment is eligible for rerendering")
def segment_only_rerender(context):
    assert context.values["result"].rerender_scope == "segment"


@then("critical-token accuracy is 1.0")
def perfect_accuracy(context):
    assert context.values["result"].accuracy == 1.0


@then("the candidate may proceed to audio quality evaluation")
def may_proceed(context):
    assert context.values["result"].passed is True
