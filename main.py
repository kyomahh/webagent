"""WebAgent - 基于大模型的测试场景生成与智能测试工具

用法:
    # 全自动流水线（爬取手册 -> 生成用例 -> 执行 -> 验证）
    python main.py --url https://demo.4gaboards.com/ --manual https://docs.4gaboards.com/

    # 使用本地手册
    python main.py --url https://demo.4gaboards.com/ --manual-dir ./manual

    # 使用 stub 模式测试框架
    python main.py --url https://demo.4gaboards.com/ --stub

    # 可视化报告
    python main.py --visualize
"""

import argparse
import glob
import logging
import os
import subprocess
import sys
from datetime import datetime

from dotenv import load_dotenv

from core.fixed_account import TEST_ACCOUNT_EMAIL, TEST_ACCOUNT_PASSWORD, TEST_ACCOUNT_USERNAME
from core.test_case_dedup import remove_duplicate_successful_registration_cases
from core.test_case_step_normalizer import normalize_test_case_steps
from scripts.randomize_test_case_credentials import randomize_test_cases, write_credentials_file

load_dotenv()


RUN_LOG_PATH = os.path.join(os.path.dirname(__file__), "runtime.log")
IMAGE_OUTPUT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


class _TeeStream:
    def __init__(self, primary, log_file):
        self.primary = primary
        self.log_file = log_file

    def write(self, data):
        self.primary.write(data)
        self.log_file.write(data)
        return len(data)

    def flush(self):
        self.primary.flush()
        self.log_file.flush()

    def __getattr__(self, name):
        return getattr(self.primary, name)


class _LegacyLogFormatter(logging.Formatter):
    def format(self, record):
        level = record.levelname.ljust(8)
        name = self._short_logger_name(record.name)
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        return f"{level} [{name}] {message}"

    @staticmethod
    def _short_logger_name(name: str) -> str:
        if "browser_use.Agent" in name:
            return "Agent"
        if "browser_use.BrowserSession" in name:
            return "BrowserSession"
        mapping = {
            "browser_use.agent.prompts": "prompts",
            "browser_use.tools.service": "tools",
            "browser_use": "browser_use",
        }
        if name in mapping:
            return mapping[name]
        if name.startswith("browser_use."):
            return name.rsplit(".", 1)[-1]
        return name


def _setup_run_log():
    """Create a per-run log file. Opening in write mode overwrites the previous run."""
    log_file = open(RUN_LOG_PATH, "w", encoding="utf-8", buffering=1)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = _TeeStream(original_stdout, log_file)
    sys.stderr = _TeeStream(original_stderr, log_file)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
    formatter = _LegacyLogFormatter()
    stream_handler = logging.StreamHandler(original_stderr)
    stream_handler.setFormatter(formatter)
    file_handler = logging.FileHandler(RUN_LOG_PATH, mode="a", encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)

    print(f"[RunLog] 本次运行日志: {RUN_LOG_PATH}")
    print(f"[RunLog] 开始时间: {datetime.now().isoformat(timespec='seconds')}")
    return log_file, original_stdout, original_stderr


def _clear_previous_run_images(output_dir: str) -> int:
    """删除上一轮运行遗留的图片文件；保留报告、PDF、JSON、日志等其他数据。"""
    if not output_dir:
        return 0

    os.makedirs(output_dir, exist_ok=True)
    deleted = 0
    for root, _, files in os.walk(output_dir):
        for filename in files:
            if os.path.splitext(filename)[1].lower() not in IMAGE_OUTPUT_EXTENSIONS:
                continue
            path = os.path.join(root, filename)
            try:
                os.remove(path)
                deleted += 1
            except OSError as exc:
                print(f"[OutputCleanup] 删除旧图片失败: {path} ({exc})")
    return deleted


