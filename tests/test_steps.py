import copy
import functools
import inspect
import importlib
import importlib.util
import pickle
import sys
import threading

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
    register_steps,
    script,
    step,
    step_namespace,
    unregister_steps,
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


def test_register_steps_binds_bare_or_namespaced():
    f, g = make_step("m", "get"), make_step("m", "put")
    register_steps(f)
    register_steps(g, namespace="x")
    assert Given.get is f
    assert Given.x.put is g


def test_register_steps_rehomes_a_decorated_step():
    f = step_namespace("http")(make_step("m", "get"))
    register_steps(f, namespace="api")
    assert Given.http.get is f
    assert Given.api.get is f


def test_register_steps_is_all_or_nothing():
    f = make_step("m", "get")
    with pytest.raises(TypeError):
        register_steps(f, 1)
    with pytest.raises(StepNotFound):
        Given.get
    step(make_step("other", "put"))
    with pytest.raises(ValueError):
        register_steps(f, make_step("m", "put"))
    with pytest.raises(StepNotFound):
        Given.get


def test_register_steps_conflicting_items_in_one_call():
    with pytest.raises(ValueError):
        register_steps(make_step("a", "get"), make_step("b", "get"))
    assert dir(Given) == []


def test_empty_calls_raise():
    with pytest.raises(TypeError):
        register_steps()
    with pytest.raises(TypeError):
        unregister_steps()


class Pinger:
    def ping(self):
        return "pong"


def test_bound_method_registered_twice_is_a_no_op():
    c = Pinger()
    register_steps(c.ping)
    register_steps(c.ping)
    assert Given.ping() == "pong"


def test_bound_methods_of_two_instances_conflict():
    register_steps(Pinger().ping)
    with pytest.raises(ValueError):
        register_steps(Pinger().ping)


def test_unregister_frees_every_path():
    f = make_step("m", "get")
    register_steps(f)
    register_steps(f, namespace="http")
    register_steps(f, namespace="api")
    unregister_steps(f)
    with pytest.raises(StepNotFound):
        Given.get
    with pytest.raises(StepNotFound):
        Given.http
    with pytest.raises(StepNotFound):
        Given.api
    assert "http" not in dir(Given)
    register_steps(f, namespace="http")
    assert Given.http.get is f


def test_unregister_unknown_step_is_a_no_op():
    unregister_steps(make_step("m", "get"))


def test_unregister_via_lookup_after_reload(tmp_path, monkeypatch):
    (tmp_path / "sd_reload_mod.py").write_text("from safetydance import step\n\n@step\ndef go(): pass\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "sd_reload_mod", raising=False)
    module = importlib.import_module("sd_reload_mod")
    try:
        with pytest.raises(ValueError) as info:
            importlib.reload(module)
        assert "imported twice" not in str(info.value)
        unregister_steps(Given.go)
        module = importlib.reload(module)
        assert Given.go is module.go
    finally:
        sys.modules.pop("sd_reload_mod", None)


def test_double_import_raises_with_a_hint(tmp_path, monkeypatch):
    path = tmp_path / "steps.py"
    path.write_text("from safetydance import step\n\n@step\ndef go(): pass\n")

    def load(name):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)

    load("pkgx.steps")
    with pytest.raises(ValueError, match=r"imported twice, as pkgx\.steps and tests_copy\.pkgx\.steps"):
        load("tests_copy.pkgx.steps")


def _run_threads(targets):
    barrier = threading.Barrier(len(targets), timeout=5)
    errors = []

    def run(target):
        barrier.wait()
        try:
            target()
        except ValueError as e:
            errors.append(e)

    threads = [threading.Thread(target=run, args=(t,), daemon=True) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive()
    return errors


def test_concurrent_registration():
    def worker(i):
        return lambda: [
            register_steps(make_step(f"m{i}", f"s{n}"), namespace=f"ns{i}") for n in range(50)
        ]

    assert _run_threads([worker(i) for i in range(8)]) == []
    assert all(
        getattr(getattr(Given, f"ns{i}"), f"s{n}")() is None
        for i in range(8)
        for n in range(50)
    )


def test_concurrent_conflicts_are_detected_exactly_once():
    targets = [lambda i=i: register_steps(make_step(f"m{i}", "same")) for i in range(8)]
    assert len(_run_threads(targets)) == 7
    assert callable(Given.same)
