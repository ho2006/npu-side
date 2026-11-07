import io
import json
import threading
import zipfile
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from npu_side.reproductions import import_package, launch_maps, list_packages, package_detail, train_status, verify_maps
from npu_side.side_infer import InferenceRefused

ARCHIVE = Path(__file__).resolve().parents[2] / "econlab-16c4c09f-34e6-47fb-aef4-6ca3c70f1f5b.zip"
RESEARCH_ID = "16c4c09f-34e6-47fb-aef4-6ca3c70f1f5b"
CONFLICT = "同一 research_id 的内容不一致，已保留原来的问卷"
DIRECT = "这份社交复现包不能当作直接阅读基线"


def _replace(archive: bytes, name: str, payload: bytes) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(archive))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as target:
        replaced = False
        for info in source.infolist():
            if info.filename == name:
                target.writestr(name, payload)
                replaced = True
            else:
                target.writestr(info.filename, source.read(info.filename))
        if not replaced:
            raise AssertionError(name)
    return output.getvalue()


def test_second_import_returns_the_same_card_and_keeps_surveys(tmp_path):
    archive = ARCHIVE.read_bytes()
    first = import_package(tmp_path, archive)
    package = tmp_path / "reproductions" / RESEARCH_ID
    surveys = (package / "surveys.json").read_bytes()
    record = (package / "package.json").read_bytes()
    stale = tmp_path / "reproductions" / f".incoming-{RESEARCH_ID}"
    stale.mkdir()
    (stale / "surveys.json").write_text("[]", encoding="utf-8")
    second = import_package(tmp_path, archive)
    assert second == first
    assert (package / "surveys.json").read_bytes() == surveys
    assert (package / "package.json").read_bytes() == record
    assert not stale.exists()
    stored = json.loads(record)
    assert "compiled" not in stored
    assert "occupancy" not in stored
    assert "matches_fit" not in stored
    listed = list_packages(tmp_path)
    assert listed == [first]
    assert listed[0]["occupancy"] is None
    assert listed[0]["compiled"] is False
    assert listed[0]["matches_fit"] is None
    assert first["research_id"] == RESEARCH_ID
    assert first["status"] == "COMPLETED"
    assert first["choice"]["mode"] == "live"
    assert first["choice"]["design"] == "clarity"
    assert first["choice"]["exposure"] == "social"
    assert first["choice"]["policy_id"] == "cn-digital-2026-v1"
    assert first["choice"]["activation_rate"] == 0.5
    assert first["choice"]["temperature"] == 0.7
    detail = package_detail(tmp_path, RESEARCH_ID)
    assert detail is not None
    assert detail["card"] == first
    assert "surveys" not in detail


def test_changed_archive_with_the_same_research_id_keeps_the_original_surveys(tmp_path):
    archive = ARCHIVE.read_bytes()
    import_package(tmp_path, archive)
    surveys_path = tmp_path / "reproductions" / RESEARCH_ID / "surveys.json"
    original = surveys_path.read_bytes()
    source = zipfile.ZipFile(io.BytesIO(archive))
    surveys = json.loads(source.read("surveys.json"))
    surveys.append({"mutated": True})
    changed = _replace(archive, "surveys.json", json.dumps(surveys).encode("utf-8"))
    with pytest.raises(ValueError) as caught:
        import_package(tmp_path, changed)
    assert str(caught.value) == CONFLICT
    assert surveys_path.read_bytes() == original
    listed = list_packages(tmp_path)
    assert len(listed) == 1
    assert listed[0]["research_id"] == RESEARCH_ID
    assert listed[0]["occupancy"] is None
    assert listed[0]["compiled"] is False


def test_direct_exposure_is_refused(tmp_path):
    archive = ARCHIVE.read_bytes()
    source = zipfile.ZipFile(io.BytesIO(archive))
    config = json.loads(source.read("config.json"))
    config["exposure"] = "direct"
    changed = _replace(archive, "config.json", json.dumps(config, ensure_ascii=False).encode("utf-8"))
    with pytest.raises(ValueError) as caught:
        import_package(tmp_path, changed)
    assert str(caught.value) == DIRECT
    assert list_packages(tmp_path) == []
    assert not (tmp_path / "reproductions" / RESEARCH_ID).exists()


