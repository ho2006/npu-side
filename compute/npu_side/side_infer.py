"""Fit the human pre-to-post map. Deployment inference runs only when an NPU is present."""
import json
import math
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Literal, Protocol, TypedDict

import torch
from torch import nn

from npu_side.scoring import score_answer
from npu_side.records import CalibrationData
from npu_side.policy import policy_for
from npu_side.io import write_json

ITEM_KEYS = ("categories", "price_limit", "subsidy_percent", "subsidy_cap", "per_category_limit")
CONDITIONS = {"A0": 0.0, "A1": 1.0}
SEED = 42
EPOCHS = 30
VALIDITY = {
    "fixture": "工程夹具，不是真人证据",
    "human": "待研究者审核，未自动认证",
}
_LOGIT_CLIP = 1e-4
_RIDGE_PENALTY = 1.0
_BOARD_KEYS = {
    "status",
    "device",
    "runtime",
    "fit_split",
    "n_development",
    "external_validity",
    "algorithms",
    "heldout",
    "agents",
}
_CHECK_KEYS = {"experiment_id", "device", "runtime", "matches_fit", "agents"}

__all__ = (
    "InferenceRefused",
    "PrePostMap",
    "encode_input",
    "encode_target",
    "fit_map",
    "convert_ir",
    "compile_for_test",
    "compile_deployed",
    "infer_row",
    "score_gap",
    "score_rows",
    "run_side",
    "read_side",
    "run_verify",
    "read_verify",
    "compile_survey_maps",
    "verify_survey_maps",
)


class InsufficientSample(TypedDict):
    status: Literal["INSUFFICIENT_SAMPLE"]


class AlgorithmColumn(TypedDict):
    id: str
    fit: dict[str, str | int | float]


class HeldoutColumns(TypedDict):
    item_brier: dict[str, float | None]
    intention_mae: dict[str, float | None]


class AgentColumns(TypedDict):
    condition: str
    seed: int
    n: int
    item_brier: dict[str, float]
    intention_mae: dict[str, float]


class SideBoard(TypedDict):
    status: Literal["INFERRED"]
    device: Literal["NPU"]
    runtime: Literal["openvino"]
    fit_split: Literal["development"]
    n_development: int
    external_validity: str
    algorithms: list[AlgorithmColumn]
    heldout: HeldoutColumns
    agents: list[AgentColumns]


class SideCheck(TypedDict):
    experiment_id: str
    device: Literal["NPU"]
    runtime: Literal["openvino"]
    matches_fit: bool | None
    agents: list[AgentColumns]


class SurveyMapBoard(TypedDict):
    status: Literal["COMPILED"]
    device: Literal["NPU"]
    runtime: Literal["openvino"]
    algorithms: list[AlgorithmColumn]
    agents: list[AgentColumns]
    n_rows: int


class InferenceRefused(RuntimeError):
    pass


class PrePostMap(nn.Module):
    def __init__(self):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(7, 16),
            nn.ReLU(),
            nn.Linear(16, 6),
            nn.Sigmoid(),
        )

    def forward(self, features):
        return self.layers(features)


class _MapSpec(Protocol):
    id: str

    def fit(self, rows: list[dict]) -> nn.Module: ...

    def fit_probability(self, rows: list[dict]) -> nn.Module: ...

    def note(self) -> dict[str, str | int | float]: ...


class _LogitMap(nn.Module):
    def __init__(self, weight: torch.Tensor, bias: torch.Tensor) -> None:
        super().__init__()
        self.linear = nn.Linear(7, 6)
        with torch.no_grad():
            # Linear starts random. The closed form replaces both tensors.
            self.linear.weight.copy_(weight)
            self.linear.bias.copy_(bias)
        self.activation = nn.Sigmoid()

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return self.activation(self.linear(features))


class _ProbabilityLogit(nn.Module):
    def __init__(self, weight: torch.Tensor, bias: torch.Tensor) -> None:
        super().__init__()
        # A second module so _LogitMap stays 7 inputs and 6 outputs.
        self.linear = nn.Linear(7, 1)
        with torch.no_grad():
            self.linear.weight.copy_(weight)
            self.linear.bias.copy_(bias)
        self.activation = nn.Sigmoid()

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return self.activation(self.linear(features))


