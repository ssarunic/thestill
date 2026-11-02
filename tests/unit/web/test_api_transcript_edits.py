"""Spec #110 §6–§7 — the transcript editor routes: permission gate and refusals."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from thestill.core.transcript_edits import EditConflict, EffectiveTranscript, SpeakerLabel
from thestill.models.annotated_transcript import AnnotatedTranscript
from thestill.models.transcript_edits import TranscriptEdit
from thestill.services.transcript_edit_service import ApplyResult, EditorState, FinishResult, TranscriptEditError
from thestill.web.dependencies import get_app_state, require_auth
from thestill.web.routes import api_transcript_edits

from .auth_harness import ADMIN_USER, PLAIN_USER, auth_client

ROUTERS = [(api_transcript_edits.router, "/api/episodes")]

ENDPOINTS = [
    ("GET", "/api/episodes/ep-1/transcript/edits", None),
    ("POST", "/api/episodes/ep-1/transcript/edits", {"base_seq": 0, "session_id": "s", "ops": [{"op": "x"}]}),
    ("POST", "/api/episodes/ep-1/transcript/edits/finish", {"session_id": "s"}),
    ("POST", "/api/episodes/ep-1/transcript/edits/save-host", {"name": "Grace"}),
    ("GET", "/api/episodes/ep-1/transcript/edits/people?q=gr", None),
    ("GET", "/api/episodes/ep-1/transcript/edits/audio", None),
    ("POST", "/api/episodes/ep-1/transcript/edits/e-1/revert", {"base_seq": 1, "session_id": "s"}),
]


class TestPermissionGate:
    """Every route depends on ``transcript.edit``: 401 anonymous, 403 non-admin."""

    @pytest.mark.parametrize("method,path,body", ENDPOINTS)
    def test_anonymous_gets_401(self, method, path, body):
        client, _ = auth_client(ROUTERS, multi_user=True, current_user=None)
        assert client.request(method, path, json=body).status_code == 401

    @pytest.mark.parametrize("method,path,body", ENDPOINTS)
    def test_non_admin_gets_403(self, method, path, body):
        client, _ = auth_client(ROUTERS, multi_user=True, current_user=PLAIN_USER)
        response = client.request(method, path, json=body)
        assert response.status_code == 403
        assert response.json()["detail"] == "Permission required: transcript.edit"

    @pytest.mark.parametrize("method,path,body", ENDPOINTS)
    def test_admin_passes_gate(self, method, path, body):
        client, _ = auth_client(ROUTERS, multi_user=True, current_user=ADMIN_USER)
        assert client.request(method, path, json=body).status_code not in (401, 403)

    @pytest.mark.parametrize("method,path,body", ENDPOINTS)
    def test_single_user_mode_passes_gate(self, method, path, body):
        client, _ = auth_client(ROUTERS, multi_user=False, current_user=PLAIN_USER)
        assert client.request(method, path, json=body).status_code not in (401, 403)

    def test_no_route_uses_require_admin(self):
        from thestill.web.dependencies import require_admin

        for route in api_transcript_edits.router.routes:
            names = {getattr(d.call, "__name__", "") for d in route.dependant.dependencies}
            assert require_admin.__name__ not in names, route.path
            assert any(name == "dependency" for name in names), f"{route.path} has no permission guard"


def _edit(seq: int, op: str = "speaker.rename_label") -> TranscriptEdit:
    return TranscriptEdit(
        id=f"e-{seq}",
        episode_id="ep-1",
        seq=seq,
        op=op,
        raw_sha256="a" * 64,
        anchor={"label": "SPEAKER_01"},
        args={"name": "Grace Hopper", "entity_id": None},
        before={"name": "SPEAKER_01"},
        session_id="s",
        actor_kind="web",
        created_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
    )


def _client(service):
    app = FastAPI()
    app.include_router(api_transcript_edits.router, prefix="/api/episodes", dependencies=[Depends(require_auth)])
    state = MagicMock()
    state.config.multi_user = False
    state.auth_service.get_current_user.return_value = ADMIN_USER
    state.transcript_edit_service = service
    app.dependency_overrides[get_app_state] = lambda: state
    return TestClient(app, raise_server_exceptions=False)


def test_get_state_reports_every_edit_status_and_the_speaker_panel():
    applied, conflicting, reverted, revert = _edit(1), _edit(2, "segment.set_text"), _edit(3), _edit(4, "revert")
    revert = revert.model_copy(update={"reverts": reverted.id, "anchor": {"edit_id": reverted.id}})
    effective = EffectiveTranscript(
        transcript=AnnotatedTranscript(episode_id="ep-1", segments=[]),
        edits=[applied, conflicting, reverted, revert],
        applied=[applied],
        conflicts=[EditConflict(conflicting.id, 2, "segment.set_text", "text_changed", {"current_text": "x"})],
        reverted_ids=frozenset({reverted.id}),
        last_seq=4,
        raw_sha256="a" * 64,
        speakers=[SpeakerLabel("SPEAKER_01", "Grace Hopper", None, [1, 3])],
        labels_by_segment={1: "SPEAKER_01", 3: "SPEAKER_01"},
    )
    service = MagicMock()
    service.get_state.return_value = EditorState(
        episode=MagicMock(id="ep-1"), podcast=MagicMock(), effective=effective, busy=False, summary_stale_edits=2
    )
    body = _client(service).get("/api/episodes/ep-1/transcript/edits").json()
    assert [e["status"] for e in body["edits"]] == ["applied", "conflict", "reverted", "revert"]
    assert body["edits"][1]["conflict"]["reason"] == "text_changed"
    assert body["conflicts"][0]["edit_id"] == conflicting.id
    assert body["speakers"] == [
        {"label": "SPEAKER_01", "name": "Grace Hopper", "entity_id": None, "segment_ids": [1, 3]}
    ]
    assert body["summary_stale_edits"] == 2 and body["last_seq"] == 4 and body["busy"] is False


def test_apply_returns_the_rows_and_maps_refusals():
    service = MagicMock()
    service.apply.return_value = ApplyResult(applied=[_edit(1)], last_seq=1)
    client = _client(service)
    body = {
        "base_seq": 0,
        "session_id": "s",
        "ops": [{"op": "speaker.rename_label", "anchor": {"label": "SPEAKER_01"}, "args": {"name": "Grace Hopper"}}],
    }
    response = client.post("/api/episodes/ep-1/transcript/edits", json=body)
    assert response.status_code == 200 and response.json()["last_seq"] == 1
    kwargs = service.apply.call_args.kwargs
    assert kwargs["base_seq"] == 0 and kwargs["actor"].user_id == ADMIN_USER.id and kwargs["report_id"] is None

    service.apply.side_effect = TranscriptEditError("stale", "moved", last_seq=3, edits=[_edit(2), _edit(3)])
    response = client.post("/api/episodes/ep-1/transcript/edits", json=body)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert (
        detail["error"] == "stale_base_seq"
        and detail["last_seq"] == 3
        and [e["seq"] for e in detail["edits"]] == [2, 3]
    )

    service.apply.side_effect = TranscriptEditError("busy", "busy")
    assert client.post("/api/episodes/ep-1/transcript/edits", json=body).status_code == 409

    service.apply.side_effect = TranscriptEditError("rejected", "no", conflict={"reason": "label_missing"})
    response = client.post("/api/episodes/ep-1/transcript/edits", json=body)
    assert response.status_code == 409 and response.json()["detail"]["error"] == "edit_rejected"

    service.apply.side_effect = TranscriptEditError("invalid", "bad op")
    assert client.post("/api/episodes/ep-1/transcript/edits", json=body).status_code == 422

    service.apply.side_effect = TranscriptEditError("not_found", "nope")
    assert client.post("/api/episodes/ep-1/transcript/edits", json=body).status_code == 404

    assert (
        client.post(
            "/api/episodes/ep-1/transcript/edits", json={"base_seq": 0, "session_id": "s", "ops": []}
        ).status_code
        == 422
    )


def test_finish_save_host_people_and_revert_delegate():
    service = MagicMock()
    service.finish.return_value = FinishResult(session_ops=2, refresh="queued", task_id="t-1")
    service.save_as_host.return_value = ["Grace Hopper"]
    service.search_people.return_value = [
        {"id": "person:grace-hopper", "canonical_name": "Grace Hopper", "matched_alias": None}
    ]
    service.revert.return_value = ApplyResult(applied=[_edit(5, "revert")], last_seq=5)
    client = _client(service)
    assert (
        client.post("/api/episodes/ep-1/transcript/edits/finish", json={"session_id": "s"}).json()["task_id"] == "t-1"
    )
    assert client.post("/api/episodes/ep-1/transcript/edits/save-host", json={"name": "Grace Hopper"}).json()[
        "hosts"
    ] == ["Grace Hopper"]
    assert (
        client.get("/api/episodes/ep-1/transcript/edits/people?q=gr").json()["people"][0]["id"] == "person:grace-hopper"
    )
    response = client.post("/api/episodes/ep-1/transcript/edits/e-1/revert", json={"base_seq": 4, "session_id": "s"})
    assert response.status_code == 200 and response.json()["last_seq"] == 5
    assert service.revert.call_args.args[:2] == ("ep-1", "e-1")


def test_audio_route_serves_the_local_copy_or_404(tmp_path):
    """Spec #110 §5.6 — the editor plays the file the timestamps were made from."""
    service = MagicMock()
    service._episode.return_value = (MagicMock(), MagicMock(id="ep-1"))
    audio = tmp_path / "ep.mp3"
    audio.write_bytes(b"ID3fake-mp3-bytes")
    service.local_audio_path.return_value = audio
    client = _client(service)
    response = client.get("/api/episodes/ep-1/transcript/edits/audio")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.content == b"ID3fake-mp3-bytes"

    service.local_audio_path.return_value = None
    response = client.get("/api/episodes/ep-1/transcript/edits/audio")
    assert response.status_code == 404 and response.json()["detail"]["error"] == "no_local_audio"


def test_missing_service_is_503():
    client = _client(None)
    assert client.get("/api/episodes/ep-1/transcript/edits").status_code == 503
