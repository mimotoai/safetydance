---
kernelspec:
  name: python3
  display_name: Python 3
---

# Testing

`@test_context` is `@script` for tests: the call's keyword arguments, typically
pytest fixtures, are put into the context by name. A `context_data` field with
the same name reads the value.

```python
from pathlib import Path

from safetydance import context_data, test_context


@context_data
class Storage:
    tmp_path: Path


@test_context
def test_writes_report(tmp_path):
    assert Storage.tmp_path == tmp_path  # the pytest fixture
```

To give a field a different value for one test, assign it inside the test:
the test's context is discarded afterwards. `monkeypatch.setattr(Cls, "field",
value)` and `mock.patch.object` also work inside a `@test_context` test; they set
the value in the test's context, and restoring clears it.

The same mechanism, shown without pytest:

```{code-cell} python
from safetydance import context_data, test_context, get_context


@context_data
class Storage:
    tmp_path: str


@test_context
def check_storage(tmp_path):
    assert Storage.tmp_path == "/somewhere"
    assert get_context()["tmp_path"] == "/somewhere"
    del Storage.tmp_path  # clears only a stored value...
    assert Storage.tmp_path == "/somewhere"  # ...the named one stays readable


check_storage(tmp_path="/somewhere")
```

`test_context` takes the same keyword-only tracing options as `script`, and works
with generator and async test functions. It is not collected as a test by pytest
when you import it.

## Given, When, Then

`Given`, `When`, `Then` and `And` read a test as a story. They are one object;
the four names are only for how the test reads. Each attribute looks up a step
by name, and the step it returns is the plain function, so it runs in the
active context. Calling one outside `@script` or `@test_context` raises that
step's own `NoActiveContext`.

Mark a function with `@step` to make it findable. It returns the function
unchanged, so you can still call it directly:

```{code-cell} python
from safetydance import Given, Then, When, context_data, step, test_context


@context_data
class Basket:
    items: list[str]


@step
def basket_with(*items):
    Basket.items = list(items)


@step
def basket_gains(item):
    Basket.items.append(item)


@step
def basket_holds(count):
    assert len(Basket.items) == count


@test_context
def check_basket():
    Given.basket_with("tea")
    When.basket_gains("milk")
    Then.basket_holds(2)


check_basket()
```

A step is found three ways. Use the shortest one that is not ambiguous:

* the **full module path**, such as `Given.my_package.steps.get`, which never
  clashes with another library's step;
* a **short namespace**, if the steps module made its decorator with
  `step_namespace`, such as `Given.http.get`;
* the **bare name**, such as `Given.get`, when only one step has that name.

```{code-cell} python
from safetydance import Given, step_namespace

step = step_namespace("greeting")


@step
def says_hello(name):
    return f"hello {name}"


assert Given.greeting.says_hello("Ada") == "hello Ada"
assert Given.says_hello("Ada") == "hello Ada"
```

A bare name or short namespace that several different steps share raises
`AmbiguousStep`, which lists every candidate's full module path. A step is
identified by where it is defined (file, line and qualified name), so loading
one module under two names is still one step. Closures made by one factory are
distinct steps, so their bare names and full paths are ambiguous: register
them explicitly, or give each a short namespace.

`register_steps` settles a collision, and always beats a step registered with
`@step`. Call it in the test module with the step you want, and the namespace
to find it under (or none, for the bare name):

```{code-cell} python
from safetydance import Given, register_steps, step


def pours():
    return "water"


register_steps(pours, namespace="kettle")
assert Given.kettle.pours() == "water"
```

Binding a different step to a path that is already registered explicitly
raises `ValueError`. So do names and namespace segments that start with `_` or
are not identifiers.

### Importing step modules

A step exists once its module has been imported. Either import the step
modules a test uses, or have the library expose them through a
`safetydance.steps` entry point. The entry point's name is the root of the
lookup, and its value is the module:

```toml
[project.entry-points."safetydance.steps"]
http = "my_package.steps"
```

A lookup of `Given.http` imports the entry points named `http`. If a root name
is still not found, or is ambiguous, all the remaining entry points are
imported and the lookup is tried once more before it fails with `StepNotFound`.
An entry point's module is imported once. If it fails to import, the import
error reaches the caller from any lookup that loads it, including a
`hasattr(Given, ...)` miss.

`StepNotFound` and `AmbiguousStep` are both `AttributeError`s, so `hasattr` and
`getattr(Given, name, default)` work. A module whose top-level name is also one
of its own step names, such as module `login` with a step `login`, makes
`Given.login` both a step and a namespace, which raises `AmbiguousStep`. Give
the steps a short namespace instead.
