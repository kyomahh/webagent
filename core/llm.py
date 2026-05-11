import os

from langchain_openai import ChatOpenAI

_MODEL_CONFIGS = {
    "glm-4-flash": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPUAI_API_KEY",
    },
    "glm-4-plus": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPUAI_API_KEY",
    },
    "deepseek-chat": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "qwen-plus": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
    },
}


def get_llm(model_name: str = "glm-4-flash", temperature: float = 0.1) -> ChatOpenAI:
    """获取 LLM 实例，通过配置切换不同国产模型。

    支持的模型: glm-4-flash, glm-4-plus, deepseek-chat, qwen-plus
    对应的 API Key 环境变量在 .env 文件中配置。
    """
    config = _MODEL_CONFIGS.get(model_name, _MODEL_CONFIGS["glm-4-flash"])
    return ChatOpenAI(
        model=model_name,
        base_url=config["base_url"],
        api_key=os.environ.get(config["api_key_env"], ""),
        temperature=temperature,
    )
