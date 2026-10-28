import logging
import threading

import pytest

import safetydance
from safetydance import Context, ContextProperty, context, get_context


nested_data = ContextProperty[str]()
fixture_data = ContextProperty[str](name="fixture_value")


@context
def test_nested_context_writes_are_not_visible_in_parent():
    with Context():
        nested_data.value = "inner"
    assert not nested_data.has_value


@context
def test_nested_context_does_not_see_parent_values():
    nested_data.value = "outer"
    with Context():
        assert not nested_data.has_value


@context
def test_parent_value_is_intact_after_nested_context_sets_it():
    nested_data.value = "outer"
    with Context():
        nested_data.value = "inner"
        assert nested_data.value == "inner"
    assert nested_data.value == "outer"


@context
def test_each_level_of_nesting_is_isolated():
    nested_data.value = "outer"
    with Context():
        nested_data.value = "middle"
        with Context():
            assert not nested_data.has_value
            nested_data.value = "innermost"
        assert nested_data.value == "middle"
    assert nested_data.value == "outer"


@context
def test_nested_script_is_isolated_from_calling_script():
    @context
    def nested_script():
        assert not nested_data.has_value
        nested_data.value = "from nested script"
        return nested_data.value

    nested_data.value = "outer"
    assert nested_script() == "from nested script"
    assert nested_data.value == "outer"


@context
def test_step_called_from_script_shares_its_context():
    def step():
        assert nested_data.value == "outer"
        nested_data.value = "from step"

    nested_data.value = "outer"
    step()
    assert nested_data.value == "from step"


def test_flow_is_reusable_as_a_step_and_as_a_script():
    def log_in():
        nested_data.value = "token"

    log_in_script = context(log_in)

    @context
    def checkout():
        log_in()
        return nested_data.value

    assert checkout() == "token"
    log_in_script()


@context
def test_nested_test_context_fixtures_stay_in_nested_context():
    @safetydance.test_context
    def inner(fixture_value):
        assert fixture_data.value == "fixture"

    inner(fixture_value="fixture")
    assert not fixture_data.has_value


def test_explicit_access_through_nested_context_is_isolated():
    with Context() as outer:
        with Context() as inner:
            nested_data[inner] = "explicit"
    assert nested_data(inner) == "explicit"
    with pytest.raises(KeyError):
        nested_data(outer)


def test_context_supports_mapping_methods():
    ctx = Context({"a": 1})
    assert len(ctx) == 1
    assert list(ctx) == ["a"]
    assert dict(ctx.items()) == {"a": 1}
    assert ctx.setdefault("b", 2) == 2
    assert ctx.pop("a") == 1
    del ctx["b"]
    assert len(ctx) == 0


def test_nested_context_initial_data_stays_in_nested_context():
    with Context() as outer:
        with Context({nested_data: "initial"}, fixture_value="kwarg"):
            assert nested_data.value == "initial"
            assert fixture_data.value == "kwarg"
        assert len(outer) == 0


def test_reentering_active_context_raises():
    with Context() as ctx:
        with pytest.raises(RuntimeError):
            with ctx:
                pass
        assert get_context() is ctx
        nested_data.value = "still usable"
        assert nested_data.value == "still usable"


def test_reentering_context_from_further_up_the_chain_raises():
    with Context() as outer:
        with Context() as inner:
            with pytest.raises(RuntimeError):
                with outer:
                    pass
            assert get_context() is inner
            nested_data.value = "still usable"
        assert get_context() is outer
        assert nested_data(inner) == "still usable"


def test_reused_context_on_new_thread_has_no_stale_parent():
    with Context() as outer:
        with Context() as reused:
            pass

    def run_on_new_thread():
        with reused:
            nested_data.value = "thread"

    thread = threading.Thread(target=run_on_new_thread)
    thread.start()
    thread.join()

    assert nested_data(reused) == "thread"
    with pytest.raises(KeyError):
        nested_data(outer)


def test_tracing_propagates_to_empty_parent(caplog):
    caplog.set_level(logging.INFO)
    with Context(tracing=True, tracing_logger=logging.getLogger("safetydance")):
        with Context():
            nested_data.value = "traced"
    assert any("set by" in record.message for record in caplog.records)
