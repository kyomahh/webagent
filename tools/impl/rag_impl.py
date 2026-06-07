"""数据与 RAG 模块实现 —— 组员 A"""

import glob
import os
import re
import time
from urllib.parse import urljoin, urlparse

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
        persist_dir = persist_dir or self.config.chroma_dir
        os.makedirs(persist_dir, exist_ok=True)

        if not documents:
            print("[RagTool] 文档列表为空，跳过向量化")
            return persist_dir

        # 转为 LangChain Document
        lc_docs = [
            Document(
                page_content=doc["content"],
                metadata={k: str(v) for k, v in doc.get("metadata", {}).items()},
            )
            for doc in documents if doc.get("content")
        ]

        # 文本分块
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=800,
            chunk_overlap=80,
            separators=["\n\n", "\n", "。", ".", " ", ""],
        )
        chunks = splitter.split_documents(lc_docs)
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
            "看板 板块 列表 拖拽 board list",
            "卡片 任务 创建 编辑 删除 card task",
            "设置 配置 权限 管理 settings admin",
            "导入 导出 数据 backup import export",
            "搜索 过滤 筛选 查询 search filter",
            "通知 消息 评论 notification comment",
        ]
        context = ""
        try:
            embeddings = ZhipuAIEmbeddings(model=self.config.embedding_model)
            vectorstore = Chroma(
                persist_directory=vector_store_path,
                embedding_function=embeddings,
            )
            # 多 query 检索，合并去重
            seen_contents = set()
            all_docs = []
            for q in queries:
                docs = vectorstore.similarity_search(q, k=5)
                for doc in docs:
                    if doc.page_content not in seen_contents:
                        seen_contents.add(doc.page_content)
                        all_docs.append(doc)
            context = "\n\n".join(doc.page_content for doc in all_docs)
            print(f"[RagTool] 多 query 检索到 {len(all_docs)} 条不重复文本块")
        except Exception as e:
            print(f"[RagTool] 加载向量库失败: {e}")

        prompt = f"""你是一个软件测试专家。请根据以下从用户手册中检索到的内容，提取出软件的所有主要功能点。

要求：
1. 每个功能点应该是一个独立的、可测试的功能模块
2. 功能点描述应简洁明确
3. 以JSON数组格式输出，每个元素包含 "feature_id"、"feature_name" 和 "description" 字段
4. feature_id 格式为 F + 三位数字（如 F001, F002）
5. 只输出JSON数组，不要有任何说明文字或markdown代码块
6. 尽可能全面地提取所有功能模块，不要遗漏

手册内容：
{context[:12000] or "（无手册内容）"}

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
            })
            counter += 1

        if not features:
            features = [{"feature_id": "F001", "feature_name": "基础功能", "description": "系统基础操作功能"}]

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

            context = ""
            if vectorstore:
                try:
                    docs = vectorstore.similarity_search(f"{fname} {fdesc}", k=8)
                    context = "\n\n".join(doc.page_content for doc in docs)
                except Exception as e:
                    print(f"[RagTool] 检索失败 {fid}: {e}")

            prompt = f"""你是一个软件测试专家。请根据以下功能点和手册内容，为该功能点生成 2-3 个测试场景。

功能点：
- ID: {fid}
- 名称: {fname}
- 描述: {fdesc}

手册内容（供参考）：
{context[:4000] or "（无相关内容）"}

要求：
1. 每个测试场景包含具体的操作步骤和预期结果
2. 步骤应该是具体可执行的操作描述（如"点击Login按钮"、"在Email输入框中输入"）
3. 以JSON数组格式输出，每个元素包含：
   - "scenario_name": 测试场景名称（字符串）
   - "steps": 操作步骤列表（字符串数组，至少1个）
   - "expectations": 预期结果列表（字符串数组）
4. 只输出JSON数组，不要有任何说明文字或markdown代码块
5. 测试数据必须统一使用以下账号（注册和登录必须一致）：
   - 邮箱/登录名: "{TEST_ACCOUNT_EMAIL}"
   - 用户名: "{TEST_ACCOUNT_USERNAME}"
   - 密码: "{TEST_ACCOUNT_PASSWORD}"
6. 重要：登录时输入的是邮箱（{TEST_ACCOUNT_EMAIL}），不是用户名！登录表单通常用 Email 字段。不要生成其他测试账号。
7. 步骤中的按钮和输入框描述应使用实际页面上出现的文本（英文页面用英文，如"Email"、"Password"、"Login"、"Register"）

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
                test_cases.append({
                    "scenario_id": f"TS_{fid}_{idx:03d}",
                    "feature_id": fid,
                    "scenario_name": str(sc.get("scenario_name", f"测试 {fname}")),
                    "steps": steps,
                    "expectations": [str(e) for e in sc.get("expectations", [])],
                })
                idx += 1
            scenario_counter[fid] = idx

        print(f"[RagTool] 共生成 {len(test_cases)} 个测试用例")

        # 确保注册用例存在且在第一位
        test_cases = self._ensure_registration_case(test_cases)
        test_cases, credentials = randomize_test_cases(test_cases)

        # 保存测试用例到 JSON 文件，便于查看和调试
        try:
            output_dir = getattr(self.config, "output_dir", "output") or "output"
            os.makedirs(output_dir, exist_ok=True)
            save_path = os.path.join(output_dir, "test_cases_manual1.json")
            credentials_path = write_credentials_file(credentials, output_dir, save_path)
            print(
                "[RagTool] 已随机化测试账号: "
                f"email={credentials.email}, username={credentials.username}"
            )
            print(f"[RagTool] 随机账号已保存到: {credentials_path}")
            import json
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(test_cases, f, ensure_ascii=False, indent=2)
            print(f"[RagTool] 测试用例已保存到: {save_path}")
        except Exception as e:
            print(f"[RagTool] 保存测试用例失败: {e}")

        return test_cases

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
            if self._is_registration_case(case):
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
