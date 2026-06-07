"""数据与 RAG 模块实现 —— 组员 A"""

import glob
import hashlib
import json
import os
import re
import shutil
import time
from typing import Any
from urllib.parse import urljoin, urlparse

# 兼容部分 embedding / protobuf 依赖组合。必须在导入相关第三方库前设置。
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

import requests
from bs4 import BeautifulSoup
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import ZhipuAIEmbeddings
from langchain_community.vectorstores import Chroma

from core.config import AgentConfig, default_config
from core.fixed_account import (
    TEST_ACCOUNT_EMAIL,
    TEST_ACCOUNT_PASSWORD,
    TEST_ACCOUNT_USERNAME,
)
from scripts.randomize_test_case_credentials import randomize_test_cases, write_credentials_file
from tools.rag_tool import RagToolInterface


class MyRagTool(RagToolInterface):

    def __init__(self, config: AgentConfig | None = None):
        self.config = config or default_config()

    def crawl_and_load_manual(self, manual_url: str) -> list[dict]:
        """爬取指定 URL 的用户手册，BFS 跟踪同域名下的文档链接（最多 3 层）。"""
        from collections import deque

        base_domain = urlparse(manual_url).netloc
        skip_exts = (".png", ".jpg", ".jpeg", ".pdf", ".zip", ".svg", ".css", ".js")
        visited = set()
        documents = []

        # BFS 队列：(url, depth)
        queue = deque([(manual_url, 0)])
        visited.add(manual_url)

        while queue:
            url, depth = queue.popleft()

            try:
                headers = {"User-Agent": "Mozilla/5.0 (compatible; WebAgent/1.0)"}
                resp = requests.get(url, headers=headers, timeout=15)
                resp.raise_for_status()
                resp.encoding = resp.apparent_encoding or "utf-8"
            except Exception as e:
                print(f"[RagTool] 爬取失败 {url}: {e}")
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            title_tag = soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else url

            # 收集子链接（在 decompose 之前，保留 nav 里的链接）
            if depth < 2:
                for a in soup.find_all("a", href=True):
                    full_url = urljoin(url, a["href"])
                    parsed = urlparse(full_url)
                    if (
                        parsed.netloc == base_domain
                        and parsed.scheme in ("http", "https")
                        and "#" not in full_url
                        and not any(parsed.path.lower().endswith(e) for e in skip_exts)
                        and full_url not in visited
                    ):
                        visited.add(full_url)
                        queue.append((full_url, depth + 1))

            # 去除噪声标签，提取正文
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()

            main = (
                soup.find("main")
                or soup.find("article")
                or soup.find("div", class_=re.compile(r"content|main|doc|body", re.I))
            )
            text = (main or soup).get_text(separator="\n", strip=True)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()

            if text and len(text) > 30:
                documents.append({
                    "content": text,
                    "source": url,
                    "metadata": {"title": title, "url": url},
                })

        # 兜底：确保返回非空列表
        if not documents:
            documents = [{
                "content": f"手册页面 {manual_url}（内容无法提取）",
                "source": manual_url,
                "metadata": {"title": "手册", "url": manual_url},
            }]

        print(f"[RagTool] 共爬取 {len(documents)} 页文档，来源: {manual_url}")
        return documents

    def load_local_manual(self, manual_dir: str) -> list[dict]:
        """从本地目录加载 .txt / .md / .html 手册文档。"""
        if not os.path.isdir(manual_dir):
            print(f"[RagTool] 目录不存在: {manual_dir}")
            return []

        documents = []
        pattern = os.path.join(manual_dir, "**", "*")

        for filepath in sorted(glob.glob(pattern, recursive=True)):
            if not os.path.isfile(filepath):
                continue
            ext = os.path.splitext(filepath)[1].lower()

            try:
                if ext in (".txt", ".md", ".markdown"):
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                    title = os.path.splitext(os.path.basename(filepath))[0]

                elif ext in (".html", ".htm"):
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                        html_content = f.read()
                    soup = BeautifulSoup(html_content, "html.parser")
                    for tag in soup(["script", "style", "nav", "footer", "header"]):
                        tag.decompose()
                    text = soup.get_text(separator="\n", strip=True)
                    t = soup.find("title")
                    title = t.get_text(strip=True) if t else os.path.basename(filepath)

                else:
                    continue

                text = re.sub(r"\n{3,}", "\n\n", text).strip()
                if text:
                    documents.append({
                        "content": text,
                        "source": filepath,
                        "metadata": {"title": title, "path": filepath},
                    })
            except Exception as e:
                print(f"[RagTool] 加载文件失败 {filepath}: {e}")

        print(f"[RagTool] 共加载 {len(documents)} 个本地文档")
        return documents

    def build_knowledge_base(self, documents: list[dict],
                             persist_dir: str | None = None) -> str:
        """将文档分块、向量化并持久化到 ChromaDB。"""
        from dotenv import load_dotenv
        load_dotenv()
        persist_root = persist_dir or self.config.chroma_dir

        if not documents:
            print("[RagTool] 文档列表为空，跳过向量化")
            os.makedirs(persist_root, exist_ok=True)
            return persist_root

        persist_dir = self._source_scoped_persist_dir(documents, persist_root)
        self._reset_vector_store_dir(persist_dir)
        os.makedirs(persist_dir, exist_ok=True)

        # 转为 LangChain Document，并保留可追溯到原始手册的稳定元数据。
        lc_docs = [
            self._document_to_lc_document(doc, index)
            for index, doc in enumerate(documents)
            if doc.get("content")
        ]

        # 文本分块
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=800,
            chunk_overlap=80,
            separators=["\n\n", "\n", "。", ".", " ", ""],
            add_start_index=True,
        )
        chunks = splitter.split_documents(lc_docs)
        chunks = self._annotate_chunks(chunks)
        print(f"[RagTool] 共 {len(lc_docs)} 篇文档，分割为 {len(chunks)} 个文本块")

        # 分批写入 ChromaDB（每批 50 个，避免 embedding API 限流）
        embeddings = ZhipuAIEmbeddings(model=self.config.embedding_model)
        batch_size = 50
        vectorstore = None

        for i in range(0, len(chunks), batch_size):
            batch = chunks[i: i + batch_size]
            if vectorstore is None:
                vectorstore = Chroma.from_documents(
                    documents=batch,
                    embedding=embeddings,
                    persist_directory=persist_dir,
                )
            else:
                vectorstore.add_documents(batch)
            print(f"[RagTool] 已向量化 {min(i + batch_size, len(chunks))}/{len(chunks)} 块")
            time.sleep(0.3)

        try:
            vectorstore.persist()
        except AttributeError:
            pass  # chromadb >= 0.4 自动持久化

        print(f"[RagTool] 向量库已保存到: {persist_dir}")
        return persist_dir

    def extract_features(self, vector_store_path: str) -> list[dict]:
        """从向量知识库中检索内容，用 LLM 提取功能点。"""
        from dotenv import load_dotenv
        load_dotenv()

        # 用多个 query 检索，覆盖手册中不同功能模块
        queries = [
            "功能 特性 操作 feature",
            "用户 登录 注册 账号 user login",
            "项目 创建 添加 仪表盘 侧边栏 project add project dashboard sidebar",
            "看板 板块 列表 拖拽 board list",
            "看板 创建 添加 模板 project board add board create board",
            "卡片 任务 创建 编辑 删除 card task",
            "设置 配置 权限 管理 settings admin",
            "导入 导出 数据 backup import export",
            "搜索 过滤 筛选 查询 search filter",
            "通知 消息 评论 notification comment",
        ]
        context = ""
        feature_evidence: list[dict] = []
        try:
            embeddings = ZhipuAIEmbeddings(model=self.config.embedding_model)
            vectorstore = Chroma(
                persist_directory=vector_store_path,
                embedding_function=embeddings,
            )
            # 多 query 检索，合并去重，并保留功能点级别的来源证据。
            seen_contents = set()
            all_docs = []
            for q in queries:
                evidence_items = self._retrieve_evidence(vectorstore, q, k=5)
                feature_evidence.extend(evidence_items)
                for item in evidence_items:
                    doc = Document(
                        page_content=str(item.get("_content") or item.get("quote") or ""),
                        metadata={
                            "source": item.get("source", ""),
                            "title": item.get("title", ""),
                            "chunk_id": item.get("chunk_id", ""),
                        },
                    )
                    if doc.page_content not in seen_contents:
                        seen_contents.add(doc.page_content)
                        all_docs.append(doc)
            coverage_evidence = self._retrieve_source_coverage_evidence(
                vectorstore,
                per_source=2,
                max_items=80,
            )
            feature_evidence.extend(coverage_evidence)
            feature_evidence = self._dedupe_and_reindex_evidence(feature_evidence)
            context = self._format_evidence_for_prompt(feature_evidence, max_chars=18000)
            if not context:
                context = "\n\n".join(doc.page_content for doc in all_docs)
            print(
                f"[RagTool] 多 query 检索到 {len(all_docs)} 条不重复文本块，"
                f"来源覆盖补充 {len(coverage_evidence)} 条"
            )
        except Exception as e:
            print(f"[RagTool] 加载向量库失败: {e}")

        prompt = f"""你是一个软件测试专家。请根据以下从用户手册中检索到的证据片段，提取出软件的所有主要功能点。

要求：
1. 每个功能点应该是一个独立的、可测试的功能模块
2. 功能点描述应简洁明确
3. 以JSON数组格式输出，每个元素包含 "feature_id"、"feature_name"、"description"、"citations" 字段
4. feature_id 格式为 F + 三位数字（如 F001, F002）
5. 只输出JSON数组，不要有任何说明文字或markdown代码块
6. 尽可能全面地提取所有功能模块，不要遗漏
7. "citations" 必须是引用的证据编号数组（如 ["C1", "C3"]），只能引用下面出现的编号
8. 如果证据包含 "+Add project" 或 "To create a project"，必须提取独立的 Project Creation 功能点
9. 如果证据包含 "+Add Board" 或 "Creating a new board"，必须提取独立的 Board Creation 功能点
10. 按 source/title 逐页检查证据；每个包含用户操作、设置、视图、导入导出、通知、权限、卡片、列表或看板管理的页面，都必须被某个功能点覆盖
11. 不要只提取高频功能；低频但独立可测试的页面也要提取为功能点或合并到语义相近的功能点

手册证据片段：
{context[:18000] or "（无手册内容）"}

输出（仅JSON数组）："""

        try:
            from core.llm import get_llm
            llm = get_llm(self.config.model_name)
            response = llm.invoke(prompt)
            text = response.content.strip()
            # 去除 markdown 代码块包裹
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            import json
            raw = json.loads(text.strip())
            features_raw = raw if isinstance(raw, list) else raw.get("features", [])
        except Exception as e:
            print(f"[RagTool] 功能点提取失败: {e}，使用兜底数据")
            features_raw = [
                {"feature_id": "F001", "feature_name": "用户登录", "description": "用户通过账号密码登录系统"},
            ]

        # 规范化格式，确保 feature_id 唯一
        seen = set()
        features = []
        counter = 1
        for item in features_raw:
            if not isinstance(item, dict):
                continue
            fid = str(item.get("feature_id", f"F{counter:03d}")).strip()
            while not fid or fid in seen:
                fid = f"F{counter:03d}"
                counter += 1
            seen.add(fid)
            features.append({
                "feature_id": fid,
                "feature_name": str(item.get("feature_name", "未知功能")),
                "description": str(item.get("description", "")),
                "citations": self._feature_citations(item, feature_evidence),
            })
            counter += 1

        if not features:
            features = [{"feature_id": "F001", "feature_name": "基础功能", "description": "系统基础操作功能"}]

        features = self._ensure_project_creation_feature(features, feature_evidence)
        features = self._ensure_board_creation_feature(features, feature_evidence)
        features = self._audit_feature_coverage(features, feature_evidence)

        print(f"[RagTool] 提取到 {len(features)} 个功能点")

        # 保存功能点到 JSON 文件
        try:
            output_dir = getattr(self.config, "output_dir", "output") or "output"
            os.makedirs(output_dir, exist_ok=True)
            save_path = os.path.join(output_dir, "features.json")
            import json
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(features, f, ensure_ascii=False, indent=2)
            print(f"[RagTool] 功能点已保存到: {save_path}")
        except Exception as e:
            print(f"[RagTool] 保存功能点失败: {e}")

        return features

    def generate_scenarios(self, features: list[dict],
                           vector_store_path: str) -> list[dict]:
        """根据功能点和向量知识库生成测试用例。"""
        from dotenv import load_dotenv
        load_dotenv()

        if not features:
            return []

        vectorstore = None
        try:
            embeddings = ZhipuAIEmbeddings(model=self.config.embedding_model)
            vectorstore = Chroma(
                persist_directory=vector_store_path,
                embedding_function=embeddings,
            )
        except Exception as e:
            print(f"[RagTool] 加载向量库失败: {e}")

        test_cases = []
        scenario_counter: dict[str, int] = {}

        # 不再强制添加 TS_REG_001
        # 让LLM根据功能点自然生成测试用例
        # 如果没有生成注册用例，main.py 会添加备用注册用例

        for feat in features:
            fid = str(feat.get("feature_id", "F001")).strip()
            fname = str(feat.get("feature_name", "未知功能"))
            fdesc = str(feat.get("description", ""))

            evidence: list[dict] = []
            context = ""
            if vectorstore:
                try:
                    evidence = self._retrieve_evidence(vectorstore, f"{fname} {fdesc}", k=8)
                    context = self._format_evidence_for_prompt(evidence, max_chars=4000)
                except Exception as e:
                    print(f"[RagTool] 检索失败 {fid}: {e}")
            if not evidence:
                evidence = self._evidence_from_feature_citations(feat)
                context = self._format_evidence_for_prompt(evidence, max_chars=4000)

            prompt = f"""你是一个软件测试专家。请根据以下功能点和手册证据，为该功能点生成 2-3 个测试场景。

功能点：
- ID: {fid}
- 名称: {fname}
- 描述: {fdesc}

手册证据片段（只能引用下面出现的 citation_id）：
{context[:4000] or "（无相关内容）"}

要求：
1. 每个测试场景包含具体的操作步骤和预期结果
2. 步骤应该是具体可执行的操作描述（如"点击Login按钮"、"在Email输入框中输入"）
3. 以JSON数组格式输出，每个元素包含：
   - "scenario_name": 测试场景名称（字符串）
   - "steps": 操作步骤列表（字符串数组，至少1个）
   - "expectations": 预期结果列表（字符串数组）
   - "citations": 引用的手册证据编号数组（如 ["C1", "C3"]）
4. 只输出JSON数组，不要有任何说明文字或markdown代码块
5. 测试数据必须统一使用以下账号（注册和登录必须一致）：
   - 邮箱/登录名: "{TEST_ACCOUNT_EMAIL}"
   - 用户名: "{TEST_ACCOUNT_USERNAME}"
   - 密码: "{TEST_ACCOUNT_PASSWORD}"
6. 重要：登录时输入的是邮箱（{TEST_ACCOUNT_EMAIL}），不是用户名！登录表单通常用 Email 字段。不要生成其他测试账号。
7. 步骤中的按钮和输入框描述应使用实际页面上出现的文本（英文页面用英文，如"Email"、"Password"、"Login"、"Register"）
8. 不要编造手册证据中没有出现的页面、按钮或功能；每个场景必须至少引用 1 个 citation_id，除非手册证据为空

输出（仅JSON数组）："""

            scenarios_raw = None
            try:
                from core.llm import get_llm
                llm = get_llm(self.config.model_name)
                response = llm.invoke(prompt)
                text = response.content.strip()
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0]
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0]
                import json
                parsed = json.loads(text.strip())
                if isinstance(parsed, list):
                    scenarios_raw = parsed
            except Exception as e:
                print(f"[RagTool] 测试场景生成失败 {fid}: {e}，使用兜底数据")

            if not scenarios_raw:
                scenarios_raw = [
                    {
                        "scenario_name": f"测试 {fname} 基础功能",
                        "steps": [f"打开 {fname} 页面", f"执行 {fname} 操作", "验证操作结果"],
                        "expectations": [f"{fname} 功能正常工作"],
                    },
                ]

            idx = scenario_counter.get(fid, 1)
            for sc in scenarios_raw:
                if not isinstance(sc, dict):
                    continue
                raw_steps = sc.get("steps", [])
                steps = [str(s) for s in raw_steps if str(s).strip()]
                if not steps:
                    steps = [f"执行 {fname} 操作"]
                citations, source_confidence = self._build_test_case_citations(sc, evidence)
                unsupported_steps = self._find_unsupported_steps(
                    steps + [str(e) for e in sc.get("expectations", [])],
                    citations,
                )
                if unsupported_steps and source_confidence == "high":
                    source_confidence = "medium"
                if not citations:
                    source_confidence = "low"
                test_cases.append({
                    "scenario_id": f"TS_{fid}_{idx:03d}",
                    "feature_id": fid,
                    "scenario_name": str(sc.get("scenario_name", f"测试 {fname}")),
                    "steps": steps,
                    "expectations": [str(e) for e in sc.get("expectations", [])],
                    "citations": citations,
                    "source_confidence": source_confidence,
                    "unsupported_steps": unsupported_steps,
                })
                idx += 1
            scenario_counter[fid] = idx

        test_cases = self._ensure_structural_setup_cases(test_cases, features)
        test_cases = self._annotate_structural_dependencies(test_cases)

        print(f"[RagTool] 共生成 {len(test_cases)} 个测试用例")

        # 确保注册用例存在且在第一位
        test_cases = self._ensure_registration_case(test_cases)
        test_cases, credentials = randomize_test_cases(test_cases)

        # 保存测试用例到 JSON 文件，便于查看和调试
        try:
            output_dir = getattr(self.config, "output_dir", "output") or "output"
            os.makedirs(output_dir, exist_ok=True)
            save_path = os.path.join(output_dir, "test_cases_manual.json")
            sources_path = os.path.join(output_dir, "test_cases_with_sources.json")
            credentials_path = write_credentials_file(credentials, output_dir, save_path)
            print(
                "[RagTool] 已随机化测试账号: "
                f"email={credentials.email}, username={credentials.username}"
            )
            print(f"[RagTool] 随机账号已保存到: {credentials_path}")
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(test_cases, f, ensure_ascii=False, indent=2)
            print(f"[RagTool] 测试用例已保存到: {save_path}")
            with open(sources_path, "w", encoding="utf-8") as f:
                json.dump(test_cases, f, ensure_ascii=False, indent=2)
            print(f"[RagTool] 带溯源测试用例已保存到: {sources_path}")
            provenance_path = self._save_provenance_report(test_cases, output_dir)
            print(f"[RagTool] 溯源报告已保存到: {provenance_path}")
        except Exception as e:
            print(f"[RagTool] 保存测试用例失败: {e}")

        return test_cases

    def _ensure_project_creation_feature(self, features: list[dict], evidence: list[dict]) -> list[dict]:
        """如果手册证据包含 +Add project，确保功能点中有独立的项目创建项。"""
        if self._has_project_creation_feature(features):
            return features

        project_evidence = self._find_project_creation_evidence(evidence)
        if not project_evidence:
            return features

        return features + [{
            "feature_id": self._next_feature_id(features),
            "feature_name": "Project Creation",
            "description": "Users with sufficient permissions can create a project from the dashboard or sidebar using +Add project.",
            "citations": [self._public_citation(project_evidence)],
        }]

    def _ensure_board_creation_feature(self, features: list[dict], evidence: list[dict]) -> list[dict]:
        """如果手册证据包含 +Add Board，确保功能点中有独立的 board 创建项。"""
        if self._has_board_creation_feature(features):
            return features

        board_evidence = self._find_board_creation_evidence(evidence)
        if not board_evidence:
            return features

        return features + [{
            "feature_id": self._next_feature_id(features),
            "feature_name": "Board Creation",
            "description": "Users can create a new board inside a project using +Add Board, project menus, or sidebar actions.",
            "citations": [self._public_citation(board_evidence)],
        }]

    def _audit_feature_coverage(self, features: list[dict], evidence: list[dict]) -> list[dict]:
        """按证据中的明确功能线索补齐 LLM 遗漏的低频功能点。"""
        audited = list(features)
        for rule in self._feature_coverage_rules():
            if self._feature_rule_covered(audited, rule):
                continue
            matched_evidence = self._find_rule_evidence(rule, evidence)
            if not matched_evidence:
                continue
            audited.append({
                "feature_id": self._next_feature_id(audited),
                "feature_name": rule["feature_name"],
                "description": rule["description"],
                "citations": [self._public_citation(matched_evidence)],
                "coverage_rule": rule["id"],
            })
        return audited

    @staticmethod
    def _feature_coverage_rules() -> list[dict]:
        """确定性功能覆盖规则；只补证据中明确出现的可测试功能。"""
        return [
            {
                "id": "user_registration",
                "feature_name": "User Registration",
                "description": "Users can create an account by entering registration details and accepting required terms.",
                "feature_patterns": [r"\bregistration\b", r"\bregister\b", r"create an account", r"sign up", r"注册"],
                "evidence_patterns": [r"create an account", r"\bregister\b", r"\bregistration\b", r"sign up", r"注册"],
                "exclude_patterns": [r"without accepting terms", r"blocked", r"denied"],
            },
            {
                "id": "user_login",
                "feature_name": "User Login",
                "description": "Registered users can authenticate with email and password.",
                "feature_patterns": [r"\blogin\b", r"log in", r"sign in", r"signin", r"登录"],
                "evidence_patterns": [r"\blogin\b", r"log in", r"sign in", r"email.*password", r"登录"],
                "exclude_patterns": [r"social login", r"sso", r"oauth"],
            },
            {
                "id": "list_view",
                "feature_name": "List View",
                "description": "Cards can be displayed and managed in a table-like list view.",
                "feature_patterns": [r"list view", r"列表视图"],
                "evidence_patterns": [r"list view", r"列表视图"],
            },
            {
                "id": "board_view",
                "feature_name": "Board View",
                "description": "Cards can be organized in a Kanban-style board view with lists and columns.",
                "feature_patterns": [r"board view", r"kanban", r"看板视图"],
                "evidence_patterns": [r"board view", r"kanban", r"看板视图"],
            },
            {
                "id": "list_management",
                "feature_name": "List Management",
                "description": "Users can create, move, hide, unhide, and manage board lists.",
                "feature_patterns": [r"list management", r"add list", r"hide.*list", r"列表管理"],
                "evidence_patterns": [r"add list", r"hide/unhide", r"hide.*lists?", r"move list", r"list menu", r"添加.*列表|隐藏.*列表|移动.*列表"],
            },
            {
                "id": "card_management",
                "feature_name": "Card Management",
                "description": "Users can create, open, edit, move, and manage cards.",
                "feature_patterns": [r"card management", r"add card", r"card view", r"卡片管理"],
                "evidence_patterns": [r"add card", r"card view", r"move card", r"card menu", r"card details", r"添加.*卡片|移动.*卡片|卡片详情"],
            },
            {
                "id": "card_tasks",
                "feature_name": "Card Tasks",
                "description": "Cards can contain tasks/subtasks with completion, members, and due dates.",
                "feature_patterns": [r"subtask", r"card task", r"task due date", r"子任务"],
                "evidence_patterns": [r"subtask", r"\btasks?\b", r"task due date", r"任务|子任务"],
            },
            {
                "id": "card_labels",
                "feature_name": "Card Labels",
                "description": "Cards can be categorized and filtered with labels.",
                "feature_patterns": [r"\blabels?\b", r"标签"],
                "evidence_patterns": [r"\blabels?\b", r"标签"],
            },
            {
                "id": "board_filtering",
                "feature_name": "Board Filtering",
                "description": "Users can filter board cards by text, labels, members, due dates, and case sensitivity.",
                "feature_patterns": [r"filter", r"search", r"match case", r"筛选|过滤|搜索"],
                "evidence_patterns": [r"filter cards", r"board filtering", r"match case", r"filter by", r"筛选|过滤|搜索"],
            },
            {
                "id": "import_export",
                "feature_name": "Import and Export",
                "description": "Users can import or export board data using supported formats.",
                "feature_patterns": [r"import", r"export", r"trello", r"导入|导出"],
                "evidence_patterns": [r"import", r"export", r"trello", r"\.csv", r"\.json", r"\.tgz", r"导入|导出"],
            },
            {
                "id": "notifications",
                "feature_name": "Notifications",
                "description": "Users can view and filter notifications for project, board, list, card, task, and comment activity.",
                "feature_patterns": [r"notification", r"消息|通知"],
                "evidence_patterns": [r"notification", r"bell icon", r"activity", r"消息|通知"],
            },
            {
                "id": "permissions_members",
                "feature_name": "Members and Permissions",
                "description": "Projects and boards support member management and role-based permissions.",
                "feature_patterns": [r"permission", r"member", r"viewer", r"commenter", r"权限|成员|角色"],
                "evidence_patterns": [r"permission", r"members?", r"viewer", r"commenter", r"project manager", r"权限|成员|角色"],
            },
            {
                "id": "user_settings",
                "feature_name": "User Settings",
                "description": "Users can configure personal preferences such as default view, sidebar style, and subscriptions.",
                "feature_patterns": [r"settings", r"default view", r"compact sidebar", r"设置"],
                "evidence_patterns": [r"default view", r"compact sidebar", r"list view style", r"subscribe to", r"settings", r"设置"],
            },
            {
                "id": "backup_restore",
                "feature_name": "Backup and Restore",
                "description": "Administrators can back up and restore instance data.",
                "feature_patterns": [r"backup", r"restore", r"备份|恢复"],
                "evidence_patterns": [r"backup", r"restore", r"boards-backup", r"boards-restore", r"备份|恢复"],
            },
        ]

    def _feature_rule_covered(self, features: list[dict], rule: dict) -> bool:
        patterns = list(rule.get("feature_patterns", []))
        return any(
            self._text_matches_any(self._feature_text(feature), patterns)
            for feature in features
        )

    def _find_rule_evidence(self, rule: dict, evidence: list[dict]) -> dict | None:
        best_match = None
        best_score = -1
        for item in evidence:
            text = self._evidence_text(item)
            if self._text_matches_any(text, rule.get("exclude_patterns", [])):
                continue
            if not self._text_matches_any(text, rule.get("evidence_patterns", [])):
                continue
            score = self._feature_signal_score(text)
            if score > best_score:
                best_match = item
                best_score = score
        return best_match

    @staticmethod
    def _text_matches_any(text: str, patterns: list[str] | None) -> bool:
        if not patterns:
            return False
        haystack = str(text or "")
        return any(re.search(pattern, haystack, re.I) for pattern in patterns)

    @staticmethod
    def _feature_signal_score(text: str) -> int:
        """估算 chunk 是否包含可测试功能描述，用于 source 覆盖抽样排序。"""
        normalized = str(text or "").lower()
        if not normalized.strip():
            return 0

        action_patterns = [
            r"\b(create|add|edit|delete|remove|rename|move|hide|unhide|open|close)\b",
            r"\b(filter|search|sort|import|export|backup|restore|login|register)\b",
            r"\b(sign in|sign up|subscribe|assign|comment|notify|notification)\b",
            r"\b(enable|disable|configure|change|set|select|upload|download)\b",
            r"创建|新增|添加|编辑|删除|移除|重命名|移动|隐藏|打开|关闭",
            r"筛选|过滤|搜索|排序|导入|导出|备份|恢复|登录|注册|配置|设置|选择|上传|下载",
        ]
        domain_patterns = [
            r"\b(project|board|list view|board view|list|card|task|subtask|label)\b",
            r"\b(member|permission|viewer|commenter|manager|setting|shortcut)\b",
            r"\b(notification|activity|comment|due date|template|sidebar|dashboard)\b",
            r"项目|看板|列表视图|看板视图|列表|卡片|任务|子任务|标签|成员|权限|设置|通知|评论",
        ]
        strong_feature_patterns = [
            r"\+ ?add project",
            r"\bto create a project\b",
            r"\bcreate (?:a |new )?project\b",
            r"\+ ?add board",
            r"\bcreating a new board\b",
            r"\bcreate (?:a |new )?board\b",
            r"\blist view\b",
            r"\bboard view\b",
            r"\bimport\b.*\bexport\b|\bexport\b.*\bimport\b",
            r"\bbackup\b.*\brestore\b|\brestore\b.*\bbackup\b",
            r"创建.*项目|新增.*项目|添加.*项目",
            r"创建.*看板|新增.*看板|添加.*看板",
            r"列表视图|看板视图|导入.*导出|备份.*恢复",
        ]
        weak_noise_patterns = [
            r"\bdonate\b",
            r"\bpricing\b",
            r"\bprofessional hosting\b",
            r"\bgoogle cloud console\b",
        ]

        score = 0
        for pattern in action_patterns:
            if re.search(pattern, normalized, re.I):
                score += 3
        for pattern in domain_patterns:
            if re.search(pattern, normalized, re.I):
                score += 1
        for pattern in strong_feature_patterns:
            if re.search(pattern, normalized, re.I):
                score += 5
        for pattern in weak_noise_patterns:
            if re.search(pattern, normalized, re.I):
                score -= 4
        return max(score, 0)

    def _ensure_structural_setup_cases(self, test_cases: list[dict], features: list[dict]) -> list[dict]:
        """补齐 Project/Board 创建这类后续业务用例依赖的结构性前置用例。"""
        cases = [dict(case) for case in test_cases]
        feature_by_kind = {
            "project": self._find_project_creation_feature(features),
            "board": self._find_board_creation_feature(features),
        }

        if feature_by_kind["project"] and not any(self._is_dedicated_project_creation_case(case) for case in cases):
            cases.append(self._make_project_creation_case(feature_by_kind["project"], cases))

        if feature_by_kind["board"] and not any(self._is_dedicated_board_creation_case(case) for case in cases):
            cases.append(self._make_board_creation_case(feature_by_kind["board"], cases))

        return sorted(cases, key=self._structural_case_order)

    def _annotate_structural_dependencies(self, test_cases: list[dict]) -> list[dict]:
        """为生成用例补充 Project/Board/List View 的 requires/produces 元数据。"""
        annotated = []
        has_project_creation_case = any(self._is_project_creation_case(case) for case in test_cases)
        has_board_creation_case = any(self._is_board_creation_case(case) for case in test_cases)
        for case in test_cases:
            copied = dict(case)
            is_project_creation = self._is_project_creation_case(copied)
            is_board_creation = self._is_board_creation_case(copied)
            is_list_view = self._is_list_view_case(copied)

            if is_project_creation:
                self._add_list_values(copied, "requires", ["registered_account"])
                self._add_list_values(copied, "produces", ["created_project", "authenticated_session"])

            if is_board_creation:
                requirements = ["registered_account"]
                if not is_project_creation and has_project_creation_case:
                    requirements.append("created_project")
                self._add_list_values(copied, "requires", requirements)
                self._add_list_values(copied, "produces", ["created_board", "authenticated_session"])

            if is_list_view and not is_board_creation and has_board_creation_case:
                self._add_list_values(copied, "requires", ["registered_account", "created_board"])

            annotated.append(copied)
        return annotated

    def _make_project_creation_case(self, feature: dict, existing_cases: list[dict]) -> dict:
        citations = self._feature_citations(feature, self._evidence_from_feature_citations(feature))
        return {
            "scenario_id": self._unique_scenario_id("TS_SETUP_PROJECT", existing_cases),
            "feature_id": str(feature.get("feature_id") or "F_PROJECT"),
            "scenario_name": "Create Project from dashboard or sidebar",
            "requires": ["registered_account"],
            "produces": ["created_project", "authenticated_session"],
            "priority": 1,
            "citations": citations,
            "source_confidence": "medium" if citations else "low",
            "unsupported_steps": [],
            "steps": [
                f"Enter '{TEST_ACCOUNT_EMAIL}' in the 'Email' input field",
                f"Enter '{TEST_ACCOUNT_PASSWORD}' in the 'Password' input field",
                "Click the 'Login' button",
                "Click the '+Add project' button from the dashboard or the bottom of the sidebar",
                "Enter 'Test Project' in the project name prompt",
                "Confirm the project creation",
            ],
            "expectations": [
                "A new project named 'Test Project' is created",
                "The new project is visible on the dashboard or in the sidebar",
            ],
        }

    def _make_board_creation_case(self, feature: dict, existing_cases: list[dict]) -> dict:
        citations = self._feature_citations(feature, self._evidence_from_feature_citations(feature))
        return {
            "scenario_id": self._unique_scenario_id("TS_SETUP_BOARD", existing_cases),
            "feature_id": str(feature.get("feature_id") or "F_BOARD"),
            "scenario_name": "Create Board inside an existing project",
            "requires": ["registered_account", "created_project"],
            "produces": ["created_board", "authenticated_session"],
            "priority": 2,
            "citations": citations,
            "source_confidence": "medium" if citations else "low",
            "unsupported_steps": [],
            "steps": [
                f"Enter '{TEST_ACCOUNT_EMAIL}' in the 'Email' input field",
                f"Enter '{TEST_ACCOUNT_PASSWORD}' in the 'Password' input field",
                "Click the 'Login' button",
                "Open the project named 'Test Project'",
                "Click the '+Add Board' button",
                "Enter 'Test Board' as the board name",
                "Select the Simple or Kanban template if prompted",
                "Confirm the board creation",
            ],
            "expectations": [
                "A new board named 'Test Board' is created inside the selected project",
                "The user can open the newly created board",
            ],
        }

    def _structural_case_order(self, test_case: dict) -> tuple[int, str]:
        if self._is_core_registration_case(test_case):
            return (0, str(test_case.get("scenario_id", "")))
        if self._is_dedicated_project_creation_case(test_case):
            return (10, str(test_case.get("scenario_id", "")))
        if self._is_dedicated_board_creation_case(test_case):
            return (20, str(test_case.get("scenario_id", "")))
        return (30, str(test_case.get("scenario_id", "")))

    def _find_project_creation_feature(self, features: list[dict]) -> dict | None:
        for feature in features:
            if self._is_project_creation_text(self._feature_text(feature)):
                return feature
        return None

    def _find_board_creation_feature(self, features: list[dict]) -> dict | None:
        for feature in features:
            if self._is_board_creation_text(self._feature_text(feature)):
                return feature
        return None

    def _has_project_creation_feature(self, features: list[dict]) -> bool:
        return self._find_project_creation_feature(features) is not None

    def _has_board_creation_feature(self, features: list[dict]) -> bool:
        return self._find_board_creation_feature(features) is not None

    def _find_project_creation_evidence(self, evidence: list[dict]) -> dict | None:
        for item in evidence:
            if self._is_project_creation_text(self._evidence_text(item)):
                return item
        return None

    def _find_board_creation_evidence(self, evidence: list[dict]) -> dict | None:
        for item in evidence:
            if self._is_board_creation_text(self._evidence_text(item)):
                return item
        return None

    def _is_project_creation_case(self, test_case: dict) -> bool:
        text = self._test_case_text(test_case)
        return self._is_project_creation_text(text) and not self._is_negative_creation_text(text)

    def _is_board_creation_case(self, test_case: dict) -> bool:
        text = self._test_case_text(test_case)
        return self._is_board_creation_text(text) and not self._is_negative_creation_text(text)

    def _is_dedicated_project_creation_case(self, test_case: dict) -> bool:
        if not self._is_project_creation_case(test_case):
            return False
        text = self._test_case_text(test_case).lower()
        if self._is_composite_setup_text(text):
            return False
        name = str(test_case.get("scenario_name", "")).lower()
        feature = self._feature_text(test_case).lower()
        return (
            bool(re.search(r"\b(create|add) (?:a |new )?project\b", name))
            or "project creation" in feature
            or "+add project" in text
        )

    def _is_dedicated_board_creation_case(self, test_case: dict) -> bool:
        if not self._is_board_creation_case(test_case):
            return False
        text = self._test_case_text(test_case).lower()
        if self._is_composite_setup_text(text):
            return False
        name = str(test_case.get("scenario_name", "")).lower()
        feature = self._feature_text(test_case).lower()
        return (
            bool(re.search(r"\b(create|add) (?:a |new )?board\b", name))
            or "board creation" in feature
            or "+add board" in text
        )

    def _is_list_view_case(self, test_case: dict) -> bool:
        text = self._test_case_text(test_case).lower()
        return "list view" in text or "列表视图" in text

    @staticmethod
    def _is_project_creation_text(text: str) -> bool:
        normalized = str(text or "").lower()
        if (
            "google cloud" in normalized
            or "project creation for all users" in normalized
            or "enable or disable project creation" in normalized
            or "project creation option" in normalized
            or "project creation setting" in normalized
        ):
            return False
        return bool(
            re.search(r"\+ ?add project", normalized)
            or re.search(r"\badd project\b", normalized)
            or re.search(r"\bcreate (?:a |new )?project\b", normalized)
            or re.search(r"\bproject creation\b", normalized)
            or re.search(r"创建.*项目|新增.*项目|添加.*项目", normalized)
        )

    @staticmethod
    def _is_board_creation_text(text: str) -> bool:
        normalized = str(text or "").lower()
        return bool(
            re.search(r"\+ ?add board", normalized)
            or re.search(r"\badd board\b", normalized)
            or re.search(r"\bcreate (?:a |new )?board\b", normalized)
            or re.search(r"\bcreating a new board\b", normalized)
            or re.search(r"\bboard creation\b", normalized)
            or re.search(r"创建.*(?:看板|board)|新增.*(?:看板|board)|添加.*(?:看板|board)", normalized)
        )

    @staticmethod
    def _is_negative_creation_text(text: str) -> bool:
        normalized = str(text or "").lower()
        return bool(
            re.search(r"\b(no new|not created|does not create|fails?|failure|invalid|unsupported|denied|blocked)\b", normalized)
            or re.search(r"未创建|不会创建|创建失败|无效|不支持|拒绝|阻止", normalized)
        )

    @staticmethod
    def _is_composite_setup_text(text: str) -> bool:
        normalized = str(text or "").lower()
        return bool(
            re.search(r"\b(notification|filter|category|backup|restore|import|export|permission|member|role|setting|settings|option|enable|disable|admin)\b", normalized)
            or re.search(r"通知|筛选|过滤|分类|备份|恢复|导入|导出|权限|成员|角色|设置|选项|启用|禁用|管理员", normalized)
        )

    @staticmethod
    def _feature_text(feature: dict) -> str:
        return " ".join([
            str(feature.get("feature_id", "")),
            str(feature.get("feature_name", "")),
            str(feature.get("description", "")),
        ])

    @staticmethod
    def _evidence_text(item: dict) -> str:
        return " ".join([
            str(item.get("title", "")),
            str(item.get("quote", "")),
            str(item.get("_content", "")),
        ])

    @staticmethod
    def _test_case_text(test_case: dict) -> str:
        return " ".join([
            str(test_case.get("scenario_id", "")),
            str(test_case.get("feature_id", "")),
            str(test_case.get("scenario_name", "")),
            " ".join(str(step) for step in test_case.get("steps", [])),
            " ".join(str(expectation) for expectation in test_case.get("expectations", [])),
        ])

    @staticmethod
    def _add_list_values(item: dict, key: str, values: list[str]) -> None:
        existing = item.get(key, [])
        if not isinstance(existing, list):
            existing = [existing]
        merged = [str(value) for value in existing if str(value).strip()]
        seen = set(merged)
        for value in values:
            value = str(value)
            if value not in seen:
                merged.append(value)
                seen.add(value)
        item[key] = merged

    @staticmethod
    def _next_feature_id(features: list[dict]) -> str:
        used = {str(feature.get("feature_id", "")) for feature in features}
        max_number = 0
        for feature_id in used:
            match = re.fullmatch(r"F(\d{3})", feature_id)
            if match:
                max_number = max(max_number, int(match.group(1)))
        candidate = max_number + 1
        while f"F{candidate:03d}" in used:
            candidate += 1
        return f"F{candidate:03d}"

    @staticmethod
    def _unique_scenario_id(base: str, existing_cases: list[dict]) -> str:
        used = {str(case.get("scenario_id", "")) for case in existing_cases}
        if base not in used:
            return base
        index = 2
        while f"{base}_{index}" in used:
            index += 1
        return f"{base}_{index}"

    def _feature_citations(self, feature: dict, evidence: list[dict]) -> list[dict]:
        refs = self._normalize_citation_refs(
            feature.get("citations")
            or feature.get("citation_ids")
            or feature.get("sources")
            or feature.get("source_ids")
        )
        by_id = {str(item.get("citation_id")): item for item in evidence}
        explicit = [by_id[ref] for ref in refs if ref in by_id]
        if explicit:
            return [self._public_citation(item) for item in explicit[:3]]
        return [self._public_citation(item) for item in evidence[:3]]

    def _source_scoped_persist_dir(self, documents: list[dict], persist_root: str) -> str:
        """为当前手册来源创建独立向量库目录，避免 manual/manual_1 数据混用。"""
        sources = [
            str(doc.get("source") or "")
            for doc in documents
            if isinstance(doc, dict) and doc.get("source")
        ]
        if not sources:
            return persist_root

        first = sources[0]
        parsed = urlparse(first)
        if parsed.scheme in {"http", "https"}:
            source_label = parsed.netloc or "remote_manual"
        else:
            abs_sources = [os.path.abspath(source) for source in sources]
            try:
                common = os.path.commonpath(abs_sources)
            except ValueError:
                common = abs_sources[0]
            if os.path.isfile(common) or os.path.splitext(common)[1]:
                common = os.path.dirname(common)
            source_label = os.path.basename(common.rstrip(os.sep)) or "local_manual"

        slug = self._safe_path_slug(source_label)
        root = os.path.abspath(persist_root)
        if os.path.basename(root) == slug:
            return root
        return os.path.join(root, slug)

    @staticmethod
    def _reset_vector_store_dir(persist_dir: str) -> None:
        """清空本次手册对应的向量库目录，确保只包含当前加载的文档。"""
        if os.path.isdir(persist_dir):
            shutil.rmtree(persist_dir)

    @staticmethod
    def _safe_path_slug(value: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-")
        return slug or "manual"

    def _dedupe_and_reindex_evidence(self, evidence: list[dict]) -> list[dict]:
        deduped = []
        seen = set()
        for item in evidence:
            key = (
                str(item.get("chunk_id") or ""),
                str(item.get("content_hash") or item.get("quote") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            copied = dict(item)
            copied["citation_id"] = f"C{len(deduped) + 1}"
            deduped.append(copied)
        return deduped

    def _evidence_from_feature_citations(self, feature: dict) -> list[dict]:
        citations = feature.get("citations", [])
        if not isinstance(citations, list):
            return []
        evidence = []
        for index, citation in enumerate(citations, 1):
            if not isinstance(citation, dict):
                continue
            item = dict(citation)
            item["citation_id"] = str(item.get("citation_id") or f"C{index}")
            item.setdefault("quote", "")
            item.setdefault("_content", item.get("quote", ""))
            item.setdefault("content_hash", self._content_hash(item.get("quote", "")))
            evidence.append(item)
        return evidence

    def _document_to_lc_document(self, doc: dict, index: int) -> Document:
        """将原始手册文档转换为带稳定溯源元数据的 LangChain Document。"""
        content = str(doc.get("content", "") or "")
        raw_metadata = doc.get("metadata", {})
        metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        source = str(
            doc.get("source")
            or metadata.get("source")
            or metadata.get("url")
            or metadata.get("path")
            or f"manual_doc_{index}"
        )
        title = str(metadata.get("title") or os.path.basename(source) or source)
        doc_id = str(
            metadata.get("doc_id")
            or self._stable_id("doc", source, title, content[:500])
        )
        source_type = "url" if source.startswith(("http://", "https://")) else "file"

        metadata.update({
            "source": source,
            "title": title,
            "doc_id": doc_id,
            "doc_index": index,
            "source_type": source_type,
            "content_hash": self._content_hash(content),
        })
        if source_type == "url":
            metadata.setdefault("url", source)
        else:
            metadata.setdefault("path", source)

        return Document(
            page_content=content,
            metadata=self._sanitize_metadata(metadata),
        )

    def _annotate_chunks(self, chunks: list[Document]) -> list[Document]:
        """为每个向量库 chunk 增加 chunk_id、字符范围和内容 hash。"""
        per_doc_counts: dict[str, int] = {}
        annotated: list[Document] = []
        for global_index, chunk in enumerate(chunks):
            metadata = dict(chunk.metadata or {})
            doc_id = str(metadata.get("doc_id") or self._stable_id("doc", metadata.get("source", ""), global_index))
            doc_chunk_index = per_doc_counts.get(doc_id, 0)
            per_doc_counts[doc_id] = doc_chunk_index + 1
            start = self._safe_int(metadata.get("start_index"), 0)
            end = start + len(chunk.page_content or "")
            chunk_id = f"{doc_id}_chunk_{doc_chunk_index:04d}"
            metadata.update({
                "doc_id": doc_id,
                "chunk_id": chunk_id,
                "chunk_index": global_index,
                "doc_chunk_index": doc_chunk_index,
                "char_start": start,
                "char_end": end,
                "chunk_hash": self._content_hash(chunk.page_content),
            })
            annotated.append(Document(
                page_content=chunk.page_content,
                metadata=self._sanitize_metadata(metadata),
            ))
        return annotated

    def _retrieve_evidence(self, vectorstore: Any, query: str, k: int = 8) -> list[dict]:
        """从向量库检索证据，并包装为可放进 prompt 和测试用例的 citation 对象。"""
        raw_results: list[tuple[Document, float | None]] = []
        if vectorstore is None:
            return []
        try:
            for doc, score in vectorstore.similarity_search_with_score(query, k=k):
                raw_results.append((doc, float(score) if score is not None else None))
        except Exception:
            docs = vectorstore.similarity_search(query, k=k)
            raw_results = [(doc, None) for doc in docs]

        evidence: list[dict] = []
        seen = set()
        for index, (doc, score) in enumerate(raw_results, 1):
            content = str(getattr(doc, "page_content", "") or "").strip()
            if not content:
                continue
            metadata = dict(getattr(doc, "metadata", {}) or {})
            chunk_id = str(metadata.get("chunk_id") or self._stable_id("chunk", content[:200], index))
            dedupe_key = (chunk_id, self._content_hash(content))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            quote = self._short_quote(content)
            evidence.append({
                "citation_id": f"C{len(evidence) + 1}",
                "source": str(metadata.get("source") or metadata.get("url") or metadata.get("path") or ""),
                "title": str(metadata.get("title") or ""),
                "section": str(metadata.get("section") or metadata.get("heading") or ""),
                "doc_id": str(metadata.get("doc_id") or ""),
                "chunk_id": chunk_id,
                "chunk_index": self._safe_int(metadata.get("chunk_index"), index - 1),
                "char_start": self._safe_int(metadata.get("char_start"), 0),
                "char_end": self._safe_int(metadata.get("char_end"), 0),
                "score": score,
                "quote": quote,
                "content_hash": self._content_hash(content),
                "_content": content,
            })
        return evidence

    def _retrieve_source_coverage_evidence(
        self,
        vectorstore: Any,
        per_source: int = 2,
        max_items: int = 80,
    ) -> list[dict]:
        """从向量库全量 chunk 中按 source/title 抽取代表证据，降低 query 漏召回风险。"""
        if vectorstore is None:
            return []

        try:
            collection = getattr(vectorstore, "_collection", None)
            if collection is None:
                return []
            raw = collection.get(include=["documents", "metadatas"])
        except Exception:
            return []

        documents = raw.get("documents") or []
        metadatas = raw.get("metadatas") or []
        if not documents:
            return []

        candidates_by_source: dict[str, list[dict]] = {}
        for index, content in enumerate(documents):
            text = str(content or "").strip()
            if not text:
                continue
            metadata = metadatas[index] if index < len(metadatas) and isinstance(metadatas[index], dict) else {}
            score = self._feature_signal_score(text)
            if score <= 0:
                continue
            evidence_item = self._evidence_item_from_content(
                text,
                metadata,
                index=index,
                score=None,
            )
            evidence_item["_feature_signal_score"] = score
            source_key = self._source_group_key(evidence_item)
            candidates_by_source.setdefault(source_key, []).append(evidence_item)

        selected: list[dict] = []
        ranked_sources = sorted(
            candidates_by_source.items(),
            key=lambda group: (
                -max(self._safe_int(item.get("_feature_signal_score"), 0) for item in group[1]),
                group[0],
            ),
        )
        for source_key, source_candidates in ranked_sources:
            candidates = sorted(
                source_candidates,
                key=lambda item: (
                    -self._safe_int(item.get("_feature_signal_score"), 0),
                    self._safe_int(item.get("chunk_index"), 0),
                ),
            )
            selected.extend(candidates[:per_source])
            if len(selected) >= max_items:
                break

        for index, item in enumerate(selected[:max_items], 1):
            item["citation_id"] = f"C{index}"
            item.pop("_feature_signal_score", None)
        return selected[:max_items]

    def _evidence_item_from_content(
        self,
        content: str,
        metadata: dict,
        index: int,
        score: float | None,
    ) -> dict:
        metadata = dict(metadata or {})
        chunk_id = str(metadata.get("chunk_id") or self._stable_id("chunk", content[:200], index))
        quote = self._short_quote(content)
        return {
            "citation_id": f"C{index + 1}",
            "source": str(metadata.get("source") or metadata.get("url") or metadata.get("path") or ""),
            "title": str(metadata.get("title") or ""),
            "section": str(metadata.get("section") or metadata.get("heading") or ""),
            "doc_id": str(metadata.get("doc_id") or ""),
            "chunk_id": chunk_id,
            "chunk_index": self._safe_int(metadata.get("chunk_index"), index),
            "char_start": self._safe_int(metadata.get("char_start"), 0),
            "char_end": self._safe_int(metadata.get("char_end"), 0),
            "score": score,
            "quote": quote,
            "content_hash": self._content_hash(content),
            "_content": content,
        }

    @staticmethod
    def _source_group_key(item: dict) -> str:
        return "|".join([
            str(item.get("source") or ""),
            str(item.get("title") or ""),
            str(item.get("doc_id") or ""),
        ])

    def _format_evidence_for_prompt(self, evidence: list[dict], max_chars: int = 4000) -> str:
        parts = []
        used = 0
        for item in evidence:
            header = (
                f"[{item.get('citation_id')}] "
                f"source={item.get('source', '')} "
                f"title={item.get('title', '')} "
                f"chunk_id={item.get('chunk_id', '')}"
            )
            quote = str(item.get("quote") or "")
            block = f"{header}\n{quote}"
            if used + len(block) > max_chars and parts:
                break
            parts.append(block[:max(0, max_chars - used)])
            used += len(block)
        return "\n\n".join(parts)

    def _build_test_case_citations(self, scenario: dict, evidence: list[dict]) -> tuple[list[dict], str]:
        refs = self._normalize_citation_refs(
            scenario.get("citations")
            or scenario.get("citation_ids")
            or scenario.get("sources")
            or scenario.get("source_ids")
        )
        by_id = {str(item.get("citation_id")): item for item in evidence}
        explicit = [by_id[ref] for ref in refs if ref in by_id]
        if explicit:
            return [self._public_citation(item) for item in explicit], "high"
        if evidence:
            return [self._public_citation(item) for item in evidence[:2]], "medium"
        return [], "low"

    def _normalize_citation_refs(self, raw_refs: Any) -> list[str]:
        if raw_refs is None:
            return []
        if isinstance(raw_refs, str):
            raw_items = re.split(r"[,，\s]+", raw_refs)
        elif isinstance(raw_refs, list):
            raw_items = raw_refs
        else:
            raw_items = [raw_refs]

        refs = []
        seen = set()
        for item in raw_items:
            if isinstance(item, dict):
                value = item.get("citation_id") or item.get("id") or item.get("source_id")
            else:
                value = item
            ref = str(value or "").strip().upper()
            if not ref:
                continue
            match = re.search(r"C\d+", ref)
            if match:
                ref = match.group(0)
            if ref and ref not in seen:
                seen.add(ref)
                refs.append(ref)
        return refs

    def _public_citation(self, item: dict) -> dict:
        public = {
            "citation_id": item.get("citation_id", ""),
            "source": item.get("source", ""),
            "title": item.get("title", ""),
            "section": item.get("section", ""),
            "doc_id": item.get("doc_id", ""),
            "chunk_id": item.get("chunk_id", ""),
            "chunk_index": item.get("chunk_index", 0),
            "char_start": item.get("char_start", 0),
            "char_end": item.get("char_end", 0),
            "score": item.get("score"),
            "quote": item.get("quote", ""),
            "content_hash": item.get("content_hash", ""),
        }
        return {k: v for k, v in public.items() if v not in ("", None)}

    def _find_unsupported_steps(self, statements: list[str], citations: list[dict]) -> list[str]:
        evidence_text = " ".join(str(item.get("quote", "")) for item in citations).lower()
        if not evidence_text:
            return [str(item) for item in statements if str(item).strip()]

        unsupported = []
        for statement in statements:
            text = str(statement or "").strip()
            if not text:
                continue
            quoted_labels = re.findall(r"[\"'“”‘’]([^\"'“”‘’]{2,60})[\"'“”‘’]", text)
            if quoted_labels:
                missing = [label for label in quoted_labels if label.lower() not in evidence_text]
                if missing:
                    unsupported.append(text)
                continue

            keywords = self._extract_support_keywords(text)
            if keywords and not any(keyword.lower() in evidence_text for keyword in keywords):
                unsupported.append(text)
        return unsupported

    @staticmethod
    def _extract_support_keywords(text: str) -> list[str]:
        stopwords = {
            "打开", "点击", "输入", "验证", "页面", "按钮", "功能", "操作", "结果", "成功",
            "执行", "进入", "选择", "如果", "存在", "the", "and", "with", "into", "page",
            "button", "input", "field", "verify", "open", "click", "enter", "test",
        }
        words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text)
        return [word for word in words if word.lower() not in stopwords and word not in stopwords][:8]

    def _save_provenance_report(self, test_cases: list[dict], output_dir: str) -> str:
        path = os.path.join(output_dir, "provenance_report.md")
        lines = ["# 测试用例溯源报告", ""]
        for tc in test_cases:
            lines.append(f"## {tc.get('scenario_id', '')} {tc.get('scenario_name', '')}".rstrip())
            lines.append(f"- 功能点: {tc.get('feature_id', '')}")
            lines.append(f"- 来源置信度: {tc.get('source_confidence', 'unknown')}")
            citations = tc.get("citations", [])
            if citations:
                lines.append("- 引用证据:")
                for item in citations:
                    source = item.get("source", "")
                    title = item.get("title", "")
                    chunk_id = item.get("chunk_id", "")
                    quote = str(item.get("quote", "")).replace("\n", " ")
                    lines.append(f"  - {item.get('citation_id', '')}: {title} `{chunk_id}` {source}")
                    if quote:
                        lines.append(f"    - 摘要: {quote[:180]}")
            else:
                lines.append("- 引用证据: 无")
            unsupported = tc.get("unsupported_steps", [])
            if unsupported:
                lines.append("- 未被证据直接支持的步骤/预期:")
                for item in unsupported:
                    lines.append(f"  - {item}")
            lines.append("")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
            f.write("\n")
        return path

    @staticmethod
    def _short_quote(content: str, limit: int = 500) -> str:
        text = re.sub(r"\s+", " ", str(content or "")).strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    @staticmethod
    def _stable_id(prefix: str, *parts: Any) -> str:
        raw = "\n".join(str(part) for part in parts)
        digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"{prefix}_{digest}"

    @staticmethod
    def _content_hash(content: Any) -> str:
        return hashlib.sha1(str(content or "").encode("utf-8", errors="ignore")).hexdigest()[:12]

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _sanitize_metadata(metadata: dict) -> dict:
        clean = {}
        for key, value in metadata.items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                clean[str(key)] = value
            else:
                clean[str(key)] = json.dumps(value, ensure_ascii=False, default=str)
        return clean

    def _ensure_registration_case(self, test_cases: list[dict]) -> list[dict]:
        """确保测试用例中包含注册用例且在第一位。

        策略：
        1. 检查是否已有注册用例（通过内容判断）
        2. 如果有，确保它在第一位
        3. 如果没有，添加备用注册用例

        Args:
            test_cases: 原始测试用例列表

        Returns:
            处理后的测试用例列表
        """
        cases = list(test_cases)
        
        # 检查是否已有注册用例
        existing_registration = None
        existing_registration_idx = -1
        
        for idx, case in enumerate(cases):
            if self._is_core_registration_case(case):
                # 优先级1: 标记为setup的注册用例
                if case.get("type") == "setup":
                    existing_registration = case
                    existing_registration_idx = idx
                    break
                # 优先级2: scenario_id包含REG的
                elif "reg" in case.get("scenario_id", "").lower() or "注册" in case.get("scenario_name", ""):
                    if existing_registration is None:
                        existing_registration = case
                        existing_registration_idx = idx
                # 优先级3: 内容包含注册关键词的
                elif existing_registration is None:
                    existing_registration = case
                    existing_registration_idx = idx
        
        if existing_registration:
            # 已有注册用例，确保它在第一位
            print(f"[RagTool] 找到现有注册用例: {existing_registration.get('scenario_id')}")
            if existing_registration_idx > 0:
                # 移到第一位
                cases.pop(existing_registration_idx)
                cases = [existing_registration] + cases
                print(f"[RagTool] 已将注册用例移到第一位")
            return cases
        
        # 没有找到注册用例，添加备用用例
        print("[RagTool] 未找到LLM生成的注册用例，添加备用注册用例")
        backup_case = self._make_registration_case()
        return [backup_case] + cases
    
    def _is_registration_case(self, test_case: dict) -> bool:
        """判断是否是注册用例。"""
        text = " ".join([
            str(test_case.get("scenario_id", "")),
            str(test_case.get("feature_id", "")),
            str(test_case.get("scenario_name", "")),
            " ".join(str(s) for s in test_case.get("steps", [])),
        ]).lower()
        return any(
            keyword in text
            for keyword in ["注册", "register", "registration", "create an account", "sign up", "ts_reg"]
        )

    def _is_external_auth_case(self, test_case: dict) -> bool:
        text = " ".join([
            str(test_case.get("scenario_id", "")),
            str(test_case.get("feature_id", "")),
            str(test_case.get("scenario_name", "")),
            " ".join(str(s) for s in test_case.get("steps", [])),
            " ".join(str(e) for e in test_case.get("expectations", [])),
        ]).lower()
        return any(
            marker in text
            for marker in [
                "sso",
                "oauth",
                "oidc",
                "第三方",
                "social login",
                "external auth",
                "google",
                "github",
                "microsoft",
            ]
        )

    def _is_core_registration_case(self, test_case: dict) -> bool:
        """只把本地账号注册当作全局前置；第三方注册失败不阻断主流程。"""
        return self._is_registration_case(test_case) and not self._is_external_auth_case(test_case)
    
    def _make_registration_case(self) -> dict:
        """创建备用注册用例。"""
        return {
            "scenario_id": "TS_REG_BACKUP",
            "feature_id": "F_REG",
            "scenario_name": "注册新用户（备用测试前置条件）",
            "type": "setup",
            "requires": [],
            "produces": ["registered_account"],
            "priority": 0,
            "citations": [],
            "source_confidence": "low",
            "unsupported_steps": [
                "备用注册用例由系统规则生成，未直接引用用户手册片段",
            ],
            "steps": [
                "打开目标网站登录页面",
                "点击登录页面上的 \"Create an account\" 按钮",
                f"在用户名输入框中输入 \"{TEST_ACCOUNT_USERNAME}\"",
                f"在邮箱输入框中输入 \"{TEST_ACCOUNT_EMAIL}\"",
                f"在密码输入框中输入 \"{TEST_ACCOUNT_PASSWORD}\"",
                f"如果存在确认密码输入框，输入 \"{TEST_ACCOUNT_PASSWORD}\"",
                "如果存在服务条款或隐私协议复选框，勾选同意",
                "点击注册按钮",
                "验证注册成功（页面跳转到登录页或首页）",
            ],
            "expectations": [
                "注册成功",
                "页面跳转到登录页面或首页",
            ],
        }
