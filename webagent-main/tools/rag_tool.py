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
              "steps": [str], "expectations": [str],
              "requires": [str], "produces": [str],
              "citations": [dict], "source_confidence": str,
              "unsupported_steps": [str]}]
        """
        ...


class DataCache:
    """跨工具模块共享的数据缓存。"""

    def __init__(self):
        self.documents: list[dict] = []
        self.features: list[dict] = []
        self.test_cases: list[dict] = []
        self.execution_plans: dict[str, list[dict]] = {}
        self.execution_results: dict[str, list[dict]] = {}
        self.execution_memory: dict = {}
        self.verification_results: dict[str, dict] = {}


def make_rag_tools(rag_impl: RagToolInterface, cache: DataCache):
    """将 RagToolInterface 实现包装为 LangGraph @tool 函数。

    工具间通过 cache 共享数据，LLM 只看到摘要字符串。
    """

    @tool
    def crawl_manual(url: str) -> str:
        """爬取指定 URL 的用户手册文档。
        可用于探测目标网站是否存在手册页面，如果 URL 无效或未找到文档会返回错误信息。

        Args:
            url: 用户手册网站 URL

        Returns:
            爬取结果摘要，包含文档数量；失败时返回错误描述
        """
        try:
            documents = rag_impl.crawl_and_load_manual(url)
            if not documents:
                return f"未从 {url} 获取到任何文档，该 URL 可能不存在手册页面。请尝试其他路径或跳过手册步骤。"
            cache.documents = documents
            return f"成功爬取 {len(documents)} 页手册文档，来源: {url}"
        except Exception as e:
            return f"爬取 {url} 失败: {e}。请尝试其他 URL 或跳过手册步骤。"

    @tool
    def load_local_manual(directory: str) -> str:
        """从本地目录加载手册文档。

        Args:
            directory: 手册目录路径

        Returns:
            加载结果摘要
        """
        documents = rag_impl.load_local_manual(directory)
        cache.documents = documents
        return f"成功加载 {len(documents)} 个本地文档，目录: {directory}"

    @tool
    def build_knowledge_base(persist_dir: str = "chroma_db") -> str:
        """构建 RAG 知识库（使用上一步获取的文档）。

        Args:
            persist_dir: 向量库持久化目录

        Returns:
            知识库路径
        """
        if not cache.documents:
            return "警告: 当前没有文档数据。请先调用 crawl_manual 或 load_local_manual 获取手册文档，再构建知识库。"
        vector_store_path = rag_impl.build_knowledge_base(
            cache.documents, persist_dir if persist_dir else None
        )
        return f"知识库已构建: {vector_store_path}，基于 {len(cache.documents)} 个文档"

    @tool
    def extract_features(vector_store_path: str) -> str:
        """从知识库中提取功能点。

        Args:
            vector_store_path: 向量库路径

        Returns:
            功能点摘要列表
        """
        features = rag_impl.extract_features(vector_store_path)
        cache.features = features
        lines = [f"共提取 {len(features)} 个功能点:"]
        for f in features:
            lines.append(f"  - {f.get('feature_id', '')}: {f.get('feature_name', '')} - {f.get('description', '')}")
        return "\n".join(lines)

    @tool
    def generate_scenarios(vector_store_path: str) -> str:
        """根据已提取的功能点生成测试用例。

        Args:
            vector_store_path: 向量库路径

        Returns:
            测试用例摘要列表
        """
        if not cache.features:
            return "警告: 当前没有功能点数据。请先调用 extract_features 提取功能点，再生成测试用例。"
        test_cases = rag_impl.generate_scenarios(cache.features, vector_store_path)
        cache.test_cases = test_cases
        lines = [f"共生成 {len(test_cases)} 个测试用例:"]
        for tc in test_cases:
            sid = tc.get("scenario_id", "")
            name = tc.get("scenario_name", "")
            steps_count = len(tc.get("steps", []))
            source_confidence = tc.get("source_confidence", "unknown")
            citation_count = len(tc.get("citations", []))
            lines.append(
                f"  - {sid}: {name} ({steps_count} 步, "
                f"来源置信度: {source_confidence}, 引用: {citation_count})"
            )
        return "\n".join(lines)

    return [crawl_manual, load_local_manual, build_knowledge_base,
            extract_features, generate_scenarios]
