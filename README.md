# safetydance

`safetydance` is a Python framework for defining type-safe and flexibly composable
application steps with a shared execution context for sharing variables between the
steps. The design of safetydance is partially inspired by [Cucumber](https://cucumber.io).

```bash
pip install safetydance
```

Documentation: <https://dcharbon.gitlab.io/safetydance/>


## Description

`safetydance` lets plain functions act as *steps* that share data through an execution
context, and provides decorators that turn a function into a *script*, which runs its
steps in a fresh context.

A step should be a function with a name that reads easily, like a short phrase or
sentence. Steps may take arguments and they may access variables defined for the
"context scope". Steps may call other steps, too.

A script is a function, decorated with `@script`, composing the execution of a series of
steps. Each call to a script runs in its own execution context, so scripts never see each
other's context data: not when one script calls another, and not when scripts run
concurrently in asyncio tasks. Generator and `async` functions can be scripts, too.

To share context, call a step rather than a script; a step always uses its caller's
context. A flow that is useful both on its own and inside larger scripts can be written
as a step and wrapped once, e.g. `log_in_script = script(log_in)`.

```python
from safetydance import context_data, initializer, script


@context_data
class Shop:
    customer: str
    cart: list[tuple[str, float]] = initializer(lambda ctx: [])
    total: float = 0.0


def customer_named(name: str):
    Shop.customer = name


def adds_to_cart(item: str, price: float):
    Shop.cart.append((item, price))


def checks_out():
    Shop.total = sum(price for _, price in Shop.cart)


@script
def buy_breakfast() -> float:
    customer_named("Ada")
    adds_to_cart("coffee", 3.50)
    adds_to_cart("bagel", 2.50)
    checks_out()
    return Shop.total
```

A default wrapped in `initializer(...)` is called with the context on first read, giving
each context its own value; any other default is a constant. Decorated scripts keep
their signatures for type checkers.

In tests, `@test_context` works like `@script` and also puts the call's keyword
arguments, such as pytest fixtures, into the context by name.

### Context Scope Variables

A context scope variable is sort of like a global variable. The run of a script defines
a context where `context_data` variables are stored for access by steps.

Think of a conversation between two friends. Much of the conversation will reference
assumed shared knowledge, or context. For `safetydance`, the context scope provides a
way for steps to share assumed context to make it possible to provide a more
conversational style of programming

## Migrating from 0.3

0.4 is a new API, not compatible with 0.3: `@step` functions are plain functions,
`step_data` and `ContextKey` became `ContextProperty` and `@context_data`, and
nested scripts no longer share a context. See the
[migration guide](https://dcharbon.gitlab.io/safetydance/migrating/), or pin
`safetydance<0.4` until you can migrate. 0.5 brings back an optional `@step`, which
only registers a plain function so `Given`, `When`, `Then` and `And` can find it; see
[Given, When, Then](https://dcharbon.gitlab.io/safetydance/testing/#given-when-then).

## Future Work

* Mypy extension to validate scripts. For example, prove that a script shouldn't fail
  due to missing `context_data` for any step in the script.
* Dry run execution of steps
* DAG derivation for scripts
* Parallel evaluation for independent up to a join for DAG legs of a script.
* Diagram output for script DAGs.

## Setup for Development

```bash
uv sync                # create .venv with the dev dependency group
uv run pytest          # run the tests on the default interpreter
uv run nox             # run the tests on every supported Python version, and mypy
uv build               # build the sdist and wheel into dist/
```

The docs need Node, which [mise](https://mise.jdx.dev) installs with the pinned uv
(see `mise.toml`). The guide builds into `docs/_build/html`, the API reference into
`docs/_build/html/api`:

```bash
mise install                          # install the pinned Node and uv
mise exec -- uv run --locked nox -s docs
```
