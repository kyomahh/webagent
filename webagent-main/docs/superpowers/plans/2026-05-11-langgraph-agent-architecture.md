# LangGraph Agent Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the LangGraph agent framework with StateGraph, ABC interfaces, and @tool wrappers — leaving three module implementations for team members.

**Architecture:** Single Agent + Multi-Tool using LangGraph StateGraph with conditional routing and retry loop. Three modules (RAG, Execution, Verification) defined as ABC interfaces and exposed as @tool functions.

**Tech Stack:** LangGraph, LangChain (ChatOpenAI), Playwright, ChromaDB, Streamlit

---

## File Map

| File | Responsibility |
|------|----------------|
| `core/__init__.py` | Package init |
| `core/config.py` | AgentConfig dataclass, path constants |
| `core/llm.py` | LangChain ChatOpenAI unified LLM factory |
| `agent/__init__.py` | Package init, exports build_agent_graph |
| `agent/state.py` | AgentState TypedDict, TestCase, ExecutionStep, StepResult |
| `agent/nodes.py` | 8 graph node functions + 2 routing functions |
| `agent/graph.py` | StateGraph construction, edge wiring, compile |
| `tools/__init__.py` | Package init |
| `tools/base.py` | BaseTool ABC |
| `tools/rag_tool.py` | RagToolInterface ABC + @tool wrapper factory |
| `tools/execution_tool.py` | ExecutionToolInterface ABC + @tool wrapper factory |
| `tools/verification_tool.py` | VerificationToolInterface ABC + @tool wrapper factory |
| `tools/stub/` | Stub implementations for testing the graph without real modules |
| `main.py` | New CLI entry point using argparse |

---

### Task 1: Core Infrastructure

**Files:**
- Create: `core/__init__.py`
- Create: `core/config.py`
- Create: `core/llm.py`

- [ ] **Step 1: Create core/__init__.py**

```python
from core.config import AgentConfig
from core.llm import get_llm
```

- [ ] **Step 2: Create core/config.py**

```python
import os
from dataclasses import dataclass, field

@dataclass
class AgentConfig:
    target_url: str = "https://demo.4gaboards.com/"
    manual_url: str = "https://docs.4gaboards.com/"
    model_name: str = "glm-4-flash"
    embedding_model: str = "embedding-3"
    chroma_dir: str = "chroma_db"
    output_dir: str = "output"
    max_retries: int = 2
    headless: bool = False

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def default_config() -> AgentConfig:
    return AgentConfig(
        chroma_dir=os.path.join(ROOT_DIR, "chroma_db"),
        output_dir=os.path.join(ROOT_DIR, "output"),
    )
```

- [ ] **Step 3: Create core/llm.py**

```python
import os
from langchain_openai import ChatOpenAI

_MODEL_CONFIGS = {
    "glm-4-flash": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPUAI_API_KEY",
    },
    "glm-4-plus": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPUAI_API_KEY",
    },
    "deepseek-chat": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "qwen-plus": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
    },
}

def get_llm(model_name: str = "glm-4-flash", temperature: float = 0.1) -> ChatOpenAI:
    config = _MODEL_CONFIGS.get(model_name, _MODEL_CONFIGS["glm-4-flash"])
    return ChatOpenAI(
        model=model_name,
        base_url=config["base_url"],
        api_key=os.environ.get(config["api_key_env"], ""),
        temperature=temperature,
    )
```

- [ ] **Step 4: Verify imports work**

Run: `source venv/bin/activate && python -c "from core.config import AgentConfig, default_config; c = default_config(); print(c)"`
Expected: Print AgentConfig with default values

- [ ] **Step 5: Commit**

```bash
git add core/__init__.py core/config.py core/llm.py
git commit -m "feat: add core infrastructure (config, unified LLM interface)"
```

---

### Task 2: AgentState Definition

**Files:**
- Create: `agent/__init__.py`
- Create: `agent/state.py`

- [ ] **Step 1: Create agent/__init__.py**

```python
from agent.graph import build_agent_graph
```

- [ ] **Step 2: Create agent/state.py**

Full TypedDict with all fields per spec. Includes TestCase, ExecutionStep, StepResult sub-types and AgentState.

- [ ] **Step 3: Verify imports**

