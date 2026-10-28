import copy
import functools
import inspect
import pickle

import pytest

from safetydance import (
    And,
    ContextProperty,
    Given,
    NoActiveContext,
    StepNotFound,
    StepPrefix,
    Then,
    When,
    _steps,
    script,
    step,
    step_namespace,
)


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    monkeypatch.setattr(_steps, "_registry", _steps._Registry())


def make_step(module, name, result=None):
    namespace = {"__name__": module, "result": result}
    exec(compile(f"def {name}(): return result", f"<{module}>", "exec"), namespace)
    return namespace[name]


def test_step_returns_the_function_unchanged():
    f = make_step("m", "get")
    assert step(f) is f


def test_step_registers_the_bare_name():
    f = make_step("m", "get", "a")
    step(f)
    assert Given.get() == "a"
    assert Given.get is f


def test_step_namespace_registers_one_path():
    step_namespace("http")(make_step("m", "get", "a"))
    assert Given.http.get() == "a"
    with pytest.raises(StepNotFound):
        Given.get


def test_dotted_namespace():
    step_namespace("my.pkg")(make_step("m", "get", "a"))
    assert Given.my.pkg.get() == "a"


def test_prefixes_are_one_object():
    assert Given is When is Then is And
    assert isinstance(Given, StepPrefix)


def test_registering_the_same_step_twice_is_a_no_op():
    f = make_step("m", "get")
    step(f)
    step(f)
    assert Given.get is f


def test_a_different_step_at_a_taken_path_raises():
    first = make_step("pkg_a", "get")
    step(first)
    with pytest.raises(ValueError, match=r"pkg_a\.get.*pkg_b\.get"):
        step(make_step("pkg_b", "get"))
    assert Given.get is first


def test_step_at_a_namespace_path_raises():
    inner = make_step("pkg_a", "get")
    step_namespace("http")(inner)
    with pytest.raises(ValueError, match=r"pkg_a\.get.*pkg_b\.http"):
        step(make_step("pkg_b", "http"))
    assert Given.http.get is inner
    assert dir(Given) == ["http"]


def test_namespace_under_a_step_raises():
    outer = make_step("pkg_a", "http")
    step(outer)
    with pytest.raises(ValueError, match=r"pkg_a\.http.*pkg_b\.get"):
        step_namespace("http")(make_step("pkg_b", "get"))
    assert Given.http is outer
    assert dir(Given) == ["http"]


def test_unknown_name_raises_step_not_found():
    with pytest.raises(StepNotFound, match=r"'nope'") as info:
        Given.nope
    assert isinstance(info.value, AttributeError)
    assert hasattr(Given, "nope") is False
    assert not str(info.value).endswith(".")
    assert info.value.name == "nope"
    assert info.value.obj is Given


def test_unknown_nested_name_names_the_path():
    step_namespace("http")(make_step("m", "get"))
    with pytest.raises(StepNotFound, match=r"'http\.nope'"):
        Given.http.nope


@pytest.mark.parametrize("name", ["__wrapped__", "__test__", "_x"])
def test_underscore_lookups_are_plain_attribute_errors(name):
    step_namespace("http")(make_step("m", "get"))
    for owner in (Given, Given.http):
        with pytest.raises(AttributeError) as info:
            getattr(owner, name)
        assert type(info.value) is AttributeError


def test_tooling_can_copy_and_pickle_prefixes_and_namespaces():
    step_namespace("http")(make_step("m", "get"))
    assert inspect.unwrap(Given) is Given
    assert copy.copy(Given) is not None
    for node in (Given, Given.http):
        assert repr(copy.copy(node)) == repr(node)
        assert repr(copy.deepcopy(node)) == repr(node)
        assert repr(pickle.loads(pickle.dumps(node))) == repr(node)


def test_prefix_rejects_assignment():
    with pytest.raises(AttributeError):
        Given.x = 1


def test_reprs():
    step_namespace("http")(make_step("m", "get"))
    assert repr(Given) == "<step prefix>"
    assert repr(Given.http) == "<step namespace 'http'>"


def test_dir_lists_registered_children():
    step_namespace("http")(make_step("m", "get"))
    assert "get" in dir(Given.http)
    assert "http" in dir(Given)


@pytest.mark.parametrize("namespace", ["", "a..b", "1a", "class", "_a"])
def test_invalid_namespaces_rejected(namespace):
    with pytest.raises(ValueError):
        step_namespace(namespace)


def test_invalid_step_names_and_types_rejected():
    with pytest.raises(ValueError):
        step(lambda: 1)
    with pytest.raises(ValueError):
        step(make_step("m", "_x"))
    with pytest.raises(TypeError):
        step(functools.partial(make_step("m", "get")))
    with pytest.raises(TypeError):
        step("not callable")


count = ContextProperty[int](initializer=lambda ctx: 0)


def bump():
    count.value += 1
    return count.value


def test_step_runs_in_the_active_context():
    step(bump)

    @script
    def run():
        Given.bump()
        return Given.bump()

    assert run() == 2
    with pytest.raises(NoActiveContext):
        Given.bump()


def test_no_frame_inspection_or_entry_points():
    source = inspect.getsource(_steps)
    for banned in ("_getframe", "currentframe", "entry_points"):
        assert banned not in source