def test_import_joins_twenty_consumers_and_leaves_scores_empty(tmp_path):
    import_package(tmp_path, ARCHIVE.read_bytes())
    package = tmp_path / "reproductions" / RESEARCH_ID
    surveys = (package / "surveys.json").read_bytes()
    detail = package_detail(tmp_path, RESEARCH_ID)
    assert detail is not None
    consumers = detail["consumers"]
    assert len(consumers) == 20
    assert sum(row["high_intent"] for row in consumers) == 12
    portrait = next(row for row in consumers if row["agent_id"] == 1 and row["condition"] == "A0")
    assert portrait["name"] == "消费者 01"
    assert portrait["group"] == "在校学生"
    assert portrait["age"] == 28
    assert portrait["budget"] == 1000
    assert portrait["post_intention"] == 4
    assert portrait["high_intent"] is True
    assert portrait["behavior"] is None
    assert portrait["probability"] is None
    assert portrait["credibility"] is None
    other = next(row for row in consumers if row["agent_id"] == 1 and row["condition"] == "A1")
    assert other["post_intention"] == 4
    assert train_status(tmp_path, RESEARCH_ID) == {
        "phase": "idle",
        "step": 0,
        "total": 6,
        "message": "尚未训练",
        "error": None,
    }
    assert not (package / "side" / "consumer_scores.json").exists()
    assert not (package / "occupancy.json").exists()
    import_package(tmp_path, ARCHIVE.read_bytes())
    assert (package / "surveys.json").read_bytes() == surveys


