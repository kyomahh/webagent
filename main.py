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
import os
import sys

from dotenv import load_dotenv

load_dotenv()


def main():
    parser = argparse.ArgumentParser(
        description="WebAgent - 基于大模型的测试场景生成与智能测试工具"
    )

    parser.add_argument(
        "--url", type=str, default="https://demo.4gaboards.com/",
        help="目标网站 URL (默认: https://demo.4gaboards.com/)",
    )
    parser.add_argument(
        "--manual", type=str, default=None,
        help="用户手册网站 URL (如: https://docs.4gaboards.com/)",
    )
    parser.add_argument(
        "--manual-dir", type=str, default=None,
        help="本地手册目录路径 (如: ./manual)",
    )
    parser.add_argument(
        "--model", type=str, default="glm-4-flash",
        choices=["glm-4-flash", "glm-4-plus", "deepseek-chat", "qwen-plus"],
        help="使用的 LLM 模型 (默认: glm-4-flash)",
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

    args = parser.parse_args()

    # 可视化模式
    if args.visualize:
        report_path = os.path.join(os.path.dirname(__file__), "output", "report_stub.json")
        viz_script = os.path.join(os.path.dirname(__file__), "tools", "stub", "visualize_stub.py")
        if os.path.exists(viz_script):
            os.system(f"streamlit run {viz_script}")
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
            execution_tool = get_execution_tool(config)
            verification_tool = get_verification_tool(config)
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
        "documents": [],
        "features": [],
        "test_cases": [],
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


if __name__ == "__main__":
    main()
