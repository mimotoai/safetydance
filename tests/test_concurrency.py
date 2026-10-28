import asyncio
import gc
import inspect
import sys
import threading
import weakref
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


def test_context_restored_after_sync_script_raises():
    @script
    def boom():
        raise ValueError("x")

    with pytest.raises(ValueError):
        boom()
    with pytest.raises(NoActiveContext):
        get_context()
    with Context() as outer:
        with pytest.raises(ValueError):
            boom()
        assert get_context() is outer


def test_context_restored_after_async_script_raises():
    @script
    async def boom():
        raise ValueError("x")

    with pytest.raises(ValueError):
        asyncio.run(boom())
    with pytest.raises(NoActiveContext):
        get_context()

    async def inside_outer():
        with Context() as outer:
            with pytest.raises(ValueError):
                await boom()
            assert get_context() is outer

    asyncio.run(inside_outer())


def test_context_restored_after_generator_script_raises():
    @script
    def boom():
        yield 1
        raise ValueError("x")

    gen = boom()
    next(gen)
    with pytest.raises(ValueError):
        next(gen)
    with pytest.raises(NoActiveContext):
        get_context()
    with Context() as outer:
        gen = boom()
        next(gen)
        with pytest.raises(ValueError):
            next(gen)
        assert get_context() is outer


def test_send_and_throw_into_generator_script():
    @script
    def echo():
        slot.value = "data"
        received = yield "ready"
        try:
            yield (received, slot.value)
        except ValueError:
            yield ("caught", slot.value)

    gen = echo()
    assert next(gen) == "ready"
    assert gen.send(42) == (42, "data")
    assert gen.throw(ValueError) == ("caught", "data")


def test_generator_script_returning_before_its_first_yield():
    @script
    def produce():
        slot.value = "early"
        return slot.value
        yield

    with pytest.raises(StopIteration) as stop:
        next(produce())
    assert stop.value.value == "early"


def test_thrown_exception_is_not_kept_by_a_suspended_generator_script():
    class Marker(Exception):
        pass

    @script
    def produce():
        while True:
            try:
                yield
            except Marker:
                pass

    gen = produce()
    next(gen)
    thrown = Marker()
    gen.throw(thrown)
    ref = weakref.ref(thrown)
    del thrown
    gc.collect()
    assert ref() is None
    gen.close()


def test_generator_finally_runs_in_context_on_close():
    seen = []

    @script
    def produce():
        slot.value = "alive"
        try:
            yield
        finally:
            seen.append(slot.value)

    gen = produce()
    next(gen)
    gen.close()
    assert seen == ["alive"]


def test_generator_close_returns_value():
    @script
    def produce():
        try:
            yield
        except GeneratorExit:
            return "closing"

    gen = produce()
    next(gen)
    assert gen.close() == ("closing" if sys.version_info >= (3, 13) else None)


def test_thrown_generator_exit_propagates_from_generator_script():
    @script
    def produce():
        yield 1

    gen = produce()
    next(gen)
    with pytest.raises(GeneratorExit):
        gen.throw(GeneratorExit)


def test_thrown_generator_exit_matches_native_when_body_returns_or_yields():
    def native():
        try:
            yield
        except GeneratorExit:
            return "r"

    def native_yield():
        try:
            yield
        except GeneratorExit:
            yield "y"

    for fn in (native, native_yield):
        expected = []
        for make in (fn, script(fn)):
            gen = make()
            next(gen)
            try:
                expected.append(("value", gen.throw(GeneratorExit)))
            except StopIteration as stop:
                expected.append(("stop", stop.value))
        assert expected[0] == expected[1]


def test_generator_script_works_as_contextmanager_closed_early():
    import contextlib

    @contextlib.contextmanager
    @script
    def managed():
        slot.value = "m"
        yield slot.value

    def consumer():
        with managed():
            yield

    gen = consumer()
    next(gen)
    assert gen.close() is None


def test_async_callable_object_is_an_async_script():
    class Job:
        async def __call__(self):
            slot.value = "x"
            return slot.value

    assert asyncio.run(script(Job())()) == "x"


def test_partial_of_async_function_is_an_async_script():
    import functools

    async def job(prefix):
        slot.value = prefix
        return slot.value

    assert asyncio.run(script(functools.partial(job, "p"))()) == "p"


def test_partial_of_generator_function_is_a_generator_script():
    import functools

    def gen_fn(prefix):
        slot.value = prefix
        yield slot.value

    assert list(script(functools.partial(gen_fn, "g"))()) == ["g"]


def test_async_mock_is_an_async_script():
    from unittest.mock import AsyncMock

    mock = AsyncMock(return_value="m")
    assert asyncio.run(script(mock)()) == "m"


def test_generator_callable_object_is_a_generator_script():
    class Job:
        def __call__(self):
            slot.value = "x"
            yield slot.value

    assert list(script(Job())()) == ["x"]


def test_async_generator_callable_object_is_rejected():
    class Job:
        async def __call__(self):
            yield 1

    with pytest.raises(TypeError):
        script(Job())


def test_concurrent_first_reads_share_one_initialized_value():
    # Both threads are inside the initializer at once, so both produce a value; the
    # first one stored must be the one each thread gets.
    both_initializing = threading.Barrier(2, timeout=5)

    def make_list(ctx):
        both_initializing.wait()
        return []

    items = ContextProperty[list](initializer=make_list)
    ctx = Context()

    def worker(name):
        with ctx:
            items.value.append(name)

    threads = [
        threading.Thread(target=copy_context().run, args=(worker, name))
        for name in ("a", "b")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(items(ctx)) == ["a", "b"]
