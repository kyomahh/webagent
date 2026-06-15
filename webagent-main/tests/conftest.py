"""共享测试 fixtures 与校验工具函数。"""

import sys
import os
import types
import pytest

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _install_optional_dependency_test_stubs():
    """为本地 helper 测试提供最小 LangChain 兼容层；真实依赖存在时不生效。"""
    try:
        from langchain_core.tools import tool  # noqa: F401
        from langchain_core.documents import Document  # noqa: F401
    except ModuleNotFoundError:
        langchain_core = types.ModuleType("langchain_core")
        tools_mod = types.ModuleType("langchain_core.tools")
        documents_mod = types.ModuleType("langchain_core.documents")
        messages_mod = types.ModuleType("langchain_core.messages")

        class Document:
            def __init__(self, page_content="", metadata=None):
                self.page_content = page_content
                self.metadata = metadata or {}

        class _ToolWrapper:
            def __init__(self, func):
                self.func = func
                self.__name__ = getattr(func, "__name__", "tool")
                self.__doc__ = getattr(func, "__doc__", None)

            def __call__(self, *args, **kwargs):
                return self.func(*args, **kwargs)

            def invoke(self, tool_input=None):
                if isinstance(tool_input, dict):
                    return self.func(**tool_input)
                if tool_input is None:
                    return self.func()
                return self.func(tool_input)

        def tool(fn=None, *args, **kwargs):
            def decorate(func):
                return _ToolWrapper(func)
            return decorate(fn) if callable(fn) else decorate

        class _Message:
            def __init__(self, content="", **kwargs):
                self.content = content

        tools_mod.tool = tool
        documents_mod.Document = Document
        messages_mod.HumanMessage = _Message
        messages_mod.SystemMessage = _Message
        sys.modules.setdefault("langchain_core", langchain_core)
        sys.modules["langchain_core.tools"] = tools_mod
        sys.modules["langchain_core.documents"] = documents_mod
        sys.modules["langchain_core.messages"] = messages_mod

    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: F401
    except ModuleNotFoundError:
        splitters_mod = types.ModuleType("langchain_text_splitters")

        class RecursiveCharacterTextSplitter:
            def __init__(self, *args, **kwargs):
                pass

            def split_documents(self, documents):
                return list(documents)

        splitters_mod.RecursiveCharacterTextSplitter = RecursiveCharacterTextSplitter
        sys.modules["langchain_text_splitters"] = splitters_mod

    try:
        from langchain_community.embeddings import ZhipuAIEmbeddings  # noqa: F401
        from langchain_community.vectorstores import Chroma  # noqa: F401
    except ModuleNotFoundError:
        community_mod = types.ModuleType("langchain_community")
        embeddings_mod = types.ModuleType("langchain_community.embeddings")
        vectorstores_mod = types.ModuleType("langchain_community.vectorstores")

        class ZhipuAIEmbeddings:
            def __init__(self, *args, **kwargs):
                pass

        class _Collection:
            def __init__(self, documents=None):
                self._documents = list(documents or [])

            def get(self, include=None):
                return {
                    "documents": [doc.page_content for doc in self._documents],
                    "metadatas": [doc.metadata for doc in self._documents],
                }

        class Chroma:
            def __init__(self, *args, **kwargs):
                self._documents = list(kwargs.get("documents") or [])
                self._collection = _Collection(self._documents)

            @classmethod
            def from_documents(cls, documents, *args, **kwargs):
                instance = cls(*args, **kwargs)
                instance.add_documents(documents)
                return instance

            def add_documents(self, documents):
                self._documents.extend(list(documents))
                self._collection = _Collection(self._documents)

            def similarity_search(self, query, k=4):
                return self._documents[:k]

            def similarity_search_with_score(self, query, k=4):
                return [(doc, 0.0) for doc in self.similarity_search(query, k=k)]

            def persist(self):
                pass

        embeddings_mod.ZhipuAIEmbeddings = ZhipuAIEmbeddings
        vectorstores_mod.Chroma = Chroma
        sys.modules.setdefault("langchain_community", community_mod)
        sys.modules["langchain_community.embeddings"] = embeddings_mod
        sys.modules["langchain_community.vectorstores"] = vectorstores_mod

    try:
        from dotenv import load_dotenv  # noqa: F401
    except ModuleNotFoundError:
        dotenv_mod = types.ModuleType("dotenv")
        dotenv_mod.load_dotenv = lambda *args, **kwargs: None
        sys.modules["dotenv"] = dotenv_mod

    try:
        from langchain_openai import ChatOpenAI  # noqa: F401
    except ModuleNotFoundError:
        openai_mod = types.ModuleType("langchain_openai")

        class ChatOpenAI:
            def __init__(self, *args, **kwargs):
                pass

            def invoke(self, prompt):
                raise RuntimeError("ChatOpenAI test stub does not call external LLMs")

        openai_mod.ChatOpenAI = ChatOpenAI
        sys.modules["langchain_openai"] = openai_mod