class _ProbabilityNet(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(7, 8),
            nn.ReLU(),
            nn.Linear(8, 1),
            nn.Sigmoid(),
        )

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return self.layers(features)


def _plain_answer(answer):
    dump = getattr(answer, "model_dump", None)
    return dump() if dump else answer


def _item_bits(answer, policy):
    scored = score_answer(_plain_answer(answer), policy)["items"]
    return [1.0 if scored[key] else 0.0 for key in ITEM_KEYS]


def _scale_intention(intention):
    return (float(intention) - 1.0) / 4.0


def encode_input(condition, answer, policy):
    answer = _plain_answer(answer)
    return [CONDITIONS[condition], *_item_bits(answer, policy), _scale_intention(answer["intention"])]


def encode_target(answer, policy):
    answer = _plain_answer(answer)
    return [*_item_bits(answer, policy), _scale_intention(answer["intention"])]


def _human_rows(participants, split, policy):
    rows = []
    for person in participants:
        if person.split != split or person.status != "completed" or person.pre is None or person.post is None:
            continue
        rows.append({
            "condition": person.condition,
            "x": encode_input(person.condition, person.pre, policy),
            "y": encode_target(person.post, policy),
        })
    return rows


def _agent_rows(surveys, policy):
    paired = {}
    for row in surveys:
        condition = row.get("condition")
        phase = row.get("phase")
        if condition not in CONDITIONS or phase not in ("pre", "post"):
            continue
        paired.setdefault((condition, row["seed"], row["agent_id"]), {})[phase] = row["answer"]
    rows = []
    for (condition, seed, agent_id), answers in paired.items():
        if "pre" not in answers or "post" not in answers:
            continue
        rows.append({
            "condition": condition,
            "seed": seed,
            "agent_id": agent_id,
            "x": encode_input(condition, answers["pre"], policy),
            "y": encode_target(answers["post"], policy),
        })
    return rows


def fit_map(rows, seed=SEED, epochs=EPOCHS):
    torch.manual_seed(seed)
    model = PrePostMap().to("cpu")
    features = torch.tensor([row["x"] for row in rows], dtype=torch.float32, device="cpu")
    targets = torch.tensor([row["y"] for row in rows], dtype=torch.float32, device="cpu")
    optimizer = torch.optim.Adam(model.parameters())
    bce = nn.BCELoss()
    mse = nn.MSELoss()
    model.train()
    for _ in range(epochs):
        optimizer.zero_grad()
        predicted = model(features)
        loss = bce(predicted[:, :5], targets[:, :5]) + mse(predicted[:, 5], targets[:, 5])
        loss.backward()
        optimizer.step()
    model.eval()
    return model


def _logit_weight(features: torch.Tensor, targets: torch.Tensor, penalty: float) -> tuple[torch.Tensor, torch.Tensor]:
    clipped = targets.clamp(_LOGIT_CLIP, 1.0 - _LOGIT_CLIP)
    z = torch.log(clipped / (1.0 - clipped))
    width = int(features.shape[1])
    if penalty == 0:
        # Wide [X | 1]. lstsq is the minimum-norm fit, intercept included.
        design = torch.cat([features, torch.ones(features.shape[0], 1)], dim=1)
        solution = torch.linalg.lstsq(design, z).solution
        weight = solution[:width].T
        bias = solution[width]
    else:
        # Penalty hits the seven slopes. The intercept stays at the unpenalized center.
        x_mean = features.mean(dim=0)
        z_mean = z.mean(dim=0)
        centered_x = features - x_mean
        centered_z = z - z_mean
        gram = centered_x.T @ centered_x + penalty * torch.eye(width, dtype=features.dtype)
        slopes = torch.linalg.solve(gram, centered_x.T @ centered_z)
        weight = slopes.T
        bias = z_mean - x_mean @ slopes
    return weight.contiguous(), bias.contiguous()


def _fit_logit(rows: list[dict], penalty: float) -> nn.Module:
    features = torch.tensor([row["x"] for row in rows], dtype=torch.float32)
    targets = torch.tensor([row["y"] for row in rows], dtype=torch.float32)
    weight, bias = _logit_weight(features, targets, penalty)
    model = _LogitMap(weight, bias)
    model.eval()
    return model


