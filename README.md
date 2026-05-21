# WebAgent - 基于大模型的测试场景生成与智能测试工具

基于 LangGraph **Plan-Execute-Verify** 循环架构的智能 Web 测试框架。LLM 通过三节点循环（规划→执行→复盘）自主完成：获取手册、提取功能点、生成测试用例、驱动浏览器执行测试、验证结果并生成报告。

## 架构

```
                  ┌─────────┐
                  │  START  │
                  └────┬────┘
                       │
                       ▼
                ┌──────────────┐
           ┌────│   planner    │◄──────────────────────────┐
           │    │  (规划者)     │                            │
           │    │              │                            │
           │    │  LLM 分析    │                            │
           │    │  当前状态     │                            │
           │    │  决定下一步   │                            │
           │    └──────┬───────┘                            │
           │           │ 输出: current_task                 │
           │           │ {"action":"crawl_manual",          │
           │           │  "args":{"url":"..."}}             │
           │           ▼                                    │
           │    ┌──────────────┐                            │
           │    │   executor   │                            │
           │    │  (执行者)     │                            │
           │    │              │                            │
           │    │ 根据 action  │                            │
           │    │ 分发到对应    │                            │
           │    │ tools 接口   │                            │
           │    └──────┬───────┘                            │
           │           │ 输出: task_result                  │
           │           ▼                                    │
           │    ┌──────────────┐   response 非空?            │
           │    │  replanner   │────────────────────────► END
           │    │  (复盘者)     │
           │    │              │   past_steps >= 30?
           │    │  LLM 检查    │────────────────────────► END
           │    │  执行结果     │
           │    │  决定继续/结束│  否则
           │    └──────┬───────┘
           │           │ 继续
           └───────────┘
```

### 三节点职责

| 节点 | 文件 | 职责 | LLM 交互 |
|------|------|------|----------|
| **planner** (规划者) | `agent/planner.py` | 分析当前状态（已完成的步骤、已有的数据），用 LLM + Pydantic 结构化输出决定下一步动作 | 是 — `with_structured_output(PlannedAction)` |
| **executor** (执行者) | `agent/executor.py` | 根据 `current_task["action"]` 分发到对应 tools 接口方法，直接调用（不通过 @tool 包装） | 否 — 纯代码分发 |
| **replanner** (复盘者) | `agent/replanner.py` | 检查执行结果，判断是否所有工作完成。完成→设 `response`→路由到 END；未完成→路由回 planner | 是 — `with_structured_output(ReplanDecision)` |

### 状态流转

所有数据内嵌在 `AgentState`（`agent/state.py`）中，节点间通过状态字典传递数据：

```
AgentState
├── 配置（只读）: target_url, manual_url, manual_dir, chroma_dir, max_retries
├── 规划调度:    input, current_task, past_steps (追加), response (非空=结束)
└── 数据缓存:    documents, features, test_cases,
                 execution_plans, execution_results, execution_memory,
                 verification_results
```

数据在各节点间的流转：

```
planner 输出 current_task → executor 读取并执行 → 写入数据字段 → replanner 检查

具体 action 与数据写入:
  crawl_manual        → documents
  load_local_manual   → documents
  build_knowledge_base → (读取 documents, 返回路径)
  extract_features    → features
  generate_scenarios  → test_cases
  plan_and_execute    → execution_plans + execution_results (+ execution_memory)
  verify_results      → verification_results
  generate_report     → response (非空, 触发 END)
```

### 工具调用机制（你的代码如何被 Agent 调用）

```
运行 python main.py（不带 --stub）
  │
  ├─ main.py 创建共享浏览器会话 BrowserSession（空壳，不启动浏览器）
  │
  ├─ main.py 从 tools/impl/__init__.py 加载组员实现
  │    rag_tool = get_rag_tool(config)                       # → 你的 MyRagTool 实例
  │    execution_tool = get_execution_tool(config, session)  # → 你的 MyExecutionTool 实例
  │    verification_tool = get_verification_tool(config, session)  # → 你的 MyVerificationTool 实例
  │
  ├─ main.py 传给 build_agent_graph()
  │    └─ graph.py 构建三节点 StateGraph
  │         executor 节点直接持有你的实例引用（闭包注入）
  │
  └─ 循环执行 planner → executor → replanner
       └─ executor 根据 action 分发到你的方法:
            "crawl_manual"    → rag_tool.crawl_and_load_manual(url)
            "extract_features" → rag_tool.extract_features(path)
            "plan_and_execute" → exec_tool.plan(tc) + exec_tool.execute(plan, url, mem)
            "verify_results"  → verify_tool.verify(tc, results, mem)
            "generate_report" → verify_tool.visualize(state)
```

