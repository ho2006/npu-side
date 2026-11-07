"""Store a finished EconLab run as its own directory and compile those survey rows later."""

import io
import json
import math
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, NewType, TypedDict

from npu_occupancy import sustain_npu
from npu_side.io import write_json
from npu_side.policy import fingerprint, policy_for
from npu_side.side_infer import (
    SideCheck,
    _agent_rows,
    _consumer_scores,
    _emit_survey_board,
    _maps,
    compile_deployed,
    convert_ir,
    verify_survey_maps,
)

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
_TRAINING = "这个复现包正在训练，已保留原来的问卷"
_TRAIN_TOTAL: Literal[6] = 6
_ACTIVE = {"fitting", "compiling", "scoring", "sampling"}
_PHASES = {"idle", "fitting", "compiling", "scoring", "sampling", "done", "failed"}
_FIT_STEPS = (
    (1, "ols", "正在拟合 OLS。"),
    (2, "ridge", "正在拟合 Ridge。"),
    (3, "mlp", "正在拟合 MLP。"),
)
_COMPILE_MESSAGE = "正在 NPU 上编译。"
_SCORE_MESSAGE = "正在计算每位消费者的分数。"
_SAMPLE_MESSAGE = "正在采样 NPU 占用。"
_DONE_MESSAGE = "训练完成。"
_IDLE_MESSAGE = "尚未训练"
_FAIL_MESSAGE = "训练没有完成。"


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


class ConsumerRow(TypedDict):
    condition: str
    seed: int
    agent_id: int
    name: str
    group: str
    age: int
    budget: int
    post_intention: int
    high_intent: bool
    behavior: float | None
    probability: float | None
    credibility: float | None


class TrainStatus(TypedDict):
    phase: Literal["idle", "fitting", "compiling", "scoring", "sampling", "done", "failed"]
    step: int
    total: Literal[6]
    message: str
    error: str | None


class PackageDetail(TypedDict):
    card: PackageCard
    metrics: dict
    population: dict
    events: dict
    consumers: list[ConsumerRow]


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
        if _training(final):
            raise ValueError(_TRAINING)
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
    """Return the card, stored metrics, population, events, and consumers joined at read time."""
    final = _published(root, research_id)
    if final is None:
        return None
    return {
        "card": _card(final),
        "metrics": _load_json(final / "metrics.json"),
        "population": _load_json(final / "population.json"),
        "events": _load_json(final / "events.json"),
        "consumers": _consumers(final),
    }


def train_status(root: Path, research_id: str) -> TrainStatus | None:
    """Status the console polls. A missing file is idle, not an error."""
    final = _published(root, research_id)
    if final is None:
        return None
    path = final / "side" / "train.json"
    if not path.is_file():
        return _train_record("idle", 0, _IDLE_MESSAGE, None)
    return _read_train(path)


def begin_training(root: Path, research_id: str) -> TrainStatus | None:
    """Record fitting step 1 before the background thread runs."""
    final = _published(root, research_id)
    if final is None:
        return None
    _write_train(final, _train_record("fitting", 1, _FIT_STEPS[0][2], None))
    return _read_train(final / "side" / "train.json")