def _make_registration_case() -> dict:
    return {
        "scenario_id": "TS_REG_001",
        "feature_id": "F_REG",
        "scenario_name": "注册新用户（测试前置条件）",
        "type": "setup",
        "requires": [],
        "produces": ["registered_account"],
        "priority": 0,
        "steps": [
            "打开目标网站登录页面",
            "点击登录页面上的 \"Create an account\" 按钮",
            f"在注册页面的用户名输入框中输入 \"{TEST_ACCOUNT_USERNAME}\"",
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


def _is_registration_case(test_case: dict) -> bool:
    text = " ".join([
        str(test_case.get("scenario_id", "")),
        str(test_case.get("feature_id", "")),
        str(test_case.get("scenario_name", "")),
        " ".join(str(s) for s in test_case.get("steps", [])),
    ]).lower()
    return any(
        keyword in text
        for keyword in [
            "ts_reg",
            "注册",
            "register",
            "registration",
            "create an account",
            "sign up",
        ]
    )


def _is_external_auth_case(test_case: dict) -> bool:
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


def _is_core_registration_case(test_case: dict) -> bool:
    """只把本地账号注册当作全局前置；第三方注册失败不能阻断主流程。"""
    return _is_registration_case(test_case) and not _is_external_auth_case(test_case)


def _registration_case_score(test_case: dict) -> int:
    text = " ".join([
        str(test_case.get("scenario_id", "")),
        str(test_case.get("feature_id", "")),
        str(test_case.get("scenario_name", "")),
        " ".join(str(s) for s in test_case.get("steps", [])),
        " ".join(str(e) for e in test_case.get("expectations", [])),
    ]).lower()
    score = 0
    if "ts_reg" in text or "前置" in text or "setup" in text:
        score += 100
    # 使用单词边界匹配，避免 "register" 匹配到 "registered"
    import re
    success_patterns = [r"\b成功\b", r"\b新用户\b", r"\b有效\b", r"\bcreate an account\b", r"\bregister(?!ed)\b"]
    if any(re.search(pattern, text, re.I) for pattern in success_patterns):
        score += 20
    if any(keyword in text for keyword in ["失败", "错误", "已存在", "为空", "invalid", "wrong"]):
        score -= 50
    return score


def _ensure_registration_first(test_cases: list[dict],
                                verification_results: dict | None = None) -> tuple[list[dict], bool]:
    """确保注册前置用例存在且在第一位。

    策略：
    1. 优先使用LLM生成的注册用例（如果存在且质量好）
    2. 如果没有LLM生成的注册用例，添加备用注册用例
    3. 如果注册已经成功，不需要再添加备用用例

    Args:
        test_cases: 测试用例列表
        verification_results: 验证结果（可选，用于检查注册是否已成功）

    Returns:
        (处理后的测试用例列表, 是否添加了备用注册用例)
    """
    cases, removed_duplicates = remove_duplicate_successful_registration_cases(list(test_cases or []))
    if removed_duplicates:
        removed_ids = [str(case.get("scenario_id", "")) for case in removed_duplicates]
        print(
            "[RegistrationCheck] 已移除重复成功注册用例: "
            f"{', '.join(removed_ids)}"
        )

    # 检查是否有LLM生成的注册用例
    llm_generated_registration = None
    backup_registration = None

    for case in cases:
        if _is_core_registration_case(case):
            # 优先级1: 标记为setup的注册用例（LLM通常这样标记）
            if case.get("type") == "setup":
                llm_generated_registration = case
                break
            # 优先级2: scenario_id包含TS_REG的（可能是备用用例）
            elif "ts_reg" in case.get("scenario_id", "").lower():
                backup_registration = case
            # 优先级3: 其他注册用例
            elif llm_generated_registration is None:
                llm_generated_registration = case

    # 检查注册是否已经成功
    registration_success = False
    if verification_results:
        for case in cases:
            if _is_core_registration_case(case):
                sid = case.get("scenario_id")
                result = verification_results.get(sid, {})
                if isinstance(result, dict) and result.get("passed"):
                    registration_success = True
                    print(f"[RegistrationCheck] ✓ 注册用例 {sid} 已验证通过")
                    break

    # 决策逻辑
    if registration_success:
        # 注册已成功，不需要添加备用用例
        print("[RegistrationCheck] 注册已成功，不需要添加备用注册用例")
        # 确保成功的注册用例在第一位
        return _move_registration_to_first(cases), False

    if llm_generated_registration:
        # 有LLM生成的注册用例，使用它
        print(f"[RegistrationCheck] 使用LLM生成的注册用例: {llm_generated_registration.get('scenario_id')}")
        return _move_registration_to_first(cases), False

    # 没有LLM生成的注册用例，添加备用用例
    print("[RegistrationCheck] 未找到LLM生成的注册用例，添加备用注册用例 TS_REG_001")
    backup_case = _make_registration_case()
    return [backup_case, *cases], True


def _move_registration_to_first(test_cases: list[dict]) -> list[dict]:
    """将注册用例移动到列表第一位。"""
    cases = list(test_cases)
    registration_indexes = [
        idx for idx, case in enumerate(cases) if _is_core_registration_case(case)
    ]

    if not registration_indexes:
        return cases

    # 找到分数最高的注册用例
    registration_index = max(
        registration_indexes,
        key=lambda idx: _registration_case_score(cases[idx])
    )

    if registration_index != 0:
        registration_case = cases.pop(registration_index)
        return [registration_case, *cases]

    return cases


def _resolve_resume_cases_path(output_dir: str, requested_path: str | None) -> str:
    """解析 --resume 使用的测试用例文件路径。"""
    if requested_path:
        candidates = [requested_path]
        if not os.path.isabs(requested_path):
            candidates.append(os.path.join(output_dir, requested_path))
        for path in candidates:
            if os.path.isfile(path):
                return path
        return requested_path

    default_path = os.path.join(output_dir, "test_cases.json")
    if os.path.isfile(default_path):
        return default_path

    matches = [
        path for path in glob.glob(os.path.join(output_dir, "test_cases*.json"))
        if os.path.isfile(path)
    ]
    if matches:
        return max(matches, key=os.path.getmtime)

    return default_path


def main():
    run_log_file, original_stdout, original_stderr = _setup_run_log()
    parser = argparse.ArgumentParser(
        description="WebAgent - 基于大模型的测试场景生成与智能测试工具"
    )

    try:
        parser.add_argument(
            "--url", type=str, default="https://demo.4gaboards.com/",
            help="目标网站 URL (默认: https://demo.4gaboards.com/)",
        )
        parser.add_argument(
            "--manual", type=str, default=None,
            help="用户手册网站 URL (如: https://docs.4gaboards.com/)",
        )
        parser.add_argument(
            "--manual-dir", type=str, default="./manual",
            help="本地手册目录路径 (默认: ./manual)",
        )
        parser.add_argument(
            "--model", type=str, default="glm-4.7",
            choices=["glm-4.7", "glm-4.7-flash", "glm-4-plus", "deepseek-chat", "qwen-plus"],
            help="使用的 LLM 模型 (默认: glm-4.7)",
        )
        parser.add_argument(
            "--max-retries", type=int, default=2,
            help="验证失败后最大重试次数 (默认: 2)",
        )
        parser.add_argument(
            "--headless", action="store_true",
            help="浏览器无头模式运行",
        )
        parser.add_argument(
            "--stub", action="store_true",
            help="使用 stub 模式测试框架（不调用真实 LLM / 浏览器）",
        )
        parser.add_argument(
            "--visualize", action="store_true",
            help="启动 Streamlit 可视化界面查看已有报告",
        )
        parser.add_argument(
            "--resume", action="store_true",
            help="复用已生成的测试用例，跳过手册加载、知识库构建、功能提取和用例生成阶段",
        )
        parser.add_argument(
            "--test-cases", type=str, default=None,
            help="--resume 时使用的测试用例 JSON 文件；未指定时自动查找 output/test_cases*.json",
        )

        args = parser.parse_args()

        # 可视化模式
        if args.visualize:
            report_path = os.path.join(os.path.dirname(__file__), "output", "report_stub.json")
            viz_script = os.path.join(os.path.dirname(__file__), "tools", "stub", "visualize_stub.py")
            if os.path.exists(viz_script):
                subprocess.run(["streamlit", "run", viz_script], check=True)
            else:
                print(f"报告路径: {report_path}")
                print("请使用 --stub 模式先生成报告，或等待可视化模块实现。")
            return

        # 导入并构建 Agent
        from core.config import default_config
        from agent import build_agent_graph

        config = default_config()
        config.target_url = args.url
        config.manual_url = args.manual
        config.manual_dir = args.manual_dir
        config.model_name = args.model
        config.max_retries = args.max_retries
        config.headless = args.headless

        deleted_images = _clear_previous_run_images(config.output_dir)
        print(f"[OutputCleanup] 已删除上一轮运行图片 {deleted_images} 个，保留其他输出数据")

        # 创建共享浏览器会话（空壳，不启动浏览器）
        # 执行模块调用 ensure_page() 时才懒启动，验证模块通过 session.page 获取同一个 page
        from core.browser import BrowserSession
        session = BrowserSession()

        try:
            # 选择 Tool 实现
            if args.stub:
                from tools.stub import StubRagTool, StubExecutionTool, StubVerificationTool
                rag_tool = StubRagTool()
                execution_tool = StubExecutionTool()
                verification_tool = StubVerificationTool()
            else:
                # 真实模块实现 —— 由组员在 tools/ 下实现后替换
                try:
                    from tools.impl import get_rag_tool, get_execution_tool, get_verification_tool
                    rag_tool = get_rag_tool(config)
                    execution_tool = get_execution_tool(config, session)
                    verification_tool = get_verification_tool(config, session)
                except ImportError:
                    print("错误: 真实模块未实现。请使用 --stub 模式测试，或在 tools/impl/ 中实现模块。")
                    print("  参考接口定义: tools/rag_tool.py, tools/execution_tool.py, tools/verification_tool.py")
                    sys.exit(1)

            print("=" * 60)
            print("WebAgent - 基于大模型的测试场景生成与智能测试工具")
            print("=" * 60)
            print(f"  目标网站: {config.target_url}")
            if args.manual:
                print(f"  手册 URL: {args.manual}")
            elif args.manual_dir:
                print(f"  手册目录: {args.manual_dir}")
            print(f"  模型: {config.model_name}")
            print(f"  模式: {'Stub (测试)' if args.stub else '真实模块'}")
            if args.resume:
                print(f"  Resume: 复用已生成测试用例")
            print(f"  最大重试: {config.max_retries}")
            print("=" * 60)

            # 构建 Plan-Execute-Verify Agent
            graph = build_agent_graph(rag_tool, execution_tool, verification_tool, config)

            # 构建初始输入
            manual_desc = ""
            if args.manual:
                manual_desc = f"远程手册 URL: {args.manual}"
            elif args.manual_dir:
                manual_desc = f"本地手册目录: {args.manual_dir}"
            else:
                manual_desc = "未指定手册来源，请自行判断目标网站是否存在用户手册并尝试获取"

            initial_input = (
                f"请对目标网站 {config.target_url} 进行自动化测试。"
                f"手册来源: {manual_desc}。"
                f"向量库目录: {config.chroma_dir}。"
                f"最大重试次数: {config.max_retries}。"
            )

            # --resume: 从 output/ 加载已保存的测试用例，跳过生成阶段
            import json
            preloaded_features = []
            preloaded_cases = []
            preloaded_docs = []

            if args.resume:
                output_dir = config.output_dir
                features_path = os.path.join(output_dir, "features.json")
                cases_path = _resolve_resume_cases_path(output_dir, args.test_cases)

                if os.path.isfile(cases_path):
                    with open(cases_path, "r", encoding="utf-8") as f:
                        preloaded_cases = json.load(f)
                    preloaded_cases, inserted_registration = _ensure_registration_first(preloaded_cases)
                    preloaded_cases = [
                        normalize_test_case_steps(case)
                        for case in preloaded_cases
                    ]
                    preloaded_cases, credentials = randomize_test_cases(preloaded_cases)
                    with open(cases_path, "w", encoding="utf-8") as f:
                        json.dump(preloaded_cases, f, ensure_ascii=False, indent=2)
                        f.write("\n")
                    credentials_path = write_credentials_file(credentials, output_dir, cases_path)
                    print(f"  [Resume] 已加载 {len(preloaded_cases)} 个测试用例: {cases_path}")
                    print(
                        "  [Resume] 已随机化测试账号: "
                        f"email={credentials.email}, username={credentials.username}"
                    )
                    print(f"  [Resume] 上次随机账号已保存: {credentials_path}")
                    if inserted_registration:
                        print("  [Resume] 旧测试用例缺少注册前置，已自动插入 TS_REG_001")
                    # 加载功能点（可选）
                    if os.path.isfile(features_path):
                        with open(features_path, "r", encoding="utf-8") as f:
                            preloaded_features = json.load(f)
                        print(f"  [Resume] 已加载 {len(preloaded_features)} 个功能点: {features_path}")
                    # 模拟 documents（占位，避免 planner 认为需要重新加载）
                    preloaded_docs = [{"content": "已缓存", "source": "resume"}]
                    initial_input = (
                        f"请对目标网站 {config.target_url} 进行自动化测试。"
                        f"测试用例已预加载（共 {len(preloaded_cases)} 个），请直接开始执行测试用例。"
                        f"最大重试次数: {config.max_retries}。"
                    )
                else:
                    print(f"  [Resume] 未找到 {cases_path}，将从头开始生成测试用例")
                    args.resume = False

            # 执行
            result = graph.invoke({
                "target_url": config.target_url,
                "manual_url": args.manual,
                "manual_dir": args.manual_dir,
                "chroma_dir": config.chroma_dir,
                "max_retries": config.max_retries,
                "input": initial_input,
                "current_task": {},
                "past_steps": [],
                "response": "",
                "documents": preloaded_docs if args.resume else [],
                "features": preloaded_features if args.resume else [],
                "test_cases": preloaded_cases if args.resume else [],
                "execution_plans": {},
                "execution_results": {},
                "execution_memory": {},
                "verification_results": {},
            })

            print()
            print("=" * 60)
            print("执行完成!")
            response = result.get("response", "")
            if response:
                print(f"  {response}")
            print("=" * 60)
        finally:
            # 统一清理浏览器资源
            session.close()
    finally:
        print(f"[RunLog] 结束时间: {datetime.now().isoformat(timespec='seconds')}")
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        run_log_file.close()


if __name__ == "__main__":
    main()
