import gzip
import json

from getter.config import Config
from getter.ratelimit import RateLimiter
from getter.storage import LocalStore
from getter.worker import fetch, load_checkpoint, run


def make_cfg(**kw):
    base = dict(api_url="http://x", api_key="k", auth_header="Authorization", param_name="offset",
                total=10, step=100, worker_index=0, worker_count=2, qps=1.0, bucket="b",
                prefix="r", batch_size=2)
    base.update(kw)
    return Config(**base)


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.ok = status < 400
        self.text = json.dumps(body)

    def json(self):
        return self._body


class Session:
    def __init__(self, responses=None):
        self.calls, self._responses = [], list(responses or [])

    def get(self, url, params, headers, timeout):
        self.calls.append(params["offset"])
        return self._responses.pop(0) if self._responses else Resp(body={"o": params["offset"]})


class NoWait:
    def wait(self):
        pass


def test_limiter_spaces_calls():
    t = [0.0]
    sleeps = []
    lim = RateLimiter(1.0, clock=lambda: t[0], sleep=lambda s: (sleeps.append(s), t.__setitem__(0, t[0] + s)))
    for _ in range(4):
        lim.wait()
    assert sleeps == [1.0, 1.0, 1.0]


def test_workers_cover_disjoint_halves_and_batch(tmp_path):
    store = LocalStore(str(tmp_path))
    for w in (0, 1):
        s = Session()
        run(make_cfg(worker_index=w), store, session=s, limiter=NoWait())
        assert s.calls == [(10 - i) * 100 for i in range(w, 10, 2)]
    lines = [l for f in (tmp_path / "r" / "w0").glob("batch-*.jsonl.gz") for l in gzip.decompress(f.read_bytes()).decode().splitlines()]
    assert [json.loads(l)["i"] for l in sorted(lines, key=lambda l: json.loads(l)["i"])] == [0, 2, 4, 6, 8]


def test_resume_from_checkpoint(tmp_path):
    store = LocalStore(str(tmp_path))
    cfg = make_cfg()
    n = [0]
    run(cfg, store, session=Session(), limiter=NoWait(), stop=lambda: (n.__setitem__(0, n[0] + 1), n[0] > 2)[1])
    assert load_checkpoint(cfg, store) == 4
    s = Session()
    run(cfg, store, session=s, limiter=NoWait())
    assert s.calls == [600, 400, 200]


def test_failures_are_logged_stored_and_not_retried(caplog):
    for resp in (Resp(429), Resp(500), Resp(404)):
        s = Session([resp, Resp(200, {"ok": 1})])
        with caplog.at_level("WARNING", logger="getter"):
            r = fetch(make_cfg(), s, NoWait(), 700)
        assert r["status"] == resp.status_code and s.calls == [700]
    assert "offset=700 failed: HTTP 404" in caplog.text


def test_exception_is_logged_and_run_continues(tmp_path, caplog):
    class Boom(Session):
        def get(self, url, params, headers, timeout):
            if params["offset"] == 1000:  # counter 10 at i=0 -> offset 1000
                raise ConnectionError("down")
            return super().get(url, params, headers, timeout)

    store = LocalStore(str(tmp_path))
    with caplog.at_level("WARNING", logger="getter"):
        run(make_cfg(), store, session=Boom(), limiter=NoWait())
    recs = [json.loads(l) for f in (tmp_path / "r" / "w0").glob("batch-*.jsonl.gz")
            for l in gzip.decompress(f.read_bytes()).decode().splitlines()]
    assert len(recs) == 5
    assert [r for r in recs if r["status"] is None][0]["error"].startswith("ConnectionError")
    assert "ConnectionError" in caplog.text


def test_flush_failure_keeps_records_and_continues(tmp_path):
    class Flaky(LocalStore):
        fail = True

        def put(self, key, data):
            if Flaky.fail:
                Flaky.fail = False
                raise OSError("s3 down")
            super().put(key, data)

    store = Flaky(str(tmp_path))
    run(make_cfg(batch_size=2), store, session=Session(), limiter=NoWait())
    recs = [json.loads(l) for f in (tmp_path / "r" / "w0").glob("batch-*.jsonl.gz")
            for l in gzip.decompress(f.read_bytes()).decode().splitlines()]
    assert sorted(r["i"] for r in recs) == [0, 2, 4, 6, 8]
