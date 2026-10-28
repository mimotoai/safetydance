import logging

import pytest

from safetydance import (
    ContextData,
    ContextDataNotSet,
    NoActiveContext,
    context_data,
    initializer,
    script,
)
from safetydance._safetydance import ContextPropertyDescriptor


@context_data
class Base:
    a: int


@context_data
class Child(Base):
    name: str
    n: int = 5


@script
def test_decorated_subclass_fields_read_values():
    assert Child.n == 5
    with pytest.raises(ContextDataNotSet):
        Child.name
    Child.a = 1
    assert Base.a == 1


def test_double_decoration_is_a_no_op():
    class C:
        x: int = 0

    once = context_data(C)
    assert context_data(once) is once

    @script
    def run():
        assert once.x == 0

    run()


@script
def test_copied_field_descriptor_is_not_a_default():
    copy = ContextData(
        "Copy", (), {"__annotations__": {"n": int}, "n": Child.__dict__["n"]}
    )
    assert copy.n == 5
    copy.n = 6
    assert copy.n == 6
    assert Child.n == 5


@context_data
class Order:
    total: float


@context_data
class Rush(Order):
    total: float = 3.0


def test_redeclared_field_shares_base_slot():
    @script
    def assign():
        Rush.total = 7.0
        assert Order.total == 7.0

    @script
    def read_default():
        assert Rush.total == 3.0
        with pytest.raises(ContextDataNotSet):
            Order.total  # reading Rush's default stores nothing for Order

    assign()
    read_default()


@context_data
class Level:
    level: int = 1


@context_data
class DeeperLevel(Level):
    level: int = 2


def test_redeclared_fields_read_their_own_default_until_assigned():
    @script
    def child_first():
        return DeeperLevel.level, Level.level

    @script
    def base_first():
        return Level.level, DeeperLevel.level

    @script
    def assigned():
        Level.level = 5
        return Level.level, DeeperLevel.level

    assert child_first() == (2, 1)
    assert base_first() == (1, 2)
    assert assigned() == (5, 5)


@context_data
class Basket:
    items: list[str] = initializer(lambda ctx: [])


@context_data
class GiftBasket(Basket):
    items: list[str] = initializer(lambda ctx: ["card"])


@script
def test_redeclared_initializers_make_one_value_per_class():
    GiftBasket.items.append("ribbon")
    Basket.items.append("apple")
    assert GiftBasket.items == ["card", "ribbon"]
    assert Basket.items == ["apple"]
    Basket.items = ["shared"]
    assert GiftBasket.items == ["shared"]


@script
def test_del_redeclared_field_clears_every_class_default():
    gift_items = GiftBasket.items
    basket_items = Basket.items
    del Basket.items
    assert GiftBasket.items is not gift_items
    assert Basket.items is not basket_items


@context_data
class Top:
    level: str


class Middle(Top):
    pass


@context_data
class Leaf(Middle):
    level: str = "leaf"


def test_three_level_hierarchy():
    classes = (Top, Middle, Leaf)

    for assigned in classes:

        @script
        def run():
            assigned.level = assigned.__name__
            for klass in classes:
                assert klass.level == assigned.__name__

        run()


@context_data
class Shop:
    customer: str


def test_del_field_unsets_value():
    @script
    def script_a():
        Shop.customer = "alice"
        del Shop.customer
        with pytest.raises(ContextDataNotSet):
            Shop.customer
        Shop.customer = "x"
        assert Shop.customer == "x"

    @script
    def script_b():
        with pytest.raises(ContextDataNotSet):
            Shop.customer

    script_a()
    script_b()
    assert isinstance(Shop.__dict__["customer"], ContextPropertyDescriptor)


@script
def test_del_unset_field_is_not_an_error():
    del Shop.customer
    with pytest.raises(ContextDataNotSet):
        Shop.customer


def test_del_field_outside_a_context_raises():
    with pytest.raises(NoActiveContext):
        del Shop.customer


def test_del_redeclared_field_unsets_shared_slot():
    @script
    def run():
        Order.total = 9.0
        del Rush.total
        with pytest.raises(ContextDataNotSet):
            Order.total
        assert Rush.total == 3.0

    run()


def test_del_non_field_attribute():
    @context_data
    class Store:
        customer: str

        @staticmethod
        def helper():
            return "help"

    assert Store.helper() == "help"
    del Store.helper
    assert "helper" not in Store.__dict__
    assert not hasattr(Store, "helper")


def test_del_field_is_traced(caplog):
    caplog.set_level(logging.INFO)

    @script(tracing_log_level=logging.INFO)
    def run():
        Shop.customer = "alice"
        del Shop.customer

    run()
    [deleted] = [r for r in caplog.records if " deleted by " in r.message]
    assert "customer" in deleted.message
    assert __file__ in deleted.message
