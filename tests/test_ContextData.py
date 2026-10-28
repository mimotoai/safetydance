import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass

import pytest

import safetydance
from safetydance import context, context_data, initializer


@context_data
class TestData:
    basic: str
    with_initializer: str = initializer(lambda ctx: "initialized_value")


@dataclass
class ComplexType:
    value: int
    name: str


@context_data
class MultipleData:
    """Context data class with multiple independent properties"""
    counter: int
    name: str
    complex_obj: ComplexType
    data_dict: dict


@context
def test_context_data_get():
    TestData.basic = "value1"
    assert TestData.basic == "value1"


@context
def test_context_data_not_found():
    with pytest.raises(KeyError):
        TestData.basic is not None


@context
def test_context_get_with_initializer():
    assert TestData.with_initializer == "initialized_value"


@context
def test_step_usage():
    """This should actually validate that the chaining of context initialization works
    as expected due to the decorator..."""

    def test_step_function(arg1, arg2=None):
        TestData.basic = "foo"
        return f"{arg1}-{arg2}"

    # Call the step function
    result = test_step_function("value1", arg2="value2")

    # Verify the result
    assert result == "value1-value2"

    # Verify that the context was added to the frame locals
    assert TestData.basic == "foo"


@context
def test_step_with_context_data():
    def set_test_data():
        TestData.basic = "test_value"

    # Create another step function that uses the same context
    def get_test_data():
        assert TestData.basic == "test_value"

    set_test_data()
    get_test_data()


@context(
    tracing_log_level=logging.INFO, tracing_logger=logging.getLogger("safetydance")
)
def test_context_tracing(caplog):  # Use pytest's caplog fixture
    # Set caplog to the right level
    caplog.set_level(logging.INFO)

    # Perform operations that should be traced
    TestData.basic = "traced_value"
    assert TestData.basic == "traced_value"

    # Modify the value to generate another trace
    TestData.basic = "updated_value"

    # Check that the log contains appropriate trace messages
    assert any(
        "retrieved by" in record.message
        for record in caplog.records
        if record.levelname == "INFO"
    )

    assert any(
        "set by" in record.message
        for record in caplog.records
        if record.levelname == "INFO"
    )


@context
def test_parameter_shadowing():
    """Test that function parameters don't interfere with context properties of the same name.

    This verifies that local function parameters shadow context properties without affecting
    the global context values.
    """
    # Set up context data
    MultipleData.counter = 42
    MultipleData.name = "global_name"

    # Function with parameters that shadow context property names
    def function_with_shadowing(counter, name, extra=None):
        # These are local parameters, not context properties
        assert counter == "string_value"  # Local parameter is a string
        assert name == 99.5  # Local parameter is a float
        assert extra == "extra_value"

        # Context properties should still be accessible and unchanged
        assert MultipleData.counter == 42  # Context still has int
        assert MultipleData.name == "global_name"  # Context still has string

        return f"{counter}-{name}-{extra}"

    # Call with arguments that shadow context property names
    result = function_with_shadowing("string_value", 99.5, extra="extra_value")
    assert result == "string_value-99.5-extra_value"

    # Verify context properties are unchanged after function returns
    assert MultipleData.counter == 42
    assert MultipleData.name == "global_name"


@context
def test_multiple_independent_properties():
    """Test that multiple context properties work independently without interference."""
    # Set all properties to different types
    MultipleData.counter = 100
    MultipleData.name = "test_name"
    MultipleData.complex_obj = ComplexType(value=42, name="complex")
    MultipleData.data_dict = {"key1": "value1", "key2": 2}

    # Verify all are set correctly
    assert MultipleData.counter == 100
    assert MultipleData.name == "test_name"
    assert isinstance(MultipleData.complex_obj, ComplexType)
    assert MultipleData.complex_obj.value == 42
    assert MultipleData.complex_obj.name == "complex"
    assert MultipleData.data_dict == {"key1": "value1", "key2": 2}

    # Modify one property
    MultipleData.counter = 200

    # Verify only that property changed
    assert MultipleData.counter == 200
    assert MultipleData.name == "test_name"  # Unchanged
    assert MultipleData.complex_obj.value == 42  # Unchanged
    assert MultipleData.data_dict["key1"] == "value1"  # Unchanged


@context
def test_complex_data_types():
    """Test that complex data types (dataclasses, custom objects) work correctly in context."""
    # Create and store a complex object
    obj = ComplexType(value=123, name="test_object")
    MultipleData.complex_obj = obj

    # Retrieve and verify
    retrieved = MultipleData.complex_obj
    assert isinstance(retrieved, ComplexType)
    assert retrieved.value == 123
    assert retrieved.name == "test_object"
    assert retrieved is obj  # Should be the same object

    # Modify the object
    retrieved.value = 456

    # Verify the context still holds the modified object
    assert MultipleData.complex_obj.value == 456
    assert MultipleData.complex_obj is obj


