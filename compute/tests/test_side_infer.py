import inspect

import pytest
import torch

import npu_side.side_infer as side_infer
from npu_side.policy import get_policy
from npu_side.side_infer import InferenceRefused, compile_deployed, compile_for_test, convert_ir, encode_input, encode_target, fit_map, infer_row


def install_devices(monkeypatch, devices):
    import openvino as ov
    real = ov.Core
    used = []

    class Core:
        available_devices = devices

        def read_model(self, *args, **kwargs):
            return real().read_model(*args, **kwargs)

        def compile_model(self, model, device):
            used.append(device)
            if device != "NPU":
                raise AssertionError(device)
            return real().compile_model(model, "CPU")

    monkeypatch.setattr(ov, "Core", lambda: Core())
    return used, real


def logit_parameters(module):
    weight = None
    bias = None
    for name, tensor in module.named_parameters():
        if name.endswith("weight") and tuple(tensor.shape) == (6, 7):
            weight = tensor.detach()
        if name.endswith("bias") and tuple(tensor.shape) == (6,):
            bias = tensor.detach()
    assert weight is not None and bias is not None
    return weight, bias


def test_public_surface_matches_the_sketch():
    public_functions = {
        name
        for name, value in vars(side_infer).items()
        if inspect.isfunction(value) and value.__module__ == side_infer.__name__ and not name.startswith("_")
    }
    assert public_functions <= set(side_infer.__all__)
    assert [name for name in dir(side_infer) if name.startswith("fit_")] == ["fit_map"]
    assert side_infer.__all__ == (
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


def test_closed_forms_match_lstsq_and_unpenalized_intercept():
    rows = [
        {"x": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "y": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]},
        {"x": [1.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.5], "y": [0.0, 1.0, 0.0, 1.0, 0.0, 0.25]},
        {"x": [0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 1.0], "y": [1.0, 0.0, 1.0, 0.0, 1.0, 0.75]},
        {"x": [1.0, 0.0, 1.0, 1.0, 0.0, 0.0, 0.25], "y": [0.0, 0.0, 0.0, 1.0, 1.0, 0.0]},
    ]
    features = torch.tensor([row["x"] for row in rows], dtype=torch.float32)
    targets = torch.tensor([row["y"] for row in rows], dtype=torch.float32)
    clipped = targets.clamp(1e-4, 1 - 1e-4)
    z = torch.log(clipped / (1 - clipped))
    design = torch.cat([features, torch.ones(features.shape[0], 1)], dim=1)
    solution = torch.linalg.lstsq(design, z).solution
    ols_weight, ols_bias = logit_parameters(side_infer._fit_logit(rows, 0.0))
    assert torch.allclose(ols_weight, solution[:7].T)
    assert torch.allclose(ols_bias, solution[7])
    x_mean = features.mean(dim=0)
    z_mean = z.mean(dim=0)
    centered_x = features - x_mean
    centered_z = z - z_mean
    slopes = torch.linalg.solve(centered_x.T @ centered_x + torch.eye(7), centered_x.T @ centered_z)
    ridge_weight, ridge_bias = logit_parameters(side_infer._fit_logit(rows, 1.0))
    assert torch.allclose(ridge_weight, slopes.T)
    assert torch.allclose(ridge_bias, z_mean - x_mean @ slopes)
    assert not torch.allclose(ols_weight, ridge_weight)


def test_answer_vectors_follow_item_order_and_intention_scale():
    policy = get_policy()
    wrong = {"categories": [], "price_limit": 0, "subsidy_percent": 0, "subsidy_cap": 0, "per_category_limit": 0, "intention": 1}
    encoded = encode_input("A1", wrong, policy)
    assert encoded[0] == 1.0
    assert encoded[1:] == [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    perfect = {**policy["facts"], "intention": 5}
    assert encode_target(perfect, policy) == [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]


def test_deploy_compile_refuses_without_npu(monkeypatch):
    used, _real = install_devices(monkeypatch, ["CPU", "GPU"])
    rows = [
        {"x": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "y": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]},
        {"x": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0], "y": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]},
    ]
    ir = convert_ir(fit_map(rows))
    with pytest.raises(InferenceRefused, match="旁侧推理未执行"):
        compile_deployed(ir)
    assert used == []


def test_fake_npu_compiles_saved_ir_on_cpu(monkeypatch, tmp_path):
    used, _real = install_devices(monkeypatch, ["CPU", "NPU"])
    rows = [
        {"x": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "y": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]},
        {"x": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0], "y": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]},
    ]
    ir = convert_ir(fit_map(rows))
    compiled = compile_deployed(ir)
    assert used == ["NPU"]
    features = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.5]
    first = infer_row(compiled, features)
    assert len(first) == 6
    assert first == infer_row(compiled, features)
    import openvino as ov
    xml = tmp_path / "model.xml"
    ov.save_model(ir, str(xml), compress_to_fp16=False)
    reloaded = ov.Core().read_model(str(xml))
    assert tuple(int(dim) for dim in reloaded.input(0).shape) == (1, 7)


def test_cpu_compile_returns_the_same_output_twice():
    rows = [
        {"x": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "y": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]},
        {"x": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0], "y": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]},
    ]
    ir = convert_ir(fit_map(rows))
    assert tuple(int(dim) for dim in ir.input(0).shape) == (1, 7)
    compiled = compile_for_test(ir, "CPU")
    features = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.5]
    first = infer_row(compiled, features)
    second = infer_row(compiled, features)
    assert len(first) == 6
    assert first == second
