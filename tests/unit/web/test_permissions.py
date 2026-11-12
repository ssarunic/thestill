"""Spec #110 §7 — named permissions: one helper, admin-only today."""

from unittest.mock import MagicMock

import pytest

from thestill.web.permissions import (
    DRIFT_ALIGN,
    PERMISSIONS,
    TRANSCRIPT_EDIT,
    has_permission,
    permissions_for,
    require_permission,
)

from .auth_harness import ADMIN_USER, PLAIN_USER


def _config(multi_user: bool, drift_alignment: bool = True):
    config = MagicMock()
    config.multi_user = multi_user
    config.drift_alignment_enabled = drift_alignment
    return config


def test_admin_holds_every_permission_in_multi_user_mode():
    assert has_permission(ADMIN_USER, TRANSCRIPT_EDIT, _config(True))
    assert not has_permission(PLAIN_USER, TRANSCRIPT_EDIT, _config(True))
    assert permissions_for(ADMIN_USER, _config(True)) == list(PERMISSIONS)
    assert permissions_for(PLAIN_USER, _config(True)) == []


def test_single_user_mode_is_the_operator():
    assert has_permission(PLAIN_USER, TRANSCRIPT_EDIT, _config(False), episode_id="ep-1")
    assert permissions_for(PLAIN_USER, _config(False)) == list(PERMISSIONS)


def test_drift_alignment_is_on_by_default_and_the_setting_turns_it_off(monkeypatch):
    """Spec #116 Phase 2 — ``DRIFT_ALIGNMENT_ENABLED`` is a kill switch."""
    from thestill.utils.config import Config, load_config

    assert Config.model_fields["drift_alignment_enabled"].default is True
    monkeypatch.delenv("DRIFT_ALIGNMENT_ENABLED", raising=False)
    assert load_config().drift_alignment_enabled is True
    monkeypatch.setenv("DRIFT_ALIGNMENT_ENABLED", "false")
    assert load_config().drift_alignment_enabled is False

    assert has_permission(ADMIN_USER, DRIFT_ALIGN, _config(True, drift_alignment=True))
    assert not has_permission(PLAIN_USER, DRIFT_ALIGN, _config(True, drift_alignment=True))
    # Switched off, nobody holds it.
    assert not has_permission(ADMIN_USER, DRIFT_ALIGN, _config(True, drift_alignment=False))
    assert permissions_for(ADMIN_USER, _config(True, drift_alignment=False)) == [TRANSCRIPT_EDIT]
    # A stand-in config that does not carry the setting grants nothing.
    assert not has_permission(ADMIN_USER, DRIFT_ALIGN, MagicMock(multi_user=True))


def test_unknown_permission_is_never_granted():
    assert not has_permission(ADMIN_USER, "reports.triage", _config(False))
    with pytest.raises(ValueError):
        require_permission("reports.triage")
