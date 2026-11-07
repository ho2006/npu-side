"""Local API for the researcher console. Training stays in npu_side. This process only stores records and calls it."""

import io
import json
import threading
import time
import traceback
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

import npu_side.reproductions as reproductions
import npu_side.side_infer as side
from npu_side.io import write_json
from npu_side.policy import get_policy, material_manifest
from npu_side.records import CalibrationData
from npu_side.scoring import score_answer
from npu_occupancy import sustain_npu
from request_log import RequestRecord, Sent, elapsed_ms, write_request

ROOT = Path(__file__).resolve().parent / "local_store"
HOST = "127.0.0.1"
PORT = 8000
MODES = {"smoke", "live"}
POLICIES = {"cn-digital-2026-v1", "cn-digital-2025-v1"}
DESIGNS = {"clarity", "factorial"}
EXPOSURES = {"social", "direct"}
STAGES = {"pilot", "heldout"}
SOURCE_KINDS = {"fixture", "human"}
LOCK = threading.Lock()


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, experiment_id: str) -> Path:
        path = self.root / "experiments" / experiment_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def experiment(self, experiment_id: str):
        path = self.root / "experiments" / experiment_id / "experiment.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save_experiment(self, record):
        write_json(self.directory(record["id"]) / "experiment.json", record)

    def experiments(self):
        base = self.root / "experiments"
        if not base.is_dir():
            return []
        found = []
        for child in sorted(base.iterdir()):
            record = self.experiment(child.name)
            if record:
                found.append(record)
        return found

    def save_project(self, project_id: str, plan):
        directory = self.root / "projects"
        directory.mkdir(parents=True, exist_ok=True)
        write_json(directory / f"{project_id}.json", {"id": project_id, "plan": plan})

    def project(self, project_id: str):
        path = self.root / "projects" / f"{project_id}.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save_protocol(self, protocol):
        directory = self.root / "protocols"
        directory.mkdir(parents=True, exist_ok=True)
        write_json(directory / f"{protocol['id']}.json", protocol)

    def protocol(self, protocol_id: str):
        path = self.root / "protocols" / f"{protocol_id}.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write_surveys(self, experiment_id: str, surveys):
        write_json(self.directory(experiment_id) / "surveys.json", surveys)

    def write_dataset(self, protocol_id: str, dataset):
        attempt = self.root / "human" / protocol_id / "attempts" / uuid.uuid4().hex[:12]
        attempt.mkdir(parents=True, exist_ok=True)
        write_json(attempt / "dataset.json", dataset)
        write_json(attempt / "receipt.json", {"recorded_at": attempt.name})


def new_id():
    return uuid.uuid4().hex[:16]


def fail(message):
    raise ValueError(message)


def require_text(value, message):
    if not isinstance(value, str) or not value.strip():
        fail(message)
    return value.strip()


def require_plan(value):
    if not isinstance(value, dict):
        fail("研究计划缺失")
    plan = {
        "title": require_text(value.get("title"), "研究计划缺少题目、问题或分析"),
        "question": require_text(value.get("question"), "研究计划缺少题目、问题或分析"),
        "analysis": require_text(value.get("analysis"), "研究计划缺少题目、问题或分析"),
    }
    for key, message in (("hypotheses", "研究计划缺少假设或局限"), ("limitations", "研究计划缺少假设或局限")):
        items = value.get(key)
        if not isinstance(items, list) or not items:
            fail(message)
        plan[key] = [require_text(item, message) for item in items]
    return plan


def require_choice(body):
    mode = body.get("mode")
    policy_id = body.get("policy_id")
    design = body.get("design")
    exposure = body.get("exposure")
    if mode not in MODES or policy_id not in POLICIES or design not in DESIGNS or exposure not in EXPOSURES:
        fail("研究设置不在可选范围内")
    if (exposure == "direct" and design != "clarity") or (design == "factorial" and exposure != "social"):
        fail("direct 与 factorial 不能同时使用")
    return {"mode": mode, "policy_id": policy_id, "design": design, "exposure": exposure}


