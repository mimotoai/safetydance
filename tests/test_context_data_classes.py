import abc
import enum
import logging
from unittest import mock

import pytest

from safetydance import (
    ContextData,
    ContextDataNotSet,
    NoActiveContext,
    context_data,
    script,
    test_context,
)
from safetydance._safetydance import ContextPropertyDescriptor


@context_data
class Base:
    a: int
    n: int = 5


def test_subclassing_a_decorated_class_raises():
    with pytest.raises(TypeError, match="Base is a context_data class"):

        @context_data
        class Child(Base):
            name: str


def test_subclassing_without_the_decorator_raises():
    with pytest.raises(TypeError, match="can't be subclassed"):

        class Child(Base):
            pass


def test_subclassing_a_metaclass_built_class_raises():
    class Built(metaclass=ContextData):
        x: int = 1

    with pytest.raises(TypeError, match=r"Child: .*Built is a context_data class"):

        class Child(Built, metaclass=ContextData):
            y: int = 2


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
        "Copy", (), {"__annotations__": {"n": int}, "n": Base.__dict__["n"]}
    )
    assert copy.n == 5
    copy.n = 6
    assert copy.n == 6
    assert Base.n == 5


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


@script
def test_del_field_through_an_instance_unsets_value():
    shop = Shop()
    shop.customer = "alice"
    assert Shop.customer == "alice"
    del shop.customer
    with pytest.raises(ContextDataNotSet):
        Shop.customer


def test_del_field_outside_a_context_raises():
    with pytest.raises(NoActiveContext):
        del Shop.customer


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


def test_failed_read_is_traced_as_not_found(caplog):
    caplog.set_level(logging.INFO)

    @script(tracing_log_level=logging.INFO)
    def run():
        with pytest.raises(ContextDataNotSet):
            Shop.customer

    run()
    messages = [r.message for r in caplog.records]
    assert any(" customer " in m and " not found by " in m for m in messages)
    assert not any(" retrieved by " in m for m in messages)


def test_slotted_class_can_be_decorated():
    @context_data
    class Slotted:
        __slots__ = ("label", "__secret")
        count: int = 1

        def reveal(self):
            self.__secret = "s"
            return self.__secret

    item = Slotted()
    item.label = "x"
    assert item.label == "x"
    assert item.reveal() == "s"
    assert not hasattr(item, "__dict__")

    @script
    def run():
        Slotted.count = 2
        return Slotted.count

    assert run() == 2


def test_abc_subclass_can_be_decorated():
    @context_data
    class Shape(abc.ABC):
        sides: int = 3

        @abc.abstractmethod
        def area(self): ...

    assert isinstance(Shape, ContextData)
    assert isinstance(Shape, abc.ABCMeta)
    with pytest.raises(TypeError, match="abstract"):
        Shape()

    @script
    def run():
        Shape.sides = 4
        return Shape.sides

    assert run() == 4


def test_enum_cannot_be_decorated():
    class Color(enum.Enum):
        RED = 1

    with pytest.raises(TypeError, match="Color: an Enum can't be a context_data"):
        context_data(Color)


@context_data
class Cfg:
    level: int = 1


@script
def test_mock_patch_object_sets_the_field_in_the_context_and_restores_it():
    with mock.patch.object(Cfg, "level", 5):
        assert Cfg.level == 5
    assert Cfg.level == 1
    assert isinstance(Cfg.__dict__["level"], ContextPropertyDescriptor)


def test_monkeypatch_restores_the_field_after_the_context_has_gone(pytester):
    pytester.makepyfile(
        """
        from safetydance import context_data, test_context

        @context_data
        class Cfg:
            level: int = 1

        @test_context
        def test_patched(monkeypatch):
            monkeypatch.setattr(Cfg, "level", 5)
            assert Cfg.level == 5

        @test_context
        def test_restored():
            assert Cfg.level == 1
        """
    )
    pytester.runpytest("-p", "no:randomly").assert_outcomes(passed=2)


@context_data
class Named:
    n: int


def test_del_keeps_a_value_supplied_by_name():
    @test_context
    def run(n):
        Named.n = 2
        del Named.n
        return Named.n

    assert run(n=1) == 1
