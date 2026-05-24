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

        优化后的验证策略（避免不必要的 LLM 调用）：
        1. 快速路径：所有步骤成功 + 简单关键词匹配 expectations（无需 LLM）
        2. 中等路径：步骤成功但 expectations 复杂，用页面检查 + 语义增强匹配
        3. LLM 路径：步骤失败 OR expectations 无法确定，才调用 LLM 深度分析

        Returns:
            {"passed": bool, "reason": str, "details": dict}
        """
        total = len(execution_results)
        success_count = sum(1 for r in execution_results if r.get("success"))
        failed_steps = [
            r.get("step_id", "?") for r in execution_results if not r.get("success")
        ]
        expectations = test_case.get("expectations", [])
        basic_passed = (success_count == total) and (total > 0)

        # ── 快速路径：所有步骤成功 + 简单 expectations 匹配 ──
        if basic_passed and expectations:
            fast_result = self._fast_verify_expectations(expectations, execution_results)
            if fast_result is not None:
                # 快速路径能够确定结果，直接返回（无 LLM 调用）
                return {
                    "passed": fast_result["passed"],
                    "reason": fast_result["reason"],
                    "details": {
                        "success_count": success_count,
                        "total": total,
                        "failed_steps": [],
                        "expectation_check": "快速关键词匹配",
                    },
                }

        # ── LLM 路径：步骤失败 OR 快速路径无法确定 ──
        llm_result = self._llm_verify(test_case, execution_results, expectations)
        if llm_result is not None:
            return llm_result

        # ── 兜底逻辑：LLM 不可用 ──
        reason = f"成功 {success_count}/{total} 步"
        if not basic_passed:
            reason += f"，失败步骤: {failed_steps}"

        page_check = self._check_page_state(expectations)
        if page_check:
            reason = f"{reason}；页面验证: {page_check}"

        return {
            "passed": basic_passed,
            "reason": reason,
            "details": {
                "success_count": success_count,
                "total": total,
                "failed_steps": failed_steps,
                "expectation_check": page_check or "未进行页面验证",
            },
        }

    def _fast_verify_expectations(self, expectations: list[str], execution_results: list[dict]) -> dict | None:
        """快速验证 expectations（避免 LLM 调用）。

        智能策略（平衡速度和准确性）：
        1. 对于注册/登录操作：只要大部分步骤成功 + 跳转到 Dashboard → passed
        2. 检查是否有明确错误消息 → failed
        3. 对于"创建/添加"类操作，检查页面是否有新内容 → passed 或 None
        4. 其他情况 → None（让 LLM 判断）

        Returns:
            {"passed": bool, "reason": str} 或 None（需要 LLM 判断）
        """
        # 统计执行结果
        total = len(execution_results)
        success_count = sum(1 for r in execution_results if r.get("success"))

        # 获取页面文本和状态
        page_text = self._get_page_text_from_results(execution_results)
        if not page_text:
            page = self._get_page()
            if page:
                try:
                    page_text = page.locator("body").inner_text(timeout=3000)
                except PlaywrightTimeoutError:
                    # 超时是预期情况，页面可能还在加载
                    page_text = ""
                except Exception:
                    # 其他异常也忽略，保持空文本
                    pass

        page_text_lower = (page_text or "").lower()

        # 从最后一个结果中获取 URL 和 title
        last_result = execution_results[-1] if execution_results else {}
        current_url = last_result.get("url", "") or ""
        current_title = last_result.get("title", "") or ""

        # ── 1. 检查是否是注册/登录操作 ───
        expectations_text = " ".join(str(e).lower() for e in expectations)
        is_register_or_login = any(
            word in expectations_text
            for word in ["注册", "register", "登录", "login", "signin"]
        )

        # 检查是否跳转到了 Dashboard（强成功信号）
        is_dashboard = any(
            indicator in (current_url + current_title + page_text_lower).lower()
            for indicator in ["dashboard", "board", "看板", "home", "首页", "welcome", "欢迎"]
        )

        # ── 2. 注册/登录特殊处理：只要大部分成功 + 跳转 Dashboard ───
        if is_register_or_login:
            # 大部分步骤成功（>= 80%）且跳转到 Dashboard
            success_rate = success_count / total if total > 0 else 0
            if success_rate >= 0.8 and is_dashboard:
                # 跳过非关键字段（如用户名）的失败
                failed_steps = [r for r in execution_results if not r.get("success")]
                failed_details = ", ".join([f.get("result", "") for f in failed_steps[:2]])
                return {
                    "passed": True,
                    "reason": f"注册/登录成功（{success_count}/{total} 步成功，已跳转至 Dashboard。非关键字段失败可忽略：{failed_details}）",
                }
            elif is_dashboard and success_count >= 2:
                # 至少 2 步成功且跳转到 Dashboard，也算成功
                return {
                    "passed": True,
                    "reason": f"注册/登录成功（已跳转至 Dashboard，{success_count}/{total} 步成功）",
                }

        # ── 3. 检查明确错误消息（强失败信号）──
        strong_error_patterns = [
            "error:", "failed:", "incorrect:", "invalid:", "wrong:",
            "错误：", "失败：", "无效：",
        ]
        has_strong_error = any(pattern in page_text_lower for pattern in strong_error_patterns)
        if has_strong_error:
            return {
                "passed": False,
                "reason": "页面显示明确的错误消息",
            }

        # ── 4. 对于创建类操作，需要更严格的验证 ───
        is_create_operation = any(
            word in expectations_text
            for word in ["创建", "添加", "新增", "create", "add", "new"]
        )

        if is_create_operation:
            # 检查是否有新内容（如刚创建的标题）
            matched_keywords = []
            for exp in expectations:
                exp_str = str(exp)
                keywords = re.findall(r"[\w\u4e00-\u9fff]{2,}", exp_str.lower())
                for kw in keywords:
                    if kw in page_text_lower and kw not in ["页面", "成功", "完成", "page", "should"]:
                        matched_keywords.append(kw)

            if matched_keywords:
                return {
                    "passed": True,
                    "reason": f"页面包含预期内容：{matched_keywords[:3]}",
                }
            else:
                # 创建操作但无法确认新内容，让 LLM 判断
                return None

        # ── 5. 其他情况：让 LLM 判断 ───
        return None

    def _get_page_text_from_results(self, execution_results: list[dict]) -> str | None:
        """从 execution_results 中提取页面文本（使用最后一个成功步骤的页面文本）。"""
        # 优先使用最后一个成功步骤的页面文本（最能反映最终状态）
        for result in reversed(execution_results):
            if result.get("success") and result.get("page_text"):
                return str(result["page_text"])
        # 如果没有成功步骤，使用任意有 page_text 的结果
        for result in execution_results:
            if result.get("page_text"):
                return str(result["page_text"])
        return None

    def _llm_verify(self, test_case: dict, execution_results: list[dict],
                    expectations: list[str]) -> dict | None:
        """使用 LLM 对比预期结果与实际执行轨迹。LLM 不可用时返回 None。"""
        llm = self._get_llm()
        if llm is None:
            return None

        scenario_id = test_case.get("scenario_id", "Unknown")
        success_count = sum(1 for r in execution_results if r.get("success"))
        total = len(execution_results)
        failed_steps = [
            r.get("step_id", "?") for r in execution_results if not r.get("success")
        ]
        exec_summary = json.dumps([
            {
                "step_id": r.get("step_id"),
                "success": r.get("success"),
                "result": r.get("result", ""),
            }
            for r in execution_results
        ], ensure_ascii=False)

        # 获取当前页面文本（如果可用）
        page_text = ""
        page = self._get_page()
        if page:
            try:
                page_text = page.locator("body").inner_text(timeout=3000)[:2000]
            except PlaywrightTimeoutError:
                # 超时是预期情况，页面可能还在加载
                page_text = ""
            except Exception:
                # 其他异常也忽略，保持空文本
                pass

        prompt = f"""你是一个软件测试验证专家。请根据以下信息判断测试是否通过。

