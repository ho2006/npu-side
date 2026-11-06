"""Drive the local API the way the console does, and require a measured NPU peak."""

import json
import urllib.request

BASE = "http://127.0.0.1:8000"


def call(method, path, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=300) as response:
        raw = response.read()
        content_type = response.headers.get("Content-Type", "")
        if "json" in content_type:
            return json.loads(raw.decode("utf-8"))
        return raw


def main():
    plan = call(
        "POST",
        "/research/plan",
        {
            "question": "随机数据能不能在 NPU 上跑起来？",
            "mode": "smoke",
            "policy_id": "cn-digital-2026-v1",
            "design": "clarity",
            "exposure": "direct",
        },
    )
    assert plan["plan"]["question"]
    assert isinstance(plan["plan"]["hypotheses"], list)
    project = call("POST", "/research/projects", {"plan": plan["plan"]})
    created = call(
        "POST",
        "/experiments",
        {
            "project_id": project["id"],
            "mode": "smoke",
            "policy_id": "cn-digital-2026-v1",
            "design": "clarity",
            "exposure": "direct",
            "agent_count": 12,
            "rounds": 2,
            "seeds": [42],
            "activation_rate": 0.5,
            "temperature": 0.7,
            "confirmed": True,
        },
    )
    finished = call("POST", f"/experiments/{created['id']}/run", {})
    peak = finished["occupancy"]["peak_percent"]
    print("peak", peak)
    print("engine", finished["occupancy"].get("engine"))
    print("training", finished["occupancy"].get("training"))
    print("inferences", finished["occupancy"].get("burst_inferences"))
    assert finished["occupancy"]["device"] == "NPU"
    assert finished["occupancy"]["training"] == "fit_map"
    assert peak > 0, finished["occupancy"]
    metrics = call("GET", f"/experiments/{created['id']}/metrics")
    assert isinstance(metrics["comparison"], list) and metrics["comparison"]
    assert any(row["metric"] == "npu_utilization" for row in metrics["comparison"])
    population = call("GET", f"/experiments/{created['id']}/population")
    events = call("GET", f"/experiments/{created['id']}/events")
    assert population["people"] and isinstance(events["events"], list)
    header = call("GET", f"/experiments/{created['id']}")
    assert header["status"] == "COMPLETED"
    listed = call("GET", "/experiments")
    assert any(row["id"] == created["id"] and row["status"] == "COMPLETED" and row["exposure"] == "direct" for row in [
        {**row, "exposure": row["config"]["exposure"]} for row in listed
    ])
    protocol = call(
        "POST",
        "/calibrations",
        {"baseline_id": created["id"], "source_kind": "fixture", "stage": "pilot"},
    )
    assert protocol["protocol_sha256"] and protocol["material"]["material_sha256"]
    board = call("POST", f"/calibrations/{protocol['id']}/side", {})
    print("side", board.get("status"), board.get("device"), board.get("runtime"))
    assert board["status"] == "INFERRED"
    assert board["device"] == "NPU"
    fetched = call("GET", f"/calibrations/{protocol['id']}/side")
    assert fetched["status"] == "INFERRED"
    check = call("POST", f"/calibrations/{protocol['id']}/verify", {"experiment_id": created["id"]})
    print("verify", check.get("matches_fit"), check.get("device"))
    assert check["device"] == "NPU"
    assert isinstance(check["agents"], list)
    print("experiment", created["id"])
    print("protocol", protocol["id"])


if __name__ == "__main__":
    main()
