"""Harness 指令 (AGENTS.md) 与 Agent 技能 (Skills) 本地资源管理桥接模块。"""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import yaml  # type: ignore[import-untyped]  # PyYAML 无内置类型；解析值在下方校验。

from vertical_harness.filesystem.file_mutation import FileMutation

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
    """读取并管理全局系统级或项目级的 Agent 指令 (AGENTS.md) 与 Skills 技能资源。"""

    def __init__(self, workspace: Path | None):
        """初始化 HarnessResources 资源管理器实例。"""
        self.workspace = workspace

    @staticmethod
    def _validate_name(name: str) -> str:
        """校验资源文件或目录名称合法性。"""
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
        """解析并校验 SKILL.md 文件头部的 YAML frontmatter 元数据。"""
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
        """递归审计路径及父级目录，严格确保不包含符号链接且未越出沙箱边界。"""
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
        """检测指定路径是否为符号链接或包含 NTFS Reparse Point 重解析点。"""
        if path.is_symlink():
            return True
        if path.exists():
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
            return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
        return False

    @classmethod
    def _ensure_tree_has_no_links(cls, root: Path, boundary: Path) -> None:
        """递归遍历整棵目录树，审计确保无任何符号链接或越界文件。"""
        for directory, subdirectories, files in os.walk(root, topdown=True, followlinks=False):
            path = Path(directory)
            for name in (*subdirectories, *files):
                cls._ensure_no_symlink(path / name, boundary)

    def _paths(self, scope: str) -> tuple[Path, Path, Path, Path]:
        """根据配置作用域解析出资源根目录、AGENTS.md 指令文件路径以及 skills 技能目录。"""
        if scope == "global":
            root = Path.home() / ".agent-harness"
            return root, root / "AGENTS.md", root / "skills", Path.home()
        if scope == "project":
            if self.workspace is None:
                raise ValueError("project resources require a workspace")
            root = self.workspace / ".agents"
            return root, self.workspace / "AGENTS.md", root / "skills", self.workspace

        raise ValueError("scope must be 'global' or 'project'")

    def inspect(self, scope: str) -> dict:
        """巡检指定作用域下的指令文件状态与可用技能列表视图。"""
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

    def save(self, scope: str, kind: str, name: str, content: str) -> dict:
        """保存或更新指定作用域下的指令文件或技能 SKILL.md 内容（原子写入）。"""
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

    def remove(self, scope: str, kind: str, name: str) -> dict:
        """删除指定作用域下的指令文件或技能完整目录树。"""
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

    def import_archive(self, scope: str, archive_path: Path) -> dict:
        """从 ZIP 归档文件中解压并安全导入单项技能资源。"""
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
