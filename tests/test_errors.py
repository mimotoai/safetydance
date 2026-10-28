import inspect
import logging

import pytest

from safetydance import (
    ContextDataNotSet,
    ContextProperty,
    NoActiveContext,
    context_data,
    get_context,
    script,
)


@context_data
class Fields:
    name: str


def test_get_context_outside_script_raises_no_active_context():
    assert issubclass(NoActiveContext, RuntimeError)
    with pytest.raises(NoActiveContext, match="@script"):
        get_context()


def test_unset_value_raises_context_data_not_set():
    prop = ContextProperty[int]("count")

    @script
    def run():
        return prop.value

    with pytest.raises(ContextDataNotSet) as excinfo:
        run()
    assert isinstance(excinfo.value, KeyError)
    assert isinstance(excinfo.value, AttributeError)


def test_hasattr_on_field_outside_script_is_false():
    assert hasattr(Fields, "name") is False


def test_getattr_default_for_unset_field_inside_script():
    @script
    def run():
        return getattr(Fields, "name", "dflt")

    assert run() == "dflt"


def test_getmembers_outside_script_does_not_raise():
    inspect.getmembers(Fields)


def test_field_trace_names_caller_file(caplog):
    caplog.set_level(logging.INFO)

    @script(tracing_logger=logging.getLogger("safetydance"))
    def run():
        Fields.name = "x"
        return Fields.name

    run()
    messages = [record.message for record in caplog.records]
    assert any("set by" in m and __file__ in m for m in messages)
    assert any("retrieved by" in m and __file__ in m for m in messages)
