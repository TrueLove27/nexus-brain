"""PostgreSQL repository layer for normalized brain data.

Import concrete repos from submodules, e.g.::

    from brain.repos.entities import EntitiesRepo
"""

__all__ = [
    "ConversationRepo",
    "MessageRepo",
    "TaskRepo",
    "ToolCallRepo",
    "ToolEffectRepo",
    "PreferenceRepo",
    "JobQueueRepo",
    "JobTraceRepo",
    "MemoryTiersRepo",
    "EntitiesRepo",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    mod_map = {
        "ConversationRepo": "brain.repos.conversations",
        "MessageRepo": "brain.repos.messages",
        "TaskRepo": "brain.repos.tasks",
        "ToolCallRepo": "brain.repos.tool_calls",
        "ToolEffectRepo": "brain.repos.tool_effects",
        "PreferenceRepo": "brain.repos.preferences",
        "JobQueueRepo": "brain.repos.job_queue",
        "JobTraceRepo": "brain.repos.job_traces",
        "MemoryTiersRepo": "brain.repos.memory_tiers",
        "EntitiesRepo": "brain.repos.entities",
    }
    mod = importlib.import_module(mod_map[name])
    return getattr(mod, name)
