from langchain_core.tools import tool
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI  # 以 OpenAI 为例
from langchain_core.messages import SystemMessage, HumanMessage
import json

from pydantic import BaseModel, Field
from typing import List, Any


class VerificationDetails(BaseModel):
    success_count: int = Field(description="成功执行的步骤数量")
    total: int = Field(description="总步骤数量")
    failed_steps: List[Any] = Field(default_factory=list, description="失败的步骤列表")
    expectation_check: str = Field(description="预期结果的验证详细说明")


class VerificationResult(BaseModel):
    passed: bool = Field(description="测试用例最终是否通过")
    reason: str = Field(description="通过或失败的原因总结")
    details: VerificationDetails


class VerificationAgent:
    def __init__(self, config: dict, page):
        self.config = config
        self.page = page  # 核心：把共享的 page 存为实例属性

        # 初始化大模型
        self.llm = ChatOpenAI(model="gpt-4o", temperature=0)

        # 获取绑定了 page 的工具
        self.tools = self._get_playwright_tools()

        # 创建 LangGraph 的 ReAct Agent
        # 这里你可以自定义 state_modifier 来注入 system prompt
        self.agent = create_agent(
            model=self.llm, tools=self.tools, state_modifier=self._get_system_prompt()
        )

    def _get_playwright_tools(self) -> list:
        """
        工具组装车间 🛠️
        利用 Python 的闭包特性，让内部的工具函数能直接使用外部的 self.page
        """
        # 为了在被 @tool 装饰的函数里安全使用，提出来作为局部变量
        current_page = self.page

        @tool
        def get_page_semantics() -> str:
            """
            获取当前页面的语义化结构树（无障碍树）。
            当你想纵观全局，了解页面上有哪些按钮、文本和主要模块时，请先调用此工具。
            """
            import yaml

            try:
                # snapshot() 返回一个字典结构，转成 YAML 格式比 JSON 更省 Token 且易读
                snapshot = current_page.accessibility.snapshot()
                return yaml.dump(snapshot, allow_unicode=True)
            except Exception as e:
                return f"获取页面结构失败: {str(e)}"

        @tool
        def query_element_info(selector: str) -> str:
            """
            输入 CSS 或 XPath 选择器，获取该元素的综合状态信息。
            用于验证某个特定元素是否存在、是否可见、包含什么文本或属性。
            """
            import json

            try:
                # 使用超时短一点的定位，防止 Agent 傻等
                loc = current_page.locator(selector).first

                # 如果元素根本不存在 DOM 中
                if loc.count() == 0:
                    return json.dumps({"exists": False, "error": "元素在页面中不存在"})

                # 汇总元素的各种状态
                info = {
                    "exists": True,
                    "is_visible": loc.is_visible(),
                    "text": loc.inner_text()
                    if loc.is_visible()
                    else "元素不可见，无法提取文本",
                    "is_enabled": loc.is_enabled(),
                }

                # 可以按需补充特定属性，比如输入框的值
                tag_name = loc.evaluate("el => el.tagName.toLowerCase()")
                if tag_name in ["input", "textarea"]:
                    info["input_value"] = loc.input_value()

                return json.dumps(info, ensure_ascii=False)
            except Exception as e:
                return f"查询元素失败: {str(e)}"

        @tool
        def take_screenshot_base64() -> str:
            """
            获取当前页面的 Base64 编码截图。
            当你无法通过文本和 DOM 结构确认验证结果，或者需要验证视觉UI时使用。
            """
            import base64

            try:
                # 截图并转为 base64，可以直接发给支持多模态的 LLM
                screenshot_bytes = current_page.screenshot(type="jpeg", quality=60)
                base64_img = base64.b64encode(screenshot_bytes).decode("utf-8")
                return f"data:image/jpeg;base64,{base64_img}"
            except Exception as e:
                return f"截图失败: {str(e)}"

        # 返回装配好的工具列表
        return [take_screenshot_base64, query_element_info, get_page_semantics]

    def _get_system_prompt(self) -> str:
        return "你是一个Web自动化测试验证专家。请使用工具观察页面状态，得出验证结论并返回严格的JSON格式..."

    def _build_system_prompt(self, test_case: dict, execution_results: list) -> str:

        # 提取关键信息
        scenario_id = test_case.get("scenario_id", "Unknown")
        expectations = json.dumps(test_case.get("expectations", []), ensure_ascii=False)
        exec_results_str = json.dumps(execution_results, ensure_ascii=False)

        prompt = f"""你是一个资深的自动化 Web 测试验证专家。
    你的任务是根据测试用例的“预期结果”，结合“历史执行轨迹”，并通过使用工具观察当前真实的网页状态，来判断测试是否通过。

    【当前测试上下文】
    - 测试用例 ID: {scenario_id}
    - 预期结果 (Expectations): {expectations}
    - 历史执行轨迹: {exec_results_str}

    【验证执行规则】
    1. 分析历史轨迹: 首先检查“历史执行轨迹”中是否有前置步骤已经失败。如果前置核心步骤失败，通常预期结果也无法达成。
    2. 观察当前页面: 使用提供的 Playwright 工具（如获取页面结构、查询元素等）来验证“预期结果”是否在当前页面上真实体现。
    3. 交叉验证: 不要仅凭历史轨迹得出结论，必须结合当前页面状态（DOM元素、文本内容、可见性等）作为最终判定依据。不要凭借猜测下结论！
    4. 总结与输出: 收集到足够证据后，停止调用工具，直接输出最终验证结果。

    【最终输出格式要求】
    当你完成验证准备输出结论时，你必须**仅输出**以下严格的 JSON 格式，不要包含任何 Markdown 标记（如 ```json），也不要有任何额外的解释性废话：
    {{
        "passed": true或者false,
        "reason": "简明扼要地说明通过或失败的核心原因",
        "details": {{
            "success_count": 成功执行的步骤数量,
            "total": 总步骤数量,
            "failed_steps": ["如果有失败的步骤，列出步骤ID或名称，否则为空列表"],
            "expectation_check": "详细描述你是如何验证预期结果的，例如：'在页面上找到了登录成功后的用户名标识，与预期相符'"
        }}
    }}
    """
        return prompt

    def verify(
        self, test_case: dict, execution_results: list, execution_memory: dict
    ) -> dict:
        """
        核心验证逻辑入口
        """
        # 1. 动态生成本次验证专属的 System Prompt
        system_prompt = self._build_system_prompt(test_case, execution_results)

        # 2. 初始化带状态修饰的 ReAct Agent
        # state_modifier 相当于告诉 Graph: 在每次思考前，把这段话作为 SystemMessage 加进去
        self.agent = create_agent(
            model=self.llm, tools=self.tools, state_modifier=system_prompt
        )

        # 3. 触发 Agent 开始工作 🚀
        # 这里的输入可以是一个简单的触发词，因为所有上下文已经在 system_prompt 里了
        inputs = {
            "messages": [
                ("user", "请开始你的验证工作，并按照要求的 JSON 格式返回最终结果。")
            ]
        }

        try:
            # invoke 会执行完整的 ReAct 循环，直到 Agent 给出最终回复
            result_state = self.agent.invoke(inputs)

            # 4. 获取 Agent 的最后一条输出消息
            final_message_content = result_state["messages"][-1].content

            # 5. 格式化并校验输出 🧹
            # 简单清理可能存在的 markdown 标记
            clean_content = (
                final_message_content.strip().strip("```json").strip("```").strip()
            )

            # 尝试解析为 JSON
            raw_result = json.loads(clean_content)

            # 使用 Pydantic 进行一次强校验 (保证字典字段完全符合要求)
            validated_result = VerificationResult(**raw_result)

            return validated_result.model_dump()  # 返回你需要的标准字典

        except json.JSONDecodeError as e:
            # 兜底：Agent 没听话，没输出合法 JSON
            return {
                "passed": False,
                "reason": f"Agent 解析页面成功，但返回的格式有误无法识别。原生报错: {str(e)}",
                "details": {
                    "success_count": 0,
                    "total": len(test_case.get("steps", [])),
                    "failed_steps": ["Verification_Agent_Format_Error"],
                    "expectation_check": final_message_content[
                        :200
                    ],  # 保留一部分原始输出供排查
                },
            }
        except Exception as e:
            # 兜底：Playwright 崩溃、大模型超时等异常
            return {
                "passed": False,
                "reason": f"验证代理执行期间发生系统级异常: {str(e)}",
                "details": {
                    "success_count": 0,
                    "total": 0,
                    "failed_steps": ["Verification_Agent_Exception"],
                    "expectation_check": "Agent 运行崩溃",
                },
            }

    def _build_html_report_prompt(
        self, state_summary: dict, html_template: str = ""
    ) -> str:
        import json

        state_json = json.dumps(state_summary, ensure_ascii=False, indent=2)

        # 如果以后有了具体的模板，可以把 html_template 拼接到这里
        template_instruction = (
            f"请参考以下基础 HTML 模板结构进行渲染：\n{html_template}\n"
            if html_template
            else "请使用美观、现代的内联 CSS 样式（如浅色背景、圆角卡片、状态标签带颜色等）自由发挥构建 HTML。"
        )

        prompt = f"""你是一个资深的前端开发和数据可视化专家 🎨。
    你的任务是根据提供的自动化 Web 测试状态数据，生成一份结构清晰、美观易读的 HTML 测试报告。

    【测试状态数据】
    {state_json}

    【生成要求】
    1. 报告应包含：
       - 顶部概览：测试目标 URL、用户原始需求、总体测试通过率等。
       - 详细列表：以卡片或表格形式展示每个测试用例（scenario_id）的执行详情和最终验证（Verification）结果。
       - 对于 passed: true 的用例使用绿色标识 ✅，passed: false 的使用红色标识 ❌。
    2. {template_instruction}
    3. **严格输出格式**：你必须且只能输出原生、合法的 HTML 代码。不要包含 ```html 这样的 Markdown 代码块标记，不要有任何开场白或结尾的解释废话。直接以 <!DOCTYPE html> 开头。
    """
        return prompt

    def visualize(self, state: dict) -> str:
        """生成可视化报告。

        Args:
            state: 完整的 AgentState 字典

        Returns:
            报告文件路径
        """
        import datetime
        import os

        # 1. 数据“瘦身”与提取 ✂️
        # 过滤掉体积过大且不需要展示的字段（如 chroma_dir, documents 等）
        report_data = {
            "meta_info": {
                "target_url": state.get("target_url"),
                "user_input": state.get("input"),
                "generated_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
            # 提取核心的验证结果
            "verification_results": state.get("verification_results", {}),
            # 可选：附带一点执行过程的结果，方便报告里展示具体的失败步骤
            "execution_summary": {
                k: {"total_steps": len(v), "status": "finished"}
                for k, v in state.get("execution_results", {}).items()
            },
        }

        # 2. 构建 Prompt 🧠
        # html_template 预留着，等你之后写好模板可以直接传进去
        system_prompt = self._build_html_report_prompt(report_data, html_template="")

        # 3. 单次调用大模型生成 HTML ⚡
        try:
            response = self.llm.invoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content="请根据数据生成 HTML 测试报告。"),
                ]
            )

            html_content = response.content

            # 4. 清理大模型可能带上的 markdown 标记 🧹
            html_content = html_content.strip()
            if html_content.startswith("```html"):
                html_content = html_content[7:]
            if html_content.endswith("```"):
                html_content = html_content[:-3]
            html_content = html_content.strip()

            # 5. 保存到本地文件 💾
            reports_dir = "reports"
            os.makedirs(reports_dir, exist_ok=True)

            # 生成带时间戳的文件名，防止覆盖
            timestamp = datetime.now().strftime("%Y%md_%H%M%S")
            file_path = os.path.join(reports_dir, f"test_report_{timestamp}.html")

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(html_content)

            print(f"✨ 可视化报告已生成: {file_path}")
            return file_path

        except Exception as e:
            print(f"❌ 生成报告时发生错误: {str(e)}")
            # 兜底：如果模型出错，生成一个简单的错误提示 HTML
            fallback_html = f"<html><body><h1>报告生成失败</h1><p>错误信息: {str(e)}</p></body></html>"
            fallback_path = "reports/error_report.html"
            os.makedirs("reports", exist_ok=True)
            with open(fallback_path, "w", encoding="utf-8") as f:
                f.write(fallback_html)
            return fallback_path
