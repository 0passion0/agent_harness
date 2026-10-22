import stat
import zipfile
from pathlib import Path

import pytest

from vertical_harness.bridge.resources import HarnessResources


def skill(name="demo", description="A demo skill", body="Use this skill."):
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"


def make_archive(path, entries):
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return path


def test_global_and_project_resources_are_isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    resources = HarnessResources(workspace)

    resources.save("global", "agent", "", "global instructions")
    resources.save("global", "skill", "common", skill("metadata-name"))
    resources.save("project", "agent", "", "project instructions")
    resources.save("project", "skill", "common", skill("common", "project skill"))

    global_view = resources.inspect("global")
    project_view = resources.inspect("project")
    assert global_view["agent"]["content"] == "global instructions"
    assert global_view["agent"]["source"] == str(home / ".agent-harness" / "AGENTS.md")
    assert global_view["skills"][0]["name"] == "common"
    assert global_view["skills"][0]["description"] == "A demo skill"
    assert project_view["agent"]["content"] == "project instructions"
    assert project_view["skills"][0]["description"] == "project skill"

    resources.remove("global", "skill", "common")
    assert not (home / ".agent-harness" / "skills" / "common").exists()
    assert (workspace / ".agents" / "skills" / "common" / "SKILL.md").is_file()
    resources.remove("project", "agent", "")
    assert not (workspace / "AGENTS.md").exists()


@pytest.mark.parametrize("wrapped", [False, True])
def test_import_archive_preserves_assets(tmp_path, monkeypatch, wrapped):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    prefix = "demo/" if wrapped else ""
    archive = make_archive(
        tmp_path / "skill.zip",
        {
            prefix + "SKILL.md": skill("metadata-name", "Imported skill"),
            prefix + "scripts/run.py": "print('ok')\n",
            prefix + "assets/data.txt": "asset payload",
        },
    )

    result = HarnessResources(workspace).import_archive("project", archive)

    folder = "demo" if wrapped else "metadata-name"
    destination = workspace / ".agents" / "skills" / folder
    assert result["skills"][0]["name"] == folder
    assert (destination / "SKILL.md").read_text(encoding="utf-8") == skill(
        "metadata-name", "Imported skill"
    )
    assert (destination / "scripts" / "run.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert (destination / "assets" / "data.txt").read_text(encoding="utf-8") == "asset payload"


def test_import_conflict_keeps_existing_skill_unchanged(tmp_path):
    workspace = tmp_path / "workspace"
    existing = workspace / ".agents" / "skills" / "demo" / "SKILL.md"
    existing.parent.mkdir(parents=True)
    existing.write_text(skill(body="existing"), encoding="utf-8")
    archive = make_archive(tmp_path / "skill.zip", {"demo/SKILL.md": skill(body="incoming")})

    with pytest.raises(FileExistsError):
        HarnessResources(workspace).import_archive("project", archive)

    assert existing.read_text(encoding="utf-8") == skill(body="existing")


def test_save_rejects_invalid_skill_frontmatter_without_writing(tmp_path):
    workspace = tmp_path / "workspace"

    with pytest.raises(ValueError, match="frontmatter"):
        HarnessResources(workspace).save("project", "skill", "demo", "missing metadata")

    assert not (workspace / ".agents").exists()


@pytest.mark.parametrize("entry", ["../escape", "/absolute", "C:/drive", "folder\\escape"])
def test_import_rejects_unsafe_archive_paths_before_writing(tmp_path, entry):
    workspace = tmp_path / "workspace"
    archive = make_archive(
        tmp_path / "unsafe.zip",
        {
            "demo/SKILL.md": skill(),
            entry: "outside",
        },
    )

    with pytest.raises(ValueError):
        HarnessResources(workspace).import_archive("project", archive)

    assert not (workspace / ".agents").exists()


def test_import_rejects_archive_symlinks_before_writing(tmp_path):
    workspace = tmp_path / "workspace"
    archive_path = tmp_path / "symlink.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("demo/SKILL.md", skill())
        link = zipfile.ZipInfo("demo/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "../../outside")

    with pytest.raises(ValueError, match="symlinks"):
        HarnessResources(workspace).import_archive("project", archive_path)

    assert not (workspace / ".agents").exists()


def test_import_rejects_case_colliding_archive_roots(tmp_path):
    workspace = tmp_path / "workspace"
    archive = make_archive(
        tmp_path / "collision.zip",
        {
            "Demo/SKILL.md": skill(),
            "demo/extra.txt": "case collision",
        },
    )

    with pytest.raises(ValueError, match="case-colliding"):
        HarnessResources(workspace).import_archive("project", archive)

    assert not (workspace / ".agents").exists()


def test_symlinked_skill_directory_is_not_removed(tmp_path):
    workspace = tmp_path / "workspace"
    external = tmp_path / "external"
    external.mkdir()
    outside_file = external / "keep.txt"
    outside_file.write_text("preserve", encoding="utf-8")
    skills = workspace / ".agents" / "skills"
    skills.mkdir(parents=True)
    try:
        (skills / "linked").symlink_to(external, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")

    with pytest.raises(ValueError, match="symlink"):
        HarnessResources(workspace).remove("project", "skill", "linked")

    assert outside_file.read_text(encoding="utf-8") == "preserve"


def test_project_resources_require_workspace():
    with pytest.raises(ValueError, match="require a workspace"):
        HarnessResources(None).inspect("project")
