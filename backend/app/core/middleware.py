import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import trace_id_var
from app.core.tracing import span

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

logger = logging.getLogger("app.access")


class RequestContextMiddleware:
    """Assigns a trace id per request, echoes it in the response and writes one access log line.

    Pure ASGI (not BaseHTTPMiddleware) so streaming responses such as SSE are not buffered.
    A client-supplied X-Request-ID is reused only if it matches a conservative pattern.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = None
        for name, value in scope["headers"]:
            if name == b"x-request-id":
                incoming = value.decode("latin-1")
                break
        trace_id = incoming if incoming and _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = trace_id_var.set(trace_id)
        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = trace_id
            await send(message)

        try:
            with span(
                f"HTTP {scope['method']}", **{"http.method": scope["method"], "http.target": scope["path"]}
            ) as current:
                await self.app(scope, receive, send_wrapper)
                current.set_attribute("http.status_code", status_code)
        finally:
            logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )
            trace_id_var.reset(token)