def _high_intent(row: dict) -> bool:
    # Post intention 4 is the scaled target 0.75. The probability label is that cut.
    return float(row["y"][5]) >= 0.75


def _binary_rows(rows: list[dict]) -> list[dict]:
    return [{"x": row["x"], "y": [1.0 if _high_intent(row) else 0.0]} for row in rows]


def _fit_probability_logit(rows: list[dict], penalty: float) -> nn.Module:
    binary = _binary_rows(rows)
    features = torch.tensor([row["x"] for row in binary], dtype=torch.float32)
    targets = torch.tensor([row["y"] for row in binary], dtype=torch.float32)
    weight, bias = _logit_weight(features, targets, penalty)
    model = _ProbabilityLogit(weight, bias)
    model.eval()
    return model


def _fit_probability_mlp(rows: list[dict], seed: int = SEED, epochs: int = EPOCHS) -> nn.Module:
    binary = _binary_rows(rows)
    torch.manual_seed(seed)
    model = _ProbabilityNet().to("cpu")
    features = torch.tensor([row["x"] for row in binary], dtype=torch.float32, device="cpu")
    targets = torch.tensor([row["y"] for row in binary], dtype=torch.float32, device="cpu")
    optimizer = torch.optim.Adam(model.parameters())
    loss_fn = nn.BCELoss()
    model.train()
    for _ in range(epochs):
        optimizer.zero_grad()
        predicted = model(features)
        loss = loss_fn(predicted, targets)
        loss.backward()
        optimizer.step()
    model.eval()
    return model


class _LogitSpec:
    def __init__(self, algorithm_id: str, penalty: float) -> None:
        self.id = algorithm_id
        self.penalty = penalty

    def fit(self, rows: list[dict]) -> nn.Module:
        return _fit_logit(rows, self.penalty)

    def fit_probability(self, rows: list[dict]) -> nn.Module:
        return _fit_probability_logit(rows, self.penalty)

    def note(self) -> dict[str, str | int | float]:
        return {"form": "closed_logit", "penalty": self.penalty, "clip": _LOGIT_CLIP}


class _MlpSpec:
    id = "mlp"

    def fit(self, rows: list[dict]) -> nn.Module:
        return fit_map(rows)

    def fit_probability(self, rows: list[dict]) -> nn.Module:
        return _fit_probability_mlp(rows)

    def note(self) -> dict[str, str | int | float]:
        return {"form": "mlp", "seed": SEED, "epochs": EPOCHS}


def _maps() -> tuple[_MapSpec, _MapSpec, _MapSpec]:
    return (_LogitSpec("ols", 0.0), _LogitSpec("ridge", _RIDGE_PENALTY), _MlpSpec())


def convert_ir(model):
    import openvino as ov
    model.eval()
    example = torch.zeros(1, 7)
    with torch.no_grad():
        ir = ov.convert_model(model, example_input=example)
    # convert_model leaves both dimensions dynamic. Deployment reads one record.
    ir.reshape([1, 7])
    return ir


def compile_for_test(model, device):
    import openvino as ov
    if device != "CPU":
        raise ValueError("test-only compile accepts CPU")
    return ov.Core().compile_model(model, device)


def compile_deployed(model):
    import openvino as ov
    core = ov.Core()
    # CPU and GPU are not deployment devices. A missing NPU stops the run before any result is written.
    if "NPU" not in list(core.available_devices):
        raise InferenceRefused("旁侧推理未执行")
    return core.compile_model(model, "NPU")


def infer_row(compiled, features):
    import numpy as np
    array = np.asarray([features], dtype=np.float32)
    values = np.asarray(compiled(array)[compiled.output(0)]).reshape(-1)
    return [float(value) for value in values]


def score_gap(predictions, targets):
    item_errors = [(pred[index] - target[index]) ** 2 for pred, target in zip(predictions, targets) for index in range(5)]
    intention_errors = [abs(pred[5] - target[5]) for pred, target in zip(predictions, targets)]
    return {
        "item_brier": sum(item_errors) / len(item_errors),
        "intention_mae": sum(intention_errors) / len(intention_errors),
    }


def score_rows(compiled, rows):
    if not rows:
        return {"item_brier": None, "intention_mae": None}
    return score_gap([infer_row(compiled, row["x"]) for row in rows], [row["y"] for row in rows])