def require_start(body):
    choice = require_choice(body)
    if body.get("confirmed") is not True:
        fail("需要确认研究计划")
    if body.get("activation_rate") != 0.5 or body.get("temperature") != 0.7:
        fail("activation_rate 与 temperature 不能改")
    count = body.get("agent_count")
    rounds = body.get("rounds")
    seeds = body.get("seeds")
    if type(count) is not int or not 10 <= count <= 60:
        fail("消费者数量需要 10 到 60 的整数")
    if type(rounds) is not int or not 1 <= rounds <= 6:
        fail("社交轮次需要 1 到 6 的整数")
    if not isinstance(seeds, list) or not 1 <= len(seeds) <= 3 or len(set(seeds)) != len(seeds):
        fail("随机种子需要 1 到 3 个互不重复的整数，范围是 0 到 4294967295")
    clean_seeds = []
    for seed in seeds:
        if type(seed) is not int or seed < 0 or seed > 2**32 - 1:
            fail("随机种子需要 1 到 3 个互不重复的整数，范围是 0 到 4294967295")
        clean_seeds.append(seed)
    project_id = require_text(body.get("project_id"), "研究项目响应里没有 id")
    choice.update(
        {
            "project_id": project_id,
            "agent_count": count,
            "rounds": rounds,
            "seeds": clean_seeds,
            "activation_rate": 0.5,
            "temperature": 0.7,
            "confirmed": True,
        }
    )
    return choice


def random_answer(rng, policy, accurate):
    facts = policy["facts"]
    if accurate:
        categories = list(facts["categories"])
        values = {key: int(facts[key]) for key in ("price_limit", "subsidy_percent", "subsidy_cap", "per_category_limit")}
    else:
        categories = ["手机"] if rng.random() < 0.5 else []
        values = {
            "price_limit": int(rng.integers(0, 6001)),
            "subsidy_percent": int(rng.integers(0, 101)),
            "subsidy_cap": int(rng.integers(0, 501)),
            "per_category_limit": int(rng.integers(0, 3)),
        }
    values["categories"] = categories
    values["intention"] = int(rng.integers(1, 6))
    return values


def build_surveys(rng, policy, agent_count, seeds):
    surveys = []
    for index in range(agent_count):
        condition = "A0" if index % 2 == 0 else "A1"
        seed = seeds[index % len(seeds)]
        for phase, odds in (("pre", 0.35), ("post", 0.55)):
            surveys.append(
                {
                    "condition": condition,
                    "phase": phase,
                    "seed": seed,
                    "agent_id": index,
                    "answer": random_answer(rng, policy, rng.random() < odds),
                }
            )
    return surveys


def build_world(surveys, exposure, rounds):
    people = []
    seen = set()
    for row in surveys:
        if row["phase"] != "pre" or row["agent_id"] in seen:
            continue
        seen.add(row["agent_id"])
        people.append({"id": row["agent_id"], "name": f"被试{row['agent_id']}", "group": row["condition"]})
    edges = []
    if exposure == "social" and len(people) > 1:
        for index, person in enumerate(people):
            edges.append({"source": person["id"], "target": people[(index + 1) % len(people)]["id"]})
    events = []
    reached = set()
    for person in people:
        if exposure == "direct":
            events.append({"action": "direct_exposure", "agent_id": person["id"]})
            reached.add(person["id"])
            continue
        for _round in range(rounds):
            if person["id"] % 2 == 0:
                events.append({"action": "feed_observed", "agent_id": person["id"], "info": {"policy_post_ids": [person["id"]]}})
                reached.add(person["id"])
            else:
                events.append({"action": "feed_observed", "agent_id": person["id"], "info": {"policy_post_ids": []}})
    return {"people": people, "edges": edges}, {"events": events, "total": len(events)}, reached


