"""Executable acceptance tests for the offline preview CLI."""

from __future__ import annotations

from hashlib import sha256
from importlib import import_module
from pathlib import Path

from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../features/preview.feature")


def _preview_module():
    try:
        return import_module("poddown.preview")
    except ModuleNotFoundError as error:
        raise AssertionError("Offline preview is not implemented") from error


def _cli_module():
    try:
        return import_module("poddown.cli")
    except ModuleNotFoundError as error:
        raise AssertionError("Preview CLI is not implemented") from error


def _profile_config(*profiles: str, selected: str | None = None) -> dict[str, object]:
    config: dict[str, object] = {"profiles": {profile: {} for profile in profiles}}
    if selected is not None:
        config["profile"] = selected
    return config


def _markdown(profile: str | None = None) -> bytes:
    frontmatter = (
        "" if profile is None else f"---\npoddown:\n  profile: {profile}\n---\n"
    )
    return (frontmatter + "# Cafe\n\nA local preview must preserve cafe.\n").encode(
        "utf-8"
    )


def _preview(context, *, flag_profile: str | None = None):
    preview = _preview_module()
    source = context.values["source"]
    context.values["result"] = preview.preview_markdown(
        source,
        source_name="episode.md",
        flag_profile=flag_profile,
        project_config=context.values.get("project_config"),
        user_config=context.values.get("user_config"),
    )


@given("a valid local Markdown source and profile configuration")
def valid_local_source(context):
    source = _markdown("local-profile")
    context.values.update(
        source=source,
        original_source=source,
        expected_sha256=sha256(source).hexdigest(),
        project_config=_profile_config("local-profile"),
        user_config=_profile_config("local-profile"),
    )


@given("a Markdown source with invalid PodDown frontmatter")
def invalid_frontmatter_source(context, tmp_path: Path):
    source_path = tmp_path / "invalid-frontmatter.md"
    source_path.write_text(
        "---\npoddown:\n  unexpected: value\n---\n# Invalid\n", encoding="utf-8"
    )
    context.values["source_path"] = source_path


@given("a Markdown source with an unknown profile")
def unknown_profile_source(context, tmp_path: Path):
    source_path = tmp_path / "missing-profile.md"
    source_path.write_bytes(_markdown("missing-profile"))
    context.values["source_path"] = source_path


def _set_precedence_context(
    context,
    *,
    document: str | None = None,
    project: str | None = None,
    user: str | None = None,
    flag: str | None = None,
):
    profiles = tuple(
        profile
        for profile in (flag, document, project, user, "default")
        if profile is not None
    )
    context.values.update(
        source=_markdown(document),
        project_config=_profile_config(*profiles, selected=project),
        user_config=_profile_config(*profiles, selected=user),
        flag_profile=flag,
    )


@given("profile values at the flag, document, project, and user levels")
def every_precedence_level(context):
    _set_precedence_context(
        context,
        flag="flag-profile",
        document="document-profile",
        project="project-profile",
        user="user-profile",
    )


@given("profile values at the document, project, and user levels")
def document_project_user_levels(context):
    _set_precedence_context(
        context,
        document="document-profile",
        project="project-profile",
        user="user-profile",
    )


@given("profile values at the project and user levels")
def project_user_levels(context):
    _set_precedence_context(context, project="project-profile", user="user-profile")


@given("a profile value at the user level")
def user_level(context):
    _set_precedence_context(context, user="user-profile")


@given("only the default local profile is configured")
def default_profile(context):
    _set_precedence_context(context)


@when("the Markdown source is previewed")
def preview_source(context):
    _preview(context)


@when("the Markdown source is previewed as JSON twice")
def preview_source_as_json_twice(context):
    _preview(context)
    preview = _preview_module()
    context.values["first_json"] = preview.render_preview_json(context.values["result"])
    context.values["second_json"] = preview.render_preview_json(
        context.values["result"]
    )


@when("the preview command runs")
def preview_command_runs(context, capsys):
    cli = _cli_module()
    context.values["exit_code"] = cli.main(
        ["preview", str(context.values["source_path"])]
    )
    context.values["stderr"] = capsys.readouterr().err


@when("the Markdown source is previewed with the profile flag")
def preview_source_with_flag(context):
    _preview(context, flag_profile=context.values["flag_profile"])


@when("the Markdown source is previewed without the profile flag")
def preview_source_without_flag(context):
    _preview(context)


@then("the preview reports the exact source SHA-256 and resolved profile")
def preview_has_exact_summary(context):
    result = context.values["result"]
    assert result.source_sha256 == context.values["expected_sha256"]
    assert result.profile_id == "local-profile"
    assert result.source_bytes == len(context.values["source"])
    assert result.block_count == 2


@then("the preview preserves the original source bytes")
def preview_preserves_source(context):
    assert context.values["source"] == context.values["original_source"]


@then("the preview reports zero provider calls")
def preview_has_no_provider_calls(context):
    assert context.values["result"].provider_calls == 0


@then("both JSON previews are byte-identical compact sorted JSON")
def deterministic_json(context):
    expected = (
        b'{"block_count":2,"profile_id":"local-profile","provider_calls":0,'
        + f'"source_bytes":{len(context.values["source"])}'.encode()
        + b',"source_sha256":"'
        + context.values["expected_sha256"].encode()
        + b'"}'
    )
    assert context.values["first_json"] == expected
    assert context.values["second_json"] == expected


@then("the preview command exits with validation code 2")
def preview_command_has_validation_exit(context):
    assert context.values["exit_code"] == 2


@then(
    parsers.parse('the preview command reports the stable validation error "{message}"')
)
def preview_command_reports_stable_error(context, message):
    assert context.values["stderr"].strip() == message


@then(parsers.parse('the resolved preview profile is "{profile}"'))
def resolved_profile(context, profile):
    assert context.values["result"].profile_id == profile
