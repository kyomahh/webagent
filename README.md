# WebAgent - 基于大模型的测试场景生成与智能测试工具

基于 LangGraph ReAct Agent 架构的智能 Web 测试框架。LLM 自主判断目标网站是否存在用户手册并下载，自动提取功能点、生成测试用例，并通过 Playwright 驱动浏览器自动执行测试、验证结果。

## 架构

```
LLM (ReAct Agent)
  │
  │  自主决策调用以下 9 个工具：
  │
  ├─ crawl_manual(url)              ─┐
  ├─ load_local_manual(directory)    │ RAG 工具组
  ├─ build_knowledge_base(dir)       │
  ├─ extract_features(vs_path)       │
  ├─ generate_scenarios(vs_path)    ─┘
  │
  ├─ plan_execution(scenario_id)    ─┐ 执行工具组
  ├─ execute_plan(scenario_id)      ─┘
  │
  ├─ verify_results(scenario_id)    ─┐ 验证工具组
  └─ generate_report()              ─┘
```

LLM 通过系统提示词引导，按推荐工作流依次调用工具。工具间通过 `DataCache` 共享数据，LLM 只看到摘要字符串。

### 工作流

1. **自主判断并获取手册** — LLM 根据目标网站推测手册地址（如 docs 子域、/docs、/help 等路径），用 `crawl_manual` 尝试爬取；如果提供了 `--manual` 或 `--manual-dir` 则直接使用
2. **构建知识库** — `build_knowledge_base` 构建向量知识库
3. **提取功能点** — `extract_features` 从知识库提取功能点
4. **生成测试用例** — `generate_scenarios` 根据功能点生成测试用例（含 expectations 预期）
5. **规划并执行** — 对每个用例调用 `plan_execution` → `execute_plan`（通过 Playwright 驱动浏览器自动执行）
6. **验证结果** — `verify_results` 将 expectations（预期）与实际执行结果对比，判断测试是否通过
7. **失败重试** — 失败用例可重新规划执行（最多 max_retries 次）
8. **生成报告** — `generate_report` 汇总所有数据生成报告

### 三个核心模块

| 模块 | 接口文件 | 职责 |
|------|---------|------|
| 数据与RAG模块 | `tools/rag_tool.py` | 爬取手册、构建知识库、提取功能点、生成测试用例 |
| 执行与交互模块 | `tools/execution_tool.py` | 规划执行步骤、基于Playwright执行测试 |
| 验证与可视化模块 | `tools/verification_tool.py` | 验证测试结果、生成可视化报告 |

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

# 使用 stub 模式测试框架（需要 API Key，工具不调真实 LLM/浏览器）
python main.py --stub --url https://demo.4gaboards.com/

# 指定模型
python main.py --url https://demo.4gaboards.com/ --model deepseek-chat

# 查看帮助
python main.py --help
```

## 项目结构

```
webagent/
├── main.py                  # CLI 入口
├── requirements.txt         # 依赖列表
├── .env                     # 环境变量（API Key）
├── core/                    # 共享基础设施
│   ├── config.py            #   配置管理 (AgentConfig)
│   └── llm.py               #   LangChain 统一 LLM 接口
├── agent/                   # ReAct Agent 核心
│   ├── state.py             #   AgentState 定义（配置 + messages）
│   ├── prompt.py            #   系统提示词（引导 LLM 工作流）
│   └── graph.py             #   create_react_agent 构建
├── tools/                   # 三个模块接口
│   ├── base.py              #   BaseTool ABC
│   ├── rag_tool.py          #   数据与RAG模块接口 + DataCache + make_rag_tools
│   ├── execution_tool.py    #   执行与交互模块接口 + make_execution_tools
│   ├── verification_tool.py #   验证与可视化模块接口 + make_verification_tools
│   └── stub/                #   Stub 实现（用于测试框架）
├── tests/                   # 测试（102 个用例）
│   ├── conftest.py          #   共享 fixtures 与格式校验函数
│   ├── test_rag_tool.py     #   RAG 接口测试
│   ├── test_execution_tool.py #   执行接口测试
│   ├── test_verification_tool.py #   验证接口测试
│   ├── test_rag_tools_wrapped.py  #   RAG 包装函数 + 缓存测试
│   ├── test_execution_tools_wrapped.py #   执行包装函数测试
│   ├── test_verification_tools_wrapped.py #   验证包装函数测试
│   ├── test_agent.py        #   Agent 构建 + 提示词测试
│   └── test_integration.py  #   端到端集成测试
├── legacy/                  # 旧架构参考代码
├── manual/                  # 4gaboards 用户手册
├── output/                  # 运行输出
└── chroma_db/               # 向量数据库
```

## 组员开发指南

### 参考旧代码

`legacy/` 目录中保留了旧架构的实现代码，组员实现新模块时可参考其中的逻辑：

| 旧文件 | 参考内容 |
|--------|---------|
| `legacy/task1_rag/document_loader.py` | 手册加载与分块逻辑 |
| `legacy/task1_rag/vector_store.py` | ChromaDB 向量库构建 |
| `legacy/task1_rag/retriever.py` | RAG 检索逻辑 |
| `legacy/task1_rag/scenario_generator.py` | 功能点提取 + 测试用例生成 |
| `legacy/task2_agent/planner.py` | 测试规划（ReAct 风格） |
| `legacy/task2_agent/smart_executor.py` | Playwright 智能执行 |
| `legacy/task2_agent/smart_element_finder.py` | 动态元素查找 |
| `legacy/task2_agent/verifier.py` | 结果验证逻辑 |
| `legacy/task2_agent/memory.py` | 执行记忆管理 |

### 实现模块接口

每个模块都有对应的 ABC 接口，组员需要继承并实现所有抽象方法。接口定义**不会改动**。

#### 1. 数据与RAG模块 → `tools/rag_tool.py`

```python
from tools.rag_tool import RagToolInterface

