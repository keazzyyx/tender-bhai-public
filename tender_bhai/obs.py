"""neatlogs instrumentation with a no-op fallback when NEATLOGS_API_KEY is unset.

Verified against neatlogs 1.4.27: ``init``, ``span(kind, name, *, tool_name, role,
session_id, ...)``, ``trace(name, session_id=...)``, ``flush(timeout_millis)`` and
``shutdown(timeout_millis)``.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Callable, Iterator
from typing import Any, TypeVar

log = logging.getLogger(__name__)
F = TypeVar("F", bound=Callable[..., Any])

_enabled = False
_initialised = False


def _noop_decorator(*_args: Any, **_kwargs: Any) -> Callable[[F], F]:
    def wrap(fn: F) -> F:
        return fn

    return wrap


@contextlib.contextmanager
def _noop_context(*_args: Any, **_kwargs: Any) -> Iterator[None]:
    yield None


def init(workflow_name: str = "tender-bhai", tags: list[str] | None = None) -> bool:
    """Initialise neatlogs once. Returns True when real export is active."""
    global _enabled, _initialised
    if _initialised:
        return _enabled
    _initialised = True
    api_key = os.getenv("NEATLOGS_API_KEY", "").strip()
    if not api_key:
        log.info("NEATLOGS_API_KEY unset - observability runs as no-op")
        _enabled = False
        return False
    try:
        import neatlogs

        neatlogs.init(
            api_key=api_key,
            workflow_name=workflow_name,
            instrumentations=["anthropic"],
            tags=tags or ["prod"],
            register_shutdown_handlers=False,
        )
        _enabled = True
    except Exception as exc:  # pragma: no cover - depends on the remote service
        log.warning("neatlogs init failed, continuing without tracing: %s", exc)
        _enabled = False
    return _enabled


def enabled() -> bool:
    return _enabled


def span(kind: str, name: str | None = None, **kwargs: Any) -> Callable[[F], F]:
    """``@span(kind="TOOL", tool_name="gem")`` - neatlogs span or no-op."""
    if not _enabled:
        return _noop_decorator()
    import neatlogs

    try:
        return neatlogs.span(kind, name, **kwargs)
    except Exception as exc:  # pragma: no cover
        log.warning("neatlogs.span failed (%s); using no-op", exc)
        return _noop_decorator()


def trace(name: str, **kwargs: Any) -> contextlib.AbstractContextManager[Any]:
    """``with trace("chat", session_id=phone):`` - groups turns of one session."""
    if not _enabled:
        return _noop_context()
    import neatlogs

    try:
        return neatlogs.trace(name, **kwargs)
    except Exception as exc:  # pragma: no cover
        log.warning("neatlogs.trace failed (%s); using no-op", exc)
        return _noop_context()


def flush(timeout_millis: int = 15000) -> None:
    if not _enabled:
        return
    import neatlogs

    try:
        neatlogs.flush(timeout_millis=timeout_millis)
    except Exception as exc:  # pragma: no cover
        log.warning("neatlogs.flush failed: %s", exc)


def shutdown(timeout_millis: int = 15000) -> None:
    global _enabled
    if not _enabled:
        return
    import neatlogs

    try:
        neatlogs.shutdown(timeout_millis=timeout_millis)
    except Exception as exc:  # pragma: no cover
        log.warning("neatlogs.shutdown failed: %s", exc)
    _enabled = False


def finish() -> None:
    """flush() + shutdown() - call at the exit of every entrypoint."""
    flush()
    shutdown()