@context
def test_nested_context_sharing():
    """Test that context is properly shared across nested function calls."""

    def level_one():
        """First level: sets initial values"""
        MultipleData.counter = 1
        MultipleData.name = "level_one"
        MultipleData.data_dict = {"level": 1}

    def level_two():
        """Second level: reads and modifies values from level_one"""
        assert MultipleData.counter == 1
        assert MultipleData.name == "level_one"

        MultipleData.counter = 2
        MultipleData.name = "level_two"
        MultipleData.data_dict["level"] = 2

    def level_three(counter, name):
        """Third level: parameters shadow context, but context is still accessible"""
        # Parameters shadow context property names
        assert counter == "param_value"
        assert name == "param_name"

        # But context properties are still accessible
        assert MultipleData.counter == 2
        assert MultipleData.name == "level_two"
        assert MultipleData.data_dict["level"] == 2

        # Modify context
        MultipleData.counter = 3
        MultipleData.data_dict["level"] = 3

    # Execute the nested call chain
    level_one()
    assert MultipleData.counter == 1

    level_two()
    assert MultipleData.counter == 2

    level_three("param_value", "param_name")
    assert MultipleData.counter == 3
    assert MultipleData.name == "level_two"  # Unchanged by level_three
    assert MultipleData.data_dict["level"] == 3


@context_data
class Order:
    total: float


@context_data
class Invoice:
    total: float


@context
def test_same_field_name_in_different_classes_is_independent():
    Order.total = 10.0
    Invoice.total = 99.0
    assert Order.total == 10.0
    assert Invoice.total == 99.0


@context
def test_same_field_name_unset_in_other_class_is_not_found():
    Order.total = 10.0
    with pytest.raises(KeyError):
        Invoice.total


@context
def test_not_found_error_names_declaring_class():
    with pytest.raises(KeyError, match="Order"):
        Order.total


def test_fixture_binds_to_context_data_field_by_name():
    @safetydance.test_context
    def uses_fixture(total):
        assert Order.total == total
        assert Invoice.total == total

    uses_fixture(total=5.0)


@context
def test_subclass_without_annotations_inherits_fields():
    class Child(Order):
        pass

    Child.total = 3.0
    assert Child.total == 3.0


@pytest.mark.skipif(sys.version_info < (3, 14), reason="lazy annotations")
@context
def test_forward_reference_annotation_is_allowed():
    @context_data
    class Pending:
        later: DefinedAfterTheClass

    Pending.later = "value"
    assert Pending.later == "value"


class Describable:
    @classmethod
    def describe(cls):
        return f"base:{cls.__name__}"


@context_data
class Described(Describable):
    label: str

    @classmethod
    def describe(cls):
        return "sub+" + super().describe()


def test_zero_arg_super_works_in_rebuilt_class():
    assert Described.describe() == "sub+base:Described"


def test_rebuilt_class_keeps_qualname():
    @context_data
    class Local:
        value: str

    assert Local.__qualname__.endswith("<locals>.Local")


def test_subclass_assignment_of_inherited_field_goes_to_context():
    class Child(Order):
        pass

    @context
    def run():
        Child.total = 7.0
        assert Order.total == 7.0

    run()
    assert "total" not in Child.__dict__

    @context
    def next_run():
        with pytest.raises(KeyError):
            Child.total

    next_run()


def test_non_field_class_attribute_can_be_assigned():
    @context_data
    class Reassigned:
        label: str

        @classmethod
        def describe(cls):
            return "original"

    Reassigned.describe = classmethod(lambda cls: "replaced")
    Reassigned.extra = 1
    assert Reassigned.describe() == "replaced"
    assert Reassigned.extra == 1


def format_total(total: float) -> str:
    return f"${total:.2f}"


@context_data
class Defaults:
    count: int = 0
    nothing: str | None = None
    built: list = initializer(lambda ctx: ["fresh"])
    formatter: Callable[[float], str] = format_total


@context
def test_non_callable_default_is_a_constant():
    assert Defaults.count == 0
    assert Defaults.nothing is None
    Defaults.count += 1
    assert Defaults.count == 1


@context
def test_initializer_default_runs_on_first_read():
    assert Defaults.built == ["fresh"]


def test_initializer_default_is_fresh_in_each_context():
    @context
    def run():
        Defaults.built.append("added")
        return Defaults.built

    assert run() == ["fresh", "added"]
    assert run() == ["fresh", "added"]


@context
def test_callable_default_is_a_constant():
    assert Defaults.formatter is format_total
    assert Defaults.formatter(3) == "$3.00"


