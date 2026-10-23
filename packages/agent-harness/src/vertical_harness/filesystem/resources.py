"""Harness 指令 (AGENTS.md) 与 Agent 技能 (Skills) 本地资源管理桥接模块。

负责全局 (~/.agent-harness) 与项目级 (<workspace>/.agents) 资源的安全读写、
符号链接沙箱边界审计、ZIP 技能包解压导入与完整性验证。

:author: tongliga
:date: 2026-10-22
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import yaml

# 正则表达式与常量限制配置
_FRONTMATTER = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*\r?\n", re.DOTALL)
_MAX_ARCHIVE_FILES = 512
_MAX_ARCHIVE_SIZE = 10 * 1024 * 1024
_INVALID_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class HarnessResources:
    """读取并管理全局系统级或项目级的 Agent 指令 (AGENTS.md) 与 Skills 技能资源。

    :param workspace: 可选的项目工作区根路径（管理项目级资源时必需）。
    :type workspace: pathlib.Path | None
    :author: tongliga
    :date: 2026-10-22
    """

    def __init__(self, workspace: Path | None):
        """初始化 HarnessResources 资源管理器实例。

        :param workspace: 项目工作区物理根路径。
        :type workspace: pathlib.Path | None
        :author: tongliga
        :date: 2026-10-22
        """
        self.workspace = workspace

    @staticmethod
    def _validate_name(name: str) -> str:
        """校验资源文件或目录名称合法性。

        断言名称非空、不含非法特殊控制字符、不以点或空格结尾，且不属于 Windows 保留设备名称。

        :param name: 待校验的资源文件或目录名称。
        :type name: str
        :return: 校验通过的安全名称。
        :rtype: str
        :raises ValueError: 当名称为空、包含非法字符、以点或空格结尾，或属于 Windows 保留设备名称时抛出。
        :author: tongliga
        :date: 2026-10-22
        """
        # 第一步：校验基础非空与相对路径保留符
        if not name or name in {".", ".."}:
            raise ValueError("invalid resource name")

        # 第二步：检测非法字符、尾随点/空格及 Windows 系统保留设备名称
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

        :param content: SKILL.md 文件完整文本内容。
        :type content: str
        :return: 包含技能名称与描述的字典映射。
        :rtype: dict[str, str]
        :raises ValueError: 当文件大小超限、缺少 frontmatter、或缺少非空的 name/description 字段时抛出。
        :author: tongliga
        :date: 2026-10-22

        .. code-block:: python

            # return 输出元数据结构契约：
            {
                "name": "sample-skill",
                "description": "技能简要业务说明"
            }
        """
        # 第一步：校验文件内容字节长度（不超过 10 MiB）
        if len(content) > _MAX_ARCHIVE_SIZE:
            raise ValueError("SKILL.md exceeds 10 MiB")

        # 第二步：正则匹配并安全解析 YAML frontmatter 映射块
        match = _FRONTMATTER.match(content)
        if match is None:
            raise ValueError("SKILL.md must start with YAML frontmatter")
        metadata = yaml.safe_load(match.group(1))
        if not isinstance(metadata, dict):
            raise ValueError("SKILL.md frontmatter must be a mapping")

        # 第三步：严格断言必需的 name 与 description 字段非空
        name, description = metadata.get("name"), metadata.get("description")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("SKILL.md frontmatter requires a non-empty string name")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("SKILL.md frontmatter requires a non-empty string description")
        return {"name": name.strip(), "description": description.strip()}

    @classmethod
    def _ensure_no_symlink(cls, path: Path, boundary: Path) -> None:
        """递归审计路径及父级目录，严格确保不包含符号链接且未越出沙箱边界。

        :param path: 待校验的目标物理路径。
        :type path: pathlib.Path
        :param boundary: 允许访问的安全沙箱边界根目录。
        :type boundary: pathlib.Path
        :raises ValueError: 当路径本身或任一级父目录为符号链接，或超出指定边界时抛出。
        :author: tongliga
        :date: 2026-10-22
        """
        # 第一步：归一化为绝对路径并检查安全边界目录本身
        path = path.absolute()
        boundary = boundary.absolute()
        if cls._is_link(boundary):
            raise ValueError("resource directory is a symlink")

        # 第二步：逐级向上回溯各级父目录，断言杜绝符号链接
        current = path
        while current != boundary:
            if cls._is_link(current):
                raise ValueError("resource path contains a symlink")
            current = current.parent
            if current == current.parent:
                raise ValueError("resource path is outside its allowed directory")

        # 第三步：断言目标路径解析后的物理位置严格处于边界相对路径之内
        if not path.resolve().is_relative_to(boundary.resolve()):
            raise ValueError("resource path is outside its allowed directory")

    @staticmethod
    def _is_link(path: Path) -> bool:
        """检测指定路径是否为符号链接或包含 NTFS Reparse Point 重解析点。

        :param path: 待检测的路径对象。
        :type path: pathlib.Path
        :return: 若为符号链接或重解析点返回 True，否则返回 False。
        :rtype: bool
        :author: tongliga
        :date: 2026-10-22
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

        :param root: 待扫描的根目录物理路径。
        :type root: pathlib.Path
        :param boundary: 允许访问的安全沙箱边界目录。
        :type boundary: pathlib.Path
        :raises ValueError: 当目录树中存在任何符号链接或越界文件时抛出。
        :author: tongliga
        :date: 2026-10-22
        """
        for directory, subdirectories, files in os.walk(root, topdown=True, followlinks=False):
            path = Path(directory)
            for name in (*subdirectories, *files):
                cls._ensure_no_symlink(path / name, boundary)

    def _paths(self, scope: str) -> tuple[Path, Path, Path]:
        """根据配置作用域解析出资源根目录、AGENTS.md 指令文件路径以及 skills 技能目录。

        :param scope: 配置生效范围，支持 'global' 或 'project'。
        :type scope: str
        :return: (资源根目录, 指令文件路径, 技能根目录) 三元元组。
        :rtype: tuple[pathlib.Path, pathlib.Path, pathlib.Path]
        :raises ValueError: 当 scope 取值非法或项目作用域缺少 workspace 时抛出。
        :author: tongliga
        :date: 2026-10-22
        """
        # 第一步：解析全局系统作用域目录路径 (~/.agent-harness)
        if scope == "global":
            root = Path.home() / ".agent-harness"
            return root, root / "AGENTS.md", root / "skills"

        # 第二步：解析项目级作用域目录路径 (<workspace>/.agents)
        if scope == "project":
            if self.workspace is None:
                raise ValueError("project resources require a workspace")
            root = self.workspace / ".agents"
            return root, self.workspace / "AGENTS.md", root / "skills"

        raise ValueError("scope must be 'global' or 'project'")

    def inspect(self, scope: str) -> dict:
        """巡检指定作用域下的指令文件状态与可用技能列表视图。

        :param scope: 配置生效范围（'global' 或 'project'）。
        :type scope: str
        :return: 包含作用域、根路径、指令元数据及技能列表的配置字典。
        :rtype: dict[str, typing.Any]
        :raises ValueError: 当作用域无效、缺少工作区或路径遭遇非法符号链接时抛出。
        :author: tongliga
        :date: 2026-10-22

        .. code-block:: python

            # return 输出配置结构契约：
            {
                "scope": "global",
                "root": "C:\\Users\\Hello\\.agent-harness",
                "agent": {
                    "content": "# Agent 指令...",
                    "source": "C:\\Users\\Hello\\.agent-harness\\AGENTS.md",
                    "exists": True
                },
                "skills": [
                    {
                        "name": "sample-skill",
                        "description": "技能说明",
                        "directory": "C:\\Users\\Hello\\.agent-harness\\skills\\sample-skill",
                        "content": "---\nname: sample-skill\ndescription: ...\n---\n..."
                    }
                ]
            }
        """
        # 第一步：获取作用域目录路径并执行符号链接沙箱安全审查
        root, agent_path, skills_root = self._paths(scope)
        boundary = Path.home() if scope == "global" else self.workspace
        self._ensure_no_symlink(root, boundary)
        self._ensure_no_symlink(agent_path, boundary)
        self._ensure_no_symlink(skills_root, boundary)

        # 第二步：读取 AGENTS.md 指令内容与存在状态
        agent_exists = agent_path.is_file()
        agent = {
            "content": agent_path.read_text(encoding="utf-8") if agent_exists else "",
            "source": str(agent_path),
            "exists": agent_exists,
        }

        # 第三步：遍历扫描并提取有效 skills 目录下的 SKILL.md 元数据
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

        # 第四步：组装标准配置视图字典契约并返回
        return {"scope": scope, "root": str(root), "agent": agent, "skills": skills}

    def save(self, scope: str, kind: str, name: str, content: str) -> dict:
        """保存或更新指定作用域下的指令文件或技能 SKILL.md 内容（原子写入）。

        :param scope: 配置生效范围（'global' 或 'project'）。
        :type scope: str
        :param kind: 资源类型（'agent' 表示指令，'skill' 表示技能）。
        :type kind: str
        :param name: 资源标识名称（kind 为 'agent' 时必须为空；kind 为 'skill' 时为技能目录名）。
        :type name: str
        :param content: 写入的文件完整内容文本。
        :type content: str
        :return: 保存更新后的完整资源检查字典契约。
        :rtype: dict[str, typing.Any]
        :raises ValueError: 当类型无效、名称非法、技能 YAML frontmatter 校验不通过或发生越界符号链接时抛出。
        :author: tongliga
        :date: 2026-10-22
        """
        # 第一步：解析目标路径并根据资源类型校验名称与内容规范
        root, agent_path, skills_root = self._paths(scope)
        boundary = Path.home() if scope == "global" else self.workspace
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

        # 第二步：执行严格的符号链接安全沙箱审计
        self._ensure_no_symlink(root, boundary)
        self._ensure_no_symlink(parent, boundary)
        self._ensure_no_symlink(target, boundary)
        parent.mkdir(parents=True, exist_ok=True)
        self._ensure_no_symlink(parent, boundary)

        # 第三步：使用同目录临时文件实现原子安全写入与重命名替换
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, target)

        # 第四步：重新巡检并返回最新的资源配置视图
        return self.inspect(scope)

    def remove(self, scope: str, kind: str, name: str) -> dict:
        """删除指定作用域下的指令文件或技能完整目录树。

        :param scope: 配置生效范围（'global' 或 'project'）。
        :type scope: str
        :param kind: 资源类型（'agent' 或 'skill'）。
        :type kind: str
        :param name: 待删除的资源标识名称（'agent' 必须为空；'skill' 为技能名称）。
        :type name: str
        :return: 删除更新后的完整资源检查字典契约。
        :rtype: dict[str, typing.Any]
        :raises ValueError: 当类型无效、名称非法或路径包含非法符号链接时抛出。
        :author: tongliga
        :date: 2026-10-22
        """
        # 第一步：解析目标路径并执行符号链接安全审计
        root, agent_path, skills_root = self._paths(scope)
        boundary = Path.home() if scope == "global" else self.workspace
        self._ensure_no_symlink(root, boundary)

        # 第二步：根据资源类型执行指令文件删除或技能目录树级联清除
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

        # 第三步：重新巡检并返回最新的资源配置视图
        return self.inspect(scope)

    def import_archive(self, scope: str, archive_path: Path) -> dict:
        """从 ZIP 归档文件中解压并安全导入单项技能资源。

        支持单层根目录或平铺根目录两种 ZIP 结构。严格审计压缩包内条目数、
        解压膨胀总体积、路径跨越与符号链接安全。

        :param scope: 配置生效范围（'global' 或 'project'）。
        :type scope: str
        :param archive_path: 待解压导入的 ZIP 压缩包物理绝对路径。
        :type archive_path: pathlib.Path
        :return: 导入完成后的最新资源配置检查字典。
        :rtype: dict[str, typing.Any]
        :raises ValueError: 当 ZIP 格式错误、条目超限、大小超限、包含软链接或路径冲突时抛出。
        :raises FileExistsError: 当目标技能名称在目标目录中已存在时抛出。
        :author: tongliga
        :date: 2026-10-22
        """
        # 第一步：校验目标存储根目录沙箱并打开 ZIP 压缩归档文件
        root, _, skills_root = self._paths(scope)
        boundary = Path.home() if scope == "global" else self.workspace
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

            # 第二步：安全审计压缩包内全部条目的路径规范、大小写碰撞与符号链接
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

        # 第三步：检测文件与目录路径冲突（Path Collision 审计）
        file_names = set(files)
        if any(
            "/".join(path.parts[:index]) in file_names
            for path in (PurePosixPath(name) for name in files)
            for index in range(1, len(path.parts))
        ):
            raise ValueError("skill archive contains a file and directory at the same path")

        # 第四步：解析并验证 SKILL.md 拓扑结构与元数据（支持平铺或单层根目录两种结构）
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

        # 第五步：检测目标技能是否已存在，并通过临时暂存目录实现原子解压与移入
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

        # 第六步：返回导入完成后的最新资源配置字典
        return self.inspect(scope)
