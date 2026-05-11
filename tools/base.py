from abc import ABC, abstractmethod


class BaseTool(ABC):
    """所有模块 Tool 的抽象基类。"""

    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def description(self) -> str:
        ...
