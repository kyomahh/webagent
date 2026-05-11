import os
from dataclasses import dataclass


@dataclass
class AgentConfig:
    target_url: str = "https://demo.4gaboards.com/"
    manual_url: str = "https://docs.4gaboards.com/"
    model_name: str = "glm-4-flash"
    embedding_model: str = "embedding-3"
    chroma_dir: str = "chroma_db"
    output_dir: str = "output"
    max_retries: int = 2
    headless: bool = False


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_config() -> AgentConfig:
    return AgentConfig(
        chroma_dir=os.path.join(ROOT_DIR, "chroma_db"),
        output_dir=os.path.join(ROOT_DIR, "output"),
    )
