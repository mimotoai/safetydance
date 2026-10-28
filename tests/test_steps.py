import copy
import functools
import inspect
import sys
import traceback

import pytest

from safetydance import ContextProperty, NoActiveContext, _safetydance, script
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
    unregister_steps,
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


# R4: AmbiguousStep and StepNotFound are sibling AttributeErrors.


def test_ambiguous_step_is_not_a_step_not_found():
    assert issubclass(AmbiguousStep, AttributeError)
    assert not issubclass(AmbiguousStep, StepNotFound)
    assert not issubclass(StepNotFound, AmbiguousStep)
    step(make_step("pkg_a.steps", "get"))
    step(make_step("pkg_b.steps", "get"))
    with pytest.raises(AmbiguousStep) as err:
        Given.get
    assert not isinstance(err.value, StepNotFound)
    assert hasattr(Given, "get") is False
    assert getattr(Given, "get", 1) == 1


# R5: below the root, only dunder names are refused.


def test_single_underscore_segments_below_the_root_resolve():
    step(make_step("pkg._impl.steps", "get", "impl"))
    assert Given.pkg._impl.steps.get() == "impl"
    assert "_impl" in dir(Given.pkg)


def test_dunder_segments_are_hidden_from_dir():
    step(make_step("pkg.__main__", "get"))
    assert "__main__" not in dir(Given.pkg)
    with pytest.raises(AttributeError) as err:
        Given.pkg.__main__
    assert type(err.value) is AttributeError


def test_root_underscore_names_are_hidden_from_dir():
    step(make_step("_private.steps", "get"))
    assert "_private" not in dir(Given)
    with pytest.raises(AttributeError) as err:
        Given._private
    assert type(err.value) is AttributeError


@pytest.mark.parametrize(
    "module",
    ["__main__", "_private.steps", "pkg.__main__", "my-steps", "<run_path>", "pkg.class.steps"],
)
def test_ambiguity_never_advises_a_full_path_that_cannot_be_typed(module):
    step(make_step(module, "http"))
    step_namespace("http")(make_step("n", "get"))
    with pytest.raises(AmbiguousStep, match="both a step and a namespace") as err:
        Given.http
    message = str(err.value)
    assert f"{module}.http" not in message
    assert "step_namespace" in message
    assert "register_steps(..., namespace=...)" in message


def test_ambiguous_name_never_advises_a_full_path_that_cannot_be_typed():
    step(make_step("__main__", "get"))
    step(make_step("pkg_b.steps", "get"))
    with pytest.raises(AmbiguousStep) as err:
        Given.get
    assert "full module path" not in str(err.value)
    assert "step_namespace" in str(err.value)


def test_closures_sharing_a_full_path_are_not_advised_it():
    make_namespaced_step("api", "api")
    make_namespaced_step("admin", "admin")
    with pytest.raises(AmbiguousStep) as err:
        Given.get
    assert "full module path" not in str(err.value)


# R6: an explicit binding cannot share a path with a namespace.


def test_register_steps_rejects_a_path_that_is_a_namespace():
    step_namespace("http")(make_step("pkg_a.steps", "get"))
    with pytest.raises(ValueError, match=r"'http'.*already a namespace"):
        register_steps(make_step("m", "http"))
    assert Given.http.get


def test_register_steps_rejects_a_path_under_a_bound_step():
    register_steps(make_step("m", "http"))
    for namespace in ["http", "http.v2"]:
        with pytest.raises(ValueError, match=r"'http' is already registered to m\.http"):
            register_steps(make_step("pkg_a.steps", "get"), namespace=namespace)
    assert Given.http() is None


def test_explicit_step_that_gains_children_is_ambiguous():
    register_steps(make_step("m", "http"))
    step_namespace("http")(make_step("pkg_a.steps", "get"))
    with pytest.raises(AmbiguousStep, match=r"'http' is both a step and a namespace.*m\.http") as err:
        Given.http
    assert not str(err.value).endswith(".")


# R7: what counts as the same step.


def fetch():
    return "fetched"


