"""Spec #110 §7 — named permissions: one helper, admin-only today."""

from unittest.mock import MagicMock

import pytest

from thestill.web.permissions import (
    DRIFT_ALIGN,
    DRIFT_ALIGN_UNVERIFIED,
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


class _Settings:
    def __init__(self, audience, unverified="off"):
        self.values = {"drift_alignment_audience": audience, "drift_unverified_browsers": unverified}

    def get(self, key):
        return self.values[key]


def test_drift_alignment_audience_opens_it_to_every_signed_in_user():
    """Spec #116 — the runtime setting widens drift.align, and only that."""
    users = _Settings("users")
    assert has_permission(PLAIN_USER, DRIFT_ALIGN, _config(True), settings=users)
    assert permissions_for(PLAIN_USER, _config(True), users) == [DRIFT_ALIGN]
    # Never anything else.
    assert not has_permission(PLAIN_USER, TRANSCRIPT_EDIT, _config(True), settings=users)
    # Admins only when it says so, or when it cannot be read.
    admins = _Settings("admins")
    assert not has_permission(PLAIN_USER, DRIFT_ALIGN, _config(True), settings=admins)
    assert has_permission(ADMIN_USER, DRIFT_ALIGN, _config(True), settings=admins)
    assert not has_permission(PLAIN_USER, DRIFT_ALIGN, _config(True))
    # The kill switch still wins.
    assert not has_permission(PLAIN_USER, DRIFT_ALIGN, _config(True, drift_alignment=False), settings=users)


def test_drift_alignment_audience_setting():
    from thestill.services.abuse.settings_registry import get_spec

    spec = get_spec("drift_alignment_audience")
    assert spec.env_default(MagicMock()) == "users"
    assert spec.validate(" Admins ") == "admins"
    assert spec.failsafe == "admins"
    with pytest.raises(ValueError):
        spec.validate("everyone")


def test_unverified_browsers_are_for_admins_only_and_can_be_switched_off():
    """Spec #116 rollout — admins try the feature where it is not verified yet."""
    on = _Settings("users", unverified="admins")
    assert has_permission(ADMIN_USER, DRIFT_ALIGN_UNVERIFIED, _config(True), settings=on)
    assert permissions_for(ADMIN_USER, _config(True), on) == [TRANSCRIPT_EDIT, DRIFT_ALIGN, DRIFT_ALIGN_UNVERIFIED]
    # Never a plain user, even though they hold drift.align.
    assert not has_permission(PLAIN_USER, DRIFT_ALIGN_UNVERIFIED, _config(True), settings=on)
    assert permissions_for(PLAIN_USER, _config(True), on) == [DRIFT_ALIGN]
    # Off, nobody holds it; drift.align itself is untouched.
    off = _Settings("users", unverified="off")
    assert not has_permission(ADMIN_USER, DRIFT_ALIGN_UNVERIFIED, _config(True), settings=off)
    assert has_permission(ADMIN_USER, DRIFT_ALIGN, _config(True), settings=off)
    # No runtime settings (single-user mode): no switch, so admins hold it.
    assert has_permission(ADMIN_USER, DRIFT_ALIGN_UNVERIFIED, _config(True))
    assert not has_permission(PLAIN_USER, DRIFT_ALIGN_UNVERIFIED, _config(True))
    # The kill switch still wins.
    assert not has_permission(ADMIN_USER, DRIFT_ALIGN_UNVERIFIED, _config(True, drift_alignment=False), settings=on)


def test_drift_unverified_browsers_setting():
    from thestill.services.abuse.settings_registry import get_spec

    spec = get_spec("drift_unverified_browsers")
    assert spec.env_default(MagicMock()) == "admins"
    assert spec.validate(" OFF ") == "off"
    assert spec.failsafe == "off"
    with pytest.raises(ValueError):
        spec.validate("users")


def test_unknown_permission_is_never_granted():
    assert not has_permission(ADMIN_USER, "reports.triage", _config(False))
    with pytest.raises(ValueError):
        require_permission("reports.triage")
