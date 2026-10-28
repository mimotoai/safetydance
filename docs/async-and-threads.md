---
kernelspec:
  name: python3
  display_name: Python 3
---

# Async code and threads

## Async scripts

An `async def` can be a script. Concurrent asyncio tasks each run in their own
context, so they never see each other's data.

```{code-cell} python
import asyncio

from safetydance import ContextProperty, script

who = ContextProperty[str]("who")


async def says_hello() -> str:
    await asyncio.sleep(0)
    return f"hello {who.value}"


@script
async def greet(name: str) -> str:
    who.value = name
    return await says_hello()


results = await asyncio.gather(greet("Ada"), greet("Grace"))
assert results == ["hello Ada", "hello Grace"]
```

## Generator scripts

A generator function can be a script; its context stays active while it is
stepped. The generator snapshots the caller's `contextvars` at its first step:
changes the caller makes later are not seen inside, and the generator's own
changes do not reach the caller.

```{code-cell} python
from contextvars import ContextVar

flag = ContextVar("flag", default="unset")


@script
def watches_flag():
    yield flag.get()
    yield flag.get()  # still the value from the first step
    flag.set("from generator")
    yield flag.get()


flag.set("before")
gen = watches_flag()
assert next(gen) == "before"
flag.set("after")  # the caller changes it
assert next(gen) == "before"
assert next(gen) == "from generator"
assert flag.get() == "after"  # the generator's change did not leak out
```

## Worker threads

A new thread starts with no active context on a standard Python build. On a
free-threaded build of Python 3.14, where `sys.flags.thread_inherit_context` is on
by default, a thread starts in a copy of the context that started it, so a pool
thread keeps whatever context was active when it was created. Either way, to run
a step against the caller's context, submit it through `copy_context().run`; take
a fresh copy for each submission.

```{code-cell} python
import contextvars
import sys
from concurrent.futures import ThreadPoolExecutor

from safetydance import NoActiveContext, get_context

seen = ContextProperty[str]("seen", initializer=lambda ctx: "default")
inherits_context = getattr(sys.flags, "thread_inherit_context", False)


def reads_seen() -> str:
    return seen.value


@script
def uses_pool() -> str:
    seen.value = "caller"
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            pool.submit(get_context).result()
        except NoActiveContext:
            assert not inherits_context  # standard build: no context in the worker
        else:
            assert inherits_context  # free-threaded 3.14: inherited from the caller
        return pool.submit(contextvars.copy_context().run, reads_seen).result()


assert uses_pool() == "caller"
```

## Async generators

Async generator functions are not supported: decorating one raises `TypeError`.

```{code-cell} python
try:
    @script
    async def agen():
        yield 1
except TypeError as e:
    print(e)
else:
    raise AssertionError("expected TypeError")
```
