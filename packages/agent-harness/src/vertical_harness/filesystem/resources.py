"""Harness 指令 (AGENTS.md) 与 Agent 技能 (Skills) 本地资源管理桥接模块。

本模块提供全局与项目级智能体资源管理器 `HarnessResources`：
1. 范围隔离：区分全局作用域（`~/.agent-harness/`）与项目作用域（`<workspace>/.agents/`）；
2. 权限与符号链接防御：深度审计路径及父级目录，严禁软链接、NTFS 重解析点（Reparse Points）越出安全边界；
3. 技能规范校验：严格校验 `SKILL.md` 的 YAML Frontmatter 元数据格式（包含非空 name 与 description）；
4. 安全归档导入：支持 ZIP 技能包解压导入，内置 Zip Bomb 防御（最大 512 个条目、10 MiB 体积）、路径遍历与大小写碰撞检测。
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import yaml  # type: ignore[import-untyped]  # PyYAML 无内置类型；解析值在下方校验。

from vertical_harness.filesystem.file_mutation import FileMutation

# 解析 SKILL.md 头部 YAML frontmatter 的正则表达式
_FRONTMATTER: re.Pattern[str] = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*\r?\n", re.DOTALL)
# 技能压缩包最大允许解压的物理文件条目数量上限，防范 Zip 炸弹
_MAX_ARCHIVE_FILES: int = 512
# 技能压缩包解压后的总字节数硬上限（10 MiB）
_MAX_ARCHIVE_SIZE: int = 10 * 1024 * 1024
# 匹配文件或目录名中包含的非法字符集（包括斜杠、冒号、通配符及不可见控制字符）
_INVALID_NAME: re.Pattern[str] = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
# Windows 系统保留设备文件名黑名单
_WINDOWS_RESERVED: set[str] = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class HarnessResources:
    """读取并管理全局系统级或项目级的 Agent 指令 (AGENTS.md) 与 Skills 技能资源。

    提供巡检、原子写入保存、安全删除以及 ZIP 归档包解压导入等完整管理功能。

    Attributes:
        workspace (Path | None): 关联的项目工作区物理根路径；若为 None 则仅允许操作全局资源。
    """

    def __init__(self, workspace: Path | None) -> None:
        """初始化 HarnessResources 资源管理器实例。

        Args:
            workspace (Path | None): 关联的项目工作区根路径；若仅管理 global 全局资源可传入 None。
        """
        self.workspace = workspace

    @staticmethod
    def _validate_name(name: str) -> str:
        """校验资源文件或目录名称合法性。

        检查名称非空、非相对符号 `.` / `..`、不含非法特殊字符或控制字符、不以点或空格结尾，
        并且不能是 Windows 保留设备名称（如 CON、NUL、COM1 等）。

        Args:
            name (str): 待校验的资源文件或文件夹名称。

        Returns:
            str: 校验通过的合法名称。

        Raises:
            ValueError: 当名称不符合上述任一命名约束时抛出。
        """
        if not name or name in {".", ".."}:
            raise ValueError("invalid resource name")
        if (
            _INVALID_NAME.search(name)
            or name.endswith((".", " "))
            or name.split(".", 1)[0].upper() in _WINDOWS_RESERVED
        ):
            raise ValueError("invalid resource name")
        return name

    @staticmethod
    def _skill_metadata(content: str) -> dict[str, str]:
        """解析并校验 SKILL.md 文件头部的 YAML frontmatter 元数据。

        验证内容体积不超过 10 MiB，且必须以 `---` 包裹的 YAML 元数据块开头，
        必须包含非空的 `name` 和 `description` 字符串键值对。

        Args:
            content (str): SKILL.md 的原始 Markdown 文本内容。

        Returns:
            dict[str, str]: 提取出的标准元数据字典，包含 `"name"` 与 `"description"`。

        Raises:
            ValueError: 当缺少 frontmatter、体积超限或缺少必填元数据字段时抛出。
        """
        if len(content) > _MAX_ARCHIVE_SIZE:
            raise ValueError("SKILL.md exceeds 10 MiB")
        match = _FRONTMATTER.match(content)
        if match is None:
            raise ValueError("SKILL.md must start with YAML frontmatter")
        metadata = yaml.safe_load(match.group(1))
        if not isinstance(metadata, dict):
            raise ValueError("SKILL.md frontmatter must be a mapping")
        name, description = metadata.get("name"), metadata.get("description")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("SKILL.md frontmatter requires a non-empty string name")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("SKILL.md frontmatter requires a non-empty string description")
        return {"name": name.strip(), "description": description.strip()}

    @classmethod
    def _ensure_no_symlink(cls, path: Path, boundary: Path) -> None:
        """递归审计路径及父级目录，严格确保不包含符号链接且未越出沙箱边界。

        从目标路径向上回溯至允许的根目录 boundary，期间任何一级目录若为软链接或重解析点则立即报错。

        Args:
            path (Path): 待审计的目标物理路径。
            boundary (Path): 允许的最大安全根边界目录。

        Raises:
            ValueError: 若路径包含符号链接、重解析点或解析后逃逸出边界目录时抛出。
        """
        path = path.absolute()
        boundary = boundary.absolute()
        if cls._is_link(boundary):
            raise ValueError("resource directory is a symlink")
        current = path
        while current != boundary:
            if cls._is_link(current):
                raise ValueError("resource path contains a symlink")
            current = current.parent
            if current == current.parent:
                raise ValueError("resource path is outside its allowed directory")
        if not path.resolve().is_relative_to(boundary.resolve()):
            raise ValueError("resource path is outside its allowed directory")

    @staticmethod
    def _is_link(path: Path) -> bool:
        """检测指定路径是否为符号链接或包含 NTFS Reparse Point 重解析点。

        Args:
            path (Path): 待检查的路径对象。

        Returns:
            bool: 若为 POSIX 软链接或 Windows 重解析点则返回 True，否则返回 False。
        """
        if path.is_symlink():
            return True
        if path.exists():
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
            return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
        return False

    @classmethod
    def _ensure_tree_has_no_links(cls, root: Path, boundary: Path) -> None:
        """递归遍历整棵目录树，审计确保无任何符号链接或越界文件。

        在删除技能目录前必须执行此检查，防止通过符号链接意外级联删除系统或外部重要文件。

        Args:
            root (Path): 待审计的本地目录树根路径。
            boundary (Path): 允许的最大安全根边界。

        Raises:
            ValueError: 若目录树内发现任何符号链接则抛出。
        """
        for directory, subdirectories, files in os.walk(root, topdown=True, followlinks=False):
            path = Path(directory)
            for name in (*subdirectories, *files):
                cls._ensure_no_symlink(path / name, boundary)

    def _paths(self, scope: str) -> tuple[Path, Path, Path, Path]:
        """根据配置作用域解析出资源根目录、AGENTS.md 指令文件路径以及 skills 技能目录。

        Args:
            scope (str): 作用域标识符，必须是 `"global"` 或 `"project"`。

        Returns:
            tuple[Path, Path, Path, Path]: 包含以下四个路径的元组：
                - `root` (Path): 资源配置根目录；
                - `agent_path` (Path): AGENTS.md 指令文件路径；
                - `skills_root` (Path): 技能存放目录；
                - `boundary` (Path): 安全审计边界根路径。

        Raises:
            ValueError: 若 `scope` 参数非法，或在未指定工作区时请求 `project` 作用域则抛出。
        """
        if scope == "global":
            root = Path.home() / ".agent-harness"
            return root, root / "AGENTS.md", root / "skills", Path.home()
        if scope == "project":
            if self.workspace is None:
                raise ValueError("project resources require a workspace")
            root = self.workspace / ".agents"
            return root, self.workspace / "AGENTS.md", root / "skills", self.workspace

        raise ValueError("scope must be 'global' or 'project'")

    def inspect(self, scope: str) -> dict[str, Any]:
        """巡检指定作用域下的指令文件状态与可用技能列表视图。

        读取 AGENTS.md 指令文件是否存在及文本内容；遍历 skills 目录下所有符合规范的技能包，
        解析其 `SKILL.md` 元数据。

        Args:
            scope (str): 巡检目标作用域，`"global"` 或 `"project"`。

        Returns:
            dict[str, Any]: 结构化巡检快照字典，格式如下：
                - `scope` (str): 作用域字符串；
                - `root` (str): 资源根目录绝对路径；
                - `agent` (dict): `{"content": str, "source": str, "exists": bool}`；
                - `skills` (list[dict]): 各技能描述列表，每个元素包含 `name`, `description`, `directory`, `content`。
        """
        root, agent_path, skills_root, boundary = self._paths(scope)
        self._ensure_no_symlink(root, boundary)
        self._ensure_no_symlink(agent_path, boundary)
        self._ensure_no_symlink(skills_root, boundary)
        agent_exists = agent_path.is_file()
        agent = {
            "content": agent_path.read_text(encoding="utf-8") if agent_exists else "",
            "source": str(agent_path),
            "exists": agent_exists,
        }
        skills = []
        if skills_root.exists():
            for directory in sorted(skills_root.iterdir(), key=lambda path: path.name.casefold()):
                self._validate_name(directory.name)
                self._ensure_no_symlink(directory, boundary)
                skill_file = directory / "SKILL.md"
                if not directory.is_dir() or not skill_file.is_file():
                    continue
                self._ensure_no_symlink(skill_file, boundary)
                content = skill_file.read_text(encoding="utf-8")
                metadata = self._skill_metadata(content)
                skills.append(
                    {
                        "name": directory.name,
                        "description": metadata["description"],
                        "directory": str(directory),
                        "content": content,
                    }
                )
        return {"scope": scope, "root": str(root), "agent": agent, "skills": skills}

    def save(self, scope: str, kind: str, name: str, content: str) -> dict[str, Any]:
        """保存或更新指定作用域下的指令文件或技能 SKILL.md 内容（原子写入）。

        Args:
            scope (str): 作用域标识（`"global"` 或 `"project"`）。
            kind (str): 资源类型，必须为 `"agent"`（修改 AGENTS.md）或 `"skill"`（修改 SKILL.md）。
            name (str): 技能名称（当 kind 为 `"agent"` 时必须为空字符串）。
            content (str): 准备保存的 UTF-8 文本内容。

        Returns:
            dict[str, Any]: 写入完成后最新的 `inspect(scope)` 资源状态快照。

        Raises:
            ValueError: 当参数不符合约束、包含软链接或元数据解析失败时抛出。
        """
        root, agent_path, skills_root, boundary = self._paths(scope)
        if kind == "agent":
            if name:
                raise ValueError("agent resource name must be empty")
            target = agent_path
            parent = target.parent
        elif kind == "skill":
            name = self._validate_name(name)
            self._skill_metadata(content)
            target = skills_root / name / "SKILL.md"
            parent = target.parent
        else:
            raise ValueError("kind must be 'agent' or 'skill'")
        self._ensure_no_symlink(root, boundary)
        self._ensure_no_symlink(parent, boundary)
        self._ensure_no_symlink(target, boundary)
        parent.mkdir(parents=True, exist_ok=True)
        self._ensure_no_symlink(parent, boundary)
        with FileMutation.acquire([target]):
            FileMutation.atomic_write(target, content.encode("utf-8"))
        return self.inspect(scope)

    def remove(self, scope: str, kind: str, name: str) -> dict[str, Any]:
        """删除指定作用域下的指令文件或技能完整目录树。

        Args:
            scope (str): 作用域（`"global"` 或 `"project"`）。
            kind (str): 资源类别（`"agent"` 或 `"skill"`）。
            name (str): 技能名称（当 kind 为 `"agent"` 时必须为空字符串）。

        Returns:
            dict[str, Any]: 删除完成后最新的 `inspect(scope)` 状态快照。

        Raises:
            ValueError: 当参数非法或目标包含符号链接时抛出。
        """
        root, agent_path, skills_root, boundary = self._paths(scope)
        self._ensure_no_symlink(root, boundary)
        if kind == "agent":
            if name:
                raise ValueError("agent resource name must be empty")
            target = agent_path
            self._ensure_no_symlink(target, boundary)
            if target.exists():
                target.unlink()
        elif kind == "skill":
            name = self._validate_name(name)
            target = skills_root / name
            self._ensure_no_symlink(target, boundary)
            if target.exists():
                self._ensure_tree_has_no_links(target, boundary)
                shutil.rmtree(target)
        else:
            raise ValueError("kind must be 'agent' or 'skill'")
        return self.inspect(scope)

    def import_archive(self, scope: str, archive_path: Path) -> dict[str, Any]:
        """从 ZIP 归档文件中解压并安全导入单项技能资源。

        执行严密的解压防护：
        1. 限制条目数量（<= 512）与展开总体积（<= 10 MiB）；
        2. 拒绝任何包含绝对路径、反斜杠、空字符、上卷符号 `..` 的条目；
        3. 检测同名大小写碰撞、目录文件同名冲突，严禁包含符号链接；
        4. 必须包含且仅包含一个根级 `SKILL.md`，并在暂存目录解压完毕后原子重命名导入。

        Args:
            scope (str): 目标导入作用域（`"global"` 或 `"project"`）。
            archive_path (Path): 本地 ZIP 归档文件的物理路径。

        Returns:
            dict[str, Any]: 导入成功后的最新资源巡检状态快照字典。

        Raises:
            ValueError: 压缩包格式非法、条目超限、包含危险路径或元数据损坏时抛出。
            FileExistsError: 若目标技能同名目录已存在时抛出。
        """
        root, _, skills_root, boundary = self._paths(scope)
        self._ensure_no_symlink(root, boundary)
        self._ensure_no_symlink(skills_root, boundary)

        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            # 限制压缩包条目数在 1 至 512 之间，防止 Zip Bomb 拒绝服务攻击
            if not entries or len(entries) > _MAX_ARCHIVE_FILES:
                raise ValueError("skill archive must contain between 1 and 512 entries")

            names: set[str] = set()
            component_names: dict[str, str] = {}
            total_size = 0
            files: dict[str, bytes] = {}
            for entry in entries:
                raw_name = entry.filename
                if "\\" in raw_name or "\x00" in raw_name or raw_name.startswith("/"):
                    raise ValueError("skill archive contains an invalid path")
                path_text = raw_name[:-1] if entry.is_dir() and raw_name.endswith("/") else raw_name
                raw_parts = path_text.split("/")
                path = PurePosixPath(path_text)
                if (
                    not path_text
                    or path.is_absolute()
                    or any(part in {"", ".", ".."} for part in raw_parts)
                ):
                    raise ValueError("skill archive contains an invalid path")

                for index, part in enumerate(raw_parts):
                    self._validate_name(part)
                    prefix = "/".join(raw_parts[: index + 1])
                    component_key = prefix.casefold()
                    if (
                        component_key in component_names
                        and component_names[component_key] != prefix
                    ):
                        raise ValueError("skill archive contains case-colliding paths")
                    component_names[component_key] = prefix

                key = path.as_posix().casefold()
                if key in names:
                    raise ValueError("skill archive contains duplicate paths")
                names.add(key)

                # 严格禁止归档条目中包含符号链接
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ValueError("skill archive cannot contain symlinks")

                total_size += entry.file_size
                if total_size > _MAX_ARCHIVE_SIZE:
                    raise ValueError("skill archive exceeds 10 MiB expanded size")

                data = archive.read(entry)
                if entry.is_dir():
                    if data:
                        raise ValueError("skill archive directory entries cannot contain data")
                    continue
                files[path.as_posix()] = data
        file_names = set(files)
        if any(
            "/".join(path.parts[:index]) in file_names
            for path in (PurePosixPath(name) for name in files)
            for index in range(1, len(path.parts))
        ):
            raise ValueError("skill archive contains a file and directory at the same path")
        file_paths = [PurePosixPath(name) for name in files]
        skill_files = [path for path in file_paths if path.name == "SKILL.md"]
        direct = "SKILL.md" in files
        if direct:
            if len(skill_files) != 1:
                raise ValueError("skill archive must contain one SKILL.md")
            skill_name = self._skill_metadata(files["SKILL.md"].decode("utf-8"))["name"]
            relative = {name: PurePosixPath(name) for name in files}
        else:
            roots = {path.parts[0] for path in file_paths}
            if len(roots) != 1:
                raise ValueError("skill archive must contain one skill root")
            skill_name = next(iter(roots))
            self._validate_name(skill_name)
            if len(skill_files) != 1 or len(skill_files[0].parts) != 2:
                raise ValueError("skill archive must contain one root SKILL.md")
            self._skill_metadata(files[skill_files[0].as_posix()].decode("utf-8"))
            relative = {name: PurePosixPath(name).relative_to(skill_name) for name in files}
        self._validate_name(skill_name)
        skills_root.mkdir(parents=True, exist_ok=True)
        self._ensure_no_symlink(skills_root, boundary)
        destination = skills_root / skill_name
        if destination.exists():
            raise FileExistsError(f"skill already exists: {skill_name}")

        with tempfile.TemporaryDirectory(dir=skills_root) as staging_name:
            staging = Path(staging_name) / skill_name
            staging.mkdir()
            for name, data in files.items():
                target = staging / relative[name]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            staging.rename(destination)
        return self.inspect(scope)
