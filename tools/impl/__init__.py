"""模块注册中心 —— main.py 运行时会调用这里的函数加载组员的实现。

使用方法：
    1. 每个组员在 tools/impl/ 下创建自己的实现文件
    2. 在这里注册你的类（添加一个函数）
    3. main.py 会自动通过这里找到你的实现

三人各自的注册信息：
    ┌───────┬───────────────────────────────┬─────────────────┬───────────────────────────────┐
    │ 组员  │ 你创建的文件                   │ 你的类名         │ 你要写的注册函数                │
    ├───────┼───────────────────────────────┼─────────────────┼───────────────────────────────┤
    │ 组员A │ tools/impl/rag_impl.py        │ MyRagTool       │ get_rag_tool(config)          │
    │ 组员B │ tools/impl/execution_impl.py  │ MyExecutionTool │ get_execution_tool(config, session)  │
    │ 组员C │ tools/impl/verification_impl.py│ MyVerificationTool│ get_verification_tool(config, session)│
    └───────┴───────────────────────────────┴─────────────────┴───────────────────────────────┘

注意：
    - 函数名必须固定（get_rag_tool / get_execution_tool / get_verification_tool）
      因为 main.py 按这个名字导入
    - 类名可以自定义，只要在函数里改成你的类名就行
    - 使用 from ... import 延迟导入，避免循环依赖
"""

from __future__ import annotations

from core.config import AgentConfig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 组员 A 注册：数据与 RAG 模块
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 你要做的事：
#   1. 创建 tools/impl/rag_impl.py
#   2. 在里面写 class MyRagTool(RagToolInterface): ...
#   3. 下面的函数里改成你的类名
#
# 示例（组员 A 完成实现后，取消下面的注释并改成你的类名）：
#
# def get_rag_tool(config: AgentConfig):
#     from tools.impl.rag_impl import MyRagTool     # ← 改成你的类名
#     return MyRagTool(config)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 组员 B 注册：执行与交互模块（Playwright）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 你要做的事：
#   1. 创建 tools/impl/execution_impl.py
#   2. 在里面写 class MyExecutionTool(ExecutionToolInterface): ...
#   3. 下面的函数里改成你的类名
#
# 示例（组员 B 完成实现后，取消下面的注释并改成你的类名）：
#
# def get_execution_tool(config: AgentConfig, session: BrowserSession):
#     from tools.impl.execution_impl import MyExecutionTool     # ← 改成你的类名
#     return MyExecutionTool(config, session)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 组员 C 注册：验证与可视化模块
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 你要做的事：
#   1. 创建 tools/impl/verification_impl.py
#   2. 在里面写 class MyVerificationTool(VerificationToolInterface): ...
#   3. 下面的函数里改成你的类名
#
# 示例（组员 C 完成实现后，取消下面的注释并改成你的类名）：
#
# def get_verification_tool(config: AgentConfig, session: BrowserSession):
#     from tools.impl.verification_impl import MyVerificationTool     # ← 改成你的类名
#     return MyVerificationTool(config, session)

from typing import Any

from core.browser import BrowserSession


def get_execution_tool(config: Any, session: BrowserSession):
    # 修改点：注册真实执行与交互模块，让 main.py 能加载 PlaywrightExecutionTool。
    # session: 共享浏览器会话，用于在执行和验证模块间传递同一个 page。
    from tools.impl.execution_impl import PlaywrightExecutionTool

    return PlaywrightExecutionTool(config, session)


def get_rag_tool(config: Any):
    # 临时兼容：如果 RAG 模块还没完成，先复用 stub，避免 main.py 导入失败。
    # 后续完成 RAG 后，可替换为：from tools.impl.rag_impl import MyRagTool; return MyRagTool(config)
    from tools.stub.rag_stub import StubRagTool

    return StubRagTool()


def get_verification_tool(config: Any, session: BrowserSession):
    # session: 共享浏览器会话，验证模块通过 session.page 获取同一个 page。
    from tools.impl.verification_impl import VerificationAgent

    # 直接把 page 对象传给你的验证类
    return VerificationAgent(config=config, page=session.page)