def launch_maps(root: Path, research_id: str, *, compile_model: Callable | None = None) -> PackageCard | None:
    """Fit, compile, score, and sample. Publish side/ only after both NPU steps return.

    Reads surveys.json and does not write it. train.json in the package is the
    status the console polls. Models and consumer_scores.json stay in a temp
    directory until compile and sustain_npu both return.
    """
    final = _published(root, research_id)
    if final is None:
        return None
    compile_one = compile_deployed if compile_model is None else compile_model
    dest = final.parent / f".launch-{research_id}"
    cursor = {"step": _FIT_STEPS[0][0], "phase": "fitting", "message": _FIT_STEPS[0][2]}
    _remove(dest)
    try:
        surveys = _load_json(final / "surveys.json")
        if not isinstance(surveys, list):
            raise ValueError("问卷无法读取")
        policy = policy_for(_load_json(final / "policy_config.json"))
        rows = _agent_rows(surveys, policy)
        if len(rows) < 2:
            raise ValueError("问卷不足以配成两对前后测")
        by_id = {spec.id: spec for spec in _maps()}
        fitted = []
        for step, algorithm_id, message in _FIT_STEPS:
            spec = by_id[algorithm_id]
            cursor.update(step=step, phase="fitting", message=message)
            _write_train(final, _train_record("fitting", step, message, None))
            fitted.append((spec, spec.fit(rows), spec.fit_probability(rows)))
        cursor.update(step=4, phase="compiling", message=_COMPILE_MESSAGE)
        _write_train(final, _train_record("compiling", 4, _COMPILE_MESSAGE, None))
        prepared = []
        compiled_maps = []
        compiled_probs = {}
        irs = []
        for spec, model, probability in fitted:
            map_ir = convert_ir(model)
            probability_ir = convert_ir(probability)
            compiled_maps.append((spec.id, compile_one(map_ir)))
            compiled_probs[spec.id] = compile_one(probability_ir)
            irs.append((spec.id, map_ir))
            prepared.append((spec, map_ir))
        _emit_survey_board(prepared, compiled_maps, irs, rows, dest)
        cursor.update(step=5, phase="scoring", message=_SCORE_MESSAGE)
        _write_train(final, _train_record("scoring", 5, _SCORE_MESSAGE, None))
        write_json(dest / "consumer_scores.json", _consumer_scores(rows, compiled_maps, compiled_probs))
        cursor.update(step=6, phase="sampling", message=_SAMPLE_MESSAGE)
        _write_train(final, _train_record("sampling", 6, _SAMPLE_MESSAGE, None))
        sample = sustain_npu(seconds=4)
        write_json(dest / "train.json", _train_record("done", 6, _DONE_MESSAGE, None))
    except Exception as error:
        _remove(dest)
        _write_train(
            final,
            _train_record("failed", cursor["step"], _FAIL_MESSAGE, str(error) or _FAIL_MESSAGE),
        )
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


def _train_record(
    phase: Literal["idle", "fitting", "compiling", "scoring", "sampling", "done", "failed"],
    step: int,
    message: str,
    error: str | None,
) -> TrainStatus:
    return {"phase": phase, "step": step, "total": _TRAIN_TOTAL, "message": message, "error": error}


def _write_train(package_dir: Path, status: TrainStatus) -> None:
    side = package_dir / "side"
    side.mkdir(parents=True, exist_ok=True)
    write_json(side / "train.json", status)


def _read_train(path: Path) -> TrainStatus:
    value = _load_json(path)
    if not isinstance(value, dict):
        raise ValueError("训练状态无法读取")
    phase = value.get("phase")
    step = value.get("step")
    message = value.get("message")
    error = value.get("error")
    if phase not in _PHASES or type(step) is not int or not 0 <= step <= _TRAIN_TOTAL:
        raise ValueError("训练状态无法读取")
    if value.get("total") != _TRAIN_TOTAL or not isinstance(message, str) or message == "":
        raise ValueError("训练状态无法读取")
    if error is not None and not isinstance(error, str):
        raise ValueError("训练状态无法读取")
    if phase == "done" and step != _TRAIN_TOTAL:
        raise ValueError("训练状态无法读取")
    if phase == "idle" and step != 0:
        raise ValueError("训练状态无法读取")
    if phase in _ACTIVE and step == 0:
        raise ValueError("训练状态无法读取")
    return {"phase": phase, "step": step, "total": _TRAIN_TOTAL, "message": message, "error": error}


def _training(package_dir: Path) -> bool:
    path = package_dir / "side" / "train.json"
    if not path.is_file():
        return False
    return _read_train(path)["phase"] in _ACTIVE


