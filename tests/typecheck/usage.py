"""Type-checked by mypy (`uv run nox -s typecheck`); not collected by pytest."""

from collections.abc import Callable
from typing import assert_type

from safetydance import (
    ContextProperty,
    Given,
    context_data,
    get_context,
    initializer,
    register_steps,
    script,
    step,
    step_namespace,
)


def format_total(total: float) -> str:
    return f"${total:.2f}"


@context_data
class Shop:
    customer: str
    cart: list[tuple[str, float]] = initializer(lambda ctx: [])
    count: int = 0
    formatter: Callable[[float], str] = format_total


discount = ContextProperty[float](initializer=lambda ctx: 0.0)


def adds_to_cart(item: str, price: float) -> None:
    Shop.cart.append((item, price))


@script
def checkout(customer: str) -> float:
    Shop.customer = customer
    adds_to_cart("coffee", 3.50)
    assert_type(Shop.cart, list[tuple[str, float]])
    assert_type(Shop.count, int)
    assert_type(Shop.formatter(1.0), str)
    assert_type(discount.value, float)
    assert_type(discount.has_value, bool)
    assert_type(discount(get_context()), float)
    return sum(price for _, price in Shop.cart) * (1 - discount.value)


@script
async def checkout_async(customer: str) -> float:
    return 0.0


assert_type(checkout("Ada"), float)


async def main() -> None:
    assert_type(await checkout_async("Ada"), float)


@step
def typed_step(x: int) -> str:
    return str(x)


assert_type(typed_step(1), str)


@step_namespace("typed")
def typed_ns_step(x: int) -> str:
    return str(x)


assert_type(typed_ns_step(1), str)

register_steps(typed_step, namespace="other")


@script
def uses_prefix() -> None:
    Given.typed.typed_ns_step(1)  # Any: resolved by name at run time
