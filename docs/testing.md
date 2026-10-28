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
  clashes with another library's step. It is unique except for steps made by
  one factory (closures) and bound methods, which need an explicit
  registration or a short namespace;
* a **short namespace**, if the steps module made its decorator with
  `step_namespace`, such as `Given.http.get`;
* the **bare name**, such as `Given.get`, when only one step has that name.

```{code-cell} python
from safetydance import Given, step_namespace

greeting_step = step_namespace("greeting")


@greeting_step
def says_hello(name):
    return f"hello {name}"


assert Given.greeting.says_hello("Ada") == "hello Ada"
assert Given.says_hello("Ada") == "hello Ada"
```

A bare name or short namespace that several different steps share raises
`AmbiguousStep`, which lists every candidate's full module path. So does any
name that is both a step and the start of another registered path, such as a
module `login` with a step `login`. A short namespace makes `Given.greeting.x`
reachable, but the bare `Given.x` stays ambiguous unless it is bound with
`register_steps`.

A step is identified by its definition location (file, line, qualified name
and code), the location of each layer of its `functools.wraps` chain, and its
default and captured values. Bound methods are identified by their instance.

Values compare by value only when they are exactly `int`, `bool`, `float`,
`complex`, `str`, `bytes` or `None`, or a tuple or frozenset of those, so `1`
and `True`, or `(1,)` and `(1.0,)`, stay distinct. Every other value
(functions, classes, instances, sentinels, `Decimal`, `Enum` members,
partials) compares by object identity, and is never hashed, compared or
inspected. That makes lazy objects, such as Django's `settings`, safe to use
as defaults.

So when a step module is imported twice, a step whose defaults or captured
values include anything other than those builtins is a different step in each
copy, and its bare name and short namespace path raise `AmbiguousStep`. Fix
that by not importing the module twice (check pytest's rootdir and import
mode), or by giving the copies distinct namespaces. Closures made by one
factory that capture different values are distinct steps too, so their bare
names and full paths are ambiguous: register them explicitly, or give each a
short namespace. A registered step keeps its default and captured values alive
until it is unregistered.

Defining a step again at a different line, as reloading a module or re-running
an edited notebook cell does, leaves the old one registered, so the name
becomes ambiguous. Remove the old one first with `unregister_steps`, which
drops a step from every path it is registered at (`@step` and
`register_steps` alike) and ignores a step that is not registered:

```python
from safetydance import unregister_steps

unregister_steps(old_says_hello)
```

`register_steps` settles a collision, and always beats a step registered with
`@step`. Call it with the step you want, and the namespace to find it under (or
none, for the bare name). The binding is global for the whole test run, and
pytest imports every test module while collecting, so two test modules that
bind different steps to one path fail with `ValueError` before any test runs.
Make the bindings once, in `conftest.py` or one module that the tests share:

```{code-cell} python
from safetydance import Given, register_steps


def pours():
    return "water"


register_steps(pours, namespace="kettle")
assert Given.kettle.pours() == "water"
```

`register_steps` raises `ValueError` when a different step is already bound at
the path, when the path is already a namespace of other steps, or when it is
under a path bound to a step. Every argument is checked first, so a failed call
binds nothing. `register_steps()` with no steps raises `TypeError`.

`@step` raises `ValueError` for a name that starts with `_`, is not an
identifier, is a keyword or is `<lambda>`, and `TypeError` for something
without a `__name__` and `__module__`. The same `ValueError` applies to names
and namespace segments given to `step_namespace` and `register_steps`.

### When a module is loaded twice

Python treats each module name as a separate module, so a file that is
reachable under two names, or executed twice, runs twice. Common triggers:

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
  effect, and the colliding copies even share the full module path.

Only steps whose defaults or captured values include non-builtin objects
(functions, classes, sentinels, instances) are affected. Plain steps, and
steps with only builtin values, are recognised as one step. An affected step
raises `AmbiguousStep` on its bare name and its short namespace path. Each
copy's full module path still resolves when the copies have different module
names, except `__main__`, which cannot be typed; after a reload or a cell
re-run the full path is shared, so it is ambiguous too.

To avoid it:

* give test packages an `__init__.py` and import them one way, or use
  `--import-mode=importlib`;
* don't import from `conftest.py`;
* keep runnable scripts separate from step modules;
* before a reload or a cell re-run, `unregister_steps` the old steps.

### Importing step modules

A step exists once its module has been imported. Either import the step
modules a test uses, or have the library expose them through a
`safetydance.steps` entry point. The entry point's name is only a label; it
should match the namespace the module registers, and its value is the module:

```toml
[project.entry-points."safetydance.steps"]
http = "my_package.steps"
```

The first lookup that does not start with `_` imports every installed
`safetydance.steps` entry point, so what a lookup returns never depends on the
order of lookups. A successfully loaded entry point is not loaded again.

If an entry point fails to import, the error reaches the caller from the
lookup, including a `hasattr(Given, ...)` miss, and the steps it had
registered are rolled back: the registrations made by modules whose import
failed are removed, and those made by modules that did import are kept, as
those modules are not imported again. The next lookup tries the entry point again
and raises again until it is fixed, so one broken plugin's `ImportError`
surfaces from every lookup. An `AttributeError` raised while importing is
re-raised as `ImportError` naming the entry point, so `hasattr` and
`getattr(Given, name, default)` do not hide it.

### Errors, names and introspection

`StepNotFound` and `AmbiguousStep` are sibling `AttributeError`s, neither a
kind of the other. `except StepNotFound` does not catch ambiguity, and
`hasattr` and `getattr(Given, name, default)` treat both as absent.

Lookups of names starting with `_` on `Given` raise a plain `AttributeError`.
Below the root, a single-underscore segment is reachable, so a module
`pkg._impl.steps` works as `Given.pkg._impl.steps.get`; dunder segments are
not. A module run as `__main__` (a script, or a notebook) therefore has no
reachable full path: its steps are found by bare name or a short namespace.

`dir(Given)` and `dir(Given.http)` list only the names already registered, so
before the first lookup they list only steps from modules already imported.
Entry points load on the first lookup, not on `dir()`.

Error messages suggest a full module path only when every segment can be typed
as an attribute, so a module named `my-steps` or `pkg.class.steps`, or one run
as `__main__`, is advised a short namespace or `register_steps` instead.

`Given`, `When`, `Then` and `And` are one object, and `Given.x = ...` raises
`AttributeError`.

```{note}
Rollback is not atomic across threads: if another thread registers the very
same step at the moment a load fails, the rollback can remove it.
```
