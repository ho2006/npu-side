"""Store a finished EconLab run as its own directory and compile those survey rows later."""

import io
import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NewType, TypedDict

from npu_occupancy import sustain_npu
from npu_side.io import write_json
from npu_side.policy import fingerprint, policy_for
from npu_side.side_infer import SideCheck, _agent_rows, compile_survey_maps, verify_survey_maps

ResearchId = NewType("ResearchId", str)

_MEMBER_CAP = 1_048_576
_MEMBERS = (
    "passport.json",
    "config.json",
    "plan.json",
    "surveys.json",
    "population.json",
    "network.json",
    "events.jsonl",
    "metrics.json",
)
_RESEARCH_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SAFE_INT = 2**53 - 1
_CONFLICT = "同一 research_id 的内容不一致，已保留原来的问卷"
_DIRECT = "这份社交复现包不能当作直接阅读基线"
_NOT_COMPILED = "尚未在 NPU 上编译"


class PackageChoice(TypedDict):
    mode: Literal["live"]
    design: Literal["clarity"]
    exposure: Literal["social"]
    policy_id: Literal["cn-digital-2026-v1"]
    activation_rate: Literal[0.5]
    temperature: Literal[0.7]
    agent_count: int
    rounds: int
    seeds: list[int]


class OccupancySample(TypedDict):
    device: Literal["NPU"]
    full_name: str
    peak_percent: float
    engine: str
    burst_inferences: int


class PackageCard(TypedDict):
    research_id: str
    status: Literal["COMPLETED"]
    title: str
    choice: PackageChoice
    occupancy: OccupancySample | None
    compiled: bool
    matches_fit: bool | None


class PackageDetail(TypedDict):
    card: PackageCard
    metrics: dict
    population: dict
    events: dict


@dataclass(frozen=True)
class _ParsedPackage:
    """Domain record after the zip boundary. Not returned to HTTP."""

    research_id: ResearchId
    title: str
    choice: PackageChoice
    policy_config: dict
    surveys: list
    population: dict
    events: dict
    metrics: dict
    content_sha256: str


def import_package(root: Path, archive: bytes) -> PackageCard:
    """Store one archive under reproductions/{research_id} and return its card.

    A second archive with the same research id and the same content hash returns
    the stored card. surveys.json is not opened for write. A different hash for
    that id raises ValueError and does not replace the directory.
    """
    parsed = _parse_archive(archive)
    base = root / "reproductions"
    base.mkdir(parents=True, exist_ok=True)
    final = base / str(parsed.research_id)
    incoming = base / f".incoming-{parsed.research_id}"
    _remove(incoming)
    if final.exists():
        package_path = final / "package.json"
        if package_path.is_file():
            stored = _load_json(package_path)
            if isinstance(stored, dict) and stored.get("content_sha256") == parsed.content_sha256:
                return _card(final)
        raise ValueError(_CONFLICT)
    incoming.mkdir()
    try:
        _write_new(incoming, parsed)
        os.rename(incoming, final)
    except Exception:
        _remove(incoming)
        raise
    return _card(final)


def list_packages(root: Path) -> list[PackageCard]:
    """Cards for every published directory, sorted by research id."""
    base = root / "reproductions"
    if not base.is_dir():
        return []
    cards = []
    for child in base.iterdir():
        if not child.is_dir() or child.name.startswith(".") or _RESEARCH_ID.fullmatch(child.name) is None:
            continue
        if not (child / "package.json").is_file():
            continue
        cards.append(_card(child))
    cards.sort(key=lambda card: card["research_id"])
    return cards


def package_detail(root: Path, research_id: str) -> PackageDetail | None:
    """Return the card plus the stored metrics, population, and events."""
    final = _published(root, research_id)
    if final is None:
        return None
    return {
        "card": _card(final),
        "metrics": _load_json(final / "metrics.json"),
        "population": _load_json(final / "population.json"),
        "events": _load_json(final / "events.json"),
    }


def launch_maps(root: Path, research_id: str) -> PackageCard | None:
    """Fit and compile the survey rows on the NPU, then sample this process.

    Reads surveys.json and does not write it. Replaces side/ and occupancy.json
    only after compile_survey_maps and sustain_npu both return.
    """
    final = _published(root, research_id)
    if final is None:
        return None
    surveys = _load_json(final / "surveys.json")
    if not isinstance(surveys, list):
        raise ValueError("问卷无法读取")
    policy = policy_for(_load_json(final / "policy_config.json"))
    dest = final.parent / f".launch-{research_id}"
    _remove(dest)
    try:
        compile_survey_maps(surveys, policy, dest)
        sample = sustain_npu(seconds=4)
    except Exception:
        _remove(dest)
        raise
    _replace_tree(final / "side", dest)
    write_json(final / "occupancy.json", sample)
    return _card(final)


