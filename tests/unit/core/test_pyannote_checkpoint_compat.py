# Copyright 2025 thestill.ai
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""pyannote checkpoints must load under torch's weights_only policy, via hub 1.x.

torch 2.6 / pytorch-lightning 2.6 made ``torch.load(weights_only=True)`` the
default on the path pyannote uses to load whisperx's VAD checkpoint and the
diarization models. Without an allowlist the VAD load raised and
``WhisperXTranscriber`` silently fell back to plain Whisper; the diarization
load raised and speakers were skipped. huggingface-hub 1.x also dropped the
``use_auth_token`` keyword pyannote 3.x passes it. These tests run without
torch, whisperx or pyannote installed.
"""

import sys
import types
from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

from thestill.core import whisper_transcriber as wt
from thestill.utils.console import ConsoleOutput


def test_resolve_skips_missing_modules_and_attributes():
    resolved = wt._resolve_checkpoint_globals(
        (
            "builtins.list",
            "collections.defaultdict",
            "no_such_module_xyz.Thing",
            "builtins.no_such_attr",
        )
    )
    import collections

    assert resolved == [list, collections.defaultdict]


def test_every_configured_global_is_a_dotted_path():
    for dotted in wt._PYANNOTE_CHECKPOINT_GLOBALS:
        module_name, _, attr = dotted.rpartition(".")
        assert module_name and attr, dotted


def test_context_is_noop_without_torch(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "torch.serialization", None)
    assert isinstance(wt.pyannote_checkpoint_safe_globals(), nullcontext)


def test_context_uses_torch_safe_globals_when_available(monkeypatch):
    calls = []

    class FakeSafeGlobals:
        def __init__(self, objs):
            calls.append(list(objs))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    torch_mod = types.ModuleType("torch")
    ser_mod = types.ModuleType("torch.serialization")
    ser_mod.safe_globals = FakeSafeGlobals
    torch_mod.serialization = ser_mod
    monkeypatch.setitem(sys.modules, "torch", torch_mod)
    monkeypatch.setitem(sys.modules, "torch.serialization", ser_mod)

    ctx = wt.pyannote_checkpoint_safe_globals()

    assert isinstance(ctx, FakeSafeGlobals)
    # Stdlib entries always resolve; the torch/omegaconf/pyannote ones only
    # when those packages are importable, so check the guaranteed subset.
    assert {list, dict, int}.issubset(set(calls[0]))


def test_load_model_wraps_whisperx_load_in_the_allowlist(monkeypatch):
    state = {"inside": False, "loaded_inside": None}

    class RecordingContext:
        def __enter__(self):
            state["inside"] = True
            return self

        def __exit__(self, *exc):
            state["inside"] = False
            return False

    fake_whisperx = MagicMock()

    def load_model(*_args, **_kwargs):
        state["loaded_inside"] = state["inside"]
        return object()

    fake_whisperx.load_model.side_effect = load_model
    monkeypatch.setattr(wt, "whisperx", fake_whisperx, raising=False)
    monkeypatch.setattr(wt, "WHISPERX_AVAILABLE", True)
    monkeypatch.setattr(wt, "pyannote_checkpoint_safe_globals", RecordingContext)
    monkeypatch.setattr(wt, "resolve_hybrid_devices", lambda device, console=None: ("cpu", "cpu", "cpu"))

    transcriber = wt.WhisperXTranscriber(model_name="base", device="cpu", console=ConsoleOutput())
    transcriber.load_model()

    assert state["loaded_inside"] is True
    assert transcriber._model is not None
    assert transcriber._whisper_fallback is None
    fake_whisperx.load_model.assert_called_once_with("base", device="cpu", compute_type="int8")


def test_load_model_still_falls_back_when_whisperx_raises(monkeypatch):
    fake_whisperx = MagicMock()
    fake_whisperx.load_model.side_effect = RuntimeError("boom")
    monkeypatch.setattr(wt, "whisperx", fake_whisperx, raising=False)
    monkeypatch.setattr(wt, "WHISPERX_AVAILABLE", True)
    monkeypatch.setattr(wt, "pyannote_checkpoint_safe_globals", nullcontext)
    monkeypatch.setattr(wt, "resolve_hybrid_devices", lambda device, console=None: ("cpu", "cpu", "cpu"))
    monkeypatch.setattr(
        wt.WhisperXTranscriber, "_load_whisper_fallback", lambda self: setattr(self, "_whisper_fallback", "fallback")
    )

    transcriber = wt.WhisperXTranscriber(model_name="base", device="cpu", console=ConsoleOutput())
    transcriber.load_model()

    assert transcriber._model is None
    assert transcriber._whisper_fallback == "fallback"


def _install_fake_hub(monkeypatch, accepts_use_auth_token: bool):
    """Register a fake huggingface_hub whose hf_hub_download matches the given API."""
    calls = []
    hub = types.ModuleType("huggingface_hub")
    if accepts_use_auth_token:

        def hf_hub_download(repo_id, filename, use_auth_token=None, token=None, **kw):
            calls.append({"use_auth_token": use_auth_token, "token": token})
            return "/path"

    else:

        def hf_hub_download(repo_id, filename, token=None, **kw):
            calls.append({"token": token})
            return "/path"

    hub.hf_hub_download = hf_hub_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    return hub, calls


def _install_fake_pyannote_module(monkeypatch, name: str, hub):
    module = types.ModuleType(name)
    module.hf_hub_download = hub.hf_hub_download  # ``from huggingface_hub import hf_hub_download``
    monkeypatch.setitem(sys.modules, name, module)
    return module


def test_hub_shim_is_noop_when_hub_still_accepts_use_auth_token(monkeypatch):
    hub, _ = _install_fake_hub(monkeypatch, accepts_use_auth_token=True)
    module = _install_fake_pyannote_module(monkeypatch, "fake_pyannote_old", hub)
    monkeypatch.setattr(wt, "_PYANNOTE_HUB_PATCHED", False)

    assert wt._patch_pyannote_hub_token_kwarg(("fake_pyannote_old",)) == 0
    assert module.hf_hub_download is hub.hf_hub_download
    assert wt._PYANNOTE_HUB_PATCHED is False


def test_hub_shim_rewrites_use_auth_token_to_token(monkeypatch):
    hub, calls = _install_fake_hub(monkeypatch, accepts_use_auth_token=False)
    module = _install_fake_pyannote_module(monkeypatch, "fake_pyannote_new", hub)
    monkeypatch.setattr(wt, "_PYANNOTE_HUB_PATCHED", False)

    assert wt._patch_pyannote_hub_token_kwarg(("fake_pyannote_new", "no_such_module_xyz")) == 1

    # The pyannote 3.x call shape now reaches the hub 1.x signature intact.
    assert module.hf_hub_download("pyannote/x", "config.yaml", use_auth_token="hf_abc") == "/path"
    assert calls == [{"token": "hf_abc"}]

    # An explicit ``token`` wins over ``use_auth_token``; the old kwarg never leaks through.
    module.hf_hub_download("pyannote/x", "config.yaml", use_auth_token="old", token="new")
    assert calls[-1] == {"token": "new"}

    # Idempotent: a second call patches nothing and does not double-wrap.
    wrapped = module.hf_hub_download
    assert wt._patch_pyannote_hub_token_kwarg(("fake_pyannote_new",)) == 0
    assert module.hf_hub_download is wrapped


def test_hub_shim_treats_missing_hub_as_compatible(monkeypatch):
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    assert wt._hub_download_accepts_use_auth_token() is True


@pytest.mark.skipif(
    not (wt.WHISPERX_AVAILABLE and wt.PYANNOTE_AVAILABLE),
    reason="whisperx + pyannote (local-transcription extra) required",
)
def test_bundled_vad_checkpoint_loads_under_the_allowlist():
    """Real check on laptops with the local-transcription extra installed."""
    import os

    import torch
    import whisperx

    checkpoint = os.path.join(os.path.dirname(whisperx.__file__), "assets", "pytorch_model.bin")
    with wt.pyannote_checkpoint_safe_globals():
        loaded = torch.load(checkpoint, map_location="cpu", weights_only=True)
    assert "state_dict" in loaded
