"""CallBridge placeholder.

ADR-0011 owns the real tokio worker, mpsc/oneshot, and asyncio Future bridge.
This scaffold intentionally avoids implementing that behavior.
"""

from __future__ import annotations


class CallBridge:
    """Placeholder import target for future CallBridge TDD work."""

    def __init__(self) -> None:
        raise NotImplementedError("CallBridge is owned by ADR-0011 implementation work")
