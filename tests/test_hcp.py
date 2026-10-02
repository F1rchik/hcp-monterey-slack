from datetime import datetime, timezone

import hcp


class FakeResp:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.headers = data, status, {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, pages):
        self.pages, self.calls, self.headers = pages, [], {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return FakeResp(self.pages[params["page"] - 1])


def test_auth_header_and_pagination_stop():
    pages = [
        {"total_pages": 3, "estimates": [{"id": "a", "created_at": "2026-09-18T00:00:00Z"},
                                         {"id": "b", "created_at": "2026-09-10T00:00:00Z"}]},
        {"total_pages": 3, "estimates": [{"id": "c", "created_at": "2026-08-01T00:00:00Z"}]},
        {"total_pages": 3, "estimates": [{"id": "d", "created_at": "2026-07-01T00:00:00Z"}]},
    ]
    session = FakeSession(pages)
    client = hcp.HCPClient("KEY", "LOC", session=session)
    got = client.recent_estimates(datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert [e["id"] for e in got] == ["a", "b"]
    assert session.headers["Authorization"] == "Token KEY"
    assert session.headers["X-Company-Id"] == "LOC"
    assert len(session.calls) == 2  # stopped on page 2 at the first too-old estimate
    assert session.calls[0][0] == "https://api.housecallpro.com/estimates"


def test_jobs_window_is_half_open_and_expands_attachments():
    start = datetime(2026, 9, 17, 7, tzinfo=timezone.utc)
    end = datetime(2026, 9, 18, 7, tzinfo=timezone.utc)
    pages = [{"total_pages": 1, "jobs": [
        {"id": "in", "schedule": {"scheduled_start": "2026-09-17T07:00:00Z"}},
        {"id": "edge", "schedule": {"scheduled_start": "2026-09-18T07:00:00Z"}},
    ]}]
    session = FakeSession(pages)
    jobs = hcp.HCPClient("KEY", "LOC", session=session).jobs_scheduled_between(start, end)
    assert [j["id"] for j in jobs] == ["in"]
    params = session.calls[0][1]
    assert params["expand[]"] == "attachments"
    assert params["scheduled_start_min"] == "2026-09-17T07:00:00Z"


def test_parse_ts_naive_is_utc():
    assert hcp.parse_ts("2026-09-17T15:30:00") == datetime(2026, 9, 17, 15, 30, tzinfo=timezone.utc)
    assert hcp.parse_ts("garbage") is None
