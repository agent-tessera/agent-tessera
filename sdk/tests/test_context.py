"""Tests for per-client task context and the shared principal."""

import asyncio
import threading

import pytest

from agent_tessera import context as context_module
from agent_tessera.context import (
    Task,
    clear_guardrails,
    current_principal,
    current_task,
    guardrails_ran,
    mark_guardrail_ran,
    use_principal,
    use_task,
)
from agent_tessera.types import Principal


@pytest.fixture(autouse=True)
def _fresh_context():
    tasks = context_module._tasks.set(())
    principal = context_module._principal.set(None)
    outside = context_module._outside.set(context_module._EMPTY_GUARDRAILS)
    yield
    context_module._tasks.reset(tasks)
    context_module._principal.reset(principal)
    context_module._outside.reset(outside)


def test_nested_task_restores_the_outer_task() -> None:
    outer = Task("outer", "A")
    inner = Task("inner", "A")
    with use_task(outer):
        assert current_task("A") is outer
        with use_task(inner):
            assert current_task("A") is inner
        assert current_task("A") is outer
    assert current_task("A") is None


def test_nested_task_restores_after_an_error() -> None:
    outer = Task("outer", "A")
    with use_task(outer):
        with pytest.raises(RuntimeError), use_task(Task("inner", "A")):
            raise RuntimeError("boom")
        assert current_task("A") is outer
    assert current_task("A") is None


def test_client_isolation_for_tasks_and_guardrails() -> None:
    mark_guardrail_ran("A", "input.outside")
    assert guardrails_ran("A") == frozenset({"input.outside"})
    assert "input.outside" not in guardrails_ran("B")

    task_a = Task("a", "A")
    with use_task(task_a):
        assert current_task("A") is task_a
        assert current_task("B") is None
        mark_guardrail_ran("A", "input.inside")
        assert guardrails_ran("A") == frozenset({"input.inside"})
        assert "input.inside" not in guardrails_ran("B")
        assert "input.outside" not in guardrails_ran("B")

    assert guardrails_ran("A") == frozenset({"input.outside"})
    clear_guardrails("A")
    assert guardrails_ran("A") == frozenset()


def test_nested_other_client_keeps_the_outer_task() -> None:
    task_a = Task("a", "A")
    task_b = Task("b", "B")
    with use_task(task_a):
        with use_task(task_b):
            assert current_task("A") is task_a
            assert current_task("B") is task_b
            mark_guardrail_ran("A", "input.a")
            mark_guardrail_ran("B", "input.b")
            assert guardrails_ran("A") == frozenset({"input.a"})
            assert "input.a" not in guardrails_ran("B")
            clear_guardrails("B")
            assert guardrails_ran("B") == frozenset()
            assert guardrails_ran("A") == frozenset({"input.a"})
        assert current_task("A") is task_a
        assert current_task("B") is None


def test_principal_is_shared_by_clients_and_restored() -> None:
    person = Principal("ada", frozenset({"support"}))
    assert current_principal() is None
    with use_principal(person) as active:
        assert active is person
        assert current_principal() is person
        with use_task(Task("a", "A")), use_task(Task("b", "B")):
            assert current_principal() is person
    assert current_principal() is None


def test_threads_do_not_share_tasks() -> None:
    barrier = threading.Barrier(2)
    seen: dict[str, str | None] = {}

    def worker(client_id: str) -> None:
        with use_task(Task(client_id, client_id)):
            barrier.wait()
            task = current_task(client_id)
            other = current_task("A" if client_id == "B" else "B")
            seen[client_id] = None if task is None else task.id
            seen[f"other-{client_id}"] = None if other is None else other.id

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert seen == {"A": "A", "B": "B", "other-A": None, "other-B": None}


def test_asyncio_tasks_do_not_share_tasks_or_later_principals() -> None:
    async def scenario() -> dict[str, str | None]:
        seen: dict[str, str | None] = {}
        changed = asyncio.Event()
        release = asyncio.Event()

        async def worker_a() -> None:
            with use_task(Task("A", "A")), use_principal(Principal("only-a")):
                changed.set()
                await release.wait()
                task = current_task("A")
                other = current_task("B")
                seen["A"] = None if task is None else task.id
                seen["other-A"] = None if other is None else other.id
                principal = current_principal()
                seen["principal-A"] = None if principal is None else principal.user

        async def worker_b() -> None:
            with use_task(Task("B", "B")):
                await changed.wait()
                task = current_task("B")
                other = current_task("A")
                seen["B"] = None if task is None else task.id
                seen["other-B"] = None if other is None else other.id
                principal = current_principal()
                seen["principal-B"] = None if principal is None else principal.user
                release.set()

        with use_principal(Principal("ada")):
            await asyncio.gather(worker_a(), worker_b())
            parent = current_principal()
            seen["after"] = None if parent is None else parent.user
        return seen

    result = asyncio.run(scenario())
    assert result == {
        "A": "A",
        "B": "B",
        "other-A": None,
        "other-B": None,
        "principal-A": "only-a",
        "principal-B": "ada",
        "after": "ada",
    }
