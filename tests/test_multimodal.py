"""真实 Agent 图与 DeepSeek 请求序列化；不调用外部模型。"""

import base64
from contextlib import asynccontextmanager

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_deepseek import ChatDeepSeek
from pydantic import Field

from vertical_harness import HarnessSettings, cli
from vertical_harness.engine import compose

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII="
)


class RecordingModel(FakeMessagesListChatModel):
    seen: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        self.seen.append(messages)
        return super()._generate(messages, *args, **kwargs)


@pytest.mark.parametrize("mcp", [False, True])
@pytest.mark.parametrize("travel", [False, True])
@pytest.mark.parametrize("prompt", [None, "Describe these images"])
def test_cli_images_reach_model_as_images(tmp_path, monkeypatch, capsys, mcp, travel, prompt):
    settings = HarnessSettings(
        _env_file=None, model="test:model", workspace_root=tmp_path / "workspace",
        enabled_plugins=[], enable_memory=False, enable_planning=False,
        mcp_servers={"local": {"transport": "stdio", "command": "unused"}} if mcp else {},
    )
    model = RecordingModel(responses=[AIMessage(content="received")], profile={"image_inputs": True})
    monkeypatch.setattr(cli, "HarnessSettings", lambda: settings)
    monkeypatch.setattr(compose, "init_configured_model", lambda *_: model)

    @asynccontextmanager
    async def no_mcp_tools(settings):
        yield ()

    monkeypatch.setattr(compose, "aopen_mcp_tools", no_mcp_tools)
    image = tmp_path / "测试图片.PNG"
    image.write_bytes(PNG)
    args = [prompt] if prompt else []
    args += ["--image", str(image), "--image", str(image), "--thread-id", "original"]
    if travel:
        cli.main(["Initial turn", "--thread-id", "original"])
        args += ["--fork-thread-id", "fork"]
    cli.main(args)

    message = next(m for m in reversed(model.seen[-1]) if isinstance(m, HumanMessage))
    images = [b for b in message.content if b["type"] == "image"]
    assert len(images) == 2
    assert all(base64.b64decode(b["base64"]) == PNG for b in images)
    assert all(b["mime_type"] == "image/png" for b in images)
    assert message.text == (prompt or "")
    # 检查实际适配器生成的 HTTP 载荷，避免仅在图内部保留图片、出站却变成文本。
    adapter = ChatDeepSeek(model="deepseek-flash", api_key="test")
    payload = adapter._get_request_payload(model.seen[-1])
    content = next(m["content"] for m in reversed(payload["messages"]) if m["role"] == "user")
    assert isinstance(content, list)
    urls = [b["image_url"]["url"] for b in content if b["type"] == "image_url"]
    assert urls == [f"data:image/png;base64,{base64.b64encode(PNG).decode('ascii')}"] * 2
    output = capsys.readouterr()
    assert output.out.splitlines() == ["received"] * (2 if travel else 1)
    assert images[0]["base64"] not in output.err


@pytest.mark.parametrize("kind", ["missing", "directory", "empty", "unsupported", "oversized"])
def test_invalid_images_fail_before_building_agent(tmp_path, monkeypatch, capsys, kind):
    image = tmp_path / ("image.txt" if kind == "unsupported" else "image.png")
    if kind == "directory":
        image.mkdir()
    elif kind != "missing":
        image.write_bytes(b"" if kind == "empty" else PNG)
        if kind == "oversized":
            with image.open("r+b") as source:
                source.truncate(20 * 1024 * 1024 + 1)

    def unexpected_settings():
        pytest.fail("Invalid images must fail before model/storage initialization")

    monkeypatch.setattr(cli, "HarnessSettings", unexpected_settings)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--image", str(image)])
    assert exc.value.code == 2
    assert "--image" in capsys.readouterr().err


@pytest.mark.parametrize("command", [
    ["--list-plugins"], ["--list-workflows"], ["--history", "--thread-id", "test"],
    ["--memory-worker"], ["--consolidate-memory"],
])
def test_images_cannot_be_silently_ignored(tmp_path, command):
    image = tmp_path / "image.png"
    image.write_bytes(PNG)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--image", str(image), *command])
    assert exc.value.code == 2
