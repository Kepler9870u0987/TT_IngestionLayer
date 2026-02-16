"""
Graceful shutdown manager with ordered cleanup callbacks.
Centralizes signal handling and resource cleanup for producer and worker.
"""
import signal
import threading
import time
from typing import Callable, List, Tuple, Optional
from enum import Enum

from src.common.logging_config import get_logger

logger = get_logger(__name__)


class ShutdownState(Enum):
    """Shutdown manager states"""
    RUNNING = "running"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"


class ShutdownManager:
    """
    Centralized shutdown manager with ordered cleanup.
    Registers cleanup callbacks with priorities and executes them in order.

    Priority levels (lower = executed first):
        0-9:   Stop accepting new work (close listeners)
        10-19: Wait for in-flight work to complete
        20-29: Flush buffers, save state
        30-39: Close external connections (IMAP, Redis)
        40-49: Final cleanup (logs, temp files)

    Usage:
        shutdown = ShutdownManager(timeout=30)
        shutdown.register(lambda: imap.disconnect(), priority=30, name="IMAP")
        shutdown.register(lambda: redis.close(), priority=35, name="Redis")
        shutdown.install_signal_handlers()

        # In main loop:
        while shutdown.is_running:
            do_work()
    """

    _instance: Optional['ShutdownManager'] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """Singleton pattern - only one shutdown manager per process."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, timeout: int = 30):
        """
        Initialize shutdown manager.

        Args:
            timeout: Maximum seconds to wait for cleanup (default: 30)
        """
        if self._initialized:
            return
        self._initialized = True

        self.timeout = timeout
        self.state = ShutdownState.RUNNING
        self._callbacks: List[Tuple[int, str, Callable]] = []
        self._state_lock = threading.Lock()
        self._shutdown_event = threading.Event()

        logger.info(f"ShutdownManager initialized (timeout={timeout}s)")

    @classmethod
    def reset(cls):
        """Reset singleton instance (for testing)."""
        with cls._lock:
            cls._instance = None

    @property
    def is_running(self) -> bool:
        """Check if system is still running (not shutting down)."""
        return self.state == ShutdownState.RUNNING

    @property
    def is_shutting_down(self) -> bool:
        """Check if shutdown is in progress."""
        return self.state == ShutdownState.SHUTTING_DOWN

    def register(
        self,
        callback: Callable,
        priority: int = 20,
        name: str = "unnamed"
    ) -> None:
        """
        Register a cleanup callback.

        Args:
            callback: Function to call during shutdown (no args)
            priority: Execution priority (lower = earlier, 0-49)
            name: Descriptive name for logging
        """
        self._callbacks.append((priority, name, callback))
        self._callbacks.sort(key=lambda x: x[0])
        logger.debug(f"Registered shutdown callback: {name} (priority={priority})")

    def unregister(self, name: str) -> bool:
        """
        Unregister a cleanup callback by name.

        Args:
            name: Name of callback to remove

        Returns:
            True if callback was found and removed
        """
        before = len(self._callbacks)
        self._callbacks = [
            (p, n, cb) for p, n, cb in self._callbacks if n != name
        ]
        removed = len(self._callbacks) < before
        if removed:
            logger.debug(f"Unregistered shutdown callback: {name}")
        return removed

    def install_signal_handlers(self) -> None:
        """Install SIGINT and SIGTERM handlers."""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        logger.info("Signal handlers installed (SIGINT, SIGTERM)")

    def _signal_handler(self, signum: int, frame) -> None:
        """
        Handle shutdown signals.

        Args:
            signum: Signal number
            frame: Current stack frame
        """
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name} (signal {signum}), initiating shutdown...")
        self.initiate_shutdown()

    def initiate_shutdown(self) -> None:
        """
        Begin the shutdown process.
        Thread-safe - can be called from signal handlers or other threads.
        """
        with self._state_lock:
            if self.state != ShutdownState.RUNNING:
                logger.warning("Shutdown already in progress, ignoring")
                return
            self.state = ShutdownState.SHUTTING_DOWN

        self._shutdown_event.set()
        logger.info(f"Shutdown initiated, executing {len(self._callbacks)} callbacks...")

        self._execute_callbacks()

        with self._state_lock:
            self.state = ShutdownState.STOPPED

        logger.info("Shutdown complete")

    def _execute_callbacks(self) -> None:
        """Execute all registered callbacks in priority order with timeout."""
        start_time = time.time()

        for priority, name, callback in self._callbacks:
            elapsed = time.time() - start_time
            remaining = self.timeout - elapsed

            if remaining <= 0:
                logger.error(
                    f"Shutdown timeout ({self.timeout}s) exceeded, "
                    f"skipping remaining callbacks"
                )
                break

            logger.info(f"Executing shutdown callback: {name} (priority={priority})")

            try:
                callback()
                logger.info(f"Callback completed: {name}")
            except Exception as e:
                logger.error(f"Callback failed: {name} - {e}")

        total_time = time.time() - start_time
        logger.info(f"All callbacks executed in {total_time:.2f}s")

    def wait_for_shutdown(self, timeout: Optional[float] = None) -> bool:
        """
        Block until shutdown is initiated.

        Args:
            timeout: Max time to wait (None = indefinite)

        Returns:
            True if shutdown was initiated, False if timeout
        """
        return self._shutdown_event.wait(timeout=timeout)

    def get_status(self) -> dict:
        """
        Get shutdown manager status.

        Returns:
            Status dictionary
        """
        return {
            "state": self.state.value,
            "is_running": self.is_running,
            "callbacks_registered": len(self._callbacks),
            "callback_names": [name for _, name, _ in self._callbacks],
            "timeout": self.timeout
        }