_install_optional_dependency_test_stubs()

from tools.stub.rag_stub import StubRagTool
from tools.stub.execution_stub import StubExecutionTool
from tools.stub.verification_stub import StubVerificationTool
from tools.rag_tool import DataCache


# ────────────────────────── Stub fixtures ──────────────────────────

@pytest.fixture
def rag_tool():
    """Stub RAG 工具实例。"""
    return StubRagTool()


@pytest.fixture
def execution_tool():
    """Stub Execution 工具实例。"""
    return StubExecutionTool()


@pytest.fixture
def verification_tool():
    """Stub Verification 工具实例。"""
    return StubVerificationTool()


@pytest.fixture
def cache():
    """空的 DataCache 实例。"""
    return DataCache()


@pytest.fixture
def populated_cache(sample_features):
    """已填充 features 和 test_cases 的 DataCache。"""
    c = DataCache()
    c.documents = [
        {"content": "stub", "source": "stub", "metadata": {}},
    ]
    c.features = sample_features
    c.test_cases = [
        {
            "scenario_id": "TS_F001_001",
            "feature_id": "F001",
            "scenario_name": "测试 用户注册",
            "steps": ["打开页面", "执行操作", "验证结果"],
            "expectations": ["功能正常工作"],
        },
        {
            "scenario_id": "TS_F002_001",
            "feature_id": "F002",
            "scenario_name": "测试 用户登录",
            "steps": ["打开页面", "执行操作"],
            "expectations": ["功能正常工作"],
        },
    ]
    return c


# ────────────────────────── 测试数据 fixtures ──────────────────────────

@pytest.fixture
def sample_documents():
    """样例文档列表。"""
    return [
        {"content": "用户注册功能说明...", "source": "https://example.com/reg", "metadata": {}},
        {"content": "用户登录功能说明...", "source": "https://example.com/login", "metadata": {}},
    ]


@pytest.fixture
def sample_features():
    """样例功能点列表。"""
    return [
        {"feature_id": "F001", "feature_name": "用户注册", "description": "用户可以通过邮箱注册新账号"},
        {"feature_id": "F002", "feature_name": "用户登录", "description": "已注册用户可以登录系统"},
    ]


@pytest.fixture
def sample_test_case():
    """样例测试用例。"""
    return {
        "scenario_id": "TS_F001_001",
        "feature_id": "F001",
        "scenario_name": "测试 用户注册",
        "steps": [
            "打开 用户注册 页面",
            "执行 用户注册 操作",
            "验证操作结果",
        ],
        "expectations": [
            "用户注册 功能正常工作",
        ],
    }


@pytest.fixture
def sample_plan():
    """样例执行步骤列表。"""
    return [
        {
            "step_id": 1,
            "action_type": "click",
            "action_detail": "打开 用户注册 页面",
            "target_element": "打开 用户注册 页面",
            "element_type": "button",
            "value": "",
            "fallback_text": "打开 用户注册 页面",
        },
        {
            "step_id": 2,
            "action_type": "type",
            "action_detail": "执行 用户注册 操作",
            "target_element": "执行 用户注册 操作",
            "element_type": "button",
            "value": "",
            "fallback_text": "执行 用户注册 操作",
        },
    ]


