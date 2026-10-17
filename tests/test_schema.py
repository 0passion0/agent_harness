"""学术引用数据契约、输入校验与工具输出的单元测试模块。"""

import pytest
from pydantic import ValidationError
from research_citation.schemas import ReferenceCheckInput, ReferenceCheckResult
from research_citation.tools import scan_numeric
from vertical_harness.contracts import PluginManifest, SkillAsset


def test_schema_rejects_extras():
    """测试非法或包含额外未知字段的调用载荷必须被拒绝（Fail-Closed 原则）。

    :return: None
    :raises AssertionError: 当未抛出预期的 ValidationError 时触发断言失败。
    """
    # 尝试传入未在 ReferenceCheckInput 中声明的额外参数 unexpected=True
    # 期望触发 ValidationError，确保严格的数据校验
    with pytest.raises(ValidationError):
        ReferenceCheckInput(manuscript_text="[1]", bibliography=["Paper"], unexpected=True)


def test_reference_mismatch():
    """测试检测未匹配或越界的引用序号，只核验索引关系而不对文献真实性下结论。

    :return: None
    :raises AssertionError: 当校验状态不符合预期或异常问题集合不匹配时触发断言失败。
    """
    # 手稿中引用了 [1] 和 [3]，但参考文献列表仅提供了 2 篇（索引 1 和 2）
    text = scan_numeric.invoke({"manuscript_text": "Source [1] and [3].", "bibliography": ["Paper A", "Paper B"]})
    result = ReferenceCheckResult.model_validate_json(text)

    # 验证状态应判定为存在问题（issues）
    assert result.status == "issues"
    # 索引 3 超出范围（out_of_range），索引 2 未被正文引用（uncited）
    assert {(i.kind, i.index) for i in result.issues} == {("out_of_range", 3), ("uncited", 2)}


def test_plugin_contract():
    """测试拒绝格式非法的插件标识符清单以及包含路径遍历攻击的技能资产。

    :return: None
    :raises AssertionError: 当非法清单或恶意技能路径未被 Pydantic 校验拦截时触发断言失败。
    """
    # 验证插件 ID 包含目录遍历字符时必须被拒绝
    with pytest.raises(ValidationError):
        PluginManifest(plugin_id="../../evil", version="0.1.0", description="Malformed plugin identifier")

    # 验证技能路径包含向上跳转 '..' 时必须被校验拦截
    with pytest.raises(ValidationError):
        SkillAsset(relative_path="../hack/SKILL.md", content="x")


def test_verified_finding_requires_evidence():
    """测试特定业务规则约束：已核实（verified）的结论必须至少包含一条来源证据。

    :return: None
    :raises AssertionError: 当缺少证据的已核实结论未触发 ValidationError 时触发断言失败。
    """
    from research_citation.report import AuditFinding

    # 状态为 'verified' 但证据列表为空，必须校验失败并抛出异常
    with pytest.raises(ValidationError):
        AuditFinding(claim="Citation claim", status="verified", evidence=[])


def test_settings_require_explicit_model(monkeypatch):
    """测试 LLM 模型必须显式通过环境或参数配置，绝不能悄悄回退到硬编码默认值。

    :param monkeypatch: pytest 提供的环境变量与对象修改测试夹具。
    :return: None
    :raises AssertionError: 当环境变量配置或模型属性断言失败时触发。
    """
    from vertical_harness.settings import HarnessSettings

    # 清除 VA_MODEL 环境变量，模拟完全未配置模型的情况
    monkeypatch.delenv("VA_MODEL", raising=False)
    # 此时实例化 HarnessSettings 必须因缺少必填项 model 而报错
    with pytest.raises(ValidationError):
        HarnessSettings(_env_file=None)

    # 显式传入有效配置进行实例化
    settings = HarnessSettings(
        _env_file=None,
        model="deepseek:deepseek-flash",
        api_key="sk-test",
        base_url="https://api.deepseek.com",
    )
    # 验证配置字段已正确注入与解析
    assert settings.model == "deepseek:deepseek-flash"
    assert settings.api_key is not None and settings.api_key.get_secret_value() == "sk-test"
    assert settings.base_url == "https://api.deepseek.com"



