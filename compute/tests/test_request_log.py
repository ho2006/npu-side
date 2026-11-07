import json
import threading
import time
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

import local_api
from request_log import RequestRecord, read_requests, write_request


def _running(store):
    local_api.App.store = store
    server = ThreadingHTTPServer(("127.0.0.1", 0), local_api.App)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _exchange(server, method, path, body=None, headers=None):
    port = server.server_address[1]
    refusal = None
    for _ in range(30):
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        except ConnectionRefusedError as error:
            refusal = error
            time.sleep(0.05)
        finally:
            connection.close()
    raise refusal


def test_get_root_writes_one_closed_record(tmp_path):
    store = local_api.Store(tmp_path)
    server, thread = _running(store)
    try:
        status, payload = _exchange(server, "GET", "/")
    finally:
        _stop(server, thread)
    assert status == 200
    assert json.loads(payload.decode("utf-8")) == {"ok": True}
    found = read_requests(store.root / "access")
    assert len(found) == 1
    record = found[0]
    assert record.phase == "closed"
    assert record.path == "/"
    assert record.status == 200
    assert record.bytes_out == len(payload)
    assert record.detail is None
    assert record.error_type is None
    assert type(record.duration_ms) is int and record.duration_ms >= 0
    names = sorted(path.name for path in (store.root / "access").glob("*.json"))
    assert names == [f"{record.request_id.hex}.json"]


def test_non_json_post_is_400_with_the_same_detail(tmp_path):
    store = local_api.Store(tmp_path)
    server, thread = _running(store)
    body = b"not-json"
    try:
        status, payload = _exchange(
            server,
            "POST",
            "/research/plan",
            body,
            {"Content-Type": "application/json"},
        )
    finally:
        _stop(server, thread)
    client = json.loads(payload.decode("utf-8"))
    found = read_requests(store.root / "access")
    assert status == 400
    assert len(found) == 1
    record = found[0]
    assert record.phase == "closed"
    assert record.detail == client["detail"]
    assert record.detail == "请求不是 JSON"
    assert record.error_type is None


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_kill_during_run_leaves_one_open_record(tmp_path, monkeypatch):
    class Killed(BaseException):
        pass

    def killed(store, record):
        raise Killed()

    monkeypatch.setattr(local_api, "run_experiment", killed)
    experiment_id = "exp1"
    store = local_api.Store(tmp_path)
    store.save_experiment({"id": experiment_id, "status": "CREATED"})
    body = b"{}"
    server, thread = _running(store)
    try:
        try:
            _exchange(server, "POST", f"/experiments/{experiment_id}/run", body)
        except Exception:
            pass
        found = read_requests(store.root / "access")
    finally:
        _stop(server, thread)
    assert len(found) == 1
    record = found[0]
    assert record.phase == "open"
    assert record.experiment_id == experiment_id
    assert record.bytes_in == len(body)
    assert record.status is None
    assert record.duration_ms is None
    assert record.bytes_out is None
    assert record.detail is None
    assert record.error_type is None


def test_second_close_keeps_the_first_status(tmp_path):
    opened = RequestRecord.started("POST", "/experiments/exp1/run", 2)
    write_request(tmp_path, opened)
    first = opened.finished(
        status=400,
        bytes_out=len('{"detail": "请求不是 JSON"}'.encode("utf-8")),
        duration_ms=1,
        detail="请求不是 JSON",
        error_type=None,
    )
    write_request(tmp_path, first)
    destination = tmp_path / f"{opened.request_id.hex}.json"
    kept = destination.read_bytes()
    second = opened.finished(
        status=200,
        bytes_out=2,
        duration_ms=5,
        detail=None,
        error_type=None,
    )
    write_request(tmp_path, second)
    assert destination.read_bytes() == kept
    found = read_requests(tmp_path)
    assert len(found) == 1
    assert found[0].status == 400
    assert found[0].detail == "请求不是 JSON"
