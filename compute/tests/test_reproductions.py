import io
import json
import zipfile
from pathlib import Path

import pytest

from npu_side.reproductions import import_package, list_packages, package_detail, verify_maps

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
