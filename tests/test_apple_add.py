"""Apple rejects some batched adds (seen live: favorites with 25 ids -> 400, single id -> 202)."""
from crossfade.models import LIKED, ApiError, Track
from crossfade.providers.apple import AppleProvider


def make(fail_batches: bool, bad: set[str]):
    am = AppleProvider.__new__(AppleProvider)
    calls: list[list[str]] = []

    def req(method, path, params=None, body=None, user=True, _attempt=0):
        ids = params["ids[songs]"].split(",")
        calls.append(ids)
        if (fail_batches and len(ids) > 1) or bad & set(ids):
            raise ApiError("HTTP 400", 400)
        return {}

    am._req = req
    return am, calls


def tracks(n):
    return [Track(service="apple", id=str(i), title=f"t{i}", artist="a") for i in range(n)]


def test_batch_rejected_falls_back_to_single_ids(monkeypatch):
    monkeypatch.setattr("crossfade.providers.apple.time.sleep", lambda s: None)
    am, calls = make(fail_batches=True, bad={"3"})
    failed = am.add(LIKED, "", tracks(5))
    assert failed == ["3"]
    assert calls[0] == ["0", "1", "2", "3", "4"]
    assert all(len(c) == 1 for c in calls[1:])


def test_batch_ok_needs_one_call(monkeypatch):
    monkeypatch.setattr("crossfade.providers.apple.time.sleep", lambda s: None)
    am, calls = make(fail_batches=False, bad=set())
    assert am.add(LIKED, "", tracks(30)) == []
    assert len(calls) == 2
