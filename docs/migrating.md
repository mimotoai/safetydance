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
| `@step` function, `Step` | a plain function |
| `step_data(...)`, `ContextKey` | `ContextProperty`, or a `@context_data` class |
| `@script`, `Script` | `@script` (alias `@context`); each call runs in a fresh `Context` |
| `NestingContext` (nested scripts could read the caller's data) | gone: every script call is isolated; call a step to share |
| `get_context()` found the `context` local of the calling script | `get_context()` returns the active `Context`; `NoActiveContext` outside one |
| implicit context variables and AST rewriting | gone: read and write context data explicitly |
| `safetydance.extensions`, `file_util`, `jupyter` | removed |

The separate `safetydance-test` package's `scripted_test` corresponds to
`@test_context` here, which puts pytest fixtures into the context by name.

## What to change

1. Remove `@step`. A step is now any function that uses context data.
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
