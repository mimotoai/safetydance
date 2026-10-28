import asyncio
import inspect
import threading
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor

import pytest

from safetydance import (
    Context,
    ContextProperty,
    NoActiveContext,
    get_context,
    script,
)


slot = ContextProperty[str]()


def test_generator_script_runs_body_in_its_context():
    @script
    def produce():
        slot.value = "first"
        yield slot.value
        slot.value = "second"
        yield slot.value

    assert list(produce()) == ["first", "second"]


def test_generator_script_return_value_is_preserved():
    @script
    def produce():
        slot.value = "returned"
        yield
        return slot.value

    def consume():
        return (yield from produce())

    gen = consume()
    next(gen)
    with pytest.raises(StopIteration) as stop:
        next(gen)
    assert stop.value.value == "returned"


def test_generator_script_context_does_not_leak_between_yields():
    @script
    def produce():
        slot.value = "inner"
        yield

    gen = produce()
    next(gen)
    with pytest.raises(NoActiveContext, match="No active context"):
        get_context()
    gen.close()


def test_interleaved_generator_scripts_are_independent():
    @script
    def produce(name):
        slot.value = name
        yield
        yield slot.value

    a, b = produce("a"), produce("b")
    next(a)
    next(b)
    assert next(a) == "a"
    assert next(b) == "b"


def test_generator_script_is_still_a_generator_function():
    @script
    def produce():
        yield

    assert inspect.isgeneratorfunction(produce)


def test_async_script_runs_body_in_its_context():
    @script
    async def run():
        slot.value = "async"
        await asyncio.sleep(0)
        return slot.value

    assert inspect.iscoroutinefunction(run)
    assert asyncio.run(run()) == "async"


def test_concurrent_async_scripts_are_independent():
    @script
    async def run(name):
        slot.value = name
        await asyncio.sleep(0.01)
        return slot.value

    async def both():
        return await asyncio.gather(run("a"), run("b"))

    assert asyncio.run(both()) == ["a", "b"]


def test_concurrent_tasks_with_explicit_contexts_are_independent():
    async def run(name):
        with Context():
            slot.value = name
            await asyncio.sleep(0.01)
            return slot.value

    async def both():
        return await asyncio.gather(run("a"), run("b"))

    assert asyncio.run(both()) == ["a", "b"]


def test_concurrent_nested_async_scripts_are_independent():
    @script
    async def nested(name):
        slot.value = name
        await asyncio.sleep(0.01)
        return slot.value

    @script
    async def outer():
        slot.value = "outer"
        results = await asyncio.gather(nested("a"), nested("b"))
        return results, slot.value

    assert asyncio.run(outer()) == (["a", "b"], "outer")


def test_generator_script_started_inside_a_script_is_isolated():
    @script
    def produce():
        assert not slot.has_value
        slot.value = "inner"
        yield slot.value

    @script
    def outer():
        slot.value = "outer"
        produced = list(produce())
        return produced, slot.value

    assert outer() == (["inner"], "outer")


def test_async_generator_script_is_rejected():
    with pytest.raises(TypeError, match="async generator"):

        @script
        async def produce():
            yield


@script
def test_worker_thread_shares_context_via_copy_context():
    slot.value = "parent"

    def step_on_worker():
        assert slot.value == "parent"
        slot.value = "from worker"

    with ThreadPoolExecutor() as pool:
        pool.submit(copy_context().run, step_on_worker).result()

    assert slot.value == "from worker"


def test_shared_context_on_two_threads_keeps_each_threads_context():
    outer, shared = Context(), Context()
    t1_inside = threading.Event()
    t2_done = threading.Event()

    def first():
        with outer:
            with shared:
                t1_inside.set()
                assert t2_done.wait(timeout=5)
            return get_context()

    def second():
        assert t1_inside.wait(timeout=5)
        with shared:
            pass
        t2_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_done = pool.submit(first)
        second_done = pool.submit(second)
        assert first_done.result() is outer
        second_done.result()


def test_shared_context_on_two_tasks_keeps_each_tasks_context():
    outer, shared = Context(), Context()

    async def both():
        t1_inside = asyncio.Event()
        t2_done = asyncio.Event()

        async def first():
            with outer:
                with shared:
                    t1_inside.set()
                    await t2_done.wait()
                return get_context()

        async def second():
            await t1_inside.wait()
            with shared:
                pass
            t2_done.set()

        return await asyncio.wait_for(asyncio.gather(first(), second()), timeout=5)

    active_after_exit, _ = asyncio.run(both())
    assert active_after_exit is outer
