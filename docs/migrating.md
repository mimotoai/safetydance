---
kernelspec:
  name: python3
  display_name: Python 3
---

# Migrating from 0.3

0.4 is a new API and is not compatible with 0.3. If you cannot migrate yet, pin
`safetydance<0.4`.

| 0.3 | 0.4 |
| --- | --- |
| `@step` function, `Step` | a plain function (0.5 brings back an optional `@step`, [see below](#from-safetydance-test-and-type-extensions)) |
| `step_data(...)`, `ContextKey` | `ContextProperty`, or a `@context_data` class |
| `@script`, `Script` | `@script` (alias `@context`); each call runs in a fresh `Context` |
| `NestingContext` (nested scripts could read the caller's data) | gone: every script call is isolated; call a step to share |
| `get_context()` found the `context` local of the calling script | `get_context()` returns the active `Context`; `NoActiveContext` outside one |
| implicit context variables and AST rewriting | gone: read and write context data explicitly |
| `safetydance.extensions`, `file_util`, `jupyter` | removed |

## What to change

1. Drop the 0.3 `@step`, which rewrote functions; that is gone. A step is any
   function that uses context data. In 0.5, `@step` only registers a plain
   function so `Given`, `When`, `Then` and `And` can find it, and it is
   optional.
2. Replace `step_data` keys with `ContextProperty` objects or `@context_data`
   classes. Use `initializer(...)` for per-context defaults.
3. Keep `@script` on the flows you run as units.
4. If a nested script relied on reading its caller's data, turn it into a step
   and wrap it with `script(step)` where it is also run on its own.
5. New in 0.4: `has_value` checks for a value, and unset or context-less reads
   raise `ContextDataNotSet` or `NoActiveContext`, which you can catch.

For example, a 0.3 `step_data` key becomes a `@context_data` field:

```{code-cell} python
from safetydance import context_data, initializer, script


@context_data
class Order:
    lines: list[str] = initializer(lambda ctx: [])  # was step_data(list, initializer=...)


def adds_line(text: str):  # was a @step
    Order.lines.append(text)


@script
def place_order():
    adds_line("tea")
    return Order.lines


assert place_order() == ["tea"]
assert place_order() == ["tea"]  # each call has a fresh context
```

See the [concepts](concepts.md) for the details and the full list of changes
in the [changelog](https://gitlab.com/dcharbon/safetydance/-/blob/main/CHANGELOG.rst).

## From safetydance-test and type_extensions

The `Given`, `When`, `Then` and `And` prefixes now live in `safetydance` (0.5),
and need neither `safetydance-test` nor `type_extensions`:

| old | new |
| --- | --- |
| `from safetydance_test import Given, When, Then, And` | `from safetydance import Given, When, Then, And` |
| `@scripted_test` | `@test_context`, which puts pytest fixtures into the context by name |
| `step_extension(f, target_type=X)` | `step_namespace("x")` |
| plain `step_extension` / `all_steps_as_step_extensions_from(module)` | `@step` on each step, or `register_steps(*fns)` |
| `type_extensions.extension_property` sub-prefixes (`Given.http`) | `step_namespace("http")` |
| `TestStepPrefix` | not needed |

See [testing](testing.md#given-when-then) for how steps are found.