def _consumers(package_dir: Path) -> list[ConsumerRow]:
    surveys = _load_json(package_dir / "surveys.json")
    population = _load_json(package_dir / "population.json")
    if not isinstance(surveys, list) or not isinstance(population, dict):
        raise ValueError("问卷无法读取")
    people = population.get("people")
    if not isinstance(people, list):
        raise ValueError("人口或关系无法读取")
    portraits = {}
    for person in people:
        if not isinstance(person, dict):
            raise ValueError("人口或关系无法读取")
        person_id = _whole_id(person.get("id"))
        if person_id is None:
            raise ValueError("人口或关系无法读取")
        portraits[person_id] = person
    scores = _score_index(package_dir)
    paired: dict[tuple[str, int, int], dict] = {}
    for row in surveys:
        if not isinstance(row, dict):
            raise ValueError("问卷无法读取")
        condition = row.get("condition")
        phase = row.get("phase")
        if condition not in ("A0", "A1") or phase not in ("pre", "post"):
            continue
        seed = _whole_id(row.get("seed"))
        agent_id = _whole_id(row.get("agent_id"))
        if seed is None or agent_id is None:
            raise ValueError("问卷无法读取")
        paired.setdefault((condition, seed, agent_id), {})[phase] = row
    consumers = []
    for condition, seed, agent_id in sorted(paired):
        phases = paired[(condition, seed, agent_id)]
        if "pre" not in phases or "post" not in phases:
            continue
        person = portraits.get(agent_id)
        if not isinstance(person, dict):
            raise ValueError("人口或关系无法读取")
        name = person.get("name")
        group = person.get("group")
        age = _whole_id(person.get("age"))
        budget = _whole_id(person.get("budget"))
        if not isinstance(name, str) or name == "" or not isinstance(group, str) or group == "":
            raise ValueError("人口或关系无法读取")
        if age is None or budget is None:
            raise ValueError("人口或关系无法读取")
        intention = _post_intention(phases["post"])
        found = None if scores is None else scores.get((condition, seed, agent_id))
        consumers.append(
            {
                "condition": condition,
                "seed": seed,
                "agent_id": agent_id,
                "name": name,
                "group": group,
                "age": age,
                "budget": budget,
                "post_intention": intention,
                "high_intent": intention >= 4,
                "behavior": None if found is None else found["behavior"],
                "probability": None if found is None else found["probability"],
                "credibility": None if found is None else found["credibility"],
            }
        )
    return consumers


def _post_intention(row: dict) -> int:
    answer = row.get("answer")
    if not isinstance(answer, dict) or type(answer.get("intention")) is not int:
        raise ValueError("问卷无法读取")
    intention = answer["intention"]
    if not 1 <= intention <= 5:
        raise ValueError("问卷无法读取")
    return intention


def _score_index(package_dir: Path) -> dict[tuple[str, int, int], dict[str, float]] | None:
    path = package_dir / "side" / "consumer_scores.json"
    if not path.is_file():
        return None
    value = _load_json(path)
    if not isinstance(value, list):
        raise ValueError("消费者分数无法读取")
    index = {}
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("消费者分数无法读取")
        condition = item.get("condition")
        seed = _whole_id(item.get("seed"))
        agent_id = _whole_id(item.get("agent_id"))
        if condition not in ("A0", "A1") or seed is None or agent_id is None:
            raise ValueError("消费者分数无法读取")
        key = (condition, seed, agent_id)
        if key in index:
            raise ValueError("消费者分数无法读取")
        index[key] = {
            "behavior": _finite(item.get("behavior")),
            "probability": _finite(item.get("probability")),
            "credibility": _finite(item.get("credibility")),
        }
    return index


def _finite(value) -> float:
    if isinstance(value, bool) or type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("消费者分数无法读取")
    return float(value)


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
    "train_status",
    "begin_training",
    "launch_maps",
    "verify_maps",
)
