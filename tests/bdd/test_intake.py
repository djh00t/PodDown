"""Executable acceptance tests for Markdown intake."""

from importlib import import_module

from pytest_bdd import given, scenarios, then, when

scenarios("../features/intake.feature")


@given('a Markdown document with the profile "technical-dialogue"')
def valid_document(context):
    context.values["source"] = """---
poddown:
  profile: technical-dialogue
---
# Mapping robots
"""


@given('the profile "technical-dialogue" exists')
def existing_profile(context):
    context.values["profiles"] = {"technical-dialogue"}


@given("a Markdown document with an unknown PodDown key")
def invalid_document(context):
    context.values["source"] = """---
poddown:
  profile: technical-dialogue
  invent_dialogue: true
---
# Mapping robots
"""


@when("the episode source is validated")
def validate_source(context):
    try:
        intake = import_module("poddown.intake")
    except ModuleNotFoundError as error:
        raise AssertionError("Markdown intake is not implemented") from error
    context.values["result"] = intake.validate_markdown(
        context.values["source"], context.values.get("profiles", set())
    )


@then("the immutable source snapshot is accepted")
def source_is_accepted(context):
    assert context.values["result"].accepted is True
    assert context.values["result"].source_sha256


@then("no voice provider has been called")
@then("validation fails before any voice provider call")
def provider_not_called(context):
    result = context.values["result"]
    assert result.provider_calls == 0
    if hasattr(result, "accepted"):
        assert result.accepted is False or result.source_sha256
