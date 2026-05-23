import os
from typing import Optional, Any, Dict
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

# 加载.env文件中的环境变量（仅开发环境需要，生产环境可注释）
load_dotenv()

# 多LLM模型配置字典
# 环境变量命名规范：提供商_API_KEY（全大写，下划线分隔）
_MODEL_CONFIGS = {
    # 智谱AI系列模型
    "glm-4.7-flash": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPUAI_API_KEY",
        "default_params": {"temperature": 0.1, "max_tokens": 4096},
    },
    "glm-4-plus": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPUAI_API_KEY",
        "default_params": {"temperature": 0.1, "max_tokens": 8192},
    },
    "glm-5.1": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPUAI_API_KEY",
        "default_params": {"temperature": 0.1, "max_tokens": 8192},
    },
    # DeepSeek系列模型
    "deepseek-chat": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "default_params": {"temperature": 0.1, "max_tokens": 4096},
    },
    # 通义千问系列模型
    "qwen-plus": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "default_params": {"temperature": 0.1, "max_tokens": 4096},
    },
}

def get_llm(
    model_name: str = "glm-4.7-flash",
    temperature: Optional[float] = None,
    **kwargs: Any
) -> ChatOpenAI:
    """
    获取LangChain兼容的LLM实例，通过配置一键切换不同国产大模型。
    
    支持的模型: glm-4-flash, glm-4-plus, deepseek-chat, qwen-plus
    对应的API Key需要在环境变量或.env文件中配置。
    
    Args:
        model_name: 要使用的模型名称
        temperature: 温度参数，0-1之间，越小越确定，越大越随机
        **kwargs: 传递给ChatOpenAI的其他参数，如max_tokens, timeout, streaming等
    
    Returns:
        ChatOpenAI: LangChain的ChatOpenAI实例
    
    Raises:
        ValueError: 不支持的模型名称或未设置对应的API密钥
    """
    # 检查模型是否支持
    if model_name not in _MODEL_CONFIGS:
        supported_models = ", ".join(_MODEL_CONFIGS.keys())
        raise ValueError(
            f"不支持的模型: {model_name}\n"
            f"当前支持的模型: {supported_models}"
        )
    
    # 获取模型配置
    config = _MODEL_CONFIGS[model_name]
    
    # 读取并验证API密钥
    api_key = os.environ.get(config["api_key_env"])
    if not api_key:
        raise ValueError(
            f"未找到{model_name}对应的API密钥\n"
            f"请设置环境变量: {config['api_key_env']}\n"
            f"你可以在项目根目录的.env文件中添加: {config['api_key_env']}=你的API密钥"
        )
    
    # 合并参数：默认参数 < 函数参数 < 自定义kwargs
    final_params = config.get("default_params", {}).copy()
    if temperature is not None:
        final_params["temperature"] = temperature
    final_params.update(kwargs)
    
    # 创建并返回ChatOpenAI实例
    return ChatOpenAI(
        model=model_name,
        base_url=config["base_url"],
        api_key=api_key,
        **final_params
    )

# 使用示例
if __name__ == "__main__":
    try:
        # 1. 基本使用（使用默认参数）
        llm = get_llm("glm-4.7-flash")
        print(llm.invoke("你好，请介绍一下自己").content)
        
        # 2. 自定义温度参数
        llm = get_llm("deepseek-chat", temperature=0.7)
        
        # 3. 传递更多参数（如流式输出、最大token数）
        llm = get_llm("qwen-plus", temperature=0.5, max_tokens=2048, streaming=True)
        
    except Exception as e:
        print(f"初始化LLM失败: {e}")