def test_mutable_default_is_rejected():
    with pytest.raises(ValueError, match=r"items.*initializer\(lambda ctx: \[\]\)"):

        @context_data
        class Shared:
            items: list = []


def test_classvar_is_not_a_field():
    from typing import ClassVar

    @context_data
    class Constants:
        VERSION: ClassVar[int] = 1
        REGISTRY: ClassVar[dict] = {}
        LABEL: "ClassVar[str]" = "x"
        QUALIFIED: "typing.ClassVar[int]" = 2
        real: int = 3

    assert Constants.VERSION == 1
    assert Constants.REGISTRY == {}
    assert Constants.LABEL == "x"
    assert Constants.QUALIFIED == 2

    @context
    def read():
        return Constants.real

    assert read() == 3


def test_metaclass_accepts_class_keywords():
    class Base:
        def __init_subclass__(cls, tag=None, **kwargs):
            super().__init_subclass__(**kwargs)
            cls.tag = tag

    class K(Base, metaclass=safetydance.ContextData, tag="x"):
        value: int = 1

    assert K.tag == "x"


def test_super_in_decorated_method():
    from functools import wraps

    def logged(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        return wrapper

    class Base:
        @classmethod
        def describe(cls):
            return "base"

    @context_data
    class Child(Base):
        value: int = 1

        @classmethod
        @logged
        def describe(cls):
            return "child+" + super().describe()

        @logged
        def instance(self):
            return super().__repr__()

    assert Child.describe() == "child+base"
    assert "Child" in Child().instance()


def test_super_in_partialmethod_and_cached_property():
    from functools import cached_property, partialmethod

    class Base:
        def greet(self, punctuation):
            return "hi" + punctuation

    @context_data
    class Child(Base):
        value: int = 1

        def greet(self, punctuation):
            return super().greet(punctuation) + "!"

        shout = partialmethod(greet, "?")

        @cached_property
        def name(self):
            return super().greet("")

    assert Child().shout() == "hi?!"
    assert Child().name == "hi"


def test_initializer_without_annotation_raises():
    with pytest.raises(TypeError, match="cart"):

        @context_data
        class Missing:
            cart = initializer(lambda ctx: [])


@pytest.mark.parametrize("empty", [{}, set()])
def test_mutable_dict_and_set_defaults_are_rejected(empty):
    with pytest.raises(ValueError, match="field"):

        @context_data
        class Shared:
            field: object = empty


@context
def test_initializer_receives_active_context():
    @context_data
    class Probe:
        base: int
        seen: int = initializer(lambda ctx: Probe.base + 1)
        active: object = initializer(lambda ctx: ctx)

    Probe.base = 4
    assert Probe.seen == 5
    assert Probe.active is safetydance.get_context()


@context_data
class Named:
    value: int


def test_stored_value_beats_name_supplied_value():
    @safetydance.test_context
    def run(value):
        assert Named.value == value
        Named.value = 2
        assert Named.value == 2

    run(value=1)


def test_metaclass_used_directly():
    class X(metaclass=safetydance.ContextData):
        a: int = 1
        b: int

    @context
    def run():
        assert X.a == 1
        X.a = 2
        X.b = 3
        return X.a, X.b

    assert run() == (2, 3)

    @context
    def fresh():
        return X.a

    assert fresh() == 1


def test_super_in_property_of_context_data_class():
    class Base:
        @property
        def label(self):
            return "base"

    @context_data
    class Child(Base):
        value: int = 1

        @property
        def label(self):
            return "child+" + super().label

    assert Child().label == "child+base"


class _CyclicWrapped:
    def __call__(self):
        pass

    @property
    def __wrapped__(self):
        return self


class _EndlessProxy:
    def __call__(self):
        pass

    def __getattr__(self, name):
        return _EndlessProxy()


class _NonCallableProxy:
    def __getattr__(self, name):
        return _NonCallableProxy()


class _BrokenWrapped:
    def __call__(self):
        pass

    @property
    def __wrapped__(self):
        raise RuntimeError("no lookups here")


@pytest.mark.parametrize(
    "attr", [_CyclicWrapped, _EndlessProxy, _NonCallableProxy, _BrokenWrapped]
)
def test_class_attributes_with_odd_wrapped_do_not_stop_decoration(attr):
    built = []

    def decorate():
        @context_data
        class Holder:
            odd = attr()
            value: int = 1

        built.append(Holder)

    # A daemon thread, so a regression to an endless __wrapped__ walk fails
    # the test instead of hanging the run.
    worker = threading.Thread(target=decorate, daemon=True)
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive(), "context_data hung on the class attribute"
    assert len(built) == 1
    assert isinstance(built[0].__dict__["odd"], attr)