def _discrepancy(compiled, rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["condition"], row["seed"])].append((infer_row(compiled, row["x"]), row["y"]))
    report = []
    for condition, seed in sorted(grouped):
        pairs = grouped[(condition, seed)]
        gap = score_gap([pair[0] for pair in pairs], [pair[1] for pair in pairs])
        report.append({
            "condition": condition,
            "seed": seed,
            "n": len(pairs),
            "item_brier": gap["item_brier"],
            "intention_mae": gap["intention_mae"],
        })
    return report


def _metric_columns(ids: tuple[str, ...], gaps_by_id: dict[str, dict], field: str) -> dict[str, float | None]:
    return {algorithm_id: gaps_by_id[algorithm_id][field] for algorithm_id in ids}


def _agent_columns(ids: tuple[str, ...], reports_by_id: dict[str, list[dict]]) -> list[dict]:
    reports = [reports_by_id[algorithm_id] for algorithm_id in ids]
    if not reports:
        return []
    if any(len(report) != len(reports[0]) for report in reports):
        raise RuntimeError("agent rows diverge across maps")
    rows = []
    for index, base in enumerate(reports[0]):
        identity = (base["condition"], base["seed"], base["n"])
        for report in reports[1:]:
            other = report[index]
            if (other["condition"], other["seed"], other["n"]) != identity:
                raise RuntimeError("agent rows diverge across maps")
        rows.append({
            "condition": base["condition"],
            "seed": base["seed"],
            "n": base["n"],
            "item_brier": {algorithm_id: reports_by_id[algorithm_id][index]["item_brier"] for algorithm_id in ids},
            "intention_mae": {algorithm_id: reports_by_id[algorithm_id][index]["intention_mae"] for algorithm_id in ids},
        })
    return rows


def _compare(compiled_in_order: list[tuple[str, object]], heldout: list[dict], agents: list[dict]) -> dict:
    gaps_by_id = {}
    reports_by_id = {}
    for algorithm_id, compiled in compiled_in_order:
        gaps_by_id[algorithm_id] = score_rows(compiled, heldout)
        reports_by_id[algorithm_id] = _discrepancy(compiled, agents)
    ids = tuple(algorithm_id for algorithm_id, _compiled in compiled_in_order)
    return {
        "heldout": {
            "item_brier": _metric_columns(ids, gaps_by_id, "item_brier"),
            "intention_mae": _metric_columns(ids, gaps_by_id, "intention_mae"),
        },
        "agents": _agent_columns(ids, reports_by_id),
    }


def _publish(side_dir: Path, ids: tuple[str, ...], irs: list[tuple[str, object]], board: dict) -> None:
    import openvino as ov
    side_dir.mkdir(parents=True, exist_ok=True)
    for algorithm_id, ir in irs:
        directory = side_dir / algorithm_id
        directory.mkdir(parents=True, exist_ok=True)
        # fp16 compression would change the file relative to the IR that just ran.
        ov.save_model(ir, str(directory / "model.xml"), compress_to_fp16=False)
    write_json(side_dir / "board.json", board)
    allowed = {"board.json", *ids}
    for child in list(side_dir.iterdir()):
        if child.name in allowed:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _latest_dataset(protocol_dir):
    attempts = protocol_dir / "attempts"
    if not attempts.exists():
        return None
    found = []
    for dataset in attempts.glob("*/dataset.json"):
        stamp = ""
        receipt = dataset.parent / "receipt.json"
        if receipt.exists():
            stamp = json.loads(receipt.read_text(encoding="utf-8")).get("recorded_at") or ""
        found.append((stamp, dataset.stat().st_mtime, dataset))
    if not found:
        return None
    return max(found)[2]


