"""Spec #119 Phase 0 spike: the named-speaker check behind sampling and attribution."""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "spikes" / "claims_spike.py"


@pytest.fixture(scope="module")
def spike():
    spec = importlib.util.spec_from_file_location("claims_spike", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "label",
    [
        "",
        "SPEAKER_03",
        "Unknown",
        "Speaker 2",
        "Unknown Speaker",
        "Guest",
        "Host 1",
        "Co-host 2",
        "Host 1 (Netokracija)",
        "Voditelj 2",
    ],
)
def test_generic_labels_are_unnamed(spike, label):
    assert spike.is_unnamed(label)


@pytest.mark.parametrize("label", ["Harry Stebbings", "Scott Galloway (Host)", "Guest Hollow"])
def test_person_names_are_named(spike, label):
    assert not spike.is_unnamed(label)


def test_named_share_does_not_count_role_labels(spike):
    segs = [{"speaker": "Host 1"}, {"speaker": "Guest"}, {"speaker": "Jane Doe"}, {"speaker": "Unknown Speaker"}]
    assert spike.named_share(segs) == 0.25
