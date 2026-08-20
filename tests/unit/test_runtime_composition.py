"""Unit coverage for explicit, offline-safe packaged runtime composition."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from temporalio import activity

from poddown.audio import DeterministicLocalRenderer, EpisodeRenderWorkflow
from poddown.audio.production_workflow import (
    MASTER_AND_FINAL_MASTER_QA_ACTIVITY_NAME,
    EpisodeProductionWorkflow,
)
from poddown.providers.settings import ProviderRuntimeSettings
from poddown.runtime import (
    ProductionWorkerHandlers,
    RuntimeConfigurationError,
    RuntimeDependencies,
    RuntimeSettings,
    compose_runtime,
)


def _live_provider_settings() -> ProviderRuntimeSettings:
    """Build validated live metadata without contacting a provider."""
    return ProviderRuntimeSettings.from_environment(
        {
            "PODDOWN_PROVIDER_ROUTE_JSON": json.dumps(
                {
                    "route_id": "live-route",
                    "mode": "live-provider",
                    "renderer": {
                        "provider": "elevenlabs",
                        "model": "eleven-multilingual-v2",
                        "voice_asset_id": "voice-public-1",
                        "required_capabilities": ["wav", "voice-pinning"],
                        "secret_ref": "env://ELEVENLABS_API_KEY",
                    },
                    "transcriber": {
                        "provider": "openai",
                        "model": "gpt-4o-transcribe",
                        "required_capabilities": ["timestamps"],
                        "secret_ref": "env://OPENAI_API_KEY",
                    },
                    "fallbacks": [],
                    "pricing_version": "2026-08-12",
                    "max_request_cost": "0.25",
                    "max_episode_cost": "5.00",
                }
            ),
            "PODDOWN_PROVIDER_LIVE_ENABLED": "1",
            "PODDOWN_PROVIDER_ENDPOINT": "https://providers.example.test/v1",
        }
    )


def test_default_runtime_composition_keeps_api_and_worker_local(
    tmp_path: Path,
) -> None:
    """Removing explicit provider mode must never create a live dispatch path."""
    settings = RuntimeSettings.from_environment(
        {"PODDOWN_RUNTIME_DATA_DIR": str(tmp_path)}
    )

    components = compose_runtime(
        settings,
        RuntimeDependencies(
            renderer=DeterministicLocalRenderer(),
            health_probes={"postgres": lambda: True},
        ),
    )

    assert settings.mode == "deterministic-local"
    assert settings.provider_settings is None
    with TestClient(components.app) as client:
        assert client.get("/health/live").json() == {"status": "healthy"}
    assert components.worker_activity is not None


def test_production_worker_registration_requires_an_explicit_named_adapter(
    tmp_path: Path,
) -> None:
    """Production registration must not advertise absent later-stage adapters."""

    @activity.defn(name=MASTER_AND_FINAL_MASTER_QA_ACTIVITY_NAME)
    async def master_and_final_master_qa(
        payload: dict[str, object],
    ) -> dict[str, object]:
        del payload
        return {}

    components = compose_runtime(
        RuntimeSettings.from_environment({"PODDOWN_RUNTIME_DATA_DIR": str(tmp_path)}),
        RuntimeDependencies(
            renderer=DeterministicLocalRenderer(),
            production_worker_handlers=ProductionWorkerHandlers(
                master_and_final_master_qa=master_and_final_master_qa
            ),
        ),
    )

    assert components.worker_workflows == (
        EpisodeRenderWorkflow,
        EpisodeProductionWorkflow,
    )
    assert components.worker_activity_names == (
        "poddown.audio.render_segment",
        MASTER_AND_FINAL_MASTER_QA_ACTIVITY_NAME,
    )


@pytest.mark.parametrize("activity_name", ["unknown", "poddown.audio.render_segment"])
def test_production_worker_registration_rejects_unknown_or_duplicate_activity_names(
    tmp_path: Path, activity_name: str
) -> None:
    """A production adapter must be named exactly once for the registered contract."""

    @activity.defn(name=activity_name)
    async def incorrectly_named_adapter(
        payload: dict[str, object],
    ) -> dict[str, object]:
        del payload
        return {}

    with pytest.raises(RuntimeConfigurationError, match="production activity"):
        compose_runtime(
            RuntimeSettings.from_environment(
                {"PODDOWN_RUNTIME_DATA_DIR": str(tmp_path)}
            ),
            RuntimeDependencies(
                renderer=DeterministicLocalRenderer(),
                production_worker_handlers=ProductionWorkerHandlers(
                    master_and_final_master_qa=incorrectly_named_adapter
                ),
            ),
        )


def test_live_mode_rejects_a_missing_credential_without_echoing_it() -> None:
    """A route reference alone must not activate live provider composition."""
    raw_secret = "do-not-leak-this-secret"
    environment = {
        "PODDOWN_RUNTIME_MODE": "live-provider",
        "PODDOWN_PROVIDER_ROUTE_JSON": json.dumps(
            {
                "route_id": "live-route",
                "mode": "live-provider",
                "renderer": {
                    "provider": "elevenlabs",
                    "model": "eleven-multilingual-v2",
                    "voice_asset_id": "voice-public-1",
                    "required_capabilities": ["wav", "voice-pinning"],
                    "secret_ref": "env://ELEVENLABS_API_KEY",
                },
                "transcriber": {
                    "provider": "openai",
                    "model": "gpt-4o-transcribe",
                    "required_capabilities": ["timestamps"],
                    "secret_ref": "env://OPENAI_API_KEY",
                },
                "fallbacks": [],
                "pricing_version": "2026-08-12",
                "max_request_cost": "0.25",
                "max_episode_cost": "5.00",
            }
        ),
        "PODDOWN_PROVIDER_LIVE_ENABLED": "1",
        "PODDOWN_PROVIDER_ENDPOINT": "https://providers.example.test/v1",
        "UNRELATED_API_KEY": raw_secret,
    }

    with pytest.raises(RuntimeConfigurationError) as error:
        RuntimeSettings.from_environment(environment)

    assert raw_secret not in str(error.value)
    assert "credential" in str(error.value)


def test_direct_local_settings_reject_live_provider_settings_without_secret_leak(
    tmp_path: Path,
) -> None:
    """Direct construction cannot bypass the deterministic-local provider boundary."""
    raw_secret = "do-not-leak-this-secret"

    with pytest.raises(RuntimeConfigurationError) as error:
        RuntimeSettings(
            mode="deterministic-local",
            data_root=tmp_path,
            database_path=tmp_path / "poddown.sqlite3",
            provider_settings=_live_provider_settings(),
        )

    assert "provider settings" in str(error.value)
    assert raw_secret not in str(error.value)


def test_runtime_composition_rejects_a_live_settings_and_local_renderer_mismatch(
    tmp_path: Path,
) -> None:
    """A deterministic renderer cannot accidentally satisfy live provider mode."""
    environment = {
        "PODDOWN_RUNTIME_DATA_DIR": str(tmp_path),
        "PODDOWN_RUNTIME_MODE": "live-provider",
        "PODDOWN_PROVIDER_ROUTE_JSON": json.dumps(
            {
                "route_id": "live-route",
                "mode": "live-provider",
                "renderer": {
                    "provider": "elevenlabs",
                    "model": "eleven-multilingual-v2",
                    "voice_asset_id": "voice-public-1",
                    "required_capabilities": ["wav", "voice-pinning"],
                    "secret_ref": "env://ELEVENLABS_API_KEY",
                },
                "transcriber": {
                    "provider": "openai",
                    "model": "gpt-4o-transcribe",
                    "required_capabilities": ["timestamps"],
                    "secret_ref": "env://OPENAI_API_KEY",
                },
                "fallbacks": [],
                "pricing_version": "2026-08-12",
                "max_request_cost": "0.25",
                "max_episode_cost": "5.00",
            }
        ),
        "PODDOWN_PROVIDER_LIVE_ENABLED": "1",
        "PODDOWN_PROVIDER_ENDPOINT": "https://providers.example.test/v1",
        "ELEVENLABS_API_KEY": "test-only-elevenlabs-key",
        "OPENAI_API_KEY": "test-only-openai-key",
    }
    settings = RuntimeSettings.from_environment(environment)

    with pytest.raises(RuntimeConfigurationError, match="renderer"):
        compose_runtime(
            settings, RuntimeDependencies(renderer=DeterministicLocalRenderer())
        )