**BrowserSession 共享浏览器会话**：执行模块和验证模块共用同一个 Playwright Page，确保验证时可以访问执行后的页面状态。详见 [共享浏览器会话](#共享浏览器会话browser-session)。

**关键点**：
- executor 直接调用你的接口方法（不通过 `@tool` 包装），方法签名和返回格式完全不变
- 你的方法返回值由 executor 写入 AgentState，组员无感知
- `tools/` 目录下的 `DataCache`、`make_*_tools` 仍保留（用于集成测试），但 Agent 不再使用

### 执行流程示例

```
第1轮: planner → "获取手册文档"      → executor → crawl_manual       → replanner → 继续
第2轮: planner → "构建知识库"        → executor → build_knowledge_base → replanner → 继续
第3轮: planner → "提取功能点"        → executor → extract_features   → replanner → 继续
第4轮: planner → "生成测试用例"      → executor → generate_scenarios → replanner → 继续
第5轮: planner → "执行 TS_F001_001"  → executor → plan_and_execute   → replanner → 继续
第6轮: planner → "验证 TS_F001_001"  → executor → verify_results     → replanner → 继续
第7轮: planner → "执行 TS_F002_001"  → executor → plan_and_execute   → replanner → 继续
  ...
最后一轮: planner → "生成报告"       → executor → generate_report    → replanner → response 非空 → END
```

如果中间某步失败（如 crawl_manual 返回空），replanner 能感知并让 planner 尝试其他策略（如换 URL 重试）。最大迭代次数 30 次，防止无限循环。

### 三个核心模块

| 模块 | 接口文件 | 职责 |
|------|---------|------|
| 数据与RAG模块 | `tools/rag_tool.py` | 爬取手册、构建知识库、提取功能点、生成测试用例 |
| 执行与交互模块 | `tools/execution_tool.py` | 规划执行步骤、基于Playwright执行测试 |
| 验证与可视化模块 | `tools/verification_tool.py` | 验证测试结果、生成可视化报告 |

### 共享浏览器会话（Browser Session）

执行模块和验证模块需要共用同一个 Playwright Page，这样验证模块可以在执行后的页面上做实时检查（检查元素、截图对比等）。框架通过 `core/browser.py` 中的 `BrowserSession` 实现：

```
BrowserSession（空壳，不启动浏览器）
  │
  ├── 执行模块调用 session.ensure_page(headless)
  │     → 懒启动：第一次调用时才启动浏览器，创建 page
  │     → 后续调用返回同一个 page
  │     → 不要关闭浏览器！验证模块还要用
  │
  ├── 验证模块通过 session.page 获取同一个 page
  │     page = self.session.page
  │     if page and not page.is_closed():
  │         current_url = page.url       # 检查当前页面
  │         body_text = page.locator("body").inner_text()
  │
  └── main.py 流程结束后统一清理
        session.close()  # 在 finally 中调用，确保浏览器资源释放
```

**组员 B（执行模块）**：构造函数接收 `session`，在 `execute()` 中调用 `self.session.ensure_page(headless)` 获取 page，**不要自己创建/销毁浏览器**。

**组员 C（验证模块）**：构造函数接收 `session`，在 `verify()` 中通过 `self.session.page` 获取执行模块操作过的同一个 page，做实时页面验证。

**组员 A（RAG 模块）**：不涉及浏览器，无需使用 session。

### 工作流

1. **获取手册** — planner 决定获取方式（远程爬取 / 本地加载），executor 调用 RAG 接口
2. **构建知识库** — executor 调用 `build_knowledge_base` 构建向量知识库
3. **提取功能点** — executor 调用 `extract_features` 从知识库提取功能点
4. **生成测试用例** — executor 调用 `generate_scenarios` 根据功能点生成测试用例（含 expectations 预期）
5. **规划并执行** — 对每个用例 executor 调用 `plan` + `execute`（合并为 `plan_and_execute`，通过 Playwright 驱动浏览器）
6. **验证结果** — executor 调用 `verify` 将 expectations（预期）与实际执行结果对比
7. **失败重试** — replanner 感知失败，planner 可对同一用例重新 plan_and_execute（最多 max_retries 次）
8. **生成报告** — executor 调用 `visualize` 生成报告，response 非空触发 END

## 快速开始

### 1. 环境准备

```bash
# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器（必须）
playwright install chromium

# 如果上面的命令网络超时，使用国内镜像：
PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright playwright install chromium
```

### 2. 配置 API Key

编辑 `.env` 文件：

```
ZHIPUAI_API_KEY=your_zhipuai_api_key_here
```

### 3. 运行

```bash
# 全自动模式：只传目标网站，LLM 自主判断手册、生成用例、执行测试、验证结果
python main.py --url https://demo.4gaboards.com/

# 提供手册 URL，LLM 直接使用
python main.py --url https://demo.4gaboards.com/ --manual https://docs.4gaboards.com/

# 使用本地手册
python main.py --url https://demo.4gaboards.com/ --manual-dir ./manual

# 无头模式（服务器/CI 环境下运行，不弹出浏览器窗口）
python main.py --url https://demo.4gaboards.com/ --headless

# 使用 stub 模式测试框架（需要 API Key，工具不调真实浏览器，LLM 仍需调用）
python main.py --stub --url https://demo.4gaboards.com/

# 指定模型
python main.py --url https://demo.4gaboards.com/ --model deepseek-chat

# 查看帮助
python main.py --help
```

## 常见问题

### Q: 访问 demo.4gaboards.com 超时怎么办？

`demo.4gaboards.com` 是一个 **React SPA（单页应用）**，它的 HTML 只包含一个空的 `<div id="root">` 和一个 `defer` 加载的 JS 脚本。使用 Playwright 默认的 `wait_until="load"` 或 `wait_until="domcontentloaded"` 会导致超时，因为页面在等待所有资源加载完成。

**解决方案**（已通过 legacy 代码验证）：

1. **导航时用 `wait_until="commit"`**：只等 HTTP 响应头返回，不等资源加载
2. **手动执行 React 脚本**：检测 `<script src="main.xxx.js">` 并主动 fetch + eval
3. **强制字体加载状态**：`document.fonts.status = 'loaded'`，防止字体加载阻塞截图
4. **适当等待**：React 渲染需要时间，导航后等 2-3 秒再操作

```python
# 关键代码（参考 legacy/task2_agent/executor.py）

# 1. 用 commit 代替 load/domcontentloaded
page.goto(url, wait_until="commit", timeout=60000)

# 2. 手动执行 React 应用的 JS 脚本
script_url = page.evaluate("""
    () => {
        const scripts = Array.from(document.scripts);
        const mainScript = scripts.find(s => s.src && s.src.includes('main.'));
        return mainScript ? mainScript.src : null;
    }
""")
if script_url:
    page.evaluate(f"fetch('{script_url}').then(r=>r.text()).then(t=>eval(t))")

# 3. 等待 React 渲染 + 强制字体加载
time.sleep(2)
page.evaluate("document.fonts.status='loaded'; document.fonts.ready=Promise.resolve();")
```

如果组员 B 在实现 `execute()` 时遇到 SPA 页面白屏或超时，请参考上述方案或直接查看 `legacy/task2_agent/executor.py` 中的 `_navigate_to()` 和 `_execute_react_app()` 函数。

### Q: Playwright install 下载超时？

```bash
# 使用国内镜像
PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright playwright install chromium
```

## 项目结构

```
webagent/
├── main.py                  # CLI 入口
├── requirements.txt         # 依赖列表
├── .env                     # 环境变量（API Key）
├── core/                    # 共享基础设施
│   ├── config.py            #   配置管理 (AgentConfig)
│   ├── llm.py               #   LangChain 统一 LLM 接口
│   └── browser.py           #   共享浏览器会话 (BrowserSession)
├── agent/                   # Plan-Execute-Verify Agent 核心
│   ├── state.py             #   AgentState 定义（配置 + 数据缓存 + 调度字段）
│   ├── prompt.py            #   planner/replanner 提示词 + 可用动作清单
│   ├── planner.py           #   planner 节点（LLM + PlannedAction 结构化输出）
│   ├── executor.py          #   executor 节点（根据 action 分发到 tools 接口方法）
│   ├── replanner.py         #   replanner 节点（LLM + ReplanDecision 结构化输出）
│   └── graph.py             #   StateGraph 构建（三节点循环 + 条件路由）
├── tools/                   # 三个模块接口（组员实现）
│   ├── base.py              #   BaseTool ABC
│   ├── rag_tool.py          #   数据与RAG模块接口 + DataCache + make_rag_tools
│   ├── execution_tool.py    #   执行与交互模块接口 + make_execution_tools
│   ├── verification_tool.py #   验证与可视化模块接口 + make_verification_tools
│   ├── impl/                #   组员实现目录
│   │   └── __init__.py      #     模块注册（每人加一个工厂函数）
│   └── stub/                #   Stub 实现（用于测试框架）
├── tests/                   # 测试（118 个用例）
│   ├── conftest.py          #   共享 fixtures 与格式校验函数
│   ├── test_agent.py        #   Agent 图构建 + 提示词 + executor 分发测试
│   ├── test_rag_tool.py     #   RAG 接口测试
│   ├── test_execution_tool.py #   执行接口测试
│   ├── test_verification_tool.py #   验证接口测试
│   ├── test_rag_tools_wrapped.py  #   RAG 包装函数 + 缓存测试
│   ├── test_execution_tools_wrapped.py #   执行包装函数测试
│   ├── test_verification_tools_wrapped.py #   验证包装函数测试
│   └── test_integration.py  #   端到端集成测试
├── legacy/                  # 旧架构参考代码
├── manual/                  # 4gaboards 用户手册
├── output/                  # 运行输出
└── chroma_db/               # 向量数据库
```

## 组员开发指南

### 三人分工总览

| 组员 | 负责模块 | 接口文件 | 要实现的方法 | 创建文件 | 对应测试 |
|------|---------|---------|-------------|---------|---------|
| 组员 A | 数据与RAG | `tools/rag_tool.py` | 5 个方法 | `tools/impl/rag_impl.py` | `tests/test_rag_tool.py` (17) |
| 组员 B | 执行与交互 | `tools/execution_tool.py` | 2 个方法 | `tools/impl/execution_impl.py` | `tests/test_execution_tool.py` (16) |
| 组员 C | 验证与可视化 | `tools/verification_tool.py` | 2 个方法 | `tools/impl/verification_impl.py` | `tests/test_verification_tool.py` (11) |

**接口定义（`tools/xxx_tool.py` 中的 ABC 类）不会改动，组员只需要继承并实现抽象方法。**

### 你只需要做 3 件事（其他框架自动搞定）

```
┌─────────────────────────────────────────────────────────────────┐
│  你（组员）只需要做:                                              │
│                                                                 │
│  1. 创建 tools/impl/xxx_impl.py                                │
│     → 继承接口类，实现抽象方法，返回正确格式的数据                    │
│                                                                 │
│  2. 在 tools/impl/__init__.py 注册你的类                        │
│     → 加一个工厂函数让框架能找到你的实现                            │
│                                                                 │
│  3. 改测试文件的 import，跑测试                                   │
│     → 确认你的返回值格式正确                                       │
│                                                                 │
│  框架负责: main.py → 加载你的类 → executor 直接调用你的方法          │
│  你不需要懂 LangGraph、StateGraph，只要返回值格式对就行。           │
└─────────────────────────────────────────────────────────────────┘
```

#### 调用链全景（你的代码在哪里）

以组员 A 为例，当你完成实现后，Agent 调用你代码的完整链路是：

```
用户运行: python main.py --url https://demo.4gaboards.com/
  │
  ├─ main.py:
  │    from tools.impl import get_rag_tool        ← 加载注册函数
  │    rag_tool = get_rag_tool(config)             ← 调用你的 MyRagTool(config)
  │
  ├─ main.py:
  │    graph = build_agent_graph(rag_tool, ...)    ← 把你的实例传给框架
  │
  ├─ graph.py:
  │    构建三节点 StateGraph:
  │    executor 节点 = make_executor_node(rag_tool, ...)
  │    # executor 通过闭包持有你的 rag_tool 实例
  │
  └─ 循环执行 planner → executor → replanner:
       planner 决定: {"action": "crawl_manual", "args": {"url": "..."}}
         → executor 匹配 action == "crawl_manual"
           → 直接调用你的 rag_tool.crawl_and_load_manual(url)
           → 你返回 [{"content": "...", "source": "...", "metadata": {...}}]
           → executor 将返回值写入 state["documents"]
         → replanner 检查结果，决定继续
       planner 决定: {"action": "extract_features", "args": {...}}
         → executor 匹配 action == "extract_features"
           → 直接调用你的 rag_tool.extract_features(path)
           → executor 将返回值写入 state["features"]
         → ... 循环直到 generate_report ...
```

**关键**: 你的方法返回什么、返回格式是什么，全在下面模板里写清楚了。你只需要照着返回正确格式的 dict/list，框架会自动处理剩下的一切。

### 参考旧代码

`legacy/` 目录中保留了旧架构的实现代码，组员实现新模块时可参考其中的逻辑：

| 旧文件 | 参考内容 | 负责组员 |
|--------|---------|---------|
| `legacy/task1_rag/document_loader.py` | 手册加载与分块逻辑 | 组员 A |
| `legacy/task1_rag/vector_store.py` | ChromaDB 向量库构建 | 组员 A |
| `legacy/task1_rag/retriever.py` | RAG 检索逻辑 | 组员 A |
| `legacy/task1_rag/scenario_generator.py` | 功能点提取 + 测试用例生成 | 组员 A |
| `legacy/task2_agent/planner.py` | 测试规划（ReAct 风格） | 组员 B |
| `legacy/task2_agent/smart_executor.py` | Playwright 智能执行 | 组员 B |
| `legacy/task2_agent/smart_element_finder.py` | 动态元素查找 | 组员 B |
| `legacy/task2_agent/memory.py` | 执行记忆管理 | 组员 B |
| `legacy/task2_agent/verifier.py` | 结果验证逻辑 | 组员 C |

---

### 组员 A：数据与 RAG 模块 — 完整开发模板

#### 接口定义（不要修改，仅供参考）

你的接口定义在 `tools/rag_tool.py` 的 `RagToolInterface` 类中，包含 5 个抽象方法。executor 节点会根据 planner 的决策直接调用你的方法，你不需要关心 `AgentState`。

你需要实现的 5 个方法及调用关系：

```
planner 决定 action: "crawl_manual"
  → executor 调用你的 crawl_and_load_manual(url)
  → 你返回 documents 列表 → executor 写入 state["documents"]

planner 决定 action: "load_local_manual"
  → executor 调用你的 load_local_manual(directory)
  → 你返回 documents 列表 → executor 写入 state["documents"]

planner 决定 action: "build_knowledge_base"
  → executor 从 state 取 documents，传给你的 build_knowledge_base(documents, persist_dir)
  → 你返回向量库路径

planner 决定 action: "extract_features"
  → executor 调用你的 extract_features(vector_store_path)
  → 你返回 features 列表 → executor 写入 state["features"]

planner 决定 action: "generate_scenarios"
  → executor 从 state 取 features，传给你的 generate_scenarios(features, vector_store_path)
  → 你返回 test_cases 列表 → executor 写入 state["test_cases"]
```

#### 实现模板

创建文件 `tools/impl/rag_impl.py`：

```python
"""数据与 RAG 模块实现 —— 组员 A 在此实现所有方法。"""

from tools.rag_tool import RagToolInterface
from core.config import AgentConfig


class MyRagTool(RagToolInterface):
    """继承 RagToolInterface，实现 5 个抽象方法。"""

    def __init__(self, config: AgentConfig):
        self.config = config
        # 可以在这里初始化你需要的资源（LLM、embedding 模型等）

    # ──────────────────────────────────────────────
    # 方法 1: 爬取远程手册
    # ──────────────────────────────────────────────
    def crawl_and_load_manual(self, manual_url: str) -> list[dict]:
        """爬取指定 URL 的用户手册，返回文档列表。

        Args:
            manual_url: 手册网站 URL，如 "https://docs.4gaboards.com/"

        Returns:
            文档列表，每个元素必须包含以下字段:
            [
                {
                    "content": "文档正文内容（字符串）",
                    "source": "来源 URL 或文件路径",
                    "metadata": {"title": "页面标题", ...其他元数据}
                },
                ...
            ]

        实现建议:
            1. 使用 requests/httpx 获取页面 HTML
            2. 用 BeautifulSoup 解析，提取正文和标题
            3. 如果是多页文档（如侧边栏有链接），递归爬取子页面
            4. 将每个页面转为一个 dict
        """
        # TODO: 实现爬取逻辑
        documents = []
        # ... 你的爬取代码 ...
        return documents

    # ──────────────────────────────────────────────
    # 方法 2: 加载本地手册
    # ──────────────────────────────────────────────
    def load_local_manual(self, manual_dir: str) -> list[dict]:
        """从本地目录加载手册文档。

        Args:
            manual_dir: 手册目录路径，如 "./manual"

        Returns:
            文档列表，格式同 crawl_and_load_manual:
            [
                {
                    "content": "文档正文内容",
                    "source": "文件路径",
                    "metadata": {"title": "文档标题", ...}
                },
                ...
            ]

        实现建议:
            1. 遍历目录下的 .md / .txt / .html / .pdf 文件
            2. 读取每个文件内容
            3. 将内容切分为合适大小的文档块
            4. 每个块生成一个 dict
        """
        # TODO: 实现本地加载逻辑
        documents = []
        # ... 你的加载代码 ...
        return documents

    # ──────────────────────────────────────────────
    # 方法 3: 构建向量知识库
    # ──────────────────────────────────────────────
    def build_knowledge_base(self, documents: list[dict],
                             persist_dir: str | None = None) -> str:
        """将文档构建为向量知识库（ChromaDB）。

        Args:
            documents: 文档列表（由方法 1 或 2 返回，已通过 cache 传入）
            persist_dir: 持久化目录路径，如 "chroma_db"

        Returns:
            向量库路径（字符串），如 "chroma_db"

        实现建议:
            1. 将 documents 的 content 进行文本分块（RecursiveCharacterTextSplitter）
            2. 使用 embedding 模型将文本向量化
            3. 存入 ChromaDB 并持久化到 persist_dir
            4. 返回 persist_dir 路径

        可参考 legacy/task1_rag/vector_store.py
        """
        # TODO: 实现知识库构建逻辑
        vector_store_path = persist_dir or "chroma_db"
        # ... 你的构建代码 ...
        return vector_store_path

    # ──────────────────────────────────────────────
    # 方法 4: 提取功能点
    # ──────────────────────────────────────────────
    def extract_features(self, vector_store_path: str) -> list[dict]:
        """从知识库中提取被测网站的功能点。

        Args:
            vector_store_path: 向量库路径

        Returns:
            功能点列表，每个元素必须包含以下字段:
            [
                {
                    "feature_id": "F001",          # 唯一标识，建议 F + 三位数字
                    "feature_name": "用户登录",     # 功能名称
                    "description": "用户可以通过邮箱..."  # 功能描述
                },
                ...
            ]

        实现建议:
            1. 从 ChromaDB 加载知识库
            2. 使用 LLM 分析知识库内容，提取功能点
            3. 每个功能点分配唯一 feature_id

        可参考 legacy/task1_rag/scenario_generator.py
        """
        # TODO: 实现功能点提取逻辑
        features = []
        # ... 你的提取代码 ...
        return features

    # ──────────────────────────────────────────────
    # 方法 5: 生成测试用例
    # ──────────────────────────────────────────────
    def generate_scenarios(self, features: list[dict],
                           vector_store_path: str) -> list[dict]:
        """根据功能点生成测试用例。

        Args:
            features: 功能点列表（由方法 4 返回，已通过 cache 传入）
            vector_store_path: 向量库路径（可用于检索更多上下文）

        Returns:
            测试用例列表，每个元素必须包含以下字段:
            [
                {
                    "scenario_id": "TS_F001_001",   # 唯一标识
                    "feature_id": "F001",           # 关联的功能点 ID
                    "scenario_name": "正常登录测试",  # 用例名称
                    "steps": [                       # 操作步骤（至少 1 个）
                        "打开登录页面",
                        "输入正确的用户名和密码",
                        "点击登录按钮"
                    ],
                    "expectations": [                # 预期结果（测试预言）
                        "登录成功，跳转到首页"
                    ]
                },
                ...
            ]

        实现建议:
            1. 遍历每个 feature
            2. 使用 LLM + RAG 检索相关文档，生成多个测试用例
            3. 每个 test case 必须有 steps 和 expectations
            4. expectations 是测试预言，描述功能完成后的预期状态，
               后续验证模块会拿它与实际执行结果对比

        可参考 legacy/task1_rag/scenario_generator.py
        """
        # TODO: 实现测试用例生成逻辑
        test_cases = []
        # ... 你的生成代码 ...
        return test_cases
```

#### 验证实现

**第 1 步**：修改 `tests/test_rag_tool.py` 顶部 import

```python
# from tools.stub.rag_stub import StubRagTool as ImplToTest  # 注释掉
from tools.impl.rag_impl import MyRagTool as ImplToTest       # 改为你的实现
```

**第 2 步**：运行测试

```bash
python -m pytest tests/test_rag_tool.py -v
```

17 个用例全通过即符合接口契约。

---

### 组员 B：执行与交互模块（Playwright） — 完整开发模板

#### 接口定义（不要修改，仅供参考）

你需要实现 `ExecutionToolInterface` 的 2 个抽象方法：

```
planner 决定 action: "plan_and_execute" (合并了规划+执行)
  → executor 从 state["test_cases"] 查找测试用例
  → 调用你的 plan(test_case) → 得到执行步骤
  → 调用你的 execute(plan, target_url, memory)
  → memory 包含运行配置: {"_config": {"target_url", "output_dir", "headless"}}
  → 你返回执行结果 → executor 写入 state["execution_results"]
```

#### 实现模板

创建文件 `tools/impl/execution_impl.py`：

```python
"""执行与交互模块实现 —— 组员 B 在此实现所有方法。"""

from tools.execution_tool import ExecutionToolInterface
from core.config import AgentConfig
from core.browser import BrowserSession


class MyExecutionTool(ExecutionToolInterface):
    """继承 ExecutionToolInterface，实现 2 个抽象方法。"""

    def __init__(self, config: AgentConfig, session: BrowserSession):
        self.config = config
        self.session = session  # 共享浏览器会话，用于获取/创建 page

    # ──────────────────────────────────────────────
    # 方法 1: 规划执行步骤
    # ──────────────────────────────────────────────
    def plan(self, test_case: dict) -> list[dict]:
        """将测试用例翻译为 Playwright 可执行的步骤序列。

        Args:
            test_case: 测试用例，格式:
                {
                    "scenario_id": "TS_F001_001",
                    "scenario_name": "正常登录测试",
                    "steps": ["打开登录页面", "输入用户名", "点击登录"],
                    "expectations": ["登录成功"]
                }

        Returns:
            执行步骤列表，每个元素必须包含以下字段:
            [
                {
                    "step_id": 1,                    # 步骤序号（从 1 开始）
                    "action_type": "navigate",       # 操作类型（见下表）
                    "action_detail": "导航到登录页面",  # 操作描述
                    "target_element": "/login",      # 目标元素（CSS选择器/URL/文本）
                    "element_type": "page",          # 元素类型
                    "value": "",                     # 输入值（仅 type 操作需要）
                    "fallback_text": "登录"          # 备用定位文本
                },
                ...
            ]

            action_type 可选值:
            ┌──────────┬─────────────────────────────┐
            │ 值       │ 说明                        │
            ├──────────┼─────────────────────────────┤
            │ navigate │ 导航到 URL（page.goto）      │
            │ click    │ 点击元素（page.click）       │
            │ type     │ 输入文本（page.fill）        │
            │ select   │ 选择下拉项（page.select）    │
            │ wait     │ 等待元素/时间（page.wait）   │
            │ screenshot │ 截图（page.screenshot）   │
            └──────────┴─────────────────────────────┘

        实现建议:
            1. 使用 LLM 将 test_case["steps"]（自然语言）翻译为 Playwright 操作指令
            2. 第一步通常是 navigate 到目标页面
            3. 最后一步建议加 screenshot 记录结果
            4. target_element 尽量提供 CSS 选择器，同时提供 fallback_text

        可参考 legacy/task2_agent/planner.py
        """
        # TODO: 实现规划逻辑
        plan = []
        # ... 你的规划代码 ...
        return plan

    # ──────────────────────────────────────────────
    # 方法 2: 执行测试计划（Playwright 驱动浏览器）
    # ──────────────────────────────────────────────
    def execute(self, plan: list[dict], target_url: str,
                memory: dict | None = None) -> dict:
        """通过 Playwright 驱动浏览器执行测试计划。

        Args:
            plan: 执行步骤列表（由 plan() 返回）
            target_url: 目标网站 URL
            memory: 执行上下文（可能包含上一次执行的轨迹），
                    运行配置在 memory["_config"] 中:
                    {
                        "target_url": "https://...",
                        "output_dir": "output",
                        "headless": False
                    }

        Returns:
            {
                "results": [
                    {
                        "step_id": 1,
                        "action_type": "navigate",
                        "action_detail": "导航到登录页面",
                        "result": "成功导航到 https://...",
                        "success": true,
                        "screenshot_path": "output/step_1.png"
                    },
                    ...
                ],
                "memory": {
                    "page_state": "...",    # 当前页面状态（供下次执行参考）
                    "cookies": [...],       # 登录后的 cookies
                    ...其他你想保留的上下文
                },
                "screenshots": ["output/step_1.png", ...]
            }

        实现建议:
            1. 从 memory["_config"] 读取运行配置:
               config = (memory or {}).get("_config", {})
               headless = config.get("headless", False)
               output_dir = config.get("output_dir", "output")

            2. 通过共享浏览器会话获取 page（懒启动，首次调用时才打开浏览器）:
               page = self.session.ensure_page(headless=headless)
               # 注意：不要关闭浏览器，验证模块会继续使用同一个 page

            3. 按 plan 逐步执行，根据 action_type 调用对应 Playwright API:
               - navigate → page.goto(target + path)
               - click → page.click(selector) 或 page.get_by_text(text).click()
               - type → page.fill(selector, value)
               - select → page.select_option(selector, value)
               - wait → page.wait_for_selector(selector) 或 page.wait_for_timeout(ms)
               - screenshot → page.screenshot(path=...)

            4. 每步执行后截图，记录成功/失败
            5. 如果元素找不到，尝试用 fallback_text 定位
            6. 返回结果（不要关闭浏览器，验证模块会继续使用同一个 page）

        可参考 legacy/task2_agent/smart_executor.py 和 smart_element_finder.py
        """
        # 读取运行配置
        config = (memory or {}).get("_config", {})
        headless = config.get("headless", False)
        output_dir = config.get("output_dir", "output")

        # 确保 output 目录存在
        import os
        os.makedirs(output_dir, exist_ok=True)

        results = []
        screenshots = []

        # 通过共享会话获取 page（不要自己创建/销毁浏览器）
        page = self.session.ensure_page(headless=headless)

        # TODO: 按 plan 逐步执行
        # for step in plan:
        #     ... 执行每个步骤 ...

        return {
            "results": results,
            "memory": {},         # 可保留页面状态供重试时使用
            "screenshots": screenshots,
        }
```

#### 验证实现

**第 1 步**：修改 `tests/test_execution_tool.py` 顶部 import

```python
# from tools.stub.execution_stub import StubExecutionTool as ImplToTest  # 注释掉
from tools.impl.execution_impl import MyExecutionTool as ImplToTest      # 改为你的实现
```

**第 2 步**：运行测试

```bash
python -m pytest tests/test_execution_tool.py -v
```

16 个用例全通过即符合接口契约。

---

### 组员 C：验证与可视化模块 — 完整开发模板

#### 接口定义（不要修改，仅供参考）

你需要实现 `VerificationToolInterface` 的 2 个抽象方法：

```
planner 决定 action: "verify_results"
  → executor 从 state 取 test_case（含 expectations）和 execution_results
  → 调用你的 verify(test_case, execution_results, execution_memory)
  → 你返回验证结果 → executor 写入 state["verification_results"]

planner 决定 action: "generate_report"
  → executor 从 state 取全部数据组装成 report_state
  → 调用你的 visualize(report_state)
  → 你返回报告路径 → executor 写入 state["response"] → 触发 END
```

#### 实现模板

创建文件 `tools/impl/verification_impl.py`：

```python
"""验证与可视化模块实现 —— 组员 C 在此实现所有方法。"""

from tools.verification_tool import VerificationToolInterface
from core.config import AgentConfig
from core.browser import BrowserSession


class MyVerificationTool(VerificationToolInterface):
    """继承 VerificationToolInterface，实现 2 个抽象方法。"""

    def __init__(self, config: AgentConfig, session: BrowserSession):
        self.config = config
        self.session = session  # 共享浏览器会话，可访问执行模块操作过的 page

    # ──────────────────────────────────────────────
    # 方法 1: 验证测试结果
    # ──────────────────────────────────────────────
    def verify(self, test_case: dict, execution_results: list[dict],
               execution_memory: dict) -> dict:
        """将预期（expectations）与实际执行结果对比，判断测试是否通过。

        Args:
            test_case: 测试用例，包含:
                {
                    "scenario_id": "TS_F001_001",
                    "steps": ["打开登录页面", ...],
                    "expectations": ["登录成功，跳转到首页"]  ← 预期状态（测试预言）
                }
            execution_results: 执行结果列表:
                [
                    {"step_id": 1, "success": True, "result": "...", ...},
                    {"step_id": 2, "success": True, "result": "...", ...},
                ]
            execution_memory: 执行上下文（截图路径、页面状态等）

        Returns:
            {
                "passed": True/False,          # 是否通过（必须是 bool）
                "reason": "成功 3/3 步",       # 原因描述（必须是 str）
                "details": {                   # 详细信息（可选）
                    "success_count": 3,
                    "total": 3,
                    "failed_steps": [],
                    "expectation_check": "登录成功，跳转到首页 → 匹配"
                }
            }

        实现建议:
            1. 基本检查: 所有步骤是否都 success=True
            2. 进阶检查: 使用 LLM 将 expectations（预期状态）与实际执行轨迹对比
               - 分析 execution_results 中每步的 result 描述
               - 分析 execution_memory 中的截图（如有）
               - 判断是否满足 expectations 描述的预期状态
            3. 页面实时验证: 通过共享的 page 检查当前页面状态
               page = self.session.page
               if page and not page.is_closed():
                   current_url = page.url
                   body_text = page.locator("body").inner_text()
                   # 可以检查元素是否存在、文本是否匹配等
            4. 返回 passed=True/False 并给出 reason

        可参考 legacy/task2_agent/verifier.py
        """
        # TODO: 实现验证逻辑
        total = len(execution_results)
        success_count = sum(1 for r in execution_results if r.get("success"))

        passed = (success_count == total) and (total > 0)
        reason = f"成功 {success_count}/{total} 步"

        # 进阶: 对比 expectations 和实际结果
        # expectations = test_case.get("expectations", [])
        # ... LLM 对比逻辑 ...

        return {
            "passed": passed,
            "reason": reason,
            "details": {"success_count": success_count, "total": total},
        }

    # ──────────────────────────────────────────────
    # 方法 2: 生成可视化报告
    # ──────────────────────────────────────────────
    def visualize(self, state: dict) -> str:
        """根据全部数据生成可视化报告。

        Args:
            state: 包含所有测试数据的字典:
                {
                    "test_cases": [
                        {"scenario_id": "TS001", "scenario_name": "...", ...},
                        ...
                    ],
                    "execution_results": {
                        "TS001": [{"step_id": 1, "success": True, ...}, ...],
                        ...
                    },
                    "verification_results": {
                        "TS001": {"passed": True, "reason": "..."},
                        ...
                    },
                    "execution_memory": {...}
                }

        Returns:
            报告文件路径（字符串），如 "output/report.html" 或 "output/report.json"

        实现建议:
            1. 从 state 中提取所有数据
            2. 统计通过率、失败用例等
            3. 生成 HTML/JSON 格式的报告
            4. 保存到 output/ 目录
            5. 返回报告文件路径
        """
        # TODO: 实现报告生成逻辑
        import os
        output_dir = "output"
        os.makedirs(output_dir, exist_ok=True)

        report_path = os.path.join(output_dir, "report.html")
        # ... 你的报告生成代码 ...

        return report_path
```

#### 验证实现

**第 1 步**：修改 `tests/test_verification_tool.py` 顶部 import

```python
# from tools.stub.verification_stub import StubVerificationTool as ImplToTest  # 注释掉
from tools.impl.verification_impl import MyVerificationTool as ImplToTest      # 改为你的实现
```

**第 2 步**：运行测试

```bash
python -m pytest tests/test_verification_tool.py -v
```

11 个用例全通过即符合接口契约。

---

### 注册模块（每个组员完成实现后都要做）

你需要把你的类注册到 `tools/impl/__init__.py`，框架才能找到你的实现。
**每个组员各自往这个文件里加一个函数**，不要动别人的。

#### 组员 A：注册 RAG 模块

你创建的文件：`tools/impl/rag_impl.py`
你的类名：`MyRagTool`
你要注册的函数：`get_rag_tool`

在 `tools/impl/__init__.py` 中添加：

```python
def get_rag_tool(config):
    """组员 A 注册处 —— 导入你的类，创建实例返回。"""
    from tools.impl.rag_impl import MyRagTool     # ← 改成你的类名
    return MyRagTool(config)
```

#### 组员 B：注册执行模块

你创建的文件：`tools/impl/execution_impl.py`
你的类名：`MyExecutionTool`
你要注册的函数：`get_execution_tool`

在 `tools/impl/__init__.py` 中添加：

```python
def get_execution_tool(config, session):
    """组员 B 注册处 —— 导入你的类，创建实例返回。"""
    from tools.impl.execution_impl import MyExecutionTool     # ← 改成你的类名
    return MyExecutionTool(config, session)
```

#### 组员 C：注册验证模块

你创建的文件：`tools/impl/verification_impl.py`
你的类名：`MyVerificationTool`
你要注册的函数：`get_verification_tool`

在 `tools/impl/__init__.py` 中添加：

```python
def get_verification_tool(config, session):
    """组员 C 注册处 —— 导入你的类，创建实例返回。"""
    from tools.impl.verification_impl import MyVerificationTool     # ← 改成你的类名
    return MyVerificationTool(config, session)
```

#### 完整的 `tools/impl/__init__.py`（三人写完后长这样）

```python
"""模块注册 —— main.py 通过这个文件加载组员的实现。"""


def get_rag_tool(config):
    from tools.impl.rag_impl import MyRagTool
    return MyRagTool(config)


def get_execution_tool(config, session):
    from tools.impl.execution_impl import MyExecutionTool
    return MyExecutionTool(config, session)


def get_verification_tool(config, session):
    from tools.impl.verification_impl import MyVerificationTool
    return MyVerificationTool(config, session)
```

#### 注册汇总表

| 组员 | 你创建的文件 | 你的类名 | 你要注册的函数 | 继承的接口 |
|------|------------|---------|--------------|-----------|
| 组员 A | `tools/impl/rag_impl.py` | `MyRagTool` | `get_rag_tool(config)` | `RagToolInterface` |
| 组员 B | `tools/impl/execution_impl.py` | `MyExecutionTool` | `get_execution_tool(config, session)` | `ExecutionToolInterface` |
| 组员 C | `tools/impl/verification_impl.py` | `MyVerificationTool` | `get_verification_tool(config, session)` | `VerificationToolInterface` |

**注意事项**：
- 类名可以自定义（不一定要叫 `MyXxxTool`），但注册函数名必须固定（`get_rag_tool` 等），因为 `main.py` 按这个名字调用
- 每个组员只加自己的注册函数，不要动别人的
- 注册函数里用 `from tools.impl.xxx_impl import ...` 延迟导入，避免循环依赖

注册完成后，去掉 `--stub` 即可使用真实模块：

```bash
python main.py --url https://demo.4gaboards.com/
```

### 数据格式约定

#### 文档格式（组员 A 返回）
```json
{
    "content": "文档正文内容",
    "source": "https://docs.example.com/getting-started",
    "metadata": {"title": "快速开始", "type": "page"}
}
```

#### 功能点格式（组员 A 返回）
```json
{
    "feature_id": "F001",
    "feature_name": "用户登录",
    "description": "用户可以通过邮箱和密码登录系统"
}
```

#### 测试用例格式（组员 A 返回）
```json
{
    "scenario_id": "TS_F001_001",
    "feature_id": "F001",
    "scenario_name": "正常登录测试",
    "steps": ["打开登录页面", "输入邮箱和密码", "点击登录按钮"],
    "expectations": ["登录成功，跳转到首页"]
}
```

#### 执行步骤格式（组员 B 的 plan 返回）
```json
{
    "step_id": 1,
    "action_type": "click",
    "action_detail": "点击登录按钮",
    "target_element": "#login-btn",
    "element_type": "button",
    "value": "",
    "fallback_text": "登录"
}
```

`action_type` 对应 Playwright 操作：`navigate`（导航）、`click`（点击）、`type`（输入）、`select`（选择）、`wait`（等待）、`screenshot`（截图）。

#### 执行结果格式（组员 B 的 execute 返回）
```json
{
    "results": [
        {
            "step_id": 1,
            "action_type": "navigate",
            "action_detail": "导航到登录页面",
            "result": "成功导航到 https://example.com/login",
            "success": true,
            "screenshot_path": "output/TS_F001_001_step_1.png"
        }
    ],
    "memory": {},
    "screenshots": ["output/TS_F001_001_step_1.png"]
}
```

#### 验证结果格式（组员 C 的 verify 返回）
```json
{
    "passed": true,
    "reason": "成功 3/3 步，预期全部匹配",
    "details": {"success_count": 3, "total": 3}
}
```

## 技术栈

| 组件 | 技术 |
|------|------|
| Agent 框架 | LangGraph StateGraph (Plan-Execute-Verify 循环) |
| LLM 接口 | LangChain ChatOpenAI (支持 GLM/DeepSeek/Qwen) |
| 结构化输出 | Pydantic (PlannedAction / ReplanDecision) |
| 向量数据库 | ChromaDB |
| 浏览器自动化 | Playwright |
| 可视化 | Streamlit |
| 文档解析 | BeautifulSoup, LangChain |
| 测试框架 | pytest (118 个用例) |

## 测试指南

### 快速运行

```bash
# 运行全部测试（118 个用例）
python -m pytest tests/ -v

# 只运行接口测试（组员验证实现）
python -m pytest tests/test_rag_tool.py -v
python -m pytest tests/test_execution_tool.py -v
python -m pytest tests/test_verification_tool.py -v

# 只运行包装函数测试（验证缓存机制）
python -m pytest tests/test_rag_tools_wrapped.py -v
python -m pytest tests/test_execution_tools_wrapped.py -v
python -m pytest tests/test_verification_tools_wrapped.py -v

# 运行 Agent 和集成测试
python -m pytest tests/test_agent.py -v
python -m pytest tests/test_integration.py -v
```

### 测试分类

| 测试文件 | 测试数 | 说明 |
|----------|--------|------|
| `test_rag_tool.py` | 17 | RAG 接口契约测试（返回值格式、字段完整性） |
| `test_execution_tool.py` | 16 | 执行接口契约测试 |
| `test_verification_tool.py` | 11 | 验证接口契约测试 |
| `test_rag_tools_wrapped.py` | 14 | RAG 包装函数：摘要返回、缓存写入、缺数据警告 |
| `test_execution_tools_wrapped.py` | 11 | 执行包装函数：scenario_id 查找、target_url 注入 |
| `test_verification_tools_wrapped.py` | 8 | 验证包装函数：expectations 对比、报告生成 |
| `test_agent.py` | 28 | 提示词生成、图构建、executor 分发（8 个 action）、错误处理 |
| `test_integration.py` | 5 | 端到端全链路集成测试 |

### 组员如何使用测试

#### 第 1 步：写好实现代码

例如你负责 RAG 模块，创建了 `tools/impl/rag_impl.py`：

```python
from tools.rag_tool import RagToolInterface

class MyRagTool(RagToolInterface):
    def crawl_and_load_manual(self, manual_url: str) -> list[dict]:
        # 你的实现
        ...
```

#### 第 2 步：修改测试文件中的 import

打开 `tests/test_rag_tool.py`，修改顶部的 import：

```python
# 注释掉 stub
# from tools.stub.rag_stub import StubRagTool as ImplToTest

# 改为你的实现
from tools.impl.rag_impl import MyRagTool as ImplToTest
```

同理：
- 执行模块 → 修改 `tests/test_execution_tool.py`
- 验证模块 → 修改 `tests/test_verification_tool.py`

#### 第 3 步：运行测试

```bash
python -m pytest tests/test_rag_tool.py -v
```

如果全部通过，说明你的实现符合接口契约。如果某个用例失败，错误信息会告诉你哪个字段缺失或类型不对。

### 校验函数

`conftest.py` 提供了一组校验函数，组员也可以在自己的测试中使用：

| 函数 | 校验内容 |
|------|----------|
| `assert_document_format(doc)` | 文档必须有 `content`, `source`, `metadata` |
| `assert_feature_format(feature)` | 功能点必须有 `feature_id`, `feature_name`, `description` |
| `assert_test_case_format(tc)` | 测试用例必须有 `scenario_id`, `steps`, `expectations` 等 |
| `assert_execution_step_format(step)` | 执行步骤必须有 `step_id`, `action_type`（仅限合法值）等 |
| `assert_step_result_format(result)` | 执行结果必须有 `step_id`, `success`（bool）等 |
| `assert_verification_format(v)` | 验证结果必须有 `passed`（bool）, `reason`（str） |
