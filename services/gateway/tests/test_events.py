import itertools
import json

import fakeredis
from app.api.events import event_stream
from rp_shared.events import STREAM, build_event


def _add(r, type_):
    return r.xadd(STREAM, {"event": json.dumps(build_event(type_, {"n": 1}))})


def test_relays_ui_events_only_and_resumes_from_last_id():
    r = fakeredis.FakeRedis(decode_responses=True)
    first = _add(r, "job.progress")
    gen = event_stream(r, start_id="0-0", block_ms=10)
    assert next(gen).startswith("retry:")
    frame = next(gen)
    assert f"id: {first}" in frame and "event: job.progress" in frame
    _add(r, "profile.updated")  # internal event: filtered out
    _add(r, "report.generated")
    frames = list(itertools.islice(gen, 2))
    assert any("event: report.generated" in f for f in frames)
    assert not any("profile.updated" in f for f in frames)


def test_idle_stream_sends_heartbeat():
    r = fakeredis.FakeRedis(decode_responses=True)
    gen = event_stream(r, start_id=None, block_ms=10)
    next(gen)
    assert next(gen) == ": ping\n\n"


def test_endpoint_requires_auth(client):
    assert client.get("/api/events/stream").status_code == 401