class MyRagTool(RagToolInterface):
    def crawl_and_load_manual(self, manual_url: str) -> list[dict]:
        ...
    def load_local_manual(self, manual_dir: str) -> list[dict]:
        ...
    def build_knowledge_base(self, documents, persist_dir=None) -> str:
        ...
    def extract_features(self, vector_store_path: str) -> list[dict]:
        ...
    def generate_scenarios(self, features, vector_store_path: str) -> list[dict]:
        ...
```

#### 2. 执行与交互模块 → `tools/execution_tool.py`

负责通过 Playwright 驱动浏览器自动执行测试（点击、输入、导航、截图等）。

```python
from tools.execution_tool import ExecutionToolInterface

class MyExecutionTool(ExecutionToolInterface):
    def plan(self, test_case: dict) -> list[dict]:
        # 将测试用例规划为 Playwright 可执行的步骤
        # action_type: navigate / click / type / select / wait / screenshot
        ...

    def execute(self, plan: list[dict], target_url: str, memory=None) -> dict:
        # 通过 Playwright 驱动浏览器执行测试计划
        # 运行配置从 memory["_config"] 中获取：
        #   - memory["_config"]["headless"]  → 是否无头模式
        #   - memory["_config"]["output_dir"] → 截图保存目录
        #   - memory["_config"]["target_url"] → 目标网站
        ...
```

**Playwright 配置获取方式**：框架会在调用 `execute` 时自动将 `headless`、`output_dir` 等配置注入 `memory["_config"]`，组员实现中直接读取即可：

```python
def execute(self, plan, target_url, memory=None):
    config = (memory or {}).get("_config", {})
    headless = config.get("headless", False)
    output_dir = config.get("output_dir", "output")

    # 启动 Playwright 浏览器
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        # 执行 plan 中的每个步骤...
```

#### 3. 验证与可视化模块 → `tools/verification_tool.py`

```python
from tools.verification_tool import VerificationToolInterface

class MyVerificationTool(VerificationToolInterface):
    def verify(self, test_case: dict, execution_results: list[dict],
               execution_memory: dict) -> dict:
        ...
    def visualize(self, state: dict) -> str:
        ...
```

### 注册模块

在 `tools/impl/__init__.py` 中创建工厂函数：

```python
from core.config import AgentConfig

def get_rag_tool(config: AgentConfig):
    from tools.impl.rag_impl import MyRagTool
    return MyRagTool(config)

def get_execution_tool(config: AgentConfig):
    from tools.impl.execution_impl import MyExecutionTool
    return MyExecutionTool(config)

def get_verification_tool(config: AgentConfig):
    from tools.impl.verification_impl import MyVerificationTool
    return MyVerificationTool(config)
```

### 数据格式约定

#### 测试用例格式
```json
{
    "scenario_id": "TS001",
    "feature_id": "F001",
    "scenario_name": "用户注册测试",
    "steps": ["打开注册页面", "输入邮箱", "点击注册按钮"],
    "expectations": ["注册成功，显示欢迎页面"]
}
```

#### 执行步骤格式（Playwright 操作指令）
```json
{
    "step_id": 1,
    "action_type": "click",
    "action_detail": "点击注册按钮",
    "target_element": "注册按钮",
    "element_type": "button",
    "value": "",
    "fallback_text": "注册"
}
```

`action_type` 对应 Playwright 操作：`navigate`（导航）、`click`（点击）、`type`（输入）、`select`（选择）、`wait`（等待）、`screenshot`（截图）。

#### 验证结果格式
```json
{
    "passed": true,
    "reason": "成功 3/3 步",
    "details": {"success_count": 3, "total": 3}
}
```

## 技术栈

| 组件 | 技术 |
|------|------|
| Agent 框架 | LangGraph (create_react_agent) |
| LLM 接口 | LangChain ChatOpenAI (支持 GLM/DeepSeek/Qwen) |
| 向量数据库 | ChromaDB |
| 浏览器自动化 | Playwright |
| 可视化 | Streamlit |
| 文档解析 | BeautifulSoup, LangChain |
| 测试框架 | pytest (102 个用例) |

## 测试指南

### 快速运行

```bash
# 运行全部测试（102 个用例）
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
| `test_agent.py` | 12 | 提示词生成、Agent 构建 |
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
