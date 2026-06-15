"""Plan-Execute-Verify Agent 提示词 —— planner 和 replanner 的提示词模板。"""

from core.fixed_account import (
    TEST_ACCOUNT_EMAIL,
    TEST_ACCOUNT_PASSWORD,
    TEST_ACCOUNT_USERNAME,
    fixed_account_label,
)


# ── 可用动作清单 ──

AVAILABLE_ACTIONS = [
    {
        "action": "crawl_manual",
        "description": "爬取远程用户手册文档",
        "args": {"url": "手册网站 URL"},
    },
    {
        "action": "load_local_manual",
        "description": "从本地目录加载手册文档",
        "args": {"directory": "本地手册目录路径"},
    },
    {
        "action": "build_knowledge_base",
        "description": "构建 RAG 向量知识库（基于已有文档）",
        "args": {"persist_dir": "向量库持久化目录"},
    },
    {
        "action": "extract_features",
        "description": "从知识库中提取功能点",
        "args": {"vector_store_path": "向量库路径"},
    },
    {
        "action": "generate_scenarios",
        "description": "根据功能点生成测试用例",
        "args": {"vector_store_path": "向量库路径"},
    },
    {
        "action": "plan_and_execute",
        "description": "规划并通过 Playwright 执行指定测试用例",
        "args": {"scenario_id": "测试用例 ID"},
    },
    {
        "action": "verify_results",
        "description": "验证指定测试用例的执行结果",
        "args": {"scenario_id": "测试用例 ID"},
    },
    {
        "action": "generate_report",
        "description": "生成最终可视化报告",
        "args": {},
    },
]


def _format_actions() -> str:
    lines = []
    for a in AVAILABLE_ACTIONS:
        args_desc = ", ".join(f'{k}: {v}' for k, v in a["args"].items()) or "无参数"
        lines.append(f'  - {a["action"]}: {a["description"]} (参数: {args_desc})')
    return "\n".join(lines)


def build_planner_prompt(
    target_url: str,
    manual_url: str | None = None,
    manual_dir: str | None = None,
    chroma_dir: str = "chroma_db",
    max_retries: int = 2,
) -> str:
    """构建 planner 提示词。"""

    # 构建手册来源信息
    if manual_url:
        manual_info = f"远程手册 URL: {manual_url}（第一步应使用 crawl_manual）"
    elif manual_dir:
        manual_info = f"本地手册目录: {manual_dir}（第一步应使用 load_local_manual）"
    else:
        manual_info = "手册来源未知，需要判断目标网站是否存在用户手册并尝试获取"

    return f"""你是一个自动化 Web 测试的规划者。你的任务是分析当前状态，决定下一步要执行的动作。

## 目标信息
- 目标网站: {target_url}
- 向量库目录: {chroma_dir}
- 最大重试次数: {max_retries}
- 手册来源: {manual_info}

## 可用动作
{_format_actions()}

## 典型工作流顺序
1. 获取手册文档: crawl_manual 或 load_local_manual
2. 构建知识库: build_knowledge_base
3. 提取功能点: extract_features
4. 生成测试用例: generate_scenarios
5. **必须第一个执行本地账号注册用例**: 找到本地账号注册/Sign up/Create an account 用例优先执行 plan_and_execute；Google/GitHub/OAuth/SSO 等第三方注册不作为全局注册前置
6. 对每个测试用例依次: plan_and_execute → verify_results
7. 全部完成后: generate_report

## 决策规则
- 根据当前数据状态判断下一步动作
- 如果文档为空且未尝试获取手册，先获取手册
- 如果已有文档但未构建知识库，先构建知识库
- 如果已有功能点但未生成测试用例，先生成测试用例
- 对每个测试用例逐一执行 plan_and_execute，然后 verify_results
- 如果验证失败，可以对同一 scenario_id 重新执行 plan_and_execute（最多重试 {max_retries} 次）
- 全部测试用例执行并验证完毕后，调用 generate_report
- 每次只输出一个动作
- args 中使用具体值，不要使用占位符

## 关键：注册优先策略
- 大多数 Web 应用需要登录才能使用功能，因此在测试任何功能前必须先完成注册
- 注册流程通常是：打开登录页 → 点击"注册"或"Sign up"按钮 → 跳转到注册页面 → 填写用户名/邮箱/密码 → 点击注册按钮
- 必须优先执行「本地账号注册」相关的测试用例（scenario_name 或步骤中包含"注册"、"register"、"Create an account" 或 "Sign up"的用例）
- Google/GitHub/OAuth/SSO 等第三方注册/登录用例只是普通认证用例，失败后继续执行其他用例；它们不能算作注册前置成功，也不能因为失败而停止整个测试套件
- 如果测试用例列表中没有注册用例，planner 应选择第一个登录用例执行，但在执行时 plan_and_execute 会自动处理（先访问登录页，寻找注册链接）
- 注册时使用固定测试账号：{fixed_account_label()}
- 注册成功后，后续所有登录用例都使用邮箱 "{TEST_ACCOUNT_EMAIL}" 和密码 "{TEST_ACCOUNT_PASSWORD}"；用户名 "{TEST_ACCOUNT_USERNAME}" 只用于注册页的用户名字段，不能当作密码拼接
- 执行顺序必须为：本地账号注册 → 登录 → 其他功能测试
- 如果所有登录相关测试都因认证失败而失败，说明注册或登录前置条件未完成，需要先收敛前置流程再继续"""


def build_replanner_prompt(max_retries: int = 2) -> str:
    """构建 replanner 提示词。"""
    return f"""你是一个自动化 Web 测试的复盘者。你的任务是检查执行结果，判断是否所有工作已完成。

## 决策规则
- 如果报告已生成（response 非空），或所有测试用例已执行并验证完毕，则在 response 中填写最终报告摘要
- 如果还有未完成的工作（如还有测试用例未执行/验证），则将 response 留空，让 planner 继续规划
- 在 analysis 中简要分析当前进度和执行结果

## 重试策略
- 如果某个测试用例验证失败，planner 可以重新对该用例执行 plan_and_execute（重新规划并执行）
- 最多重试 {max_retries} 次失败用例，超过后跳过继续下一个
- Google/GitHub/OAuth/SSO 等第三方注册失败不算主流程注册失败，应标记为可忽略并继续其他用例
- 如果大部分用例已通过，即使有少量失败也可以进入 generate_report

## 判断完成的标准
- response 非空表示整个流程结束
- 如果 generate_report 已执行成功，response 应包含报告路径信息
- 如果中间步骤失败但仍可继续，response 留空让 planner 调整策略"""
