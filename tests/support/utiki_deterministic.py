"""Deterministic async test seams for the uTiki refactor.

These helpers deliberately do not import production modules.  Tests decide when
an I/O result becomes available and can advance monotonic and wall time
independently without triggering that result.
"""

from __future__ import annotations

import asyncio
import math
import types
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar


ResultT = TypeVar("ResultT")


class FakeClock:
    """A clock with independent monotonic and wall-time inputs."""

    def __init__(
        self,
        *,
        monotonic: float = 100.0,
        wall: float = 1_700_000_000.0,
    ) -> None:
        self._monotonic = self._valid_time(monotonic, "monotonic")
        self._wall = self._valid_time(wall, "wall")

    @staticmethod
    def _valid_time(value: float, label: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"{label} time must be finite")
        return result

    @staticmethod
    def _valid_delta(seconds: float) -> float:
        result = float(seconds)
        if not math.isfinite(result) or result < 0:
            raise ValueError("clock advance must be finite and non-negative")
        return result

    def monotonic(self) -> float:
        return self._monotonic

    def wall_time(self) -> float:
        return self._wall

    def advance_monotonic(self, seconds: float) -> float:
        self._monotonic += self._valid_delta(seconds)
        return self._monotonic

    def advance_wall(self, seconds: float) -> float:
        self._wall += self._valid_delta(seconds)
        return self._wall


@dataclass
class PendingIOCall(Generic[ResultT]):
    """One observed I/O call whose completion is controlled by the test."""

    sequence: int
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    _future: asyncio.Future[ResultT] = field(repr=False)

    @property
    def done(self) -> bool:
        return self._future.done()

    def resolve(self, result: ResultT) -> None:
        if self._future.done():
            raise AssertionError("pending I/O call was already completed")
        self._future.set_result(result)

    def fail(self, error: BaseException) -> None:
        if self._future.done():
            raise AssertionError("pending I/O call was already completed")
        self._future.set_exception(error)

    async def wait(self) -> ResultT:
        return await self._future


class ControlledPendingIO(Generic[ResultT]):
    """A semantic provider that completes calls only when told by the test."""

    def __init__(self, label: str, *, event_log: list[str] | None = None) -> None:
        self.label = label
        self.calls: list[PendingIOCall[ResultT]] = []
        self._started: asyncio.Queue[PendingIOCall[ResultT]] = asyncio.Queue()
        self._event_log = event_log

    async def request(self, *args: Any, **kwargs: Any) -> ResultT:
        loop = asyncio.get_running_loop()
        call = PendingIOCall[ResultT](
            sequence=len(self.calls),
            args=tuple(args),
            kwargs=dict(kwargs),
            _future=loop.create_future(),
        )
        self.calls.append(call)
        if self._event_log is not None:
            self._event_log.append(self.label)
        self._started.put_nowait(call)
        return await call.wait()

    async def next_call(self) -> PendingIOCall[ResultT]:
        """Wait for the next call without polling or sleeping."""
        return await self._started.get()

    def assert_no_calls(self) -> None:
        if self.calls:
            raise AssertionError(
                f"{self.label} unexpectedly received {len(self.calls)} call(s)"
            )

    def assert_all_completed(self) -> None:
        pending = [call.sequence for call in self.calls if not call.done]
        if pending:
            raise AssertionError(
                f"{self.label} has incomplete call(s): {pending}"
            )


class BrowserEffectSentinel:
    """Minimal tab identity that fails on any browser-facing operation."""

    def __init__(self, url: str) -> None:
        self.target = types.SimpleNamespace(url=url)
        self.attempts: list[str] = []

    async def _reject(self, operation: str) -> Any:
        self.attempts.append(operation)
        raise AssertionError(f"unexpected browser operation: {operation}")

    async def evaluate(self, *_args: Any, **_kwargs: Any) -> Any:
        return await self._reject("evaluate")

    async def query_selector(self, *_args: Any, **_kwargs: Any) -> Any:
        return await self._reject("query_selector")

    async def query_selector_all(self, *_args: Any, **_kwargs: Any) -> Any:
        return await self._reject("query_selector_all")

    async def reload(self, *_args: Any, **_kwargs: Any) -> Any:
        return await self._reject("reload")

    async def send(self, *_args: Any, **_kwargs: Any) -> Any:
        return await self._reject("send")

    async def sleep(self, *_args: Any, **_kwargs: Any) -> Any:
        return await self._reject("sleep")

    def assert_no_effects(self) -> None:
        if self.attempts:
            raise AssertionError(
                f"unexpected browser operations: {self.attempts}"
            )


class TaskLeakGuard:
    """Assert that an async scenario leaves no task it started unfinished."""

    def __init__(self) -> None:
        self._baseline: frozenset[asyncio.Task[Any]] | None = None
        self._owned: set[asyncio.Task[Any]] = set()

    async def __aenter__(self) -> TaskLeakGuard:
        if self._baseline is not None:
            raise RuntimeError("TaskLeakGuard cannot be entered twice")
        self._baseline = frozenset(asyncio.all_tasks())
        return self

    def create_task(
        self,
        coroutine: Any,
        *,
        name: str | None = None,
    ) -> asyncio.Task[Any]:
        if self._baseline is None:
            raise RuntimeError("TaskLeakGuard must be entered before creating tasks")
        task = asyncio.create_task(coroutine, name=name)
        self._owned.add(task)
        return task

    def unfinished_tasks(self) -> set[asyncio.Task[Any]]:
        if self._baseline is None:
            raise RuntimeError("TaskLeakGuard is not active")
        current = asyncio.current_task()
        created = set(asyncio.all_tasks()).difference(self._baseline)
        return {
            task
            for task in created.union(self._owned)
            if task is not current and not task.done()
        }

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        leaks = self.unfinished_tasks()
        descriptions = sorted(_task_description(task) for task in leaks)
        for task in leaks:
            task.cancel()
        if leaks:
            await asyncio.gather(*leaks, return_exceptions=True)
        self._baseline = None
        if descriptions and exc is None:
            raise AssertionError(
                "unfinished asyncio task(s): " + ", ".join(descriptions)
            )
        return False


def _task_description(task: asyncio.Task[Any]) -> str:
    name = task.get_name()
    coroutine = task.get_coro()
    qualified_name = getattr(coroutine, "__qualname__", type(coroutine).__name__)
    return f"{name}<{qualified_name}>"
