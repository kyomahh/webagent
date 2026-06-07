"""验证与可视化模块实现 —— 组员 C 在此实现所有方法。

核心能力：
1. verify(): 基础步骤成功率检查 + LLM 对比 expectations 与实际执行轨迹 + 可选页面实时验证
2. visualize(): LLM 生成 HTML 报告（优先），模板生成报告（兜底）
3. 通过共享 BrowserSession 的 session.page 获取执行模块操作过的同一个 page
"""

from __future__ import annotations

import html as html_module
import json
import os
import re
from datetime import datetime
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from core.config import AgentConfig, default_config
from tools.verification_tool import VerificationToolInterface


class MyVerificationTool(VerificationToolInterface):
    """继承 VerificationToolInterface，实现 verify 和 visualize 两个抽象方法。

    注意：
    - tools/impl/__init__.py 中会以 MyVerificationTool(config, session) 调用；
    - tests/test_verification_tool.py 中会以 MyVerificationTool() 调用；
    因此 config 和 session 都设置为可选参数，保证两种入口都兼容。
    """

    def __init__(self, config: AgentConfig | None = None, session: Any | None = None):
        self.config = config or default_config()
        self.session = session

    def name(self) -> str:
        return "verification_tool"

    def description(self) -> str:
        return "验证与可视化模块：验证测试结果、生成可视化报告"

    # ==================================================================
    # 一、verify(): 验证测试结果
    # ==================================================================

    def verify(self, test_case: dict, execution_results: list[dict],
               execution_memory: dict) -> dict:
        """将预期（expectations）与实际执行结果对比，判断测试是否通过。

        简化策略：完全交给 LLM 进行语义分析
        - LLM 可以理解不同手册的不同表述方式
        - LLM 可以分析 execution_results 中的 exception 信息
        - LLM 可以进行真正的语义匹配，而不是关键词匹配

        Returns:
            {"passed": bool, "reason": str, "details": dict}
        """
        # 输入验证
        if test_case is None:
            test_case = {}
        if execution_results is None:
            execution_results = []
        if not isinstance(execution_results, list):
            return {
                "passed": False,
                "reason": "execution_results 参数类型错误",
                "details": {"error": "invalid_input_type"}
            }

        total = len(execution_results)

        # 处理空执行结果
        if total == 0:
            return {
                "passed": False,
                "reason": "没有执行结果可供验证",
                "details": {"total": 0, "success_count": 0, "failed_steps": []}
            }

        success_count = sum(1 for r in execution_results if r.get("success"))
        failed_steps = [
            r.get("step_id", "?") for r in execution_results if not r.get("success")
        ]
        expectations = test_case.get("expectations", [])

        # ── 直接使用 LLM 验证 ──
        llm_result = self._llm_verify(test_case, execution_results, expectations, execution_memory)
        if llm_result is not None:
            return llm_result

        # ── 兜底逻辑：LLM 不可用时的基础验证 ──
        basic_passed = (success_count == total) and (total > 0)
        reason = f"成功 {success_count}/{total} 步"
        if not basic_passed:
            reason += f"，失败步骤: {failed_steps}"

        return {
            "passed": basic_passed,
            "reason": reason + "（LLM 验证不可用，使用基础验证）",
            "details": {
                "success_count": success_count,
                "total": total,
                "failed_steps": failed_steps,
                "expectation_check": "LLM 不可用，未进行语义验证",
                "failure_type": "none" if basic_passed else "other",
                "auth_failure_permanent": False,
            },
        }

    def _llm_verify(self, test_case: dict, execution_results: list[dict],
                    expectations: list[str], execution_memory: dict | None = None) -> dict | None:
        """使用 LLM 进行语义分析验证，对比预期结果与实际执行轨迹。

        核心改进：
        1. 重点关注 execution_results 中的 exception 信息
        2. 进行真正的语义分析，而非关键词匹配
        3. 理解不同手册的不同表述方式
        """
        llm = self._get_llm()
        if llm is None:
            return None

        scenario_id = test_case.get("scenario_id", "Unknown")
        scenario_name = test_case.get("scenario_name", "")
        success_count = sum(1 for r in execution_results if r.get("success"))
        total = len(execution_results)

        # 构建详细的执行轨迹，包含 exception 信息
        exec_details = []
        for idx, r in enumerate(execution_results):
            detail = {
                "step_id": r.get("step_id"),
                "action": r.get("action", ""),
                "success": r.get("success", False),
            }

            # 如果失败，添加 exception 和 error 信息
            if not r.get("success"):
                if r.get("exception"):
                    detail["exception"] = str(r["exception"])[:500]  # 限制长度
                if r.get("error"):
                    detail["error"] = str(r["error"])[:500]

            # 如果成功，添加 result 和 page_text
            if r.get("success"):
                if r.get("result"):
                    detail["result"] = str(r["result"])[:300]
                # 只在最后几步添加 page_text，避免 token 过大
                if r.get("page_text") and idx >= len(execution_results) - 3:
                    detail["page_snippet"] = str(r["page_text"])[:500]

            exec_details.append(detail)

        page_info = self._get_page_info(execution_memory or {})

        prompt = f"""你是一个软件测试验证专家。请通过语义分析判断测试是否通过。

测试用例信息：
- ID: {scenario_id}
- 名称: {scenario_name}
- 预期结果: {json.dumps(expectations, ensure_ascii=False)}

执行结果：
- 总步骤数: {total}
- 成功步骤数: {success_count}
- 失败步骤数: {total - success_count}

详细执行轨迹（包含 exception 信息）:
{json.dumps(exec_details, ensure_ascii=False, indent=2)}

当前页面状态:
{json.dumps(page_info, ensure_ascii=False, indent=2)}

请进行语义分析，判断测试是否通过：

**判断标准**：
1. **步骤成功率**：是否所有关键步骤都成功执行
2. **Exception 分析**：
   - 如果 exception 是"元素未找到"，说明页面状态不符合预期
   - 如果 exception 是"超时"，说明页面响应慢或卡住
   - 如果 exception 是"验证失败"，说明操作未达到预期效果
	3. **预期结果匹配**：
	   - 通过语义分析判断当前页面是否体现预期结果
	   - 例如：预期登录成功，当前页面状态体现已进入登录后区域 → 通过
	   - 例如：预期显示错误消息，页面出现与失败原因一致的提示 → 通过
	4. **不同表述的理解**：
	   - 对中英文、多语言和同义表达按业务含义理解，不要逐字匹配固定词表

	**输出格式**（严格 JSON，不要 markdown）：
	{{
	    "passed": true或false,
	    "reason": "基于语义分析的判断原因（50-100字）",
	    "details": {{
	        "success_count": {success_count},
	        "total": {total},
	        "failed_steps": [失败步骤ID列表],
	        "exception_analysis": "对关键exception的分析",
	        "expectation_match": "预期结果与实际结果的语义匹配说明",
	        "failure_type": "none|auth_failure|page_state|element_missing|timeout|data_conflict|other",
	        "auth_failure_permanent": true或false
	    }}
	}}

	字段说明：
	- failure_type 必须通过执行轨迹和页面状态做语义判断，不要依赖固定错误文案；通过时填 none。
	- 只有当失败原因是登录/认证流程阻止进入目标功能时，failure_type 才填 auth_failure。
	- auth_failure_permanent 只在当前证据能说明所给账号或凭据本身不可用于认证时填 true；加载中、网络慢或证据不足时填 false。"""

        try:
            from langchain_core.messages import SystemMessage, HumanMessage
            response = llm.invoke([
                SystemMessage(content="你是软件测试验证专家。只输出JSON格式，不要包含markdown标记。"),
                HumanMessage(content=prompt),
            ])
            text = response.content.strip()

            # 清理 markdown 包裹
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if m:
                text = m.group(1)
            else:
                m = re.search(r"\{.*\}", text, re.DOTALL)
                if m:
                    text = m.group(0)

            raw = json.loads(text.strip())
            passed = bool(raw.get("passed", False))
            details = raw.get("details", {})
            if not isinstance(details, dict):
                details = {}
            details.setdefault("failure_type", "none" if passed else "other")
            details.setdefault("auth_failure_permanent", False)
            return {
                "passed": passed,
                "reason": str(raw.get("reason", "")),
                "details": details,
            }
        except Exception as e:
            print(f"[VerificationTool] LLM 验证失败: {e}")
            return None

    def _get_llm(self):
        """获取 LLM 实例，使用项目统一的 core.llm.get_llm() 接口。"""
        try:
            from core.llm import get_llm
            timeout = float(os.environ.get("VERIFICATION_LLM_TIMEOUT_SECONDS", "60"))
            return get_llm(self.config.model_name, timeout=timeout, max_retries=1)
        except Exception:
            return None

    def _get_page(self):
        """安全获取共享 BrowserSession 的 page（可能为 None 或已关闭）。"""
        if self.session is not None and hasattr(self.session, "page"):
            page = self.session.page
            if page is not None and not page.is_closed():
                return page
        return None

    def _get_page_info(self, execution_memory: dict) -> dict:
        """获取验证用页面证据，优先实时 page，其次 Browser-use 写入的状态。"""
        page = self._get_page()
        if page:
            try:
                return {
                    "url": page.url,
                    "title": page.title(),
                    "text_snippet": page.locator("body").inner_text(timeout=3000)[:1500],
                    "source": "playwright_page",
                }
            except Exception as exc:
                return {"error": f"无法获取 Playwright 页面信息: {exc}"}

        if self.session is not None:
            browser_use_state = getattr(self.session, "browser_use_state", None)
            if isinstance(browser_use_state, dict) and browser_use_state:
                return dict(browser_use_state)

        browser_use_page = execution_memory.get("browser_use_page")
        if isinstance(browser_use_page, dict) and browser_use_page:
            return dict(browser_use_page)

        page_states = execution_memory.get("page_states", [])
        if isinstance(page_states, list) and page_states:
            latest = page_states[-1]
            if isinstance(latest, dict):
                return {
                    "url": latest.get("url", ""),
                    "title": latest.get("title", ""),
                    "text_snippet": latest.get("text", ""),
                    "source": "execution_memory.page_states",
                }

        return {}

    def _check_page_state(self, expectations: list[str]) -> str | None:
        """通过共享 page 检查当前页面状态是否匹配预期。"""
        page = self._get_page()
        if not page:
            return None

        try:
            body_text = page.locator("body").inner_text(timeout=3000)
        except PlaywrightTimeoutError:
            # 超时是预期情况，页面可能还在加载
            return None
        except Exception:
            # 其他异常也忽略
            return None

        checks = []
        for exp in expectations:
            exp_str = str(exp)
            keywords = re.findall(r"[\w\u4e00-\u9fff]{2,}", exp_str)
            found = [kw for kw in keywords if kw in body_text]
            if found:
                checks.append(f"页面包含 '{', '.join(found[:3])}'，与预期 '{exp_str[:50]}' 相关")
            else:
                checks.append(f"页面未找到与 '{exp_str[:50]}' 相关的内容")

        return "；".join(checks) if checks else None

    # ==================================================================
    # 二、visualize(): 生成可视化报告
    # ==================================================================

    def visualize(self, state: dict) -> str:
        """生成可视化 HTML 报告。

        策略：优先使用 LLM 生成美观的 HTML；LLM 不可用时回退到模板生成。
        """
        output_dir = getattr(self.config, "output_dir", "output") or "output"
        os.makedirs(output_dir, exist_ok=True)

        report_data = self._prepare_report_data(state)

        # 优先使用 LLM 生成报告
        report_path = self._llm_generate_report(report_data, output_dir)
        if report_path:
            return report_path

        # LLM 不可用，回退到模板报告
        return self._template_generate_report(report_data, output_dir)

    def _prepare_report_data(self, state: dict) -> dict:
        """提取和整理报告数据。"""
        test_cases = state.get("test_cases", [])
        verification_results = state.get("verification_results", {})
        execution_results = state.get("execution_results", {})

        passed_count = sum(1 for v in verification_results.values() if v.get("passed"))
        total_count = len(verification_results)

        return {
            "generated_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "target_url": state.get("target_url", ""),
            "user_input": state.get("input", ""),
            "pass_rate": f"{passed_count}/{total_count}" if total_count > 0 else "N/A",
            "passed_count": passed_count,
            "total_count": total_count,
            "test_cases": test_cases,
            "verification_results": verification_results,
            "execution_results": execution_results,
        }

    def _llm_generate_report(self, report_data: dict, output_dir: str) -> str | None:
        """使用 LLM 生成 HTML 报告。LLM 不可用时返回 None。"""
        llm = self._get_llm()
        if llm is None:
            return None

        # 截断大数据避免超出 token 限制
        summary_data = {
            "generated_time": report_data["generated_time"],
            "pass_rate": report_data["pass_rate"],
            "passed_count": report_data["passed_count"],
            "total_count": report_data["total_count"],
            "results": {
                sid: {"passed": v.get("passed"), "reason": v.get("reason", "")}
                for sid, v in report_data["verification_results"].items()
            },
            "test_cases": [
                {"id": tc.get("scenario_id"), "name": tc.get("scenario_name")}
                for tc in report_data.get("test_cases", [])
            ],
        }

        prompt = f"""根据以下测试数据生成一份 HTML 测试报告。

测试数据:
{json.dumps(summary_data, ensure_ascii=False, indent=2)}

要求：
1. 使用内联 CSS，美观现代（浅色背景、圆角卡片、状态标签带颜色）
2. 包含：顶部概览（通过率）、详细用例列表
3. 通过用例绿色标识，失败用例红色标识
4. 只输出合法 HTML，从 <!DOCTYPE html> 开始，不要 markdown 标记"""

        try:
            from langchain_core.messages import SystemMessage, HumanMessage
            response = llm.invoke([
                SystemMessage(content="你是前端开发专家，只输出HTML代码，不要任何解释。"),
                HumanMessage(content=prompt),
            ])
            html_content = response.content.strip()

            # 清理 markdown 包裹
            if html_content.startswith("```html"):
                html_content = html_content[7:]
            if html_content.endswith("```"):
                html_content = html_content[:-3]
            html_content = html_content.strip()

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            file_path = os.path.join(output_dir, f"report_{timestamp}.html")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(html_content)

            print(f"[VerificationTool] LLM 报告已生成: {file_path}")
            return file_path
        except Exception as e:
            print(f"[VerificationTool] LLM 报告生成失败，回退到模板报告: {e}")
            return None

    def _template_generate_report(self, report_data: dict, output_dir: str) -> str:
        """兜底：使用 HTML 模板生成报告，不依赖 LLM。"""
        passed_count = report_data["passed_count"]
        total_count = report_data["total_count"]
        pass_rate = report_data["pass_rate"]
        generated_time = report_data["generated_time"]
        target_url = report_data["target_url"]

        # 构建用例行
        rows_html = ""
        test_cases = report_data.get("test_cases", [])
        verification_results = report_data.get("verification_results", {})

        for tc in test_cases:
            sid = html_module.escape(str(tc.get("scenario_id", "")))
            name = html_module.escape(str(tc.get("scenario_name", "")))
            v = verification_results.get(tc.get("scenario_id", ""), {})
            passed = v.get("passed", False)
            reason = html_module.escape(str(v.get("reason", "")))
            status_label = "通过" if passed else "失败"
            status_color = "#4caf50" if passed else "#f44336"
            rows_html += f"""
            <tr>
                <td>{sid}</td>
                <td>{name}</td>
                <td><span style="background:{status_color};color:white;padding:2px 8px;border-radius:4px;font-size:12px;">{status_label}</span></td>
                <td>{reason}</td>
            </tr>"""

        # 如果没有 test_cases 但有 verification_results，也从 results 生成行
        if not test_cases and verification_results:
            for sid, v in verification_results.items():
                passed = v.get("passed", False)
                reason = html_module.escape(str(v.get("reason", "")))
                sid_esc = html_module.escape(str(sid))
                status_label = "通过" if passed else "失败"
                status_color = "#4caf50" if passed else "#f44336"
                rows_html += f"""
            <tr>
                <td>{sid_esc}</td>
                <td>-</td>
                <td><span style="background:{status_color};color:white;padding:2px 8px;border-radius:4px;font-size:12px;">{status_label}</span></td>
                <td>{reason}</td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WebAgent 测试报告</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; margin: 0; padding: 20px; }}
.container {{ max-width: 960px; margin: 0 auto; }}
h1 {{ color: #333; text-align: center; }}
.summary {{ background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
.summary-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; text-align: center; }}
.summary-grid .metric {{ font-size: 28px; font-weight: bold; color: #1976d2; }}
.summary-grid .label {{ font-size: 14px; color: #666; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
th {{ background: #1976d2; color: white; padding: 12px; text-align: left; }}
td {{ padding: 10px 12px; border-bottom: 1px solid #eee; }}
tr:hover {{ background: #f9f9f9; }}
.footer {{ text-align: center; color: #999; margin-top: 20px; font-size: 12px; }}
</style>
</head>
<body>
<div class="container">
<h1>WebAgent 自动化测试报告</h1>
<div class="summary">
<div class="summary-grid">
<div><div class="metric">{pass_rate}</div><div class="label">通过率</div></div>
<div><div class="metric">{passed_count}</div><div class="label">通过用例</div></div>
<div><div class="metric">{total_count - passed_count}</div><div class="label">失败用例</div></div>
</div>
</div>
<table>
<thead><tr><th>用例 ID</th><th>名称</th><th>状态</th><th>详情</th></tr></thead>
<tbody>
{rows_html}
</tbody>
</table>
<div class="footer">
<p>目标 URL: {target_url} | 生成时间: {generated_time} | 由 WebAgent 自动生成</p>
</div>
</div>
</body>
</html>"""

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        file_path = os.path.join(output_dir, f"report_{timestamp}.html")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html)

        print(f"[VerificationTool] 模板报告已生成: {file_path}")
        return file_path
