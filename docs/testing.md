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
active context. Outside `@script`, `@test_context` or `with Context():`, a step
fails exactly as it would if called directly: reading a `@context_data` field
raises `ContextDataNotSet`; writing one, or using `ContextProperty.value`,
raises `NoActiveContext`.

Mark a function with `@step` to register it under its own name. It returns the
function unchanged, so you can still call it directly:

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

### Namespaces

A library should register its steps under a namespace, so that its names do not
clash with anyone else's. `step_namespace("http")` makes a decorator that
registers `get` as `Given.http.get`; a dotted namespace such as `"my.pkg"` gives
`Given.my.pkg.get`. `register_steps(*fns, namespace=...)` does the same from the
caller's side, for functions you did not decorate, and without a namespace it
registers the bare names:

```{code-cell} python
from safetydance import Given, register_steps, step_namespace

greeting_step = step_namespace("greeting")


@greeting_step
def says_hello(name):
    return f"hello {name}"


def pours():
    return "water"


register_steps(pours, namespace="kettle")

assert Given.greeting.says_hello("Ada") == "hello Ada"
assert Given.kettle.pours() == "water"
assert dir(Given.greeting) == ["says_hello"]
```

Each registration binds exactly one path, so `says_hello` above is not also
`Given.says_hello`. The same function can be registered at several paths, for
example by a library decorator and again by `register_steps` under your own
namespace. Step names and namespace segments must be public identifiers: not a
keyword, not starting with `_`, so `<lambda>` is rejected with `ValueError`.
Something that is not callable, or has no `__name__`, raises `TypeError`, and so
does calling `register_steps()` or `unregister_steps()` with no steps.
`register_steps` is all or nothing: if any step is invalid or conflicts,
nothing is bound.

### Conflicts

Registering the same step again does nothing. Two registrations are the same
step when they are the same object, or bound methods with the same instance and
function (`obj.method` makes a new object on every access). Anything else is a
different step, and nothing is ever replaced silently. These raise `ValueError`
when you register, naming both steps as `module.qualname (file:line)`:

* a different step at a path that is already bound;
* a step at a path that is already a namespace of other steps;
* a step under a path that is bound to a step.

A bare-name clash between two libraries therefore surfaces at import, not in the
middle of a test. Registering the same method of two instances says "a method of
a different instance". The registry is global for the whole test run, so make
bindings once, in `conftest.py` or a module the tests share.

### Looking steps up

Importing a module is what registers its steps, and nothing is loaded
automatically. Import the modules whose steps a test uses, typically in
`conftest.py`.

A name nothing is registered at raises `StepNotFound`, whose message ends
"import the module that defines it". It is an `AttributeError`, so `hasattr`
and `getattr(Given, name, default)` treat it as absent. A path with registered
children gives a namespace object; `dir(Given)` and `dir(Given.http)` list the
registered child names. Names starting with `_` raise a plain `AttributeError`,
which keeps tools that probe objects (pytest, `inspect.unwrap`, `copy`,
`pickle`, Sphinx, `mock.patch.object`) harmless. `Given`, `When`, `Then` and
`And` are one object, and `Given.x = ...` raises `AttributeError`.

### Removing steps

`unregister_steps(*steps)` removes every path each step is registered at, and
namespaces left empty disappear. A step that is not registered is ignored. Use
it before redefining a step in a notebook cell or after a reload:

```{code-cell} python
from safetydance import Given, step, unregister_steps


@step
def one_off_step():
    return 1


unregister_steps(Given.one_off_step)
assert not hasattr(Given, "one_off_step")


@step
def one_off_step():
    return 2


assert Given.one_off_step() == 2
```

The same-step rule applies to Python functions and methods. A builtin bound
method such as `lst.append` is a new object on each access, so register it
once, or wrap it in a function.

### Mocking

The registry holds the original function, so `mock.patch("pkg.steps.get")` does
not change what `Given.get` calls. Patch what the step calls instead, or
`unregister_steps` the step and register the replacement.
`mock.patch.object(Given, ...)` raises `AttributeError`, because the prefixes
have no attributes of their own.

### When a module is loaded twice

Python treats each module name as a separate module, so a file that is
reachable under two names, or executed twice, runs twice and registers its
steps twice. Common triggers:

* Two `sys.path` entries reach the same file. Under pytest's default
  `--import-mode=prepend`, a test directory without `__init__.py` is put on
  `sys.path`, so `tests/example/steps.py` can be both `example.steps` and
  `tests.example.steps`. The same happens with `PYTHONPATH=.:src`, or an IDE
  that adds both the repo root and `src`, which makes `src.pkg.steps` and
  `pkg.steps` both importable.
* Importing from `conftest.py`. pytest imports a conftest under its own name,
  so `from conftest import ...` or `from tests.conftest import ...` can load a
  second copy. Steps defined in a conftest are the most likely way to hit
  this.
* Running a step module as a script (`python pkg/steps.py`,
  `python -m pkg.steps`). It runs as `__main__`, and an import of `pkg.steps`
  runs it again.
* Re-running code under the same name: `importlib.reload()`, or re-running a
  notebook cell. This is not strictly a double import, but it has the same
  effect.

The second import raises `ValueError`, naming both module names ("the module
was imported twice, as A and B"). A reload or a cell re-run raises too, without
that hint, because both copies have the same module name.

To avoid it:

* give test packages an `__init__.py` and import them one way, or use
  `--import-mode=importlib`;
* don't import from `conftest.py`;
* keep runnable scripts separate from step modules;
* before a reload or a cell re-run, `unregister_steps` the old steps.

### Testing step libraries

In-process `pytester` runs (`runpytest_inprocess()`, and `runpytest()` by
default) share the registry of the process they run in. A `@step` in a
`conftest.py` passes on the first run, and the second run fails with "ImportError
while loading conftest" wrapping the conflict `ValueError`. Use
`pytester.runpytest_subprocess()`, or call `unregister_steps(...)` on those steps
in a fixture teardown.
