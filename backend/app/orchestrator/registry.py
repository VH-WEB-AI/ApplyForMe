from app.orchestrator.engine_base import Engine

_REGISTRY: dict[str, Engine] = {}


def register_engine(engine: Engine) -> None:
    _REGISTRY[engine.name] = engine


def get_engine(name: str) -> Engine:
    if name not in _REGISTRY:
        raise KeyError(f"No engine registered under name '{name}'. Known engines: {list(_REGISTRY)}")
    return _REGISTRY[name]