@pytest.fixture
def sample_execution_results():
    """样例执行结果列表。"""
    return [
        {
            "step_id": 1,
            "action_type": "click",
            "action_detail": "打开 用户注册 页面",
            "result": "成功执行: 打开 用户注册 页面",
            "success": True,
            "screenshot_path": "",
        },
        {
            "step_id": 2,
            "action_type": "type",
            "action_detail": "执行 用户注册 操作",
            "result": "成功执行: 执行 用户注册 操作",
            "success": True,
            "screenshot_path": "",
        },
    ]


# ────────────────────────── 校验工具函数 ──────────────────────────

def assert_document_format(doc: dict):
    """校验文档格式是否正确。"""
    assert isinstance(doc, dict), "文档必须是 dict"
    assert "content" in doc, "文档缺少 'content' 字段"
    assert "source" in doc, "文档缺少 'source' 字段"
    assert "metadata" in doc, "文档缺少 'metadata' 字段"
    assert isinstance(doc["content"], str), "'content' 必须是 str"
    assert isinstance(doc["source"], str), "'source' 必须是 str"


def assert_feature_format(feature: dict):
    """校验功能点格式是否正确。"""
    assert isinstance(feature, dict), "功能点必须是 dict"
    assert "feature_id" in feature, "功能点缺少 'feature_id' 字段"
    assert "feature_name" in feature, "功能点缺少 'feature_name' 字段"
    assert "description" in feature, "功能点缺少 'description' 字段"


def assert_test_case_format(test_case: dict):
    """校验测试用例格式是否正确。"""
    assert isinstance(test_case, dict), "测试用例必须是 dict"
    assert "scenario_id" in test_case, "测试用例缺少 'scenario_id' 字段"
    assert "feature_id" in test_case, "测试用例缺少 'feature_id' 字段"
    assert "scenario_name" in test_case, "测试用例缺少 'scenario_name' 字段"
    assert "steps" in test_case, "测试用例缺少 'steps' 字段"
    assert "expectations" in test_case, "测试用例缺少 'expectations' 字段"
    assert isinstance(test_case["steps"], list), "'steps' 必须是 list"
    assert isinstance(test_case["expectations"], list), "'expectations' 必须是 list"
    assert len(test_case["steps"]) >= 1, "'steps' 至少包含 1 个步骤"


def assert_execution_step_format(step: dict):
    """校验执行步骤格式是否正确。"""
    assert isinstance(step, dict), "执行步骤必须是 dict"
    assert "step_id" in step, "执行步骤缺少 'step_id' 字段"
    assert "action_type" in step, "执行步骤缺少 'action_type' 字段"
    assert "action_detail" in step, "执行步骤缺少 'action_detail' 字段"
    assert "target_element" in step, "执行步骤缺少 'target_element' 字段"
    assert isinstance(step["step_id"], int), "'step_id' 必须是 int"
    valid_action_types = {"navigate", "click", "type", "select", "wait", "screenshot"}
    assert step["action_type"] in valid_action_types, (
        f"'action_type' 必须是 {valid_action_types} 之一，实际为 '{step['action_type']}'"
    )


def assert_step_result_format(result: dict):
    """校验执行结果格式是否正确。"""
    assert isinstance(result, dict), "执行结果必须是 dict"
    assert "step_id" in result, "执行结果缺少 'step_id' 字段"
    assert "action_type" in result, "执行结果缺少 'action_type' 字段"
    assert "result" in result, "执行结果缺少 'result' 字段"
    assert "success" in result, "执行结果缺少 'success' 字段"
    assert isinstance(result["success"], bool), "'success' 必须是 bool"


def assert_verification_format(verification: dict):
    """校验验证结果格式是否正确。"""
    assert isinstance(verification, dict), "验证结果必须是 dict"
    assert "passed" in verification, "验证结果缺少 'passed' 字段"
    assert "reason" in verification, "验证结果缺少 'reason' 字段"
    assert isinstance(verification["passed"], bool), "'passed' 必须是 bool"
    assert isinstance(verification["reason"], str), "'reason' 必须是 str"