def run_side(store, protocol) -> SideBoard | InsufficientSample:
    dataset_path = _latest_dataset(store.root / "human" / str(protocol["id"]))
    if dataset_path is None:
        raise ValueError("请先导入已通过校验的问卷")
    surveys_path = store.directory(str(protocol["baseline_id"])) / "surveys.json"
    if not surveys_path.exists():
        raise ValueError("基线实验缺少 surveys.json")
    dataset = CalibrationData.model_validate_json(dataset_path.read_text(encoding="utf-8"))
    surveys = json.loads(surveys_path.read_text(encoding="utf-8"))
    policy = policy_for(protocol["baseline_config"])
    development = _human_rows(dataset.participants, "development", policy)
    heldout = _human_rows(dataset.participants, "heldout", policy)
    counts = {condition: sum(row["condition"] == condition for row in development) for condition in CONDITIONS}
    if any(counts[condition] < protocol["min_per_condition"] for condition in CONDITIONS):
        return {"status": "INSUFFICIENT_SAMPLE"}
    specs = _maps()
    prepared = [(spec, convert_ir(spec.fit(development))) for spec in specs]
    compiled_in_order = []
    irs = []
    for spec, ir in prepared:
        compiled_in_order.append((spec.id, compile_deployed(ir)))
        irs.append((spec.id, ir))
    compared = _compare(compiled_in_order, heldout, _agent_rows(surveys, policy))
    board = {
        "status": "INFERRED",
        "device": "NPU",
        "runtime": "openvino",
        "fit_split": "development",
        "n_development": len(development),
        "external_validity": VALIDITY[protocol["source_kind"]],
        "algorithms": [{"id": spec.id, "fit": spec.note()} for spec, _ir in prepared],
        "heldout": compared["heldout"],
        "agents": compared["agents"],
    }
    _publish(
        store.root / "human" / str(protocol["id"]) / "side",
        tuple(spec.id for spec, _ir in prepared),
        irs,
        board,
    )
    return board


def _is_int(value):
    return type(value) is int


def _is_number(value):
    return type(value) in (int, float)


def _is_scalar(value):
    return type(value) in (str, int, float)


def _require(ok):
    if not ok:
        raise ValueError("侧端记录无法读取")


def _require_scores(value, ids, *, allow_none):
    _require(isinstance(value, dict) and set(value) == set(ids))
    for item in value.values():
        if allow_none and item is None:
            continue
        _require(_is_number(item))


def _require_board(board):
    _require(isinstance(board, dict) and set(board) == _BOARD_KEYS)
    _require(board["status"] == "INFERRED")
    _require(board["device"] == "NPU")
    _require(board["runtime"] == "openvino")
    _require(board["fit_split"] == "development")
    _require(_is_int(board["n_development"]))
    _require(isinstance(board["external_validity"], str))
    algorithms = board["algorithms"]
    _require(isinstance(algorithms, list))
    ids = []
    for column in algorithms:
        _require(isinstance(column, dict) and set(column) == {"id", "fit"})
        algorithm_id = column["id"]
        fit = column["fit"]
        _require(isinstance(algorithm_id, str) and algorithm_id not in ids)
        _require(isinstance(fit, dict))
        _require(all(isinstance(key, str) and _is_scalar(value) for key, value in fit.items()))
        ids.append(algorithm_id)
    heldout = board["heldout"]
    _require(isinstance(heldout, dict) and set(heldout) == {"item_brier", "intention_mae"})
    for field in ("item_brier", "intention_mae"):
        _require_scores(heldout[field], ids, allow_none=True)
    agents = board["agents"]
    _require(isinstance(agents, list))
    for row in agents:
        _require(isinstance(row, dict) and set(row) == {"condition", "seed", "n", "item_brier", "intention_mae"})
        _require(isinstance(row["condition"], str) and _is_int(row["seed"]) and _is_int(row["n"]))
        _require_scores(row["item_brier"], ids, allow_none=False)
        _require_scores(row["intention_mae"], ids, allow_none=False)


def read_side(store, protocol_id: str) -> SideBoard | None:
    path = store.root / "human" / str(protocol_id) / "side" / "board.json"
    if not path.is_file():
        return None
    try:
        board = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("侧端记录无法读取") from error
    _require_board(board)
    return board


def _check_path(store, protocol_id, experiment_id) -> Path:
    return store.root / "human" / str(protocol_id) / "side" / "checks" / f"{experiment_id}.json"


def _read_ir(path: Path):
    import openvino as ov
    return ov.Core().read_model(str(path))


def _close(left, right) -> bool:
    return math.isclose(float(left), float(right), rel_tol=1e-5, abs_tol=1e-5)


