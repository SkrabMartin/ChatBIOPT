"""Executes chat runs off the request path and fans their events out to any number of SSE subscribers.

Runs are short (one model response), so an in-process asyncio task is the right tool; long scientific jobs will
need a durable queue (M10/M11). State that matters is in the database: if the process dies mid-run, startup
recovery marks the run failed (`interrupted`), and finished runs are replayed to late subscribers from the DB.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.context import build_context
from app.ai.gateway.base import GatewayError, GenerationRequest, StreamEnd, TextDelta
from app.ai.gateway.router import ModelRouter
from app.core.errors import AppError
from app.core.logging import trace_id_var
from app.models import Message, Run
from app.schemas.runs import RunRead
from app.schemas.workspace import MessageRead
from app.services.conversations import append_message

logger = logging.getLogger(__name__)

TERMINAL_EVENTS = frozenset({"completed", "failed", "cancelled"})


@dataclass(frozen=True)
class RunEvent:
    id: int
    type: str
    data: dict[str, Any]


class RunChannel:
    """Append-only event log for one run. Subscribers replay from any point, then follow live."""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []
        self.closed = False
        self._changed = asyncio.Condition()

    async def publish(self, type_: str, data: dict[str, Any]) -> None:
        async with self._changed:
            self.events.append(RunEvent(len(self.events) + 1, type_, data))
            self.closed = type_ in TERMINAL_EVENTS
            self._changed.notify_all()

    async def follow(self, after: int, heartbeat_s: float) -> AsyncIterator[RunEvent | None]:
        """Events with id > `after`; yields None as a keep-alive when nothing happened for `heartbeat_s`."""
        position = after
        while True:
            async with self._changed:
                if position >= len(self.events) and not self.closed:
                    try:
                        await asyncio.wait_for(self._changed.wait(), heartbeat_s)
                    except TimeoutError:
                        pass
                pending, closed = self.events[position:], self.closed
            for event in pending:
                position = event.id
                yield event
            if closed and position >= len(self.events):
                return
            if not pending:
                yield None


class RunManager:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        router: ModelRouter,
        *,
        max_duration_s: float,
        retain_channel_s: float = 120,
    ) -> None:
        self._session_factory = session_factory
        self._router = router
        self._max_duration_s = max_duration_s
        self._retain_channel_s = retain_channel_s
        self._channels: dict[uuid.UUID, RunChannel] = {}
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}

    def channel(self, run_id: uuid.UUID) -> RunChannel | None:
        return self._channels.get(run_id)

    def start(self, run_id: uuid.UUID) -> None:
        self._channels[run_id] = RunChannel()
        task = asyncio.create_task(self._execute(run_id, trace_id_var.get()), name=f"run-{run_id}")
        self._tasks[run_id] = task

    def cancel(self, run_id: uuid.UUID) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute(self, run_id: uuid.UUID, trace_id: str | None) -> None:
        trace_id_var.set(trace_id)
        channel = self._channels[run_id]
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._max_duration_s):
                await self._generate(run_id, channel, started)
        except GatewayError as exc:
            await self._fail(run_id, channel, started, exc.code, exc.message)
        except AppError as exc:
            await self._fail(run_id, channel, started, exc.code, exc.message)
        except TimeoutError:
            await self._fail(run_id, channel, started, "run_timeout", "The model did not finish within the time limit")
        except asyncio.CancelledError:
            await self._finish(run_id, started, status="cancelled")
            await channel.publish("cancelled", {"run": await self._run_json(run_id)})
        except Exception:
            logger.exception("run crashed", extra={"run_id": str(run_id)})
            await self._fail(run_id, channel, started, "internal_error", "Internal error while generating")
        finally:
            self._tasks.pop(run_id, None)
            # Keep the event log briefly so a client that reconnects right after the end still gets the deltas.
            asyncio.get_running_loop().call_later(self._retain_channel_s, self._channels.pop, run_id, None)

    async def _generate(self, run_id: uuid.UUID, channel: RunChannel, started: float) -> None:
        async with self._session_factory() as session:
            run = await session.get(Run, run_id)
            if run is None:  # conversation deleted before the run began
                await channel.publish("failed", {"error_code": "not_found", "message": "Run no longer exists"})
                return
            profile = self._router.profile(run.model_profile)
            gateway = self._router.gateway(profile)
            availability = await gateway.availability()
            if not availability.available:
                raise GatewayError("model_unavailable", availability.reason or "Model is not available")
            history = (
                await session.scalars(
                    select(Message)
                    .where(Message.conversation_id == run.conversation_id)
                    .where(Message.sequence <= run.context_until_sequence)
                    .order_by(Message.sequence)
                )
            ).all()
            context = build_context(
                list(history), context_window=profile.context_window, max_output_tokens=profile.max_output_tokens
            )
            run.status = "running"
            run.started_at = datetime.now(UTC)
            run.model_revision = availability.revision
            run.context_message_count = context.history_message_count
            conversation_id = run.conversation_id
            await session.commit()
        await channel.publish("status", {"status": "running", "model_revision": availability.revision})

        parts: list[str] = []
        first_token_ms: int | None = None
        end: StreamEnd | None = None
        request = GenerationRequest(
            messages=context.messages,
            temperature=profile.temperature,
            max_output_tokens=profile.max_output_tokens,
            context_window=profile.context_window,
        )
        async for event in gateway.stream(request):
            if isinstance(event, TextDelta):
                if first_token_ms is None:
                    first_token_ms = _ms_since(started)
                parts.append(event.text)
                await channel.publish("delta", {"text": event.text})
            else:
                end = event
        content = "".join(parts)
        if end is None or not content.strip():
            # Never store or show an empty/partial answer as if it were a response.
            raise GatewayError("model_error", "The model returned an empty response")

        async with self._session_factory() as session:
            message = await append_message(session, conversation_id, role="assistant", content=content, run_id=run_id)
            await session.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(
                    status="succeeded",
                    input_tokens=end.input_tokens,
                    output_tokens=end.output_tokens,
                    estimated_cost_usd=profile.estimate_cost_usd(end.input_tokens, end.output_tokens),
                    time_to_first_token_ms=first_token_ms,
                    latency_ms=_ms_since(started),
                    finish_reason=end.finish_reason,
                    finished_at=datetime.now(UTC),
                )
            )
            await session.commit()
            message_json = MessageRead.model_validate(message).model_dump(mode="json")
        run_json = await self._run_json(run_id)
        logger.info(
            "run succeeded",
            extra={
                "run_id": str(run_id),
                "model_profile": profile.id,
                "input_tokens": end.input_tokens,
                "output_tokens": end.output_tokens,
                "ttft_ms": first_token_ms,
                "latency_ms": _ms_since(started),
            },
        )
        await channel.publish("completed", {"run": run_json, "message": message_json})

    async def _fail(self, run_id: uuid.UUID, channel: RunChannel, started: float, code: str, message: str) -> None:
        logger.warning("run failed", extra={"run_id": str(run_id), "error_code": code})
        await self._finish(run_id, started, status="failed", error_code=code, error_message=message)
        await channel.publish("failed", {"error_code": code, "message": message, "run": await self._run_json(run_id)})

    async def _finish(self, run_id: uuid.UUID, started: float, **values: Any) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(**values, latency_ms=_ms_since(started), finished_at=datetime.now(UTC))
            )
            await session.commit()

    async def _run_json(self, run_id: uuid.UUID) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            run = await session.get(Run, run_id)
            return RunRead.model_validate(run).model_dump(mode="json") if run else None


def _ms_since(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)