def pair_people(surveys):
    people = {}
    for row in surveys:
        people.setdefault(row["agent_id"], {"condition": row["condition"], "seed": row["seed"]})[row["phase"]] = row["answer"]
    return people


def mean(values):
    return sum(values) / len(values) if values else 0.0


def study_rows(surveys, policy, reached):
    grouped = {}
    for agent_id, person in pair_people(surveys).items():
        pre = person["pre"]
        post = person["post"]
        scored = score_answer(post, policy)
        accuracy = scored["correct"] / scored["total"]
        bucket = grouped.setdefault((person["condition"], person["seed"]), [])
        bucket.append(
            {
                "accuracy": accuracy,
                "intention": post["intention"],
                "change": post["intention"] - pre["intention"],
                "reach": 1 if agent_id in reached else 0,
            }
        )
    runs = []
    for (condition, seed), rows in sorted(grouped.items()):
        runs.append(
            {
                "condition": condition,
                "seed": seed,
                "n": len(rows),
                "comprehension": mean([row["accuracy"] for row in rows]),
                "item_accuracy": mean([row["accuracy"] for row in rows]),
                "reach": mean([row["reach"] for row in rows]),
                "intention": mean([row["intention"] for row in rows]),
                "intention_change": mean([row["change"] for row in rows]),
            }
        )
    return runs


def comparison_row(metric, runs, field):
    by_seed = {}
    for run in runs:
        by_seed.setdefault(run["seed"], {})[run["condition"]] = run[field]
    differences = []
    for pair in by_seed.values():
        if "A0" in pair and "A1" in pair:
            differences.append(pair["A1"] - pair["A0"])
    a0 = mean([run[field] for run in runs if run["condition"] == "A0"])
    a1 = mean([run[field] for run in runs if run["condition"] == "A1"])
    return {
        "metric": metric,
        "A0": a0,
        "A1": a1,
        "difference": a1 - a0,
        "seed_min": min(differences) if differences else a1 - a0,
        "seed_max": max(differences) if differences else a1 - a0,
    }


def train_map(rows, seed):
    model = side.fit_map(rows, seed=seed)
    compiled = side.compile_deployed(side.convert_ir(model))
    grouped = {}
    for row in rows:
        grouped.setdefault(row["condition"], {"predicted": [], "target": []})
        grouped[row["condition"]]["predicted"].append(side.infer_row(compiled, row["x"]))
        grouped[row["condition"]]["target"].append(row["y"])
    gaps = {condition: side.score_gap(bucket["predicted"], bucket["target"]) for condition, bucket in grouped.items()}
    return gaps


def build_metrics(runs, gaps, occupancy, design):
    rows = [
        comparison_row("comprehension", runs, "comprehension"),
        comparison_row("item_accuracy", runs, "item_accuracy"),
        comparison_row("reach", runs, "reach"),
        comparison_row("intention", runs, "intention"),
        comparison_row("intention_change", runs, "intention_change"),
    ]
    if gaps:
        for field in ("item_brier", "intention_mae"):
            a0 = gaps.get("A0", {}).get(field, 0.0) or 0.0
            a1 = gaps.get("A1", {}).get(field, 0.0) or 0.0
            rows.append(
                {
                    "metric": field,
                    "A0": a0,
                    "A1": a1,
                    "difference": a1 - a0,
                    "seed_min": a1 - a0,
                    "seed_max": a1 - a0,
                }
            )
    peak = occupancy["peak_percent"]
    rows.append(
        {
            "metric": "npu_utilization",
            "A0": peak,
            "A1": peak,
            "difference": 0,
            "seed_min": peak,
            "seed_max": peak,
        }
    )
    payload = {
        "comparison": rows,
        "runs": runs,
        "definitions": {
            "comprehension": "后测答对条目的比例",
            "item_accuracy": "后测答对条目的比例",
            "reach": "被政策触达的比例",
            "intention": "后测购买意向，1 到 5",
            "intention_change": "后测意向减去前测意向",
            "item_brier": "fit_map 在 NPU 上的条目 Brier",
            "intention_mae": "fit_map 在 NPU 上的意向绝对误差",
            "npu_utilization": "本次运行采到的 Intel NPU 计算引擎占用峰值",
        },
        "unit": f"NPU {occupancy['full_name']} 占用峰值 {peak:.2f}%",
    }
    if design == "factorial":
        wording = rows[0]
        payload["factorial_effects"] = [
            {
                "factor": "wording",
                "metric": "comprehension",
                "mean": wording["difference"],
                "seed_min": wording["seed_min"],
                "seed_max": wording["seed_max"],
            }
        ]
    return payload


