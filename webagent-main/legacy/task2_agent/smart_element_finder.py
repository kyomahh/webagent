"""
智能元素查找器 - 使用LLM和多种策略动态查找页面元素
"""
import time
from playwright.sync_api import Page
from common_utils import call_llm


FIND_ELEMENT_PROMPT = """你是一个Web元素查找专家。根据以下页面信息和目标描述，找到最合适的元素选择器。

页面URL: {page_url}
页面标题: {page_title}

页面可见文本内容（前1000字符）:
{page_text}

页面HTML结构（相关部分）:
{page_html}

要查找的目标元素: {target_description}
元素类型: {element_type}
辅助文本: {fallback_text}

请分析页面并返回JSON格式的查找结果：
{{
  "found": true/false,
  "selector": "具体的CSS选择器",
  "strategy": "text/attribute/structure/visual",
  "confidence": 0.0-1.0,
  "alternative_selectors": ["备选选择器1", "备选选择器2"],
  "reason": "选择理由"
}}

注意：
1. 优先使用文本内容匹配（如：包含"Create an account"的链接）
2. 其次使用属性匹配（如：type="email"的输入框）
3. 最后使用结构匹配（如：form中的第一个button）
4. 返回的选择器必须是Playwright支持的CSS选择器格式
5. 如果找不到完全匹配的元素，返回最接近的元素"""


class SmartElementFinder:
    def __init__(self):
        self.find_history = []

    def _get_page_text(self, page: Page) -> str:
        """获取页面可见文本"""
        try:
            return page.evaluate("() => document.body ? document.body.textContent : ''")[:1000]
        except:
            return ""

    def _get_page_html_structure(self, page: Page) -> str:
        """获取页面HTML结构（简化版）"""
        try:
            return page.evaluate("""
                () => {
                    const body = document.body;
                    if (!body) return '';

                    // 获取交互元素的结构
                    const interactive = body.querySelectorAll('button, a, input, select, textarea, [role="button"]');
                    return Array.from(interactive).map(el => {
                        const tag = el.tagName.toLowerCase();
                        const id = el.id ? `#${el.id}` : '';
                        const className = el.className ? `.${el.className.split(' ')[0]}` : '';
                        const type = el.type ? `[type="${el.type}"]` : '';
                        const text = el.textContent?.trim().substring(0, 30) || '';
                        const placeholder = el.placeholder ? `[placeholder*="${el.placeholder}"]` : '';
                        return `<${tag}${id}${className}${type}${placeholder}>${text}</${tag}>`;
                    }).join('\\n');
                }
            """)[:2000]
        except:
            return ""

    def _use_llm_to_find_element(self, page: Page, target_description: str,
                                  element_type: str, fallback_text: str) -> dict:
        """使用LLM智能查找元素"""
        page_url = page.url
        page_title = page.title()
        page_text = self._get_page_text(page)
        page_html = self._get_page_html_structure(page)

        prompt = FIND_ELEMENT_PROMPT.format(
            page_url=page_url,
            page_title=page_title,
            page_text=page_text,
            page_html=page_html,
            target_description=target_description,
            element_type=element_type,
            fallback_text=fallback_text
        )

        try:
            result = call_llm(prompt, "find_element", max_retries=2)
            # 解析JSON结果
            if "```json" in result:
                result = result.split("```json")[1].split("```")[0]
            elif "```" in result:
                result = result.split("```")[1].split("```")[0]

            find_result = eval(result)  # 简化解析，实际应该用json.loads
            self.find_history.append({
                "target": target_description,
                "result": find_result,
                "page_url": page_url
            })
            return find_result
        except Exception as e:
            print(f"LLM查找失败: {e}")
            return {"found": False, "selector": "", "strategy": "none"}

    def _try_text_based_finding(self, page: Page, text: str, element_type: str) -> str:
        """基于文本的元素查找"""
        text = text.strip()
        if not text:
            return ""

        # 尝试不同的文本匹配策略
        strategies = [
            f"text='{text}'",
            f":has-text('{text}')",
            f"[placeholder*='{text}']",
            f"value='{text}'"
        ]

        for strategy in strategies:
            try:
                element = page.query_selector(strategy)
                if element and element.is_visible():
                    return strategy
            except:
                continue

        return ""

    def _try_type_based_finding(self, page: Page, element_type: str) -> str:
        """基于类型的元素查找"""
        type_mapping = {
            "button": "button",
            "input": "input",
            "link": "a",
            "select": "select",
            "textarea": "textarea"
        }

        tag = type_mapping.get(element_type.lower(), "")
        if not tag:
            return ""

        try:
            # 查找第一个可见的该类型元素
            elements = page.query_selector_all(tag)
            for elem in elements:
                if elem.is_visible():
                    return tag  # 返回标签选择器，Playwright会自动选择第一个
        except:
            pass

        return ""

    def find_element(self, page: Page, target_description: str = "",
                     element_type: str = "", fallback_text: str = "",
                     use_llm: bool = True) -> tuple:
        """
        智能查找页面元素

        返回: (selector: str, strategy: str, found: bool)
        """
        # 1. 首先尝试基于文本的查找
        if fallback_text:
            text_selector = self._try_text_based_finding(page, fallback_text, element_type)
            if text_selector:
                return text_selector, "text_match", True

        # 2. 尝试基于类型的查找
        if element_type:
            type_selector = self._try_type_based_finding(page, element_type)
            if type_selector:
                return type_selector, "type_match", True

        # 3. 使用LLM智能查找
        if use_llm and target_description:
            llm_result = self._use_llm_to_find_element(page, target_description, element_type, fallback_text)
            if llm_result.get("found") and llm_result.get("selector"):
                selector = llm_result["selector"]
                # 验证选择器是否有效
                try:
                    element = page.query_selector(selector)
                    if element and element.is_visible():
                        return selector, llm_result.get("strategy", "llm"), True
                except:
                    pass

                # 尝试备选选择器
                for alt_selector in llm_result.get("alternative_selectors", []):
                    try:
                        element = page.query_selector(alt_selector)
                        if element and element.is_visible():
                            return alt_selector, "llm_alternative", True
                    except:
                        continue

        return "", "not_found", False


# 全局实例
_element_finder = None

def get_element_finder():
    """获取元素查找器实例"""
    global _element_finder
    if _element_finder is None:
        _element_finder = SmartElementFinder()
    return _element_finder
