---
kernelspec:
  name: python3
  display_name: Python 3
---

# safetydance

Steps are plain functions that share data through an execution context; a
**script** runs its steps in a fresh one.

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


buy_breakfast()
```