def run_experiment(store: Store, record):
    policy = get_policy(record["config"]["policy_id"])
    rng = np.random.default_rng(record["config"]["seeds"][0])
    surveys = build_surveys(rng, policy, record["config"]["agent_count"], record["config"]["seeds"])
    store.write_surveys(record["id"], surveys)
    population, events, reached = build_world(surveys, record["config"]["exposure"], record["config"]["rounds"])
    rows = side._agent_rows(surveys, policy)
    if len(rows) < 2:
        fail("随机问卷没有配成前测和后测")
    gaps = train_map(rows, record["config"]["seeds"][0])
    # The fitted map is one row at a time. The engine counter stays near zero unless the device stays busy.
    occupancy = sustain_npu(seconds=4)
    occupancy["training"] = "fit_map"
    record["population"] = population
    record["events"] = events
    record["metrics"] = build_metrics(study_rows(surveys, policy, reached), gaps, occupancy, record["config"]["design"])
    record["occupancy"] = occupancy
    record["status"] = "COMPLETED"
    record["error"] = None
    store.save_experiment(record)
    return record


def build_dataset(rng, policy, source_kind):
    participants = []
    for condition in ("A0", "A1"):
        for split, count in (("development", 4), ("heldout", 2)):
            for _index in range(count):
                participants.append(
                    {
                        "participant_id": "p_" + uuid.uuid4().hex[:16],
                        "condition": condition,
                        "consented": True,
                        "eligible": True,
                        "split": split,
                        "status": "completed",
                        "pre": random_answer(rng, policy, rng.random() < 0.4),
                        "post": random_answer(rng, policy, rng.random() < 0.7),
                    }
                )
    manifest = material_manifest(policy)
    dataset = {
        "source_kind": source_kind,
        "protocol_sha256": manifest["policy_sha256"],
        "material_sha256": manifest["material_sha256"],
        "survey_sha256": manifest["survey_sha256"],
        "participants": participants,
    }
    return CalibrationData.model_validate(dataset).model_dump(mode="json")


def listed(record):
    return {
        "id": record["id"],
        "status": record["status"],
        "config": {"exposure": record["config"]["exposure"], "mode": record["config"]["mode"]},
        "plan": {"title": record["plan"]["title"]},
    }


