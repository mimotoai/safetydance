import logging

import pytest

from safetydance import context, context_data


@context_data
class TestData:
    basic: str
    with_initializer: str = lambda ctx: "initialized_value"


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
    # assert TestData.is_set("basic")
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
