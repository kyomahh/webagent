"""Stub RAG Tool —— 仅用于测试 Graph 流程，不调用真实 LLM/向量库。"""

import os

from tools.rag_tool import RagToolInterface


class StubRagTool(RagToolInterface):

    def crawl_and_load_manual(self, manual_url: str) -> list[dict]:
        print(f"[StubRag] 模拟爬取手册: {manual_url}")
        return [{"content": "4gaboards 用户注册功能手册内容（stub）",
                 "source": manual_url, "metadata": {}}]

    def load_local_manual(self, manual_dir: str) -> list[dict]:
        print(f"[StubRag] 模拟加载本地手册: {manual_dir}")
        docs = []
        if os.path.isdir(manual_dir):
            for f in sorted(os.listdir(manual_dir))[:3]:
                path = os.path.join(manual_dir, f)
                if os.path.isfile(path):
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        docs.append({"content": fh.read()[:500],
                                     "source": path, "metadata": {}})
        if not docs:
            docs = [{"content": "stub 手册内容", "source": "stub", "metadata": {}}]
        return docs

    def build_knowledge_base(self, documents: list[dict],
                             persist_dir: str | None = None) -> str:
        print(f"[StubRag] 模拟构建知识库，文档数: {len(documents)}")
        return persist_dir or "chroma_db"

    def extract_features(self, vector_store_path: str) -> list[dict]:
        print(f"[StubRag] 模拟提取功能点")
        return [
            {"feature_id": "F001", "feature_name": "用户注册",
             "description": "用户可以通过邮箱注册新账号"},
            {"feature_id": "F002", "feature_name": "用户登录",
             "description": "已注册用户可以登录系统"},
        ]

    def generate_scenarios(self, features: list[dict],
                           vector_store_path: str) -> list[dict]:
        print(f"[StubRag] 模拟生成测试用例，功能点数: {len(features)}")
        scenarios = []
        for feat in features:
            fid = feat.get("feature_id", "F999")
            scenarios.append({
                "scenario_id": f"TS_{fid}_001",
                "feature_id": fid,
                "scenario_name": f"测试 {feat.get('feature_name', '')}",
                "steps": [
                    f"打开 {feat.get('feature_name', '')} 页面",
                    f"执行 {feat.get('feature_name', '')} 操作",
                    "验证操作结果",
                ],
                "expectations": [
                    f"{feat.get('feature_name', '')} 功能正常工作",
                ],
            })
        return scenarios
