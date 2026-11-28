import threading
import weakref

from app.services.blueprint_loader import load_blueprints


_lock = threading.RLock()
_troubleshooters = weakref.WeakSet()


def register_troubleshooter(troubleshooter) -> None:
    with _lock:
        _troubleshooters.add(troubleshooter)


def reindex() -> bool:
    """Rebuild live troubleshooters' retrievers; return whether any were updated."""
    blueprints = load_blueprints(eligible_only=True)
    with _lock:
        troubleshooters = list(_troubleshooters)
    for troubleshooter in troubleshooters:
        troubleshooter.replace_retriever(blueprints)
    return bool(troubleshooters)