from abc import abstractmethod

from langchain_core.tools import tool
from tools.base import BaseTool


class RagToolInterface(BaseTool):
    """数据与 RAG 模块接口 —— 组员实现此类。"""

    def name(self) -> str:
        return "rag_tool"

    def description(self) -> str:
        return "数据与RAG模块：爬取手册、构建知识库、提取功能点、生成测试用例"

    @abstractmethod
    def crawl_and_load_manual(self, manual_url: str) -> list[dict]:
        """爬取用户手册并加载文档。

        Args:
            manual_url: 用户手册网站 URL（如 https://docs.4gaboards.com/）

        Returns:
            文档列表，每项格式:
            {"content": str, "source": str, "metadata": dict}
        """
        ...

    @abstractmethod
    def load_local_manual(self, manual_dir: str) -> list[dict]:
        """从本地目录加载已有的手册文档。

        Args:
            manual_dir: 手册目录路径

        Returns:
            文档列表，格式同 crawl_and_load_manual
        """
        ...

    @abstractmethod
    def build_knowledge_base(self, documents: list[dict],
                             persist_dir: str | None = None) -> str:
        """构建 RAG 知识库（分块 + 嵌入 + 向量存储）。

        Args:
            documents: 文档列表
            persist_dir: 向量库持久化目录

        Returns:
            向量库路径
        """
        ...

    @abstractmethod
    def extract_features(self, vector_store_path: str) -> list[dict]:
        """从知识库中提取功能点。

        Args:
            vector_store_path: 向量库路径

        Returns:
            功能点列表:
            [{"feature_id": str, "feature_name": str, "description": str}]
        """
        ...

    @abstractmethod
    def generate_scenarios(self, features: list[dict],
                           vector_store_path: str) -> list[dict]:
        """根据功能点生成测试用例。

        测试用例构成: [[step]+ [expectation]?]+
        - steps: 完成功能所需的具体操作步骤（至少1个）
        - expectations: 功能完成后的预期状态，即测试预言，
          包含用于评估功能是否成功完成的关键特征（可选）

        Args:
            features: 功能点列表
            vector_store_path: 向量库路径

        Returns:
            测试用例列表:
            [{"scenario_id": str, "feature_id": str, "scenario_name": str,
              "steps": [str], "expectations": [str]}]
        """
        ...


def make_rag_tools(rag_impl: RagToolInterface):
    """将 RagToolInterface 实现包装为 LangGraph @tool 函数。"""

    @tool
    def crawl_manual(url: str) -> list[dict]:
        """爬取指定 URL 的用户手册文档。"""
        return rag_impl.crawl_and_load_manual(url)

    @tool
    def load_local_manual(directory: str) -> list[dict]:
        """从本地目录加载手册文档。"""
        return rag_impl.load_local_manual(directory)

    @tool
    def build_knowledge_base(documents: list[dict], persist_dir: str = "") -> str:
        """构建 RAG 知识库。"""
        return rag_impl.build_knowledge_base(
            documents, persist_dir if persist_dir else None
        )

    @tool
    def extract_features(vector_store_path: str) -> list[dict]:
        """从知识库中提取功能点。"""
        return rag_impl.extract_features(vector_store_path)

    @tool
    def generate_scenarios(features: list[dict], vector_store_path: str) -> list[dict]:
        """根据功能点生成测试用例。"""
        return rag_impl.generate_scenarios(features, vector_store_path)

    return [crawl_manual, load_local_manual, build_knowledge_base,
            extract_features, generate_scenarios]
