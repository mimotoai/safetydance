import copy
import inspect

import pytest

from safetydance import ContextProperty, NoActiveContext, script
from safetydance import _steps
from safetydance._steps import (
    AmbiguousStep,
    And,
    Given,
    StepNotFound,
    Then,
    When,
    step,
    step_namespace,
)


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    fresh = _steps._Registry(entry_points=lambda: ())
    monkeypatch.setattr(_steps, "_registry", fresh)
    return fresh


def make_step(module, name, result=None, filename=None):
    """A function whose __module__ is `module`, defined in a file of its own."""
    namespace = {"__name__": module}
    source = f"def {name}(*args, **kwargs):\n    return {result!r}\n"
    exec(compile(source, filename or f"<{module}>", "exec"), namespace)
    return namespace[name]


def test_step_returns_the_function_unchanged():
    f = make_step("pkg_a.steps", "get")
    assert step(f) is f


def test_bare_name_resolves():
    step(make_step("pkg_a.steps", "get", "a"))
    assert Given.get() == "a"


def test_full_module_path_resolves():
    step(make_step("pkg_a.steps", "get", "a"))
    assert Given.pkg_a.steps.get() == "a"


def test_short_namespace_resolves():
    step_namespace("http")(make_step("pkg_a.steps", "get", "a"))
    assert Given.http.get() == "a"
    assert Given.get() == "a"
    assert Given.pkg_a.steps.get() == "a"


def test_dotted_short_namespace_resolves():
    step_namespace("my.package")(make_step("pkg_a.steps", "get", "a"))
    assert Given.my.package.get() == "a"


def test_prefixes_share_one_registry():
    step(make_step("pkg_a.steps", "get", "a"))
    assert Given.get is When.get is Then.get is And.get


def test_resolved_step_is_the_original_function():
    f = step(make_step("pkg_a.steps", "get", "a"))
    assert Given.get is f


def test_ambiguous_bare_name_lists_candidates():
    # Registered in reverse order: the message must sort candidates.
    step(make_step("pkg_b.steps", "get", "b"))
    step(make_step("pkg_a.steps", "get", "a"))
    with pytest.raises(AmbiguousStep, match=r"pkg_a\.steps\.get.*pkg_b\.steps\.get") as err:
        Given.get
    assert isinstance(err.value, AttributeError)
    assert Given.pkg_b.steps.get() == "b"


def test_same_step_registered_twice_is_not_ambiguous():
    step(make_step("pkg_a.steps", "get", "a"))
    step(make_step("pkg_a.steps", "get", "a"))
    assert Given.get() == "a"


def test_module_imported_under_two_names_is_not_ambiguous():
    http = step_namespace("http")
    http(make_step("example.steps", "get", "x", filename="/src/example/steps.py"))
    http(make_step("tests.example.steps", "get", "x", filename="/src/example/steps.py"))
    assert Given.get() == "x"
    assert Given.http.get() == "x"


def test_step_and_namespace_at_one_path_is_ambiguous():
    step(make_step("m", "http"))
    step_namespace("http")(make_step("n", "get"))
    with pytest.raises(AmbiguousStep, match="both a step and a namespace"):
        Given.http


def test_unknown_name_raises_step_not_found():
    with pytest.raises(StepNotFound, match=r"'nope'"):
        Given.nope
    assert hasattr(Given, "nope") is False
    assert getattr(Given, "nope", 1) == 1


def test_unknown_name_under_namespace_names_the_path():
    step_namespace("http")(make_step("pkg_a.steps", "get"))
    with pytest.raises(StepNotFound, match=r"'http\.nope'"):
        Given.http.nope


def test_underscore_lookups_are_plain_attribute_errors():
    for name in ["__wrapped__", "__test__", "_private"]:
        with pytest.raises(AttributeError) as err:
            getattr(Given, name)
        assert type(err.value) is AttributeError
    assert inspect.unwrap(Given) is Given
    copy.copy(Given)


def test_underscore_lookups_on_a_namespace_are_plain_attribute_errors():
    step_namespace("http")(make_step("pkg_a.steps", "get"))
    with pytest.raises(AttributeError) as err:
        Given.http.__wrapped__
    assert type(err.value) is AttributeError


def test_namespace_repr_names_the_path():
    step_namespace("my.package")(make_step("pkg_a.steps", "get"))
    assert repr(Given.my.package) == "<step namespace 'my.package'>"


def test_private_step_name_is_rejected():
    with pytest.raises(ValueError):
        step(make_step("m", "_hidden"))


@pytest.mark.parametrize("ns", ["", "a..b", "1a", "class", "_a", "a._b"])
def test_invalid_namespace_is_rejected(ns):
    with pytest.raises(ValueError):
        step_namespace(ns)


def test_dir_lists_root_names():
    step(make_step("pkg_a.steps", "get"))
    assert {"get", "pkg_a"} <= set(dir(Given))
    assert "get" in dir(Given.pkg_a.steps)


count = ContextProperty[int](initializer=lambda ctx: 0)


def bump():
    count.value += 1


def test_step_shares_the_active_context():
    step(bump)

    @script
    def run():
        Given.bump()
        Given.bump()
        return count.value

    assert run() == 2


def test_step_outside_a_context_raises_no_active_context():
    step(bump)
    with pytest.raises(NoActiveContext):
        Given.bump()
