"""Unit contracts for immutable publication approvals."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from poddown.approvals import PublicationApproval


def test_approval_requires_forward_utc_window_and_digest() -> None:
    with pytest.raises(ValueError, match="expires"):
        PublicationApproval(
            tenant_id=UUID("018f2c8b-7b46-7cc5-b2e1-111111111111"),
            project_id=UUID("018f2c8b-7b46-7cc5-b2e1-222222222222"),
            approval_id=UUID("018f2c8b-7b46-7cc5-b2e1-333333333333"),
            episode_id=UUID("018f2c8b-7b46-7cc5-b2e1-444444444444"),
            target_id="target-1",
            operation="publish",
            package_sha256="a" * 64,
            actor_id="actor-1",
            nonce_sha256="b" * 64,
            issued_at=datetime(2026, 8, 14, tzinfo=UTC),
            expires_at=datetime(2026, 8, 14, tzinfo=UTC),
        )


def test_approval_dict_is_secret_safe() -> None:
    approval = PublicationApproval(
        tenant_id=UUID("018f2c8b-7b46-7cc5-b2e1-111111111111"),
        project_id=UUID("018f2c8b-7b46-7cc5-b2e1-222222222222"),
        approval_id=UUID("018f2c8b-7b46-7cc5-b2e1-333333333333"),
        episode_id=UUID("018f2c8b-7b46-7cc5-b2e1-444444444444"),
        target_id="target-1",
        operation="publish",
        package_sha256="a" * 64,
        actor_id="actor-1",
        nonce_sha256="b" * 64,
        issued_at=datetime(2026, 8, 14, tzinfo=UTC),
        expires_at=datetime(2026, 8, 14, tzinfo=UTC) + timedelta(minutes=1),
    )
    assert "raw" not in str(approval.to_dict()).casefold()
