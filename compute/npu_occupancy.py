"""Sustain a random workload on the Intel NPU and sample this process's engine counter."""

import os
import subprocess
import threading
import time

import numpy as np
import openvino as ov
from openvino import opset13 as ops


def build_sustained_model():
    rng = np.random.default_rng(0)
    entered = ops.parameter([1, 512], np.float32, name="x")
    value = entered
    for index in range(8):
        weight = ops.constant(rng.standard_normal((512, 512)).astype(np.float32), name=f"w{index}")
        value = ops.relu(ops.matmul(value, weight, False, False))
    return ov.Model([value], [entered], "random-npu")


def _engine_paths(pid):
    listed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "(Get-Counter -ListSet 'GPU Engine').PathsWithInstances | "
                f"Where-Object {{ $_ -like '*pid_{pid}_*' -and $_ -like '*Utilization Percentage*' }}"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return [line.strip() for line in listed.stdout.splitlines() if line.strip().startswith("\\")]


def _sample(paths):
    if not paths:
        return {}
    quoted = ",".join("'" + path.replace("'", "") + "'" for path in paths)
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-Counter {quoted}).CounterSamples | ForEach-Object {{ \"$($_.Path)`t$($_.CookedValue)\" }}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    values = {}
    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        path, raw = line.rsplit("\t", 1)
        try:
            values[path] = float(raw)
        except ValueError:
            continue
    return values


def measure_while(work):
    """Run work while sampling. A one-row model can finish between samples, so work must keep the NPU busy."""
    stop = threading.Event()
    found = {"paths": [], "peaks": {}}

    def sampler():
        deadline = time.perf_counter() + 4
        paths = []
        while not paths and time.perf_counter() < deadline and not stop.is_set():
            paths = _engine_paths(os.getpid())
            if not paths:
                time.sleep(0.2)
        found["paths"] = paths
        peaks = {path: 0.0 for path in paths}
        while not stop.is_set():
            for path, value in _sample(paths).items():
                peaks[path] = max(peaks.get(path, 0.0), value)
            time.sleep(0.05)
        for path, value in _sample(paths).items():
            peaks[path] = max(peaks.get(path, 0.0), value)
        found["peaks"] = peaks

    thread = threading.Thread(target=sampler, daemon=True)
    thread.start()
    try:
        result = work()
    finally:
        time.sleep(0.3)
        stop.set()
        thread.join(timeout=20)
    peaks = found["peaks"]
    if not peaks:
        return result, 0.0, ""
    engine, peak = max(peaks.items(), key=lambda item: item[1])
    return result, peak, engine


def sustain_npu(seconds=4):
    core = ov.Core()
    if "NPU" not in core.available_devices:
        raise RuntimeError("OpenVINO 没有 NPU")
    full_name = core.get_property("NPU", "FULL_DEVICE_NAME")
    compiled = core.compile_model(build_sustained_model(), "NPU")
    request = compiled.create_infer_request()
    data = np.random.default_rng(1).standard_normal((1, 512)).astype(np.float32)

    def work():
        started = time.perf_counter()
        count = 0
        while time.perf_counter() - started < seconds:
            request.infer({0: data})
            count += 1
        return count

    count, peak, engine = measure_while(work)
    return {
        "device": "NPU",
        "full_name": full_name,
        "peak_percent": peak,
        "engine": engine,
        "burst_inferences": count,
    }


if __name__ == "__main__":
    report = sustain_npu()
    print(f"device {report['device']} {report['full_name']}")
    print(f"inferences {report['burst_inferences']}")
    if report["peak_percent"] > 0:
        print(f"occupancy {report['peak_percent']:.2f} {report['engine']}")
    else:
        print("occupancy none")
