def __getattr__(name):
    if name == "build_agent_graph":
        from agent.graph import build_agent_graph
        return build_agent_graph
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["build_agent_graph"]
