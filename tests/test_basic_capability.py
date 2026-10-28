import logging
import pytest
from safetydance import Context, ContextProperty, context


basic_data = ContextProperty[str]()
data_with_initializer = ContextProperty[str](initializer=lambda ctx: "initialized_value")


@context
def test_context_data_get():
    basic_data.value = "value1"
    assert basic_data.value == "value1"


@context
def test_context_data_not_found():
    with pytest.raises(KeyError):
        basic_data.value is None


@context
def test_context_data_is_set():
    assert not basic_data.is_set

    basic_data.value = "value"
    assert basic_data.is_set
    assert basic_data.value == "value"


@context
def test_context_get_with_initializer():
    assert data_with_initializer.is_set
    assert data_with_initializer.value == "initialized_value"


@context
def test_step_usage():
    """This should actually validate that the chaining of context initialization works
    as expected due to the decorator..."""
    some_context_data = ContextProperty[str]()

    def test_step_function(arg1, arg2=None):
        some_context_data.value = "foo"
        return f"{arg1}-{arg2}"

    # Call the step function
    result = test_step_function("value1", arg2="value2")

    # Verify the result
    assert result == "value1-value2"

    # Verify that the context was added to the frame locals
    assert some_context_data.is_set
    assert some_context_data.value == "foo"


@context
def test_step_with_context_data():
    # Create ContextData variables
    test_data = ContextProperty()
    test_data.initializer = None

    # Create a step function that uses the context
    def set_test_data():
        test_data.value = "test_value"
        assert test_data.is_set

    # Create another step function that uses the same context
    def get_test_data():
        assert test_data.is_set
        assert test_data.value == "test_value"

    # Test the integration
    set_test_data()
    get_test_data()


@context(
    tracing_log_level=logging.INFO, tracing_logger=logging.getLogger("safetydance")
)
def test_context_tracing(caplog):  # Use pytest's caplog fixture
    # Create a ContextData to track
    traced_data = ContextProperty()
    traced_data.initializer = None

    # Set caplog to the right level
    caplog.set_level(logging.INFO)

    # Perform operations that should be traced
    traced_data.value = "traced_value"
    assert traced_data.value == "traced_value"

    # Modify the value to generate another trace
    traced_data.value = "updated_value"

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


def test_call_gets_value_from_given_context():
    ctx = Context()
    ctx[basic_data] = "explicit"
    assert basic_data(ctx) == "explicit"


def test_call_not_found_raises_key_error():
    with pytest.raises(KeyError):
        basic_data(Context())


def test_call_runs_initializer_in_given_context():
    ctx = Context()
    assert data_with_initializer(ctx) == "initialized_value"
    assert ctx[data_with_initializer] == "initialized_value"


def test_setitem_sets_value_in_given_context():
    ctx = Context()
    basic_data[ctx] = "assigned"
    assert basic_data(ctx) == "assigned"
    with ctx:
        assert basic_data.value == "assigned"


@context
def test_call_and_setitem_ignore_active_context():
    other = Context()
    basic_data[other] = "other"
    assert not basic_data.is_set

    basic_data.value = "active"
    assert basic_data(other) == "other"


def test_call_and_setitem_trace_given_context(caplog):
    caplog.set_level(logging.INFO)
    ctx = Context(tracing=True, tracing_logger=logging.getLogger("safetydance"))

    basic_data[ctx] = "traced"
    basic_data(ctx)

    messages = [record.message for record in caplog.records]
    assert any("set by" in message and __file__ in message for message in messages)
    assert any("retrieved by" in message and __file__ in message for message in messages)