def test_import_while_training_keeps_the_directory(tmp_path):
    archive = ARCHIVE.read_bytes()
    import_package(tmp_path, archive)
    package = tmp_path / "reproductions" / RESEARCH_ID
    surveys = (package / "surveys.json").read_bytes()
    side = package / "side"
    side.mkdir()
    (side / "train.json").write_text(
        json.dumps(
            {"phase": "fitting", "step": 1, "total": 6, "message": "正在拟合 OLS。", "error": None},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="正在训练"):
        import_package(tmp_path, archive)
    assert (package / "surveys.json").read_bytes() == surveys
    assert (package / "package.json").is_file()
    assert not (tmp_path / "reproductions" / f".incoming-{RESEARCH_ID}").exists()


def install_npu_stand_in(monkeypatch, devices):
    import openvino as ov

    real = ov.Core
    used = []

    class Core:
        available_devices = devices

        def read_model(self, *args, **kwargs):
            return real().read_model(*args, **kwargs)

        def get_property(self, device, name):
            if device == "NPU" and name == "FULL_DEVICE_NAME":
                return "stub-npu"
            raise AssertionError((device, name))

        def compile_model(self, model, device):
            used.append(device)
            if device != "NPU":
                raise AssertionError(device)
            return real().compile_model(model, "CPU")

    monkeypatch.setattr(ov, "Core", lambda: Core())
    return used


def test_cpu_stand_in_writes_twenty_scores_in_range(tmp_path, monkeypatch):
    import npu_occupancy

    used = install_npu_stand_in(monkeypatch, ["CPU", "NPU"])
    captured = {}
    original = npu_occupancy.sustain_npu

    def wrapped(seconds=4):
        captured["seconds"] = seconds
        captured["sample"] = original(seconds)
        return captured["sample"]

    monkeypatch.setattr("npu_side.reproductions.sustain_npu", wrapped)
    archive = ARCHIVE.read_bytes()
    import_package(tmp_path, archive)
    package = tmp_path / "reproductions" / RESEARCH_ID
    surveys = (package / "surveys.json").read_bytes()
    launch_maps(tmp_path, RESEARCH_ID)
    assert (package / "surveys.json").read_bytes() == surveys
    assert captured["seconds"] == 4
    scores = json.loads((package / "side" / "consumer_scores.json").read_text(encoding="utf-8"))
    assert len(scores) == 20
    for row in scores:
        assert 1 <= row["behavior"] <= 5
        assert 0 <= row["probability"] <= 1
        assert 0 <= row["credibility"] <= 1
    detail = package_detail(tmp_path, RESEARCH_ID)
    assert detail is not None
    assert len(detail["consumers"]) == 20
    joined = {(row["condition"], row["seed"], row["agent_id"]): row for row in scores}
    for consumer in detail["consumers"]:
        score = joined[(consumer["condition"], consumer["seed"], consumer["agent_id"])]
        assert consumer["behavior"] == score["behavior"]
        assert consumer["probability"] == score["probability"]
        assert consumer["credibility"] == score["credibility"]
    occupancy = json.loads((package / "occupancy.json").read_text(encoding="utf-8"))
    assert occupancy == captured["sample"]
    assert occupancy["full_name"] == "stub-npu"
    assert isinstance(occupancy["peak_percent"], float)
    assert used
    status = train_status(tmp_path, RESEARCH_ID)
    assert status is not None
    assert status["phase"] == "done"
    assert status["step"] == 6
    assert status["total"] == 6
    assert status["error"] is None


def test_missing_npu_leaves_scores_and_occupancy_absent(tmp_path, monkeypatch):
    install_npu_stand_in(monkeypatch, ["CPU", "GPU"])

    def refuse_sample(seconds=4):
        raise AssertionError(seconds)

    monkeypatch.setattr("npu_side.reproductions.sustain_npu", refuse_sample)
    archive = ARCHIVE.read_bytes()
    import_package(tmp_path, archive)
    package = tmp_path / "reproductions" / RESEARCH_ID
    surveys = (package / "surveys.json").read_bytes()
    with pytest.raises(InferenceRefused, match="旁侧推理未执行"):
        launch_maps(tmp_path, RESEARCH_ID)
    assert (package / "surveys.json").read_bytes() == surveys
    assert not (package / "side" / "consumer_scores.json").exists()
    assert not (package / "occupancy.json").exists()
    assert not (package / "side" / "board.json").exists()
    status = train_status(tmp_path, RESEARCH_ID)
    assert status is not None
    assert status["phase"] == "failed"
    assert status["step"] == 4
    assert status["error"] == "旁侧推理未执行"


def _exchange(server, method, path, body=None, headers=None):
    port = server.server_address[1]
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_second_launch_does_not_start_another_training(tmp_path, monkeypatch):
    import local_api

    archive = ARCHIVE.read_bytes()
    import_package(tmp_path, archive)
    package = tmp_path / "reproductions" / RESEARCH_ID
    surveys = (package / "surveys.json").read_bytes()
    started = threading.Event()
    release = threading.Event()
    calls = []

    def fake_launch(root, research_id, **kwargs):
        calls.append(research_id)
        started.set()
        assert release.wait(5)

    monkeypatch.setattr(local_api.reproductions, "launch_maps", fake_launch)
    local_api._TRAIN_THREADS.clear()
    local_api.App.store = local_api.Store(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), local_api.App)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _exchange(
            server,
            "POST",
            f"/reproductions/{RESEARCH_ID}/launch",
            b"{}",
            {"Content-Type": "application/json"},
        )
        assert status == 200
        body = json.loads(payload.decode("utf-8"))
        assert body["phase"] == "fitting"
        assert body["step"] == 1
        assert body["total"] == 6
        assert body["error"] is None
        assert started.wait(2)
        status, payload = _exchange(
            server,
            "POST",
            f"/reproductions/{RESEARCH_ID}/launch",
            b"{}",
            {"Content-Type": "application/json"},
        )
        assert status == 200
        assert json.loads(payload.decode("utf-8"))["phase"] == "fitting"
        assert calls == [RESEARCH_ID]
        status, payload = _exchange(server, "GET", f"/reproductions/{RESEARCH_ID}/train")
        assert status == 200
        assert json.loads(payload.decode("utf-8"))["phase"] == "fitting"
        status, payload = _exchange(
            server,
            "POST",
            "/reproductions",
            archive,
            {"Content-Type": "application/zip"},
        )
        assert status == 400
        assert "正在训练" in payload.decode("utf-8")
        assert (package / "surveys.json").read_bytes() == surveys
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        local_api._TRAIN_THREADS.clear()


def test_verify_before_compile_leaves_the_surveys_and_occupancy(tmp_path):
    archive = ARCHIVE.read_bytes()
    import_package(tmp_path, archive)
    package = tmp_path / "reproductions" / RESEARCH_ID
    surveys = (package / "surveys.json").read_bytes()
    assert package_detail(tmp_path, "not-a-package") is None
    assert verify_maps(tmp_path, "not-a-package") is None
    with pytest.raises(ValueError) as caught:
        verify_maps(tmp_path, RESEARCH_ID)
    assert str(caught.value) == "尚未在 NPU 上编译"
    assert (package / "surveys.json").read_bytes() == surveys
    assert not (package / "occupancy.json").exists()
    assert not (package / "side" / "check.json").exists()
