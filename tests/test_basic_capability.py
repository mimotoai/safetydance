import logging
import pytest
from safetydance import ContextProperty, context


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
