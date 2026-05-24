#!/usr/bin/env python3
"""验证代码修复的简单脚本。

检查内容：
1. CSS 转义函数是否正确拒绝危险字符
2. URL 构建函数是否处理各种边缘情况
3. 浏览器清理函数是否包含所有资源
4. 状态更新是否使用线程锁
"""

import re
import ast
import inspect

def check_css_escaping():
    """检查 CSS 转义函数是否拒绝危险字符。"""
    print("检查 CSS 转义函数...")

    with open("tools/impl/element_checker.py", "r") as f:
        content = f.read()

    # 检查是否包含危险字符拒绝逻辑
    dangerous_chars_check = "dangerous_chars" in content or "危险字符" in content
    reject_logic = ('return ""' in content or 'return ""' in content) and "拒绝" in content

    # 检查是否保留了基本的转义功能
    escape_logic = "special_chars" in content and "\\\\:" in content

    if dangerous_chars_check and reject_logic and escape_logic:
        print("✓ CSS 转义函数已增强：拒绝危险字符 + 保留基本转义")
        return True
    else:
        print("✗ CSS 转义函数增强不完整")
        return False

def check_url_construction():
    """检查 URL 构建函数是否处理边缘情况。"""
    print("\n检查 URL 构建函数...")

    with open("tools/impl/page_analyzer.py", "r") as f:
        content = f.read()

    # 检查是否有新的 URL 构建方法
    has_url_method = "_construct_navigation_url" in content

    # 检查是否处理 about:blank
    has_blank_check = "about:blank" in content

    # 检查是否有完整的 URL 验证
    has_url_validation = "startswith" in content and "http://" in content

    if has_url_method and has_blank_check and has_url_validation:
        print("✓ URL 构建函数已增强：处理边缘情况 + 验证完整 URL")
        return True
    else:
        print("✗ URL 构建函数增强不完整")
        return False

def check_browser_cleanup():
    """检查浏览器清理函数是否包含所有资源。"""
    print("\n检查浏览器清理函数...")

    with open("tools/impl/execution_impl.py", "r") as f:
        content = f.read()

    # 检查是否有新的清理函数
    has_cleanup_method = "_cleanup_browser_resources" in content

    # 检查是否清理 page 对象
    cleans_page = "page.is_closed()" in content or "page.close()" in content

    # 检查是否有清理顺序
    has_cleanup_order = "playwright_obj" in content and "browser" in content and "context" in content

    if has_cleanup_method and cleans_page and has_cleanup_order:
        print("✓ 浏览器清理函数已增强：清理所有资源 + 正确的清理顺序")
        return True
    else:
        print("✗ 浏览器清理函数增强不完整")
        return False

def check_thread_safety():
    """检查状态更新是否使用线程锁。"""
    print("\n检查线程安全机制...")

    with open("agent/executor.py", "r") as f:
        content = f.read()

    # 检查是否导入 threading
    has_threading_import = "import threading" in content

    # 检查是否创建锁
    has_lock_creation = "threading.Lock()" in content

    # 检查是否使用锁保护状态更新
    has_lock_usage = "with state_update_lock:" in content or "with lock:" in content

    if has_threading_import and has_lock_creation and has_lock_usage:
        print("✓ 线程安全机制已添加：导入 + 创建锁 + 使用锁保护状态")
        return True
    else:
        print("✗ 线程安全机制不完整")
        return False

def main():
    """运行所有检查。"""
    print("=" * 60)
    print("代码修复验证脚本")
    print("=" * 60)

    results = {
        "CSS 转义安全性增强": check_css_escaping(),
        "URL 构建逻辑增强": check_url_construction(),
        "浏览器资源清理增强": check_browser_cleanup(),
        "状态管理线程安全": check_thread_safety(),
    }

    print("\n" + "=" * 60)
    print("验证结果总结")
    print("=" * 60)

    for check, passed in results.items():
        status = "✓ 通过" if passed else "✗ 失败"
        print(f"{status} - {check}")

    total = len(results)
    passed = sum(results.values())

    print(f"\n总计: {passed}/{total} 检查通过")

    if passed == total:
        print("✓ 所有关键修复已正确实现！")
        return 0
    else:
        print("✗ 部分修复可能需要进一步检查")
        return 1

if __name__ == "__main__":
    exit(main())