测试用例 ID: {scenario_id}
预期结果: {json.dumps(expectations, ensure_ascii=False)}
执行轨迹: {exec_summary}
当前页面文本（截取）: {page_text[:1500] or "（无页面信息）"}

请分析：
1. 所有执行步骤是否都成功
2. 预期结果是否在实际执行中体现
3. 当前页面状态是否与预期一致

你必须只输出以下 JSON 格式，不要包含 markdown 标记：
{{
    "passed": true或false,
    "reason": "通过或失败的核心原因",
    "details": {{
        "success_count": {success_count},
        "total": {total},
        "failed_steps": {json.dumps(failed_steps)},
        "expectation_check": "预期结果验证详细说明"
    }}
}}"""

        try:
            from langchain_core.messages import SystemMessage, HumanMessage
            response = llm.invoke([
                SystemMessage(content="你是软件测试验证专家，只输出JSON，不输出其他内容。"),
                HumanMessage(content=prompt),
            ])
            text = response.content.strip()

            # 清理 markdown 包裹（使用正则，避免截断有效内容）
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if m:
                text = m.group(1)
            else:
                # 尝试直接找 JSON 对象
                m = re.search(r"\{.*\}", text, re.DOTALL)
                if m:
                    text = m.group(0)

            raw = json.loads(text.strip())
            return {
                "passed": bool(raw.get("passed", False)),
                "reason": str(raw.get("reason", "")),
                "details": raw.get("details", {}),
            }
        except Exception as e:
            print(f"[VerificationTool] LLM 验证失败，回退到基础验证: {e}")
            return None

    def _get_llm(self):
        """获取 LLM 实例，使用项目统一的 core.llm.get_llm() 接口。"""
        try:
            from core.llm import get_llm
            return get_llm(self.config.model_name)
        except Exception:
            return None

    def _get_page(self):
        """安全获取共享 BrowserSession 的 page（可能为 None 或已关闭）。"""
        if self.session is not None and hasattr(self.session, "page"):
            page = self.session.page
            if page is not None and not page.is_closed():
                return page
        return None

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
