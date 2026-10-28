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
    Storage.tmp_path  # the pytest fixture
    ...
```

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
