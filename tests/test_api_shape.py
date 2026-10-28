import logging

import pytest

from safetydance import Context, ContextProperty, context, script, test_context


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
    with pytest.raises(TypeError, match="script.*keyword-only"):
        script(logging.DEBUG)
    with pytest.raises(TypeError, match="context.*keyword-only"):
        context(logging.DEBUG)
    with pytest.raises(TypeError, match="test_context.*keyword-only"):
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


@pytest.mark.parametrize("decorator", [script, test_context])
def test_scripts_keep_the_wrapped_function_metadata(decorator):
    def sync(a: int) -> int:
        "sync doc"
        return a

    async def coroutine(a: int) -> int:
        "async doc"
        return a

    def generator(a: int):
        "generator doc"
        yield a

    for func in (sync, coroutine, generator):
        wrapped = decorator(func)
        assert wrapped.__name__ == func.__name__
        assert wrapped.__doc__ == func.__doc__
        assert wrapped.__wrapped__ is func