Run: `source venv/bin/activate && python -c "from agent.state import AgentState; print('OK')"`
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add agent/__init__.py agent/state.py
git commit -m "feat: add AgentState TypedDict definition"
```

---

### Task 3: Tool Interfaces (ABC + @tool wrappers)

**Files:**
- Create: `tools/__init__.py`
- Create: `tools/base.py`
- Create: `tools/rag_tool.py`
- Create: `tools/execution_tool.py`
- Create: `tools/verification_tool.py`

- [ ] **Step 1: Create tools/__init__.py** — exports all interfaces

- [ ] **Step 2: Create tools/base.py** — BaseTool ABC

- [ ] **Step 3: Create tools/rag_tool.py** — RagToolInterface + make_rag_tools()

- [ ] **Step 4: Create tools/execution_tool.py** — ExecutionToolInterface + make_execution_tools()

- [ ] **Step 5: Create tools/verification_tool.py** — VerificationToolInterface + make_verification_tools()

- [ ] **Step 6: Verify imports**

Run: `source venv/bin/activate && python -c "from tools.rag_tool import RagToolInterface; from tools.execution_tool import ExecutionToolInterface; from tools.verification_tool import VerificationToolInterface; print('OK')"`
Expected: OK

- [ ] **Step 7: Commit**

```bash
git add tools/__init__.py tools/base.py tools/rag_tool.py tools/execution_tool.py tools/verification_tool.py
git commit -m "feat: add ABC tool interfaces with @tool wrapper factories"
```

---

### Task 4: Stub Implementations

**Files:**
- Create: `tools/stub/__init__.py`
- Create: `tools/stub/rag_stub.py`
- Create: `tools/stub/execution_stub.py`
- Create: `tools/stub/verification_stub.py`

- [ ] **Step 1-3: Create stub implementations** that return sample data so the graph can be tested end-to-end without real modules.

- [ ] **Step 4: Verify stubs import**

- [ ] **Step 5: Commit**

```bash
git add tools/stub/
git commit -m "feat: add stub tool implementations for graph testing"
```

---

### Task 5: Graph Nodes and Routing

**Files:**
- Create: `agent/nodes.py`

- [ ] **Step 1: Create agent/nodes.py** with all 8 node functions and 2 routing functions.

- [ ] **Step 2: Verify node imports**

Run: `source venv/bin/activate && python -c "from agent.nodes import classify_input, crawl_manual, build_rag, generate_test_cases, plan_execution, execute_tests, verify_results, visualize_report; print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add agent/nodes.py
git commit -m "feat: add graph node functions and routing logic"
```

---

### Task 6: StateGraph Construction

**Files:**
- Create: `agent/graph.py`

- [ ] **Step 1: Create agent/graph.py** — build_agent_graph() wiring nodes, edges, conditional routing.

- [ ] **Step 2: Verify graph compiles**

Run: `source venv/bin/activate && python -c "from tools.stub.rag_stub import StubRagTool; from tools.stub.execution_stub import StubExecutionTool; from tools.stub.verification_stub import StubVerificationTool; from agent import build_agent_graph; graph = build_agent_graph(StubRagTool(), StubExecutionTool(), StubVerificationTool()); print('Graph compiled:', graph.name)"`
Expected: Graph compiled with name

- [ ] **Step 3: Commit**

```bash
git add agent/graph.py
git commit -m "feat: add StateGraph construction with conditional routing and retry loop"
```

---

### Task 7: New Entry Point (main.py)

**Files:**
- Rewrite: `main.py`

- [ ] **Step 1: Rewrite main.py** with argparse supporting --url, --manual, --manual-dir, --model, --interactive, --visualize.

- [ ] **Step 2: Verify CLI help**

Run: `source venv/bin/activate && python main.py --help`
Expected: Usage text with all options

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat: rewrite main.py as unified CLI entry point"
```

---

### Task 8: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write README.md** with project overview, architecture diagram, setup instructions, CLI usage, module interface guide for team members.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README with setup and usage instructions"
```

---

### Task 9: End-to-End Smoke Test

- [ ] **Step 1: Run stub-based end-to-end test**

Run: `source venv/bin/activate && python main.py --url https://demo.4gaboards.com/ --manual-dir ./manual`
Expected: Full pipeline runs with stub data, outputs report.

- [ ] **Step 2: Final commit**
