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
    register_steps,
    step,
    step_namespace,
)


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    fresh = _steps._Registry(read_entry_points=lambda: ())
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


def make_namespaced_step(ns, result):
    @step_namespace(ns)
    def get():
        return result

    return get


def test_closures_from_one_factory_are_distinct_steps():
    make_namespaced_step("api", "api")
    make_namespaced_step("admin", "admin")
    assert Given.api.get() == "api"
    assert Given.admin.get() == "admin"
    with pytest.raises(AmbiguousStep):
        Given.get
    module = Given
    for segment in __name__.split("."):
        module = getattr(module, segment)
    with pytest.raises(AmbiguousStep):
        module.get


def test_ambiguity_message_locates_candidates_sharing_a_name():
    api = make_namespaced_step("api", "api")
    make_namespaced_step("admin", "admin")
    code = api.__code__
    location = f"{code.co_filename}:{code.co_firstlineno}"
    with pytest.raises(AmbiguousStep) as err:
        Given.get
    assert str(err.value).count(location) == 2


def test_same_closure_registered_twice_is_not_ambiguous():
    get = make_namespaced_step("api", "api")
    step(get)
    assert Given.get() == "api"
    assert Given.api.get() == "api"


def test_step_and_namespace_at_one_path_is_ambiguous():
    step(make_step("m", "http", "m"))
    step_namespace("http")(make_step("n", "get"))
    with pytest.raises(AmbiguousStep, match=r"both a step and a namespace.*m\.http"):
        Given.http
    assert Given.m.http() == "m"


def test_step_named_like_its_module_root_is_not_advised_its_full_path():
    # Given.login.login fails too: the root segment is checked first.
    step(make_step("login", "login"))
    with pytest.raises(AmbiguousStep, match="both a step and a namespace") as err:
        Given.login
    message = str(err.value)
    assert "full module path" not in message
    assert "login.login" not in message
    assert "step_namespace" in message
    assert "register_steps(..., namespace=...)" in message


def test_unknown_name_raises_step_not_found():
    with pytest.raises(StepNotFound, match=r"'nope'"):
        Given.nope
    assert hasattr(Given, "nope") is False
    assert getattr(Given, "nope", 1) == 1


def test_step_not_found_message_has_no_final_period():
    # Python may append ". Did you mean ...?" to an AttributeError's message.
    with pytest.raises(StepNotFound) as err:
        Given.nope
    assert str(err.value).endswith("'safetydance.steps' entry point")


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


def test_explicit_registration_resolves_ambiguity():
    step(make_step("pkg_a.steps", "get", "a"))
    b_get = step(make_step("pkg_b.steps", "get", "b"))
    with pytest.raises(AmbiguousStep):
        Given.get
    register_steps(b_get)
    assert Given.get() == "b"


def test_explicit_namespace_rehomes_a_step():
    step(make_step("pkg_a.steps", "get", "a"))
    b_get = step(make_step("pkg_b.steps", "get", "b"))
    register_steps(b_get, namespace="other")
    assert Given.other.get() == "b"
    assert Given.pkg_a.steps.get() == "a"


def test_explicit_beats_a_single_default():
    step(make_step("pkg_a.steps", "get", "a"))
    register_steps(make_step("pkg_b.steps", "get", "b"))
    assert Given.get() == "b"


def test_registration_without_step_decorator():
    register_steps(make_step("m", "only", "o"), namespace="x")
    assert Given.x.only() == "o"
    with pytest.raises(StepNotFound):
        Given.only


def test_explicit_namespaces_appear_in_dir():
    register_steps(make_step("m", "only"), namespace="x.y")
    assert "x" in dir(Given)
    assert "only" in dir(Given.x.y)


def test_conflicting_explicit_registrations_raise():
    a_get = make_step("pkg_a.steps", "get", "a")
    b_get = make_step("pkg_b.steps", "get", "b")
    register_steps(a_get)
    with pytest.raises(ValueError, match=r"pkg_a\.steps\.get.*pkg_b\.steps\.get"):
        register_steps(b_get)
    assert Given.get() == "a"


def test_repeating_an_explicit_registration_is_allowed():
    a_get = make_step("pkg_a.steps", "get", "a")
    register_steps(a_get)
    register_steps(a_get)
    assert Given.get() == "a"


def test_register_steps_rejects_non_callables():
    with pytest.raises(TypeError):
        register_steps(1)


def test_register_steps_rejects_private_names():
    with pytest.raises(ValueError):
        register_steps(make_step("m", "_hidden"))


def test_register_steps_is_all_or_nothing():
    a_get = make_step("pkg_a.steps", "get", "a")
    with pytest.raises(TypeError):
        register_steps(a_get, 1)
    with pytest.raises(StepNotFound):
        Given.get


def test_register_steps_conflict_within_one_call_binds_nothing():
    with pytest.raises(ValueError):
        register_steps(make_step("pkg_a.steps", "get"), make_step("pkg_b.steps", "get"))
    with pytest.raises(StepNotFound):
        Given.get


def test_register_steps_validates_namespace():
    with pytest.raises(ValueError):
        register_steps(make_step("pkg_a.steps", "get"), namespace="a..b")


def test_ambiguity_hint_names_every_way_out():
    step(make_step("pkg_a.steps", "get"))
    step(make_step("pkg_b.steps", "get"))
    with pytest.raises(AmbiguousStep, match=r"full module path.*namespace.*register_steps"):
        Given.get


class Client:
    def __init__(self, name):
        self.name = name

    def fetch(self):
        return self.name


class SuperClient(Client):
    # super() gives the method a __class__ cell, so it has a __closure__.
    def fetch(self):
        return super().fetch()


@pytest.mark.parametrize("cls", [Client, SuperClient])
def test_bound_methods_of_different_instances_are_distinct_steps(cls):
    step(cls("a").fetch)
    step(cls("b").fetch)
    with pytest.raises(AmbiguousStep):
        Given.fetch


@pytest.mark.parametrize("cls", [Client, SuperClient])
def test_explicit_bound_methods_of_different_instances_conflict(cls):
    register_steps(cls("a").fetch)
    with pytest.raises(ValueError):
        register_steps(cls("b").fetch)
    assert Given.fetch() == "a"


@pytest.mark.parametrize("cls", [Client, SuperClient])
def test_same_bound_method_registered_twice_is_one_step(cls):
    client = cls("a")
    # Each attribute access makes a new bound method object.
    step(client.fetch)
    step(client.fetch)
    assert Given.fetch() == "a"
    register_steps(client.fetch, namespace="x")
    register_steps(client.fetch, namespace="x")
    assert Given.x.fetch() == "a"


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
