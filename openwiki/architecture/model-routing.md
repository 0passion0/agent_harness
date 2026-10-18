---
type: concept
title: 动态模型路由与模型适配
description: 阐述基于 LangChain wrap_model_call 与 RunContext.task_tier 的经济型与高品质模型动态分流机制，以及 HarnessProfile 约束。
tags: [model-routing, langchain, architecture, dynamic-dispatch]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T12:40:07.975Z
sources:
  - id: openwiki-source-20e6f612e4230e00fcfd13ac
    resource: repo://packages/agent-harness/src/vertical_harness/config/settings.py
  - id: openwiki-source-fe5e5c14a08406c9691f9cea
    resource: repo://packages/agent-harness/src/vertical_harness/engine/routing.py
generated: { by: "antigravity", at: "2026-10-18T07:44:10.514Z" }
---

# 动态模型路由与模型适配

在垂直领域 Agent 系统中，不同阶段的任务往往具有差异化的能力要求和成本约束。`vertical-agent-harness` 采用原生的 LangChain 中间件机制实现了多模型动态路由分流，避免了侵入性重写底层 Agent Loop 或模型调用链。

## 1. 动态路由机制

动态模型路由的核心实现在 [packages/agent-harness/src/vertical_harness/engine/routing.py](../../packages/agent-harness/src/vertical_harness/engine/routing.py) 中：

- **路由初始化校验**：`make_router(settings)` 通过 [config/settings.py](../../packages/agent-harness/src/vertical_harness/config/settings.py) 中的 `settings.has_dynamic_routing` 属性检查 `economy_model`（`VA_ECONOMY_MODEL`）与 `quality_model`（`VA_QUALITY_MODEL`）的配置。若两者均未配置，则不启用动态路由（返回 `None`）；若仅配置了其中一个，则抛出 `ValueError` 阻断启动，确保配置成对生效。
- **调用级模型拦截**：利用 LangChain 提供的 `@wrap_model_call` 装饰器定义 `route_model` 拦截器。在每次模型实际调用前，拦截器从请求的运行时上下文 `request.runtime.context` 中提取可信的 `task_tier` 字段（默认为 `TaskTier.QUALITY`）。
- **动态模型覆盖**：当 `task_tier == TaskTier.ECONOMY` 时将模型切换为经济型模型（`economy_model`），其余默认路由到高品质模型（`quality_model`）。通过 `request.override(model=target_model)` 完成模型替换，并无缝交由下游处理器执行。

```python
@wrap_model_call
def route_model(
    request: ModelRequest,
    handler: Callable[[ModelRequest], ModelResponse],
) -> ModelResponse:
    task_tier = getattr(request.runtime.context, "task_tier", TaskTier.QUALITY)
    target_model = economy_model if task_tier == TaskTier.ECONOMY else quality_model
    logger.debug(
        "model_routed",
        extra={
            "event_fields": {
                "task_tier": TaskTier.ECONOMY.value
                if task_tier == TaskTier.ECONOMY
                else TaskTier.QUALITY.value,
                "model": settings.economy_model
                if task_tier == TaskTier.ECONOMY
                else settings.quality_model,
            }
        },
    )
    return handler(request.override(model=target_model))
```

## 2. 运行时上下文注入

模型路由所依赖的决策信号必须由宿主可信环境注入，而不是依赖大模型生成的自由文本或简单的消息计数：

- **RunContext 强契约与 TaskTier 枚举**：在 [packages/agent-harness/src/vertical_harness/plugins/contracts.py](../../packages/agent-harness/src/vertical_harness/plugins/contracts.py) 中定义了不可变数据类 `RunContext`，包含 `tenant_id`、`user_id` 与 `task_tier: Literal["economy", "quality"] = "quality"`；在 [config/settings.py](../../packages/agent-harness/src/vertical_harness/config/settings.py) 中定义了对应的 `TaskTier(StrEnum)`（`ECONOMY = "economy"`、`QUALITY = "quality"`）。
- **调用时注入**：在 CLI（如 [cli.py](../../packages/agent-harness/src/vertical_harness/cli.py) 的 `_invoke_prompt`）或上层服务调用 `agent.invoke()` 时，作为 `context` 参数传入，供底层 LangGraph runtime 及中间件读取。

## 3. 模型初始化与配置映射

模型构建函数 `init_configured_model` 封装了 `langchain.chat_models.init_chat_model`：
- 读取 [HarnessSettings](../operations/configuration.md) 的 `model` 以及计算属性 `settings.model_init_kwargs`（安全解包 `api_key` 的 `SecretStr` 并过滤非空 `base_url`）。
- 支持在动态路由初始化时传入自定义模型标识符（如 `settings.economy_model` 或 `settings.quality_model`），使用统一的鉴权与端点配置实例化对应的 `BaseChatModel`。

## 4. HarnessProfile 边界约束

根据架构设计规范 [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)：
- **作用边界**：`HarnessProfile`（如 `register_harness_profile`）仅用于针对特定供应商或模型适配底层 Harness 行为（如系统提示词微调、工具可见性支持），属于底层全局注册机制。
- **设计禁忌**：严禁将业务规则或动态任务路由逻辑写入模型 Profile 中；插件亦不得对全局 Profile 随意进行覆盖。模型分流始终由 `make_router` 中间件与运行时上下文协同处理。

## 相关页面

- [Harness 系统架构与核心组装](./overview.md)：了解整体中间件链与 Agent 编译流程。
- [环境变量配置与运维部署](../operations/configuration.md)：查看 `VA_MODEL`、`VA_ECONOMY_MODEL` 等环境变量定义。
