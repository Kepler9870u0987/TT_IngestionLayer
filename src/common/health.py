"""
Health check HTTP server for liveness, readiness, and status endpoints.
Runs in a separate thread to avoid blocking main processing.

Endpoints:
    GET /health  - Liveness: process is alive (always 200 if server running)
    GET /ready   - Readiness: all dependencies connected
    GET /status  - Detailed status with statistics
"""
import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, Callable, Optional, List
from datetime import datetime, timezone

from src.common.logging_config import get_logger
from src.common.circuit_breaker import CircuitBreakers

logger = get_logger(__name__)


class HealthCheck:
    """
    Represents a single health check for a dependency.

    Usage:
        def check_redis():
            redis.ping()
            return True

        health = HealthCheck("redis", check_redis, critical=True)
        result = health.run()
    """

    def __init__(
        self,
        name: str,
        check_fn: Callable[[], bool],
        critical: bool = True,
        timeout: float = 5.0
    ):
        """
        Initialize health check.

        Args:
            name: Check name (e.g., "redis", "imap")
            check_fn: Function that returns True if healthy.
                      Should raise or return False if unhealthy.
            critical: If True, failure makes service unready
            timeout: Check timeout in seconds
        """
        self.name = name
        self.check_fn = check_fn
        self.critical = critical
        self.timeout = timeout
        self.last_result: Optional[bool] = None
        self.last_check_time: Optional[float] = None
        self.last_error: Optional[str] = None
        self.consecutive_failures = 0

    def run(self) -> Dict[str, Any]:
        """
        Execute the health check.

        Returns:
            Result dictionary with status, timing, and error info
        """
        start = time.time()
        try:
            result = self.check_fn()
            elapsed = time.time() - start
            self.last_result = bool(result)
            self.last_check_time = time.time()
            self.last_error = None

            if self.last_result:
                self.consecutive_failures = 0
            else:
                self.consecutive_failures += 1
                self.last_error = "Check returned False"

            return {
                "name": self.name,
                "status": "healthy" if self.last_result else "unhealthy",
                "critical": self.critical,
                "response_time_ms": round(elapsed * 1000, 2),
                "error": self.last_error,
                "consecutive_failures": self.consecutive_failures
            }

        except Exception as e:
            elapsed = time.time() - start
            self.last_result = False
            self.last_check_time = time.time()
            self.last_error = str(e)
            self.consecutive_failures += 1

            return {
                "name": self.name,
                "status": "unhealthy",
                "critical": self.critical,
                "response_time_ms": round(elapsed * 1000, 2),
                "error": str(e),
                "consecutive_failures": self.consecutive_failures
            }


