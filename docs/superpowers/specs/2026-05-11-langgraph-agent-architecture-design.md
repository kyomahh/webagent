# LangGraph Agent Architecture Design

## Overview

Refactor the webagent project from a simple two-stage pipeline into a unified LangGraph-based agent architecture. The system automatically generates test scenarios from user manuals and executes them against web applications using AI-powered browser automation.

**Target application**: 4gaboards (https://demo.4gaboards.com/)
**User manual**: https://docs.4gaboards.com/

## Architecture

### Pattern: Single Agent + Multi-Tool with StateGraph

- One main agent using LangGraph `StateGraph` for state flow orchestration
- Three modules exposed as `@tool` functions
- Modules implement ABC abstract base classes for team collaboration
- Conditional branching + retry loop for intelligent test execution

### Directory Structure

```
webagent/
├── main.py                          # New entry point (CLI + interactive)
├── requirements.txt
├── .env
├── agent/                           # LangGraph Agent core
│   ├── __init__.py
│   ├── graph.py                     # StateGraph definition & orchestration
│   ├── state.py                     # AgentState definition
│   └── nodes.py                     # Graph node functions
├── tools/                           # Three module Tool interfaces
│   ├── __init__.py
│   ├── base.py                      # ABC abstract base classes
│   ├── rag_tool.py                  # Data & RAG module interface
│   ├── execution_tool.py            # Execution & Interaction module interface
│   └── verification_tool.py         # Verification & Visualization module interface
├── core/                            # Shared infrastructure
│   ├── __init__.py
│   ├── llm.py                       # LangChain unified LLM interface
│   └── config.py                    # Configuration management
├── manual/                          # User manual data
├── output/                          # Runtime output
└── chroma_db/                       # Vector database
```

### StateGraph Flow

```
START → classify_input
           ├─(need crawl)─→ crawl_manual ─→ build_rag
           └─(has manual)────────────────→ build_rag
                                            │
                                            ▼
                                     generate_test_cases
                                            │
                                            ▼
                                       plan_execution ◀─────┐
                                            │                │
                                            ▼                │
                                       execute_tests         │
                                            │                │
                                            ▼                │
                                      verify_results         │
                                       ├─(passed)─→ visualize_report → END
                                       ├─(retry)──→ plan_execution (loop)
                                       └─(max)────→ visualize_report → END
```

## AgentState Definition

```python
from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages

class TestCase(TypedDict):
    scenario_id: str
    feature_id: str
    scenario_name: str
    steps: list[str]
    expectations: list[str]

class ExecutionStep(TypedDict):
    step_id: int
    action_type: str       # navigate/click/type/select/wait/screenshot
    action_detail: str
    target_element: str
    element_type: str
    value: str
    fallback_text: str

class StepResult(TypedDict):
    step_id: int
    action_type: str
    action_detail: str
    result: str
    success: bool
    screenshot_path: str

class AgentState(TypedDict):
    # Input
    target_url: str                     # Target website URL
    manual_url: str | None              # User manual URL (optional)

    # RAG stage
    manual_documents: list              # Crawled manual documents
    vector_store_path: str | None       # Vector store path
    features: list[dict]                # Extracted features
    test_cases: list[TestCase]          # Generated test cases

    # Execution stage
    execution_plans: dict[str, list[ExecutionStep]]   # scenario_id -> plan
    execution_results: dict[str, list[StepResult]]     # scenario_id -> results
    execution_memory: dict              # Execution context memory
    execution_screenshots: list[str]    # Screenshot paths

    # Verification stage
    verification_results: dict[str, dict]  # scenario_id -> {passed, reason}
    retry_count: int                    # Current retry count
    max_retries: int                    # Max retry limit

    # Report
    report_path: str | None             # Final report path

    # LangGraph messages (for Tool calls)
    messages: Annotated[list, add_messages]
```

## Graph Nodes

### 1. classify_input(state) -> state
- Analyze user input URL
- Check if manual data already exists in `manual/` directory
- Route to `crawl_manual` or `build_rag`

### 2. crawl_manual(state) -> state
- Call `rag_tool.crawl_and_load_manual(manual_url)`
- Store documents in `state.manual_documents`

### 3. build_rag(state) -> state
- Call `rag_tool.build_knowledge_base(documents)`
- Store vector store path in `state.vector_store_path`

### 4. generate_test_cases(state) -> state
- Call `rag_tool.extract_features()` then `rag_tool.generate_scenarios()`
- Store in `state.features` and `state.test_cases`

### 5. plan_execution(state) -> state
- Call `execution_tool.plan()` for each test case
- Store plans in `state.execution_plans`

### 6. execute_tests(state) -> state
- Call `execution_tool.execute()` for each plan
- Store results in `state.execution_results`
- Store screenshots in `state.execution_screenshots`

### 7. verify_results(state) -> state
- Call `verification_tool.verify()` for each test case
- Store results in `state.verification_results`
- Conditional routing:
  - All passed -> `visualize_report`
  - Some failed & retry_count < max_retries -> `plan_execution` (retry failed cases only)
  - retry_count >= max_retries -> `visualize_report`

### 8. visualize_report(state) -> state
- Call `verification_tool.visualize()`
- Generate final report at `state.report_path`

## Module Interfaces (ABC)

### RagToolInterface (tools/rag_tool.py)

```python
class RagToolInterface(BaseTool):
    def crawl_and_load_manual(self, manual_url: str) -> list[dict]:
        """Crawl user manual from URL and load documents.
        Returns: [{"content": str, "source": str, "metadata": dict}]
        """

    def build_knowledge_base(self, documents: list[dict],
                              persist_dir: str | None = None) -> str:
        """Build RAG knowledge base (chunking + embedding + vector store).
        Returns: vector store path
        """

    def extract_features(self, vector_store_path: str) -> list[dict]:
        """Extract features from knowledge base.
        Returns: [{"feature_id", "feature_name", "description"}]
        """

    def generate_scenarios(self, features: list[dict],
                           vector_store_path: str) -> list[dict]:
        """Generate test scenarios from features.
        Returns: [{"scenario_id", "feature_id", "scenario_name",
                   "steps": [str], "expectations": [str]}]
        """
```

### ExecutionToolInterface (tools/execution_tool.py)

```python
class ExecutionToolInterface(BaseTool):
    def plan(self, test_case: dict) -> list[dict]:
        """Plan executable steps from test case.
        Returns: [{"step_id", "action_type", "action_detail",
                   "target_element", "element_type", "value", "fallback_text"}]
        """

    def execute(self, plan: list[dict], target_url: str,
                memory: dict | None = None) -> dict:
        """Execute test plan using Playwright.
        Returns: {"results": [StepResult], "memory": dict, "screenshots": [str]}
        """
```

### VerificationToolInterface (tools/verification_tool.py)

```python
class VerificationToolInterface(BaseTool):
    def verify(self, test_case: dict, execution_results: list[dict],
               execution_memory: dict) -> dict:
        """Verify test results against expectations.
        Returns: {"passed": bool, "reason": str, "details": dict}
        """

    def visualize(self, state: dict) -> str:
        """Generate visualization report.
        Returns: report file path
        """
```

## Tool Wrapping for LangGraph

After team members implement the ABC interfaces, wrap them in `@tool` decorators for LangGraph integration:

```python
from langchain_core.tools import tool

def make_rag_tools(rag_impl: RagToolInterface):
    @tool
    def crawl_manual(url: str) -> list[dict]:
        """Crawl user manual from URL"""
        return rag_impl.crawl_and_load_manual(url)

    @tool
    def build_rag(documents: list[dict]) -> str:
        """Build RAG knowledge base"""
        return rag_impl.build_knowledge_base(documents)

    @tool
    def extract_features(vector_store_path: str) -> list[dict]:
        """Extract features from knowledge base"""
        return rag_impl.extract_features(vector_store_path)

    @tool
    def generate_scenarios(features: list[dict], vector_store_path: str) -> list[dict]:
        """Generate test scenarios"""
        return rag_impl.generate_scenarios(features, vector_store_path)

    return [crawl_manual, build_rag, extract_features, generate_scenarios]

def make_execution_tools(exec_impl: ExecutionToolInterface):
    @tool
    def plan_execution(test_case: dict) -> list[dict]:
        """Plan executable steps"""
        return exec_impl.plan(test_case)

    @tool
    def execute_plan(plan: list[dict], target_url: str, memory: dict | None = None) -> dict:
        """Execute test plan"""
        return exec_impl.execute(plan, target_url, memory)

    return [plan_execution, execute_plan]

def make_verification_tools(verify_impl: VerificationToolInterface):
    @tool
    def verify_results(test_case: dict, results: list[dict], memory: dict) -> dict:
        """Verify test results"""
        return verify_impl.verify(test_case, results, memory)

    @tool
    def generate_report(state: dict) -> str:
        """Generate visualization report"""
        return verify_impl.visualize(state)

    return [verify_results, generate_report]
```

## LLM Unified Interface

Use LangChain's `ChatOpenAI` compatible interface to support multiple Chinese LLM providers:

```python
from langchain_openai import ChatOpenAI

def get_llm(model_name: str = "glm-4-flash", temperature: float = 0.1) -> ChatOpenAI:
    model_configs = {
        "glm-4-flash": {
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
    config = model_configs.get(model_name, model_configs["glm-4-flash"])
    return ChatOpenAI(
        model=model_name,
        base_url=config["base_url"],
        api_key=os.environ.get(config["api_key_env"], ""),
        temperature=temperature,
    )
```

## Configuration

```python
from dataclasses import dataclass

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
```

## Retry Mechanism

- On verification failure, only retry the failed test cases (not all)
- `state.retry_count` increments on each retry cycle
- When `retry_count >= max_retries`, force exit to visualization
- On retry, the planner receives failure reasons to adjust execution strategy
- Execution memory persists across retries for context

## Entry Point (main.py)

```python
# CLI usage:
# python main.py --url https://demo.4gaboards.com/ --manual https://docs.4gaboards.com/
# python main.py --url https://demo.4gaboards.com/ --manual-dir ./manual
# python main.py --config config.yaml

# Interactive mode:
# python main.py --interactive
```

## Migration from Existing Code

### What to keep and refactor:
- `task1_rag/document_loader.py` -> Refactor into `RagToolInterface` implementation
- `task1_rag/vector_store.py` -> Refactor into `RagToolInterface` implementation
- `task1_rag/retriever.py` -> Refactor into `RagToolInterface` implementation
- `task1_rag/scenario_generator.py` -> Refactor into `RagToolInterface` implementation
- `task2_agent/memory.py` -> Refactor into shared state management
- `task2_agent/planner.py` -> Refactor into `ExecutionToolInterface` implementation
- `task2_agent/smart_executor.py` -> Refactor into `ExecutionToolInterface` implementation
- `task2_agent/smart_element_finder.py` -> Part of `ExecutionToolInterface` implementation
- `task2_agent/verifier.py` -> Refactor into `VerificationToolInterface` implementation

### What to remove:
- Root-level test files (test_*.py) - these were debugging scripts
- Root-level task2_*.py variants - superseded by the new architecture
- `common_utils/__init__.py` - replaced by `core/llm.py` and `core/config.py`

### Key changes:
1. ZhipuAI SDK calls -> LangChain ChatOpenAI unified interface
2. Sequential pipeline -> LangGraph StateGraph with conditional routing
3. Hard-coded two-task separation -> Unified agent with tool-based modularity
4. Manual-only document loading -> Web crawling + local loading
