"""插件架构契约小型架构测试模块。

验证以下架构约束：
1. 每个业务插件根目录下严格仅有一个 Python 文件（`__init__.py`）；
2. 业务插件必须显式继承 DomainPlugin ABC；
3. 缺失抽象成员（manifest 或 build）的插件子类在实例化时必定失败（Fail-Fast 抛出 TypeError）；
4. 公共层（vertical_plugins.common）与宿主层（vertical_harness）严禁直接导入具体业务插件。
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from vertical_plugins.common.base import DomainPlugin
from vertical_plugins.common.contracts import (
    BuildServices,
    PluginContribution,
    PluginManifest,
)
from vertical_plugins.data_analysis import DataAnalysisPlugin
from vertical_plugins.research_citation import ResearchCitationPlugin

PLUGINS_ROOT = Path(__file__).resolve().parents[1] / "packages" / "agent-harness" / "src" / "vertical_plugins"
HARNESS_ROOT = Path(__file__).resolve().parents[1] / "packages" / "agent-harness" / "src" / "vertical_harness"
COMMON_ROOT = PLUGINS_ROOT / "common"


def test_business_plugin_root_has_exactly_one_python_file() -> None:
    """每个业务插件根目录严格只有一个 Python 文件：__init__.py。"""
    plugin_dirs = [
        d
        for d in PLUGINS_ROOT.iterdir()
        if d.is_dir() and d.name != "common" and not d.name.startswith((".", "_"))
    ]
    assert len(plugin_dirs) >= 2, f"Expected business plugin directories under {PLUGINS_ROOT}"

    for plugin_dir in plugin_dirs:
        py_files = sorted(p.name for p in plugin_dir.glob("*.py"))
        assert py_files == ["__init__.py"], (
            f"Plugin directory {plugin_dir.name} must have only __init__.py at root, found: {py_files}"
        )


def test_business_plugins_inherit_domain_plugin_abc() -> None:
    """业务插件必须显式继承 DomainPlugin 抽象基类。"""
    for plugin_cls in (DataAnalysisPlugin, ResearchCitationPlugin):
        assert issubclass(plugin_cls, DomainPlugin), f"{plugin_cls.__name__} must inherit DomainPlugin"
        instance = plugin_cls()
        assert isinstance(instance, DomainPlugin), f"{plugin_cls.__name__} instance must be a DomainPlugin"
        assert isinstance(instance.manifest, PluginManifest)
        assert callable(instance.build)


def test_domain_plugin_incomplete_implementation_fails_instantiation() -> None:
    """缺失 manifest 或 build 抽象方法时，DomainPlugin 子类实例化必须失败抛出 TypeError。"""

    class MissingAll(DomainPlugin):
        pass

    with pytest.raises(TypeError) as exc_all:
        MissingAll()  # type: ignore[abstract]
    assert "Can't instantiate abstract class MissingAll" in str(exc_all.value)

    class MissingBuild(DomainPlugin):
        manifest = PluginManifest(
            plugin_id="test.no_build", version="0.1.0", description="Plugin without build"
        )

    with pytest.raises(TypeError) as exc_build:
        MissingBuild()  # type: ignore[abstract]
    assert "build" in str(exc_build.value)

    class MissingManifest(DomainPlugin):
        def build(self, services: BuildServices) -> PluginContribution:
            return PluginContribution()

    with pytest.raises(TypeError) as exc_manifest:
        MissingManifest()  # type: ignore[abstract]
    assert "manifest" in str(exc_manifest.value)


def _extract_module_imports(py_path: Path) -> set[str]:
    """使用 AST 提取 Python 文件中的顶层导入模块前缀。"""
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
    return imported_modules


def test_common_and_host_do_not_import_business_plugins() -> None:
    """静态检查：公共层与宿主层严禁直接导入具体业务插件。"""
    forbidden_prefixes = (
        "vertical_plugins.data_analysis",
        "vertical_plugins.research_citation",
        "data_analysis",
        "research_citation",
    )

    checked_dirs = [COMMON_ROOT, HARNESS_ROOT]
    for check_dir in checked_dirs:
        for py_path in check_dir.rglob("*.py"):
            imports = _extract_module_imports(py_path)
            for imp in imports:
                for forbidden in forbidden_prefixes:
                    assert not (imp == forbidden or imp.startswith(f"{forbidden}.")), (
                        f"Layer boundary violation in {py_path.relative_to(check_dir.parents[1])}: "
                        f"imports business plugin {imp!r}"
                    )


def test_runtime_isolation_clean_sys_modules() -> None:
    """运行时检查：在干净进程中导入 vertical_harness 不会间接导入业务插件实现。"""
    cmd = [
        sys.executable,
        "-c",
        (
            "import sys, vertical_harness, vertical_plugins.common; "
            "business = [m for m in sys.modules if 'data_analysis' in m or 'research_citation' in m]; "
            "assert not business, f'Leaked business modules: {business}'; "
            "print('ISOLATION_OK')"
        ),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"Runtime isolation failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    assert "ISOLATION_OK" in proc.stdout
