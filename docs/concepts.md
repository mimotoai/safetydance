---
kernelspec:
  name: python3
  display_name: Python 3
---

# Concepts

## Steps and scripts

A **step** is a plain function. It reaches shared data through the *active
context*. A **script** is a function decorated with `@script`: each call runs in
a fresh `Context`, and the steps it calls share that context.

```{code-cell} python
from safetydance import ContextProperty, NoActiveContext, get_context, script

count = ContextProperty[int]("count", initializer=lambda ctx: 0)


def increments():
    count.value += 1


@script
def counts_twice() -> int:
    increments()
    increments()
    return count.value


assert counts_twice() == 2
assert counts_twice() == 2  # every call starts from a fresh context
```

Outside any script (and outside `with Context():`) there is no context, and
asking for one raises `NoActiveContext`:

```{code-cell} python
try:
    get_context()
except NoActiveContext as e:
    print(e)
else:
    raise AssertionError("expected NoActiveContext")
```

## Scripts are isolated

A script called from another script gets its own context; it neither sees nor
changes the caller's. To share the context, call a step instead. To use a flow
both ways, wrap the step once with `script(step)`.

```{code-cell} python
@script
def inner() -> int:
    return count.value  # a fresh context: the initializer's 0


@script
def outer() -> tuple[int, int]:
    count.value = 10
    return inner(), count.value


assert outer() == (0, 10)

counts_alone = script(increments)  # the same step, in its own context
```

## Context properties and `@context_data`

`ContextProperty` declares one variable. `@context_data` turns the annotated
attributes of a class into properties; reading `Cls.field` reads the active
context and `Cls.field = v` writes it.

```{code-cell} python
from safetydance import context_data, initializer


@context_data
class Cart:
    owner: str
    currency: str = "EUR"
    items: list[str] = initializer(lambda ctx: [])


@script
def fills_cart():
    Cart.owner = "Ada"
    Cart.items.append("apple")
    return Cart.owner, Cart.currency, Cart.items


assert fills_cart() == ("Ada", "EUR", ["apple"])
```

## Constants and `initializer`

A plain default is a constant shared by every context, so an unhashable default such
as `[]` is rejected, as in dataclasses. Wrap a default in `initializer(fn)` to
compute it per context: `fn(ctx)` runs on the first read and the result is
stored.

```{code-cell} python
try:
    @context_data
    class Bad:
        items: list[str] = []
except ValueError as e:
    print(e)
else:
    raise AssertionError("expected ValueError")
```

## Missing values and `has_value`

Reading a field that has no value raises `ContextDataNotSet`, which is both a
`KeyError` and an `AttributeError`, so `hasattr` and `getattr` with a default
work. A `ContextProperty` also has `has_value`, which tells whether a read would
produce a value without initializing anything.

```{code-cell} python
from safetydance import ContextDataNotSet

name = ContextProperty[str]("name")


@script
def unset_then_set():
    assert not name.has_value
    assert not hasattr(Cart, "owner")
    assert getattr(Cart, "owner", None) is None
    try:
        name.value
    except ContextDataNotSet as e:
        assert isinstance(e, KeyError) and isinstance(e, AttributeError)
    name.value = "Ada"
    assert name.has_value


unset_then_set()
```

Reading a field outside any context raises `ContextDataNotSet` too.

## Reading another context: `prop(ctx)` and `prop[ctx]`

Call the property to read a given `Context`, and index it to write one. This is
how code outside the script, such as a test, inspects the result.

```{code-cell} python
from safetydance import Context

total = ContextProperty[float]("total")
ctx = Context()
total[ctx] = 4.5
assert total(ctx) == 4.5

with ctx:  # make it the active context
    assert total.value == 4.5
```

## Subclasses and redeclared fields

A subclass that redeclares a field shares the base class's value and only changes
the default.

```{code-cell} python
@context_data
class Base:
    level: int = 1


@context_data
class Child(Base):
    level: int = 2


@script
def reads_shared_level():
    assert Child.level == 2  # Child's default
    Base.level = 5
    assert Child.level == 5  # the same value


reads_shared_level()
```

One order-dependent edge: if the subclass redeclares a field *without* a default
while the base has one, an unset read through the subclass raises until something
reads the base field, which stores the base default.

```{code-cell} python
@context_data
class NoDefaultChild(Base):
    level: int


@script
def order_matters():
    try:
        NoDefaultChild.level
    except ContextDataNotSet:
        pass
    else:
        raise AssertionError("expected ContextDataNotSet")
    Base.level  # reading the base stores its default
    assert NoDefaultChild.level == 1


order_matters()
```

## `del` and `ClassVar`

`del Cls.field` clears the value stored in the active context. A value supplied
by name (such as a [test fixture](testing.md)) remains readable. Annotate with
`ClassVar` to keep an ordinary class attribute.

```{code-cell} python
from typing import ClassVar


@context_data
class Settings:
    mode: str = "fast"
    registry: ClassVar[dict] = {}


@script
def clears_value():
    Settings.mode = "slow"
    del Settings.mode
    assert Settings.mode == "fast"  # back to the default


clears_value()
Settings.registry["x"] = 1  # a plain class attribute, no context needed
```

## Tracing

`@script(tracing_logger=...)` or `tracing_log_level=...` (keyword-only) logs each
read, write and delete of context data. A tracing script also logs events from
the scripts it calls; each event is logged once, by the innermost tracing
context.

```{code-cell} python
import logging

records = []


class Collect(logging.Handler):
    def emit(self, record):
        records.append(record.getMessage())


logger = logging.getLogger("trace-demo")
logger.addHandler(Collect())
logger.setLevel(logging.INFO)
logger.propagate = False


@script
def calls_inner():
    return count.value


@script(tracing_logger=logger)
def traced():
    count.value = 1
    return calls_inner()


traced()
assert any("set by" in r for r in records)
assert any("retrieved by" in r for r in records)
assert len(records) == len(set(records))  # nothing logged twice
```
