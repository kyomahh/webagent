"""共享浏览器会话 —— 在执行模块和验证模块间传递同一个 Playwright Page。

使用方式：
    1. main.py 创建 BrowserSession（空壳，不启动浏览器）
    2. 传给执行模块和验证模块的构造函数
    3. 执行模块调用 ensure_page() 懒启动浏览器，获得 page
    4. 验证模块通过 session.page 获取同一个 page 做实时验证
    5. main.py 在整个流程结束后调用 session.close() 统一清理
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright


class BrowserSession:
    """在执行模块和验证模块间共享同一个浏览器 page。"""

    def __init__(self):
        self.page: Page | None = None
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._lock = threading.Lock()

    def ensure_page(self, headless: bool = False, target_url: str | None = None) -> Page:
        """懒启动：第一次调用时才启动浏览器，后续调用返回同一个 page。

        如果 page 已关闭（如浏览器崩溃），会自动重建。

        Args:
            headless: 是否无头模式
            target_url: 自动导航的目标URL（可选，避免 about:blank 状态）

        Returns:
            可用的 Playwright Page 对象
        """
        with self._lock:
            if self.page is not None and not self.page.is_closed():
                return self.page

            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=headless,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                ignore_https_errors=True,
                java_script_enabled=True,
            )
            self.page = self._context.new_page()

            # 如果提供了 target_url，自动导航过去，避免 about:blank 状态
            if target_url:
                try:
                    self.page.goto(target_url, timeout=10000)
                    print(f"[BrowserSession] 自动导航到: {target_url}")
                except Exception as e:
                    print(f"[BrowserSession] 自动导航失败: {e}，使用空白页面")

            return self.page

    def close(self) -> None:
        """统一清理浏览器资源。在 main.py 的 finally 中调用。"""
        for obj in [self.page, self._context, self._browser]:
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self.page = None
        self._context = None
        self._browser = None
        self._playwright = None