class App(BaseHTTPRequestHandler):
    store: Store

    def log_message(self, fmt, *args):
        return

    def send_error(self, code, message=None, explain=None):
        status = int(code)
        if message:
            detail = message
        else:
            detail = self.responses.get(status, ("???",))[0]
        if getattr(self, "_request", None) is None:
            self._begin()
        self._error(status, detail)

    def _access(self):
        return self.store.root / "access"

    def _begin(self):
        self._started_ns = time.monotonic_ns()
        self._bytes_read = 0
        self._sent = None
        command = getattr(self, "command", None) or "UNKNOWN"
        path = getattr(self, "path", None) or "/"
        self._request = RequestRecord.started(command, path, 0)
        write_request(self._access(), self._request)

    def _open_record(self):
        if self._request.bytes_in != self._bytes_read:
            self._request = self._request.with_bytes_in(self._bytes_read)
        return self._request

    def _finish(self):
        sent = self._sent
        if sent is None:
            return
        detail = sent.detail
        error_type = sent.error_type
        if 200 <= sent.status < 300:
            detail = None
            error_type = None
        elif not detail:
            detail = self.responses.get(sent.status, ("请求失败",))[0]
        write_request(
            self._access(),
            self._open_record().finished(
                status=sent.status,
                bytes_out=sent.bytes_out,
                duration_ms=elapsed_ms(self._started_ns),
                detail=detail,
                error_type=error_type,
            ),
        )

    def _send(self, status, payload=None, body=None, content_type="application/json; charset=utf-8", disposition=None):
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        outgoing = body or b""
        self._sent = Sent(int(status), len(outgoing), None, None)
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(outgoing)))
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        if outgoing:
            self.wfile.write(outgoing)

    def _error(self, status, message, error_type=None):
        detail = message if message else "请求失败"
        status = int(status)
        body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
        self._send(status, body=body)
        self._sent = Sent(status, len(body), detail, error_type)
        closed = self._open_record().finished(
            status=status,
            bytes_out=len(body),
            duration_ms=elapsed_ms(self._started_ns),
            detail=detail,
            error_type=error_type,
        )
        write_request(self._access(), closed)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 1_048_576:
            fail("文件超过 1MiB，未发送。")
        raw = self.rfile.read(length) if length else b""
        self._bytes_read = len(raw)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            fail("请求不是 JSON")

    def _is_zip(self):
        content_type = self.headers.get("Content-Type", "")
        return content_type.split(";", 1)[0].strip().lower() == "application/zip"

    def _read_limited(self, cap):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > cap:
            fail("文件超过 1MiB，未发送。")
        return self.rfile.read(length) if length else b""

    def _parts(self):
        return [part for part in urlparse(self.path).path.split("/") if part]

    def do_GET(self):
        self._begin()
        try:
            with LOCK:
                self._get(self._parts())
        except ValueError as error:
            self._error(400, str(error))
            return
        except side.InferenceRefused as error:
            self._error(503, str(error))
            return
        except Exception as error:
            traceback.print_exc()
            self._error(500, str(error) or "请求失败", type(error).__name__)
            return
        self._finish()

    def do_POST(self):
        self._begin()
        try:
            parts = self._parts()
            if parts == ["reproductions"] and self._is_zip():
                archive = self._read_limited(1_048_576)
                self._bytes_read = len(archive)
                self._request = self._open_record()
                write_request(self._access(), self._request)
                with LOCK:
                    self._send(200, reproductions.import_package(self.store.root, archive))
            else:
                with LOCK:
                    self._post(parts, self._read_body())
        except ValueError as error:
            self._error(400, str(error))
            return
        except side.InferenceRefused as error:
            self._error(503, str(error))
            return
        except Exception as error:
            traceback.print_exc()
            self._error(500, str(error) or "请求失败", type(error).__name__)
            return
        self._finish()

    def _get(self, parts):
        if parts == ["reproductions"]:
            self._send(200, reproductions.list_packages(self.store.root))
            return
        if len(parts) == 2 and parts[0] == "reproductions":
            detail = reproductions.package_detail(self.store.root, parts[1])
            if detail is None:
                self._error(404, "没有这个复现包")
                return
            self._send(200, detail)
            return
        if parts == []:
            self._send(200, {"ok": True})
            return
        if parts == ["experiments"]:
            self._send(200, [listed(record) for record in self.store.experiments()])
            return
        if len(parts) >= 2 and parts[0] == "experiments":
            record = self.store.experiment(parts[1])
            if record is None:
                self._error(404, "没有这个实验")
                return
            tail = parts[2] if len(parts) > 2 else ""
            if tail == "":
                self._send(200, {"id": record["id"], "status": record["status"], "plan": record["plan"], "error": record["error"]})
                return
            if tail == "metrics":
                if not record.get("metrics"):
                    self._error(404, "实验还没有运行")
                    return
                self._send(200, record["metrics"])
                return
            if tail == "population":
                if not record.get("population"):
                    self._error(404, "实验还没有运行")
                    return
                self._send(200, record["population"])
                return
            if tail == "events":
                if not record.get("events"):
                    self._error(404, "实验还没有运行")
                    return
                self._send(200, record["events"])
                return
            if tail == "export":
                self._zip({"experiment.json": record, "surveys.json": self._surveys(record["id"]), "occupancy.json": record.get("occupancy")})
                return
        if len(parts) >= 2 and parts[0] == "calibrations":
            protocol = self.store.protocol(parts[1])
            if protocol is None:
                self._error(404, "没有这个协议")
                return
            tail = parts[2] if len(parts) > 2 else ""
            if tail == "":
                self._send(200, {"protocol": {"baseline_id": protocol["baseline_id"], "id": protocol["id"]}})
                return
            if tail == "side":
                board = side.read_side(self.store, protocol["id"])
                if board is None:
                    self._error(404, "尚未侧端拟合")
                    return
                self._send(200, board)
                return
            if tail == "export":
                dataset = self._dataset(protocol["id"])
                self._zip({"protocol.json": protocol, "dataset.json": dataset})
                return
        self._error(404, "没有这个接口")

    def _post(self, parts, body):
        if len(parts) == 3 and parts[0] == "reproductions" and parts[2] == "launch":
            result = reproductions.launch_maps(self.store.root, parts[1])
            if result is None:
                self._error(404, "没有这个复现包")
                return
            self._send(200, result)
            return
        if len(parts) == 3 and parts[0] == "reproductions" and parts[2] == "verify":
            result = reproductions.verify_maps(self.store.root, parts[1])
            if result is None:
                self._error(404, "没有这个复现包")
                return
            self._send(200, result)
            return
        if parts == ["research", "plan"]:
            question = require_text(body.get("question") if isinstance(body, dict) else None, "研究问题是空的")
            choice = require_choice(body)
            source = "local-case" if choice["mode"] == "smoke" else "local"
            self._send(
                200,
                {
                    "source": source,
                    "plan": {
                        "title": question[:40],
                        "question": question,
                        "hypotheses": ["随机生成的前测和后测可以在 NPU 上完成一次映射。"],
                        "analysis": "计划由本地随机数据生成。live 也没有把问题发给 Qwen。",
                        "limitations": ["样本是随机数，不是调查。"],
                    },
                },
            )
            return
        if parts == ["research", "projects"]:
            plan = require_plan(body.get("plan") if isinstance(body, dict) else None)
            project_id = new_id()
            self.store.save_project(project_id, plan)
            self._send(200, {"id": project_id})
            return
        if parts == ["experiments"]:
            config = require_start(body)
            project = self.store.project(config["project_id"])
            if project is None:
                fail("研究项目响应里没有 id")
            experiment_id = new_id()
            record = {"id": experiment_id, "status": "CREATED", "plan": project["plan"], "config": config, "error": None}
            self.store.save_experiment(record)
            self._send(200, {"id": experiment_id})
            return
        if len(parts) == 3 and parts[0] == "experiments" and parts[2] == "run":
            record = self.store.experiment(parts[1])
            if record is None:
                self._error(404, "没有这个实验")
                return
            record["status"] = "RUNNING"
            self.store.save_experiment(record)
            self._request = self._open_record().with_experiment(parts[1])
            write_request(self._access(), self._request)
            try:
                finished = run_experiment(self.store, record)
            except Exception as error:
                record["status"] = "FAILED"
                record["error"] = str(error) or "训练或 NPU 推理失败"
                self.store.save_experiment(record)
                raise
            self._send(200, {"id": finished["id"], "status": finished["status"], "occupancy": finished["occupancy"]})
            return
        if parts == ["calibrations"]:
            self._freeze(body)
            return
        if len(parts) == 3 and parts[0] == "calibrations" and parts[2] == "side":
            protocol = self._need_protocol(parts[1])
            board = side.run_side(self.store, protocol)
            self._send(200, board)
            return
        if len(parts) == 3 and parts[0] == "calibrations" and parts[2] == "verify":
            protocol = self._need_protocol(parts[1])
            experiment_id = require_text(body.get("experiment_id") if isinstance(body, dict) else None, "须选择已完成的实验")
            self._send(200, side.run_verify(self.store, protocol, experiment_id))
            return
        if len(parts) == 3 and parts[0] == "calibrations" and parts[2] == "import":
            protocol = self._need_protocol(parts[1])
            try:
                dataset = CalibrationData.model_validate(body).model_dump(mode="json")
            except Exception as error:
                fail(str(error))
            if dataset["source_kind"] != protocol["source_kind"]:
                fail("导入文件的 source_kind 与协议不一致")
            self.store.write_dataset(protocol["id"], dataset)
            self._send(200, {"result": {"status": "stored", "external_validity": side.VALIDITY[protocol["source_kind"]]}})
            return
        self._error(404, "没有这个接口")

    def _freeze(self, body):
        if not isinstance(body, dict):
            fail("请求不是 JSON")
        baseline_id = require_text(body.get("baseline_id"), "需要已完成的直接阅读实验")
        source_kind = body.get("source_kind")
        stage = body.get("stage")
        if source_kind not in SOURCE_KINDS or stage not in STAGES:
            fail("来源或阶段不在可选范围内")
        baseline = self.store.experiment(baseline_id)
        if baseline is None or baseline["status"] != "COMPLETED":
            fail("需要已完成的直接阅读实验")
        if baseline["config"]["exposure"] != "direct":
            fail("基线必须是直接阅读实验")
        if source_kind == "human":
            if baseline["config"]["mode"] != "live":
                fail("真人数据需要 live 基线")
            if body.get("authorization_confirmed") is not True:
                fail("真人数据需要授权确认")
            require_text(body.get("authorization_reference"), "真人数据需要授权记录")
            require_text(body.get("consent_version"), "真人数据需要同意版本")
        policy = get_policy(baseline["config"]["policy_id"])
        dataset = build_dataset(np.random.default_rng(baseline["config"]["seeds"][0]), policy, source_kind)
        protocol = {
            "id": new_id(),
            "source_kind": source_kind,
            "stage": stage,
            "baseline_id": baseline_id,
            "baseline_config": {"policy_id": baseline["config"]["policy_id"]},
            "min_per_condition": 2,
            "protocol_sha256": dataset["protocol_sha256"],
            "material": {
                "material_sha256": dataset["material_sha256"],
                "survey_sha256": dataset["survey_sha256"],
            },
        }
        self.store.save_protocol(protocol)
        self.store.write_dataset(protocol["id"], dataset)
        self._send(
            200,
            {
                "id": protocol["id"],
                "source_kind": protocol["source_kind"],
                "protocol_sha256": protocol["protocol_sha256"],
                "material": protocol["material"],
            },
        )

    def _need_protocol(self, protocol_id):
        protocol = self.store.protocol(protocol_id)
        if protocol is None:
            fail("没有这个协议")
        return protocol

    def _surveys(self, experiment_id):
        path = self.store.directory(experiment_id) / "surveys.json"
        if not path.is_file():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def _dataset(self, protocol_id):
        found = side._latest_dataset(self.store.root / "human" / protocol_id)
        if found is None:
            return None
        return json.loads(found.read_text(encoding="utf-8"))

    def _zip(self, members):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, value in members.items():
                archive.writestr(name, json.dumps(value, ensure_ascii=False, indent=2))
        payload = buffer.getvalue()
        self._send(200, body=payload, content_type="application/zip", disposition='attachment; filename="export.zip"')


def main():
    App.store = Store(ROOT)
    server = ThreadingHTTPServer((HOST, PORT), App)
    print(f"listening http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