def _same_agents(fresh, saved) -> bool:
    if len(fresh) != len(saved):
        return False
    for new_row, old_row in zip(fresh, saved):
        if (new_row["condition"], new_row["seed"], new_row["n"]) != (old_row["condition"], old_row["seed"], old_row["n"]):
            return False
        for field in ("item_brier", "intention_mae"):
            new_scores = new_row[field]
            old_scores = old_row[field]
            if set(new_scores) != set(old_scores):
                return False
            if any(not _close(new_scores[algorithm_id], old_scores[algorithm_id]) for algorithm_id in new_scores):
                return False
    return True


def _require_check(check, experiment_id):
    _require(isinstance(check, dict) and set(check) == _CHECK_KEYS)
    _require(check["experiment_id"] == str(experiment_id))
    _require(check["device"] == "NPU")
    _require(check["runtime"] == "openvino")
    _require(check["matches_fit"] is None or type(check["matches_fit"]) is bool)
    agents = check["agents"]
    _require(isinstance(agents, list))
    ids = None
    for row in agents:
        _require(isinstance(row, dict) and set(row) == {"condition", "seed", "n", "item_brier", "intention_mae"})
        _require(isinstance(row["condition"], str) and _is_int(row["seed"]) and _is_int(row["n"]))
        if ids is None:
            _require(isinstance(row["item_brier"], dict))
            ids = list(row["item_brier"])
        _require_scores(row["item_brier"], ids, allow_none=False)
        _require_scores(row["intention_mae"], ids, allow_none=False)


def _load_surveys(path: Path):
    try:
        surveys = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("实验问卷无法读取") from error
    if not isinstance(surveys, list):
        raise ValueError("实验问卷无法读取")
    return surveys


