---
kernelspec:
  name: python3
  display_name: Python 3
---

# Installation

```bash
pip install safetydance
```

safetydance needs Python 3.10 or later and has no runtime dependencies. It ships
type hints (`py.typed`), and decorated scripts keep their signatures for type
checkers.

```{code-cell} python
import safetydance

print(sorted(safetydance.__all__))
assert "script" in safetydance.__all__
```

Next: the [quickstart](index.md) or the [concepts](concepts.md).
