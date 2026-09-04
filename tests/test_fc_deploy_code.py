"""FC 自动部署只换代码，不覆盖生产环境配置。"""

import importlib.util
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _load_module():
    path = ROOT / "linyuan/fc/deploy_code.py"
    spec = importlib.util.spec_from_file_location("fc_deploy_code", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_code_only_deploy_preserves_function_configuration(monkeypatch, tmp_path):
    calls = {}

    class Config:
        def __init__(self, **kwargs):
            calls["config"] = kwargs

    class InputCodeLocation:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class UpdateFunctionInput:
        def __init__(self, **kwargs):
            calls["update_fields"] = kwargs

    class UpdateFunctionRequest:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Client:
        def __init__(self, config):
            calls["client"] = config

        def update_function(self, function_name, request):
            calls["function_name"] = function_name
            calls["request"] = request

    fc_pkg = types.ModuleType("alibabacloud_fc20230330")
    fc_client = types.ModuleType("alibabacloud_fc20230330.client")
    fc_models = types.ModuleType("alibabacloud_fc20230330.models")
    tea_pkg = types.ModuleType("alibabacloud_tea_openapi")
    tea_models = types.ModuleType("alibabacloud_tea_openapi.models")
    fc_client.Client = Client
    fc_models.InputCodeLocation = InputCodeLocation
    fc_models.UpdateFunctionInput = UpdateFunctionInput
    fc_models.UpdateFunctionRequest = UpdateFunctionRequest
    fc_pkg.models = fc_models
    tea_models.Config = Config
    tea_pkg.models = tea_models
    monkeypatch.setitem(sys.modules, "alibabacloud_fc20230330", fc_pkg)
    monkeypatch.setitem(sys.modules, "alibabacloud_fc20230330.client", fc_client)
    monkeypatch.setitem(sys.modules, "alibabacloud_fc20230330.models", fc_models)
    monkeypatch.setitem(sys.modules, "alibabacloud_tea_openapi", tea_pkg)
    monkeypatch.setitem(sys.modules, "alibabacloud_tea_openapi.models", tea_models)

    code_zip = tmp_path / "fc-code.zip"
    code_zip.write_bytes(b"zip bytes")
    monkeypatch.setenv("ALIYUN_AK", "ak-test")
    monkeypatch.setenv("ALIYUN_SK", "sk-test")
    monkeypatch.setenv("FC_CODE_ZIP", str(code_zip))

    _load_module().main()

    assert calls["function_name"] == "linyuan-pipeline"
    assert set(calls["update_fields"]) == {"code"}
    assert calls["config"]["endpoint"] == "fcv3.cn-hangzhou.aliyuncs.com"


def test_deploy_workflow_uses_only_aliyun_secrets():
    workflow = (ROOT / ".github/workflows/fc-production-deploy.yml").read_text()
    assert "secrets.ALIYUN_AK" in workflow
    assert "secrets.ALIYUN_SK" in workflow
    assert "BILIBILI_COOKIES" not in workflow
    assert "GITHUB_TOKEN" not in workflow
    assert "runner.temp" not in workflow
