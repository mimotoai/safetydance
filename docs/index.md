---
kernelspec:
  name: python3
  display_name: Python 3
---

# safetydance

safetydance lets plain functions act as **steps** that share data through an
execution context. A **script** runs its steps in a fresh context, so scripts
never see each other's data, even when one calls another or they run
concurrently. Write tests and flows that read like sentences, with typed shared
state instead of passing arguments through every call.

```bash
pip install safetydance
```

```{code-cell} python
from safetydance import context_data, initializer, script


@context_data
class Shop:
    cart: list[tuple[str, float]] = initializer(lambda ctx: [])
    total: float = 0.0


def adds_to_cart(item: str, price: float):
    Shop.cart.append((item, price))


def checks_out():
    Shop.total = sum(price for _, price in Shop.cart)


@script
def buy_breakfast() -> float:
    adds_to_cart("coffee", 3.50)
    adds_to_cart("bagel", 2.50)
    checks_out()
    return Shop.total


assert buy_breakfast() == 6.0
```

Next: [installation](installation.md), the [concepts](concepts.md),
[async code and threads](async-and-threads.md), [testing](testing.md), and
[migrating from 0.3](migrating.md).