def run_verify(store, protocol, experiment_id: str) -> SideCheck:
    # The saved IR is the fit. This pass compiles it for the NPU and scores one experiment.
    side_dir = store.root / "human" / str(protocol["id"]) / "side"
    board = read_side(store, protocol["id"])
    if board is None:
        raise ValueError("尚未侧端拟合")
    experiment = store.experiment(str(experiment_id))
    if not experiment or experiment["status"] != "COMPLETED":
        raise ValueError("须选择已完成的实验")
    surveys_path = store.directory(str(experiment_id)) / "surveys.json"
    if not surveys_path.is_file():
        raise ValueError("实验缺少 surveys.json")
    ids = tuple(column["id"] for column in board["algorithms"])
    xmls = []
    for algorithm_id in ids:
        xml = side_dir / algorithm_id / "model.xml"
        if not xml.is_file():
            raise ValueError("侧端模型不完整")
        xmls.append((algorithm_id, xml))
    surveys = _load_surveys(surveys_path)
    try:
        rows = _agent_rows(surveys, policy_for(protocol["baseline_config"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("实验问卷无法读取") from error
    compiled_in_order = [(algorithm_id, compile_deployed(_read_ir(xml))) for algorithm_id, xml in xmls]
    reports = {algorithm_id: _discrepancy(compiled, rows) for algorithm_id, compiled in compiled_in_order}
    agents = _agent_columns(ids, reports)
    matches_fit = _same_agents(agents, board["agents"]) if str(experiment_id) == str(protocol["baseline_id"]) else None
    check = {
        "experiment_id": str(experiment_id),
        "device": "NPU",
        "runtime": "openvino",
        "matches_fit": matches_fit,
        "agents": agents,
    }
    path = _check_path(store, protocol["id"], experiment_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, check)
    return check


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _consumer_scores(rows: list[dict], compiled_maps: list[tuple[str, object]], compiled_probs: dict[str, object]) -> list[dict]:
    maps = {algorithm_id: compiled for algorithm_id, compiled in compiled_maps}
    mlp = maps["mlp"]
    scores = []
    ordered = sorted(rows, key=lambda row: (row["condition"], row["seed"], row["agent_id"]))
    for row in ordered:
        predicted = infer_row(mlp, row["x"])
        behavior = _clamp(1.0 + 4.0 * float(predicted[5]), 1.0, 5.0)
        probabilities = [float(infer_row(compiled_probs[name], row["x"])[0]) for name in ("ols", "ridge", "mlp")]
        spread = max(probabilities) - min(probabilities)
        scores.append({
            "condition": row["condition"],
            "seed": row["seed"],
            "agent_id": row["agent_id"],
            "behavior": behavior,
            "probability": sum(probabilities) / len(probabilities),
            "credibility": _clamp(1.0 - spread, 0.0, 1.0),
        })
    return scores


def _emit_survey_board(prepared, compiled_in_order, irs, rows, dest: Path) -> SurveyMapBoard:
    ids = tuple(spec.id for spec, _ir in prepared)
    reports = {algorithm_id: _discrepancy(compiled, rows) for algorithm_id, compiled in compiled_in_order}
    board = {
        "status": "COMPILED",
        "device": "NPU",
        "runtime": "openvino",
        "algorithms": [{"id": spec.id, "fit": spec.note()} for spec, _ir in prepared],
        "agents": _agent_columns(ids, reports),
        "n_rows": len(rows),
    }
    _publish(dest, ids, irs, board)
    return board


def compile_survey_maps(surveys: list, policy: dict, dest: Path) -> SurveyMapBoard:
    """Fit every map from _maps on these survey rows and compile each IR on the NPU.

    The study seed is not a fit parameter. mlp uses fit_map with SEED and EPOCHS.
    n_rows is the number of pre/post pairs. status stays COMPILED so a calibration
    reader that requires INFERRED does not accept this file. Writes only under dest.
    """
    rows = _agent_rows(surveys, policy)
    if len(rows) < 2:
        raise ValueError("问卷不足以配成两对前后测")
    specs = _maps()
    prepared = [(spec, convert_ir(spec.fit(rows))) for spec in specs]
    compiled_in_order = []
    irs = []
    for spec, ir in prepared:
        compiled_in_order.append((spec.id, compile_deployed(ir)))
        irs.append((spec.id, ir))
    return _emit_survey_board(prepared, compiled_in_order, irs, rows, dest)


def verify_survey_maps(surveys: list, policy: dict, side_dir: Path) -> SideCheck:
    """Compile the saved IR on the NPU and score these survey rows.

    matches_fit compares the new scores with the saved board. This pass scores
    the rows the board was fitted on, so matches_fit is a bool. experiment_id
    is the research id stored beside this side directory.
    """
    package_path = side_dir.parent / "package.json"
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("复现包无法读取") from error
    if not isinstance(package, dict) or not isinstance(package.get("research_id"), str):
        raise ValueError("复现包无法读取")
    experiment_id = package["research_id"]
    board_path = side_dir / "board.json"
    if not board_path.is_file():
        raise ValueError("尚未在 NPU 上编译")
    try:
        board = json.loads(board_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("侧端记录无法读取") from error
    if (
        not isinstance(board, dict)
        or board.get("status") != "COMPILED"
        or board.get("device") != "NPU"
        or board.get("runtime") != "openvino"
    ):
        raise ValueError("侧端记录无法读取")
    algorithms = board.get("algorithms")
    saved = board.get("agents")
    if not isinstance(algorithms, list) or not algorithms or not isinstance(saved, list):
        raise ValueError("侧端记录无法读取")
    ids = []
    for column in algorithms:
        if not isinstance(column, dict) or not isinstance(column.get("id"), str) or column["id"] in ids:
            raise ValueError("侧端记录无法读取")
        ids.append(column["id"])
    compiled_in_order = []
    for algorithm_id in ids:
        xml = side_dir / algorithm_id / "model.xml"
        if not xml.is_file():
            raise ValueError("侧端模型不完整")
        compiled_in_order.append((algorithm_id, compile_deployed(_read_ir(xml))))
    rows = _agent_rows(surveys, policy)
    reports = {algorithm_id: _discrepancy(compiled, rows) for algorithm_id, compiled in compiled_in_order}
    agents = _agent_columns(tuple(ids), reports)
    try:
        matches_fit = _same_agents(agents, saved)
    except (KeyError, TypeError) as error:
        raise ValueError("侧端记录无法读取") from error
    return {
        "experiment_id": str(experiment_id),
        "device": "NPU",
        "runtime": "openvino",
        "matches_fit": matches_fit,
        "agents": agents,
    }


def read_verify(store, protocol_id: str, experiment_id: str) -> SideCheck | None:
    path = _check_path(store, protocol_id, experiment_id)
    if not path.is_file():
        return None
    try:
        check = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("侧端记录无法读取") from error
    _require_check(check, experiment_id)
    return check