def verify_maps(root: Path, research_id: str) -> SideCheck | None:
    """Recompile the saved IR and score the stored survey rows.

    Does not write surveys.json or occupancy.json. Replaces side/check.json
    only after the new check is complete.
    """
    final = _published(root, research_id)
    if final is None:
        return None
    side_dir = final / "side"
    if not (side_dir / "board.json").is_file():
        raise ValueError(_NOT_COMPILED)
    surveys = _load_json(final / "surveys.json")
    if not isinstance(surveys, list):
        raise ValueError("问卷无法读取")
    policy = policy_for(_load_json(final / "policy_config.json"))
    check = verify_survey_maps(surveys, policy, side_dir)
    write_json(side_dir / "check.json", check)
    return check


def _parse_archive(archive: bytes) -> _ParsedPackage:
    """The only function that knows zip member names."""
    try:
        bundle = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile as error:
        raise ValueError("复现包不是 zip") from error
    with bundle:
        for info in bundle.infolist():
            if info.file_size > _MEMBER_CAP:
                raise ValueError("复现包里有超过 1MiB 的文件")
        names = set(bundle.namelist())
        for name in _MEMBERS:
            if name not in names:
                raise ValueError("复现包缺少必要文件")
        try:
            raw = {name: bundle.read(name) for name in _MEMBERS}
        except (OSError, zipfile.BadZipFile, RuntimeError) as error:
            raise ValueError("复现包无法读取") from error
    passport = _json_object(raw["passport.json"], "护照无法读取")
    config = _json_object(raw["config.json"], "研究设置不在可选范围内")
    plan = _json_object(raw["plan.json"], "复现包缺少题目")
    surveys = _json_value(raw["surveys.json"], "问卷无法读取")
    people = _json_value(raw["population.json"], "人口或关系无法读取")
    edges = _json_value(raw["network.json"], "人口或关系无法读取")
    metrics = _json_object(raw["metrics.json"], "指标无法读取")
    if not isinstance(surveys, list):
        raise ValueError("问卷无法读取")
    if not isinstance(metrics.get("comparison"), list):
        raise ValueError("指标无法读取")
    title = plan.get("title")
    if not isinstance(title, str) or title == "":
        raise ValueError("复现包缺少题目")
    research_id = passport.get("research_id")
    if not isinstance(research_id, str) or _RESEARCH_ID.fullmatch(research_id) is None:
        raise ValueError("research_id 无法读取")
    choice = _choice_from(config)
    if passport.get("status") != "COMPLETED" or passport.get("mode") != choice["mode"]:
        raise ValueError("复现包不是已完成的 live 运行")
    snapshot = config.get("policy_snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("id") != choice["policy_id"]:
        raise ValueError("政策快照与 policy_id 不一致")
    policy_config = {"policy_id": choice["policy_id"], "policy_snapshot": snapshot}
    population = _population(people, edges)
    events = _events(raw["events.jsonl"])
    if len(_paired_rows(surveys, policy_config)) < 2:
        raise ValueError("问卷无法配成前后测")
    content_sha256 = fingerprint(
        {
            "surveys": surveys,
            "population": population,
            "events": events,
            "metrics": metrics,
            "choice": choice,
            "policy_snapshot": snapshot,
        }
    )
    return _ParsedPackage(
        research_id=ResearchId(research_id),
        title=title,
        choice=choice,
        policy_config=policy_config,
        surveys=surveys,
        population=population,
        events=events,
        metrics=metrics,
        content_sha256=content_sha256,
    )


def _card(package_dir: Path) -> PackageCard:
    """Join package.json with the derived occupancy, compiled, and matches_fit facts."""
    meta = _load_json(package_dir / "package.json")
    if not isinstance(meta, dict):
        raise ValueError("复现包无法读取")
    if meta.get("research_id") != package_dir.name or meta.get("status") != "COMPLETED":
        raise ValueError("复现包无法读取")
    title = meta.get("title")
    if not isinstance(title, str) or title == "":
        raise ValueError("复现包无法读取")
    choice = meta.get("choice")
    if not isinstance(choice, dict):
        raise ValueError("复现包无法读取")
    return {
        "research_id": package_dir.name,
        "status": "COMPLETED",
        "title": title,
        "choice": _choice_from(choice),
        "occupancy": _occupancy(package_dir),
        "compiled": (package_dir / "side" / "board.json").is_file(),
        "matches_fit": _matches_fit(package_dir),
    }


def _choice_from(source: dict) -> PackageChoice:
    if source.get("exposure") == "direct":
        raise ValueError(_DIRECT)
    if (
        source.get("mode") != "live"
        or source.get("design") != "clarity"
        or source.get("exposure") != "social"
        or source.get("policy_id") != "cn-digital-2026-v1"
        or source.get("activation_rate") != 0.5
        or source.get("temperature") != 0.7
    ):
        raise ValueError("研究设置不在可选范围内")
    agent_count = source.get("agent_count")
    rounds = source.get("rounds")
    seeds = source.get("seeds")
    if type(agent_count) is not int or type(rounds) is not int or not isinstance(seeds, list):
        raise ValueError("研究设置不在可选范围内")
    clean_seeds = []
    for seed in seeds:
        if type(seed) is not int:
            raise ValueError("研究设置不在可选范围内")
        clean_seeds.append(seed)
    return {
        "mode": "live",
        "design": "clarity",
        "exposure": "social",
        "policy_id": "cn-digital-2026-v1",
        "activation_rate": 0.5,
        "temperature": 0.7,
        "agent_count": agent_count,
        "rounds": rounds,
        "seeds": clean_seeds,
    }


def _paired_rows(surveys, policy_config):
    try:
        return _agent_rows(surveys, policy_for(policy_config))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("问卷无法配成前后测") from error


def _population(people, edges) -> dict:
    if not isinstance(people, list) or not isinstance(edges, list):
        raise ValueError("人口或关系无法读取")
    ids = set()
    for person in people:
        if not isinstance(person, dict) or not isinstance(person.get("name"), str):
            raise ValueError("人口或关系无法读取")
        person_id = _whole_id(person.get("id"))
        if person_id is None or person_id in ids:
            raise ValueError("人口或关系无法读取")
        ids.add(person_id)
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("人口或关系无法读取")
        source = _whole_id(edge.get("source"))
        target = _whole_id(edge.get("target"))
        if source is None or target is None or source not in ids or target not in ids:
            raise ValueError("人口或关系无法读取")
    return {"people": people, "edges": edges}


def _events(raw: bytes) -> dict:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("事件记录无法读取") from error
    events = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("事件记录无法读取") from error
        if not isinstance(item, dict):
            raise ValueError("事件记录无法读取")
        events.append(item)
    return {"events": events, "total": len(events)}


def _occupancy(package_dir: Path) -> OccupancySample | None:
    path = package_dir / "occupancy.json"
    if not path.is_file():
        return None
    value = _load_json(path)
    if not isinstance(value, dict) or set(value) != {"device", "full_name", "peak_percent", "engine", "burst_inferences"}:
        raise ValueError("占用记录无法读取")
    peak = value["peak_percent"]
    burst = value["burst_inferences"]
    if value["device"] != "NPU" or not isinstance(value["full_name"], str) or not isinstance(value["engine"], str):
        raise ValueError("占用记录无法读取")
    if isinstance(peak, bool) or type(peak) not in (int, float) or isinstance(burst, bool) or type(burst) is not int:
        raise ValueError("占用记录无法读取")
    return {
        "device": "NPU",
        "full_name": value["full_name"],
        "peak_percent": float(peak),
        "engine": value["engine"],
        "burst_inferences": burst,
    }


def _matches_fit(package_dir: Path) -> bool | None:
    path = package_dir / "side" / "check.json"
    if not path.is_file():
        return None
    check = _load_json(path)
    if not isinstance(check, dict) or type(check.get("matches_fit")) is not bool:
        raise ValueError("验证记录无法读取")
    return check["matches_fit"]


def _published(root: Path, research_id: str) -> Path | None:
    if not isinstance(research_id, str) or _RESEARCH_ID.fullmatch(research_id) is None:
        return None
    final = root / "reproductions" / research_id
    if not (final / "package.json").is_file():
        return None
    return final


def _write_new(directory: Path, parsed: _ParsedPackage) -> None:
    write_json(
        directory / "package.json",
        {
            "research_id": str(parsed.research_id),
            "status": "COMPLETED",
            "title": parsed.title,
            "choice": parsed.choice,
            "content_sha256": parsed.content_sha256,
        },
    )
    write_json(directory / "surveys.json", parsed.surveys)
    write_json(directory / "population.json", parsed.population)
    write_json(directory / "events.json", parsed.events)
    write_json(directory / "metrics.json", parsed.metrics)
    write_json(directory / "policy_config.json", parsed.policy_config)


def _replace_tree(target: Path, source: Path) -> None:
    backup = target.parent / ".side-previous"
    if not target.exists():
        os.rename(source, target)
        _remove(backup)
        return
    _remove(backup)
    os.rename(target, backup)
    try:
        os.rename(source, target)
    except Exception:
        os.rename(backup, target)
        raise
    _remove(backup)


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _whole_id(value):
    if type(value) is int:
        return value
    if type(value) is float and value.is_integer() and abs(value) <= _SAFE_INT:
        return int(value)
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        if parsed <= _SAFE_INT:
            return parsed
    return None


def _json_value(raw: bytes, message: str):
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(message) from error


def _json_object(raw: bytes, message: str) -> dict:
    value = _json_value(raw, message)
    if not isinstance(value, dict):
        raise ValueError(message)
    return value


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("复现包无法读取") from error


__all__ = (
    "ResearchId",
    "PackageChoice",
    "OccupancySample",
    "PackageCard",
    "PackageDetail",
    "import_package",
    "list_packages",
    "package_detail",
    "launch_maps",
    "verify_maps",
)