class HealthRegistry:
    """
    Registry of health checks and stats providers.
    Central point for all health-related data.
    """

    def __init__(self, component: str = "unknown"):
        """
        Initialize health registry.

        Args:
            component: Component name (e.g., "producer", "worker")
        """
        self.component = component
        self.checks: List[HealthCheck] = []
        self.stats_providers: Dict[str, Callable[[], Dict[str, Any]]] = {}
        self.start_time = time.time()

    def register_check(self, check: HealthCheck) -> None:
        """Register a health check."""
        self.checks.append(check)
        logger.debug(f"Registered health check: {check.name}")

    def register_stats_provider(
        self,
        name: str,
        provider: Callable[[], Dict[str, Any]]
    ) -> None:
        """
        Register a statistics provider.

        Args:
            name: Provider name
            provider: Function returning stats dictionary
        """
        self.stats_providers[name] = provider
        logger.debug(f"Registered stats provider: {name}")

    def run_checks(self) -> Dict[str, Any]:
        """
        Run all registered health checks.

        Returns:
            Aggregated results with overall status
        """
        results = []
        all_healthy = True

        for check in self.checks:
            result = check.run()
            results.append(result)
            if check.critical and result["status"] != "healthy":
                all_healthy = False

        return {
            "status": "healthy" if all_healthy else "unhealthy",
            "checks": results
        }

    def get_liveness(self) -> Dict[str, Any]:
        """
        Liveness check - is the process alive?

        Returns:
            Liveness status (always healthy if this code runs)
        """
        return {
            "status": "alive",
            "component": self.component,
            "uptime_seconds": round(time.time() - self.start_time, 1),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        }

    def get_readiness(self) -> Dict[str, Any]:
        """
        Readiness check - are all critical dependencies available?

        Returns:
            Readiness status with individual check results
        """
        check_results = self.run_checks()
        return {
            "status": "ready" if check_results["status"] == "healthy" else "not_ready",
            "component": self.component,
            "checks": check_results["checks"],
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        }

    def get_status(self) -> Dict[str, Any]:
        """
        Full status with health checks, stats, and circuit breakers.

        Returns:
            Comprehensive status dictionary
        """
        check_results = self.run_checks()

        # Gather stats from providers
        stats = {}
        for name, provider in self.stats_providers.items():
            try:
                stats[name] = provider()
            except Exception as e:
                stats[name] = {"error": str(e)}

        return {
            "component": self.component,
            "status": check_results["status"],
            "uptime_seconds": round(time.time() - self.start_time, 1),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "health_checks": check_results["checks"],
            "circuit_breakers": CircuitBreakers.get_all_stats(),
            "statistics": stats
        }


class HealthHTTPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for health endpoints."""

    # Class-level reference to HealthRegistry (set by HealthServer)
    registry: Optional[HealthRegistry] = None

    def do_GET(self):
        """Handle GET requests for health endpoints."""
        if self.path == "/health":
            data = self.registry.get_liveness() if self.registry else {"status": "alive"}
            self._send_json(200, data)

        elif self.path == "/ready":
            if self.registry:
                data = self.registry.get_readiness()
                status_code = 200 if data["status"] == "ready" else 503
            else:
                data = {"status": "not_ready", "error": "No registry configured"}
                status_code = 503
            self._send_json(status_code, data)

        elif self.path == "/status":
            data = self.registry.get_status() if self.registry else {"error": "No registry"}
            self._send_json(200, data)

        else:
            self._send_json(404, {"error": "Not found"})

    def _send_json(self, status_code: int, data: Dict[str, Any]):
        """Send JSON response."""
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """Suppress default access logging to avoid noise."""
        pass


class HealthServer:
    """
    Threaded HTTP server for health check endpoints.
    Runs in a daemon thread so it doesn't block shutdown.

    Usage:
        registry = HealthRegistry("worker")
        registry.register_check(HealthCheck("redis", redis.ping))

        server = HealthServer(registry, port=8080)
        server.start()
        # ... main processing ...
        server.stop()
    """

    def __init__(self, registry: HealthRegistry, port: int = 8080):
        """
        Initialize health server.

        Args:
            registry: HealthRegistry with checks and stats
            port: HTTP port to listen on
        """
        self.registry = registry
        self.port = port
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the health check server in a daemon thread."""
        # Create handler class with registry reference
        handler = type(
            'HealthHandler',
            (HealthHTTPHandler,),
            {'registry': self.registry}
        )

        try:
            self._server = HTTPServer(("0.0.0.0", self.port), handler)
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="health-server",
                daemon=True
            )
            self._thread.start()
            logger.info(f"Health server started on port {self.port}")
            logger.info(
                f"  /health - Liveness | /ready - Readiness | /status - Full status"
            )
        except OSError as e:
            logger.error(f"Failed to start health server on port {self.port}: {e}")

    def stop(self) -> None:
        """Stop the health check server."""
        if self._server:
            self._server.shutdown()
            logger.info("Health server stopped")

    @property
    def is_running(self) -> bool:
        """Check if health server is running."""
        return self._thread is not None and self._thread.is_alive()