def labelled(label):
    def decorate(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            return label, f(*args, **kwargs)

        return wrapper

    return decorate


def shouting(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        return f(*args, **kwargs).upper()

    return wrapper


def test_wrappers_capturing_different_values_are_distinct_steps():
    step(labelled("a")(fetch))
    step(labelled("b")(fetch))
    with pytest.raises(AmbiguousStep):
        Given.fetch


def test_different_wrappers_around_one_function_are_distinct_steps():
    step(labelled("a")(fetch))
    step(shouting(fetch))
    with pytest.raises(AmbiguousStep):
        Given.fetch


def test_wrapper_and_the_function_it_wraps_are_distinct_steps():
    step(fetch)
    step(shouting(fetch))
    with pytest.raises(AmbiguousStep):
        Given.fetch


def equal_values():
    """Two equal strings that are distinct objects, as two imports would make."""
    a, b = "".join(["label", "-a"]), "".join(["label", "-a"])
    assert a == b and a is not b
    return a, b


def test_equal_wrappers_around_one_function_are_one_step():
    # Two wrapper objects, made alike, as a module imported twice would make them.
    a, b = equal_values()
    step(labelled(a)(fetch))
    step(labelled(b)(fetch))
    assert Given.fetch() == ("label-a", "fetched")


def make_default_step(value):
    def get(result=value):
        return result

    return get


def make_kwdefault_step(value):
    def get(*, result=value):
        return result

    return get


@pytest.mark.parametrize("factory", [make_default_step, make_kwdefault_step])
def test_same_code_with_different_defaults_is_distinct_steps(factory):
    step_namespace("api")(factory("api"))
    step_namespace("admin")(factory("admin"))
    assert Given.api.get() == "api"
    assert Given.admin.get() == "admin"
    with pytest.raises(AmbiguousStep):
        Given.get


@pytest.mark.parametrize("factory", [make_default_step, make_kwdefault_step])
def test_same_code_with_equal_defaults_is_one_step(factory):
    a, b = equal_values()
    step(factory(a))
    step(factory(b))
    assert Given.get() == "label-a"


def test_unhashable_defaults_are_compared_by_identity():
    shared = []
    step(make_default_step(shared))
    step(make_default_step(shared))
    assert Given.get() is shared
    step(make_default_step([]))
    with pytest.raises(AmbiguousStep):
        Given.get


def test_closures_capturing_equal_values_are_one_step():
    a, b = equal_values()
    make_namespaced_step("api", a)
    make_namespaced_step("admin", b)
    assert Given.get() == "label-a"
    # One step: the later registration replaced the stored object at the bare name.
    assert Given.get is Given.admin.get


def test_closures_capturing_unhashable_values_are_compared_by_identity():
    shared = []
    make_namespaced_step("api", shared)
    make_namespaced_step("admin", shared)
    assert Given.get() is shared
    make_namespaced_step("other", [])
    with pytest.raises(AmbiguousStep):
        Given.get


def test_double_imported_module_with_defaults_is_one_step():
    # Each compile makes its own float and tuple constants: equal, not identical.
    source = "def get(result=2.5, *, flag=(1, 'two')):\n    return result\n"
    for module in ["example.steps", "tests.example.steps"]:
        namespace = {"__name__": module}
        exec(compile(source, "/src/example/steps.py", "exec"), namespace)
        step_namespace("http")(namespace["get"])
    assert Given.get() == 2.5
    assert Given.http.get() == 2.5


def test_exec_d_steps_with_one_pseudo_file_in_different_modules_are_distinct():
    # "<string>" names no file, so the same code there in two modules is two steps.
    step(make_step("pkg_a.steps", "get", "same", filename="<string>"))
    step(make_step("pkg_b.steps", "get", "same", filename="<string>"))
    with pytest.raises(AmbiguousStep):
        Given.get
    assert Given.pkg_a.steps.get() == "same"


def test_different_code_at_one_location_is_distinct_steps():
    step(make_step("m", "get", "a", filename="<string>"))
    step(make_step("m", "get", "b", filename="<string>"))
    with pytest.raises(AmbiguousStep):
        Given.m.get


def test_cyclic_wrapped_chain_is_registered():
    def ping():
        return "ping"

    def pong():
        return "pong"

    ping.__wrapped__ = pong
    pong.__wrapped__ = ping
    with pytest.raises(ValueError):
        inspect.unwrap(ping)
    step(ping)
    step(ping)
    assert Given.ping() == "ping"


def test_recursive_closure_is_registered():
    def factory():
        def countdown(n=0):
            return n if n <= 0 else countdown(n - 1)

        return countdown

    step(factory())
    step(factory())
    assert Given.countdown(3) == 0


class Fetcher:
    def __init__(self, result):
        self.__name__ = "fetcher"
        self.result = result

    def __call__(self):
        return self.result


def test_callable_instances_are_keyed_by_identity():
    a = Fetcher("a")
    step(a)
    step(a)
    assert Given.fetcher() == "a"
    step(Fetcher("a"))
    with pytest.raises(AmbiguousStep):
        Given.fetcher


def test_partial_is_rejected_for_lacking_a_name():
    with pytest.raises(TypeError, match="__name__"):
        step(functools.partial(fetch))
    with pytest.raises(TypeError, match="__name__"):
        register_steps(functools.partial(fetch))


def test_lambda_is_rejected_for_its_name():
    with pytest.raises(ValueError, match="<lambda>"):
        step(lambda: None)
    with pytest.raises(ValueError, match="<lambda>"):
        register_steps(lambda: None)


# R8: unregister_steps.


def test_unregister_removes_every_registration_of_a_step():
    get = step_namespace("http")(make_step("pkg_a.steps", "get", "a"))
    register_steps(get, namespace="other")
    unregister_steps(get)
    for look_up in [
        lambda: Given.get,
        lambda: Given.http,
        lambda: Given.pkg_a,
        lambda: Given.other,
    ]:
        with pytest.raises(StepNotFound):
            look_up()
    assert dir(Given) == []


def test_unregister_matches_an_equal_step():
    client = Client("a")
    step(client.fetch)
    register_steps(client.fetch, namespace="x")
    # A new bound method object, and a copy of the function made alike.
    unregister_steps(client.fetch)
    with pytest.raises(StepNotFound):
        Given.fetch
    step(make_step("pkg_a.steps", "get"))
    unregister_steps(make_step("pkg_a.steps", "get"))
    with pytest.raises(StepNotFound):
        Given.get


def test_unregister_keeps_other_steps():
    a_get = step(make_step("pkg_a.steps", "get", "a"))
    step(make_step("pkg_b.steps", "get", "b"))
    step_namespace("http")(make_step("pkg_a.steps", "post", "p"))
    unregister_steps(a_get)
    assert Given.get() == "b"
    assert Given.pkg_a.steps.post() == "p"
    assert dir(Given.pkg_a.steps) == ["post"]


def test_unregister_unknown_step_is_a_no_op():
    step(make_step("pkg_a.steps", "get", "a"))
    unregister_steps(make_step("pkg_b.steps", "get"), fetch)
    assert Given.get() == "a"


def test_unregister_steps_needs_a_step():
    with pytest.raises(TypeError, match="at least one step"):
        unregister_steps()


def test_unregister_rejects_non_callables_and_removes_nothing():
    get = step(make_step("pkg_a.steps", "get", "a"))
    with pytest.raises(TypeError):
        unregister_steps(get, 1)
    assert Given.get() == "a"


def test_register_unregister_register_again():
    get = make_step("pkg_a.steps", "get", "a")
    other = make_step("pkg_b.steps", "get", "b")
    register_steps(get)
    unregister_steps(get)
    # The path is free again, so a different step can take it.
    register_steps(other)
    assert Given.get() == "b"
    unregister_steps(other)
    register_steps(get)
    step(get)
    assert Given.get() == "a"
    assert Given.pkg_a.steps.get() == "a"


# R9: StepPrefix has no attributes of its own.


def test_attributes_cannot_be_set_on_a_prefix():
    with pytest.raises(AttributeError):
        Given.x = 1
    with pytest.raises(StepNotFound):
        Given.x


def test_prefix_repr():
    assert repr(Given) == "<step prefix>"


# R10: errors carry name and obj, so Python can suggest a close name.


def test_step_not_found_carries_name_and_obj(registry):
    step_namespace("http")(make_step("pkg_a.steps", "get"))
    with pytest.raises(StepNotFound) as err:
        Given.gett
    assert (err.value.name, err.value.obj) == ("gett", Given)
    namespace = Given.http
    with pytest.raises(StepNotFound) as err:
        namespace.gett
    assert err.value.name == "gett"
    assert err.value.obj is namespace
    # Python fills these in for attribute syntax anyway; the registry sets them itself.
    with pytest.raises(StepNotFound) as err:
        registry.resolve(("http", "gett"), namespace)
    assert err.value.name == "gett"
    assert err.value.obj is namespace


def test_ambiguous_step_carries_name_and_obj(registry):
    step(make_step("pkg_a.steps", "get"))
    step(make_step("pkg_b.steps", "get"))
    with pytest.raises(AmbiguousStep) as err:
        registry.resolve(("get",), Given)
    assert (err.value.name, err.value.obj) == ("get", Given)
    assert not str(err.value).endswith(".")


@pytest.mark.skipif(sys.version_info < (3, 12), reason="traceback suggests names from 3.12")
def test_missing_step_suggests_a_close_name():
    step(make_step("pkg_a.steps", "fetch"))
    with pytest.raises(StepNotFound) as err:
        Given.pkg_a.steps.fetchh
    (line,) = traceback.format_exception_only(err.value)
    assert line.rstrip().endswith("entry point. Did you mean: 'fetch'?")


# R11: small fixes.


def test_all_is_sorted_like_safetydance():
    assert _safetydance.__all__ == sorted(_safetydance.__all__)
    assert _steps.__all__ == sorted(_steps.__all__)


def test_register_steps_needs_a_step():
    with pytest.raises(TypeError, match="at least one step"):
        register_steps()
    with pytest.raises(TypeError):
        register_steps(namespace="x")


def test_full_path_is_advised_when_it_does_not_pass_through_the_name():
    # x.get is a step (namespace "x") and a namespace (module x.get); the step's
    # full path x.y.get shares the root x but not the ambiguous x.get.
    step_namespace("x")(make_step("x.y", "get", "xy"))
    step(make_step("x.get", "other"))
    with pytest.raises(AmbiguousStep, match=r"full module path, x\.y\.get") as err:
        Given.x.get
    assert "step_namespace" not in str(err.value)
    assert Given.x.y.get() == "xy"


# Review fixes: paths a message may advise, and namespace internals.


def test_path_segment_named_like_the_namespace_slot_resolves():
    step(make_step("pkg._path.steps", "get", "p"))
    assert Given.pkg._path.steps.get() == "p"
    assert "_path" in dir(Given.pkg)
