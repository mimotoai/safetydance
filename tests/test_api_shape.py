import logging

import pytest

from safetydance import Context, ContextProperty, script, test_context


def test_has_value_replaces_is_set():
    assert not hasattr(ContextProperty, "is_set")
    prop = ContextProperty[int]()

    @script
    def run():
        before = prop.has_value
        prop.value = 1
        return before, prop.has_value

    assert run() == (False, True)


def test_tracing_level_is_keyword_only():
    with pytest.raises(TypeError, match="keyword"):
        script(logging.DEBUG)
    with pytest.raises(TypeError, match="keyword"):
        test_context(logging.DEBUG)


def test_tracing_options_work_as_keywords():
    @script(tracing_log_level=logging.DEBUG)
    def run():
        return 1

    assert run() == 1


def test_contexts_compare_by_identity():
    first = Context()
    assert first != Context()
    assert first == first
    assert {first: 1}[first] == 1
