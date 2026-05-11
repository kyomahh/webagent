# WebAgent - 基于大模型的测试场景生成与智能测试工具

基于 LangGraph 架构的智能 Web 测试框架。自动从用户手册提取功能点、生成测试用例，并通过 Playwright 驱动浏览器自动执行测试。

## 架构

```
START → classify_input (分析输入)
           ├─(需爬取)─→ crawl_manual → build_rag
           └─(已有)──────────────────→ build_rag
                                       │
                                       ▼
                              generate_test_cases
                                       │
                                       ▼
                                  plan_execution ◀──┐
                                       │            │
                                       ▼            │
                                  execute_tests      │
                                       │            │
                                       ▼            │
                                 verify_results ─────┘ (重试)
                                    │
                                    ▼
                             visualize_report → END
```

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
```

### 2. 配置 API Key

编辑 `.env` 文件：

```
ZHIPUAI_API_KEY=your_zhipuai_api_key_here
```

### 3. 运行

```bash
# 使用 stub 模式测试框架（不需要 API Key）
python main.py --stub --manual-dir ./manual

# 完整模式：爬取手册 → 生成用例 → 执行测试 → 验证
python main.py --url https://demo.4gaboards.com/ --manual https://docs.4gaboards.com/

# 使用本地手册
python main.py --url https://demo.4gaboards.com/ --manual-dir ./manual

# 指定模型
python main.py --stub --model deepseek-chat

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
├── agent/                   # LangGraph Agent 核心
│   ├── state.py             #   AgentState 定义
│   ├── nodes.py             #   Graph 节点函数
│   └── graph.py             #   StateGraph 编排
├── tools/                   # 三个模块接口
│   ├── base.py              #   BaseTool ABC
│   ├── rag_tool.py          #   数据与RAG模块接口 (RagToolInterface)
│   ├── execution_tool.py    #   执行与交互模块接口 (ExecutionToolInterface)
│   ├── verification_tool.py #   验证与可视化模块接口 (VerificationToolInterface)
│   └── stub/                #   Stub 实现（用于测试框架）
├── legacy/                  # 旧架构参考代码（task1_rag, task2_agent, common_utils）
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
| `legacy/task2_agent/smart_element_finder.py` | 动态元素查找（文本→类型→LLM） |
| `legacy/task2_agent/verifier.py` | 结果验证逻辑 |
| `legacy/task2_agent/memory.py` | 执行记忆管理 |

### 实现模块接口

每个模块都有对应的 ABC 接口，组员需要继承并实现所有抽象方法：

#### 1. 数据与RAG模块 → `tools/rag_tool.py`

```python
from tools.rag_tool import RagToolInterface

class MyRagTool(RagToolInterface):
    def crawl_and_load_manual(self, manual_url: str) -> list[dict]:
        # 实现爬取逻辑
        ...

    def load_local_manual(self, manual_dir: str) -> list[dict]:
        # 实现本地加载逻辑
        ...

    def build_knowledge_base(self, documents, persist_dir=None) -> str:
        # 实现向量库构建
        ...

    def extract_features(self, vector_store_path: str) -> list[dict]:
        # 实现功能点提取
        ...

    def generate_scenarios(self, features, vector_store_path: str) -> list[dict]:
        # 实现测试用例生成
        ...
```

#### 2. 执行与交互模块 → `tools/execution_tool.py`

```python
from tools.execution_tool import ExecutionToolInterface

class MyExecutionTool(ExecutionToolInterface):
    def plan(self, test_case: dict) -> list[dict]:
        # 实现测试规划
        ...

    def execute(self, plan: list[dict], target_url: str, memory=None) -> dict:
        # 实现 Playwright 执行
        ...
```

#### 3. 验证与可视化模块 → `tools/verification_tool.py`

```python
from tools.verification_tool import VerificationToolInterface

class MyVerificationTool(VerificationToolInterface):
    def verify(self, test_case: dict, execution_results: list[dict],
               execution_memory: dict) -> dict:
        # 实现结果验证
        ...

    def visualize(self, state: dict) -> str:
        # 实现可视化报告
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

#### 执行步骤格式
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
| Agent 框架 | LangGraph (StateGraph) |
| LLM 接口 | LangChain ChatOpenAI (支持 GLM/DeepSeek/Qwen) |
| 向量数据库 | ChromaDB |
| 浏览器自动化 | Playwright |
| 可视化 | Streamlit |
| 文档解析 | BeautifulSoup, LangChain |
