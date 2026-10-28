import importlib
import importlib.metadata
import sys
import threading
import types

import pytest

from safetydance import _steps
from safetydance._steps import (
    ENTRY_POINT_GROUP,
    AmbiguousStep,
    Given,
    StepNotFound,
    register_steps,
    step,
    step_namespace,
)

MODULE = "sd_ep_steps"

STEPS_SOURCE = """\
from safetydance import step_namespace
step = step_namespace("http")

@step
def get():
    return "ep"
"""


class FakeEntryPoint:
    def __init__(self, name, load, value=None):
        self.name, self._load, self.loads = name, load, 0
        self.value = value or f"fake_{name}"

    def load(self):
        self.loads += 1
        return self._load()


class InstalledEntryPoints:
    """Stands in for ``_installed_entry_points`` and counts how often it is read."""

    def __init__(self, entry_points):
        self.entry_points = entry_points
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return list(self.entry_points)


def import_steps():
    return importlib.import_module(MODULE)


@pytest.fixture
def installed(tmp_path, monkeypatch):
    (tmp_path / f"{MODULE}.py").write_text(STEPS_SOURCE)
    monkeypatch.syspath_prepend(tmp_path)
    # delitem(..., raising=False) records no undo for an absent key, so the module
    # this test imports is removed explicitly on teardown.
    monkeypatch.delitem(sys.modules, MODULE, raising=False)
    entry_points = InstalledEntryPoints([FakeEntryPoint("http", import_steps)])
    monkeypatch.setattr(_steps, "_registry", _steps._Registry(read_entry_points=entry_points))
    yield entry_points
    sys.modules.pop(MODULE, None)


@pytest.fixture
def add_entry_point(installed, tmp_path):
    """Install one more entry point, whose module has the given source."""
    modules = []

    def add(name, module, source):
        (tmp_path / f"{module}.py").write_text(source)
        modules.append(module)
        entry_point = FakeEntryPoint(name, lambda: importlib.import_module(module), module)
        installed.entry_points.append(entry_point)
        return entry_point

    yield add
    for module in modules:
        sys.modules.pop(module, None)


def test_namespace_lookup_imports_the_entry_point(installed):
    assert MODULE not in sys.modules
    assert Given.http.get() == "ep"
    assert MODULE in sys.modules


def test_entry_point_is_loaded_once(installed):
    for _ in range(3):
        Given.http.get
    assert installed.entry_points[0].loads == 1


def test_entry_points_are_read_once(installed):
    Given.http.get
    Given.get
    assert not hasattr(Given, "missing")
    assert not hasattr(Given, "also_missing")
    with pytest.raises(StepNotFound):
        Given.http.missing
    assert installed.reads == 1
    assert installed.entry_points[0].loads == 1


def test_bare_name_loads_every_entry_point_first(installed):
    # No entry point is named "get", so any of them may register it.
    assert Given.get() == "ep"
    assert installed.entry_points[0].loads == 1


def test_bare_name_lookup_is_independent_of_order(installed):
    @step
    def get():
        return "local"

    # The entry point's module registers a get too, so even the first lookup sees two.
    with pytest.raises(AmbiguousStep):
        Given.get


def test_shared_namespace_extended_by_another_entry_point(add_entry_point):
    uploads = add_entry_point(
        "uploads",
        "sd_ep_uploads",
        "from safetydance import step_namespace\n"
        "step = step_namespace('http')\n"
        "@step\n"
        "def upload():\n"
        "    return 'uploaded'\n",
    )
    assert Given.http.upload() == "uploaded"
    assert uploads.loads == 1


def test_first_lookup_loads_every_entry_point(installed):
    unrelated = FakeEntryPoint("other", lambda: None)
    installed.entry_points.append(unrelated)
    assert Given.http.get() == "ep"
    assert installed.entry_points[0].loads == 1
    assert unrelated.loads == 1


def test_first_lookup_below_the_root_loads_every_entry_point(installed):
    # A namespace node kept from before any lookup still loads on its first one.
    namespace = _steps._StepNamespace(("http",))
    assert namespace.get() == "ep"
    assert installed.reads == 1


def test_dunder_lookup_below_the_root_loads_nothing(installed):
    with pytest.raises(AttributeError):
        _steps._StepNamespace(("http",)).__wrapped__
    assert installed.reads == 0


def test_later_entry_point_settles_ambiguity_on_the_first_lookup(add_entry_point):
    # The "http" entry point and this test both register a get; the chooser entry
    # point, listed last, binds one explicitly before the first lookup resolves.
    @step
    def get():
        return "local"

    chooser = add_entry_point(
        "chooser",
        "sd_ep_chooser",
        "from safetydance import register_steps\n"
        "def get():\n"
        "    return 'chosen'\n"
        "register_steps(get)\n",
    )
    assert Given.get() == "chosen"
    assert chooser.loads == 1


def test_entry_point_import_error_propagates(installed):
    def load():
        raise ImportError("boom")

    installed.entry_points[:] = [FakeEntryPoint("http", load)]
    with pytest.raises(ImportError, match="boom") as info:
        Given.http
    assert not isinstance(info.value, StepNotFound)


def test_failed_entry_point_is_retried(installed):
    def load():
        raise ImportError("boom")

    broken = FakeEntryPoint("http", load)
    installed.entry_points[:] = [broken]
    for _ in range(2):
        with pytest.raises(ImportError, match="boom"):
            Given.http
    assert broken.loads == 2


def test_underscore_lookup_loads_nothing(installed):
    assert hasattr(Given, "__wrapped__") is False
    assert installed.entry_points[0].loads == 0
    assert installed.reads == 0


def test_step_module_may_look_steps_up_while_it_is_imported(installed, tmp_path):
    # The module being loaded looks up its own root name and a missing one; neither
    # may deadlock or load the entry point a second time.
    (tmp_path / f"{MODULE}.py").write_text(
        STEPS_SOURCE + "\nfrom safetydance._steps import Given\n"
        "SELF = Given.http.get\n"
        "MISSING = hasattr(Given, 'missing')\n"
    )
    assert Given.http.get() == "ep"
    assert sys.modules[MODULE].MISSING is False
    assert installed.entry_points[0].loads == 1


def test_concurrent_first_lookups_load_once(installed):
    started, release = threading.Event(), threading.Event()

    def load():
        started.set()
        # Held open until every thread has been released toward the lookup.
        assert release.wait(timeout=5)
        return import_steps()

    gated = FakeEntryPoint("http", load)
    installed.entry_points[:] = [gated]
    barrier = threading.Barrier(9, timeout=5)
    results, errors = [], []

    def look_up():
        try:
            barrier.wait()
            results.append(Given.http.get())
        except BaseException as e:
            errors.append(e)

    # Daemon threads, so a deadlock fails the test instead of hanging the run.
    threads = [threading.Thread(target=look_up, daemon=True) for _ in range(8)]
    for thread in threads:
        thread.start()
    barrier.wait()
    assert started.wait(timeout=5)
    release.set()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert results == ["ep"] * 8
    assert gated.loads == 1


def test_installed_entry_points_reads_the_group(monkeypatch):
    calls = []

    def entry_points(**kwargs):
        calls.append(kwargs)
        return ["ep"]

    monkeypatch.setattr(_steps, "entry_points", entry_points)
    assert list(_steps._installed_entry_points()) == ["ep"]
    assert calls == [{"group": "safetydance.steps"}]


def test_attribute_error_from_a_load_becomes_an_import_error(installed):
    error = AttributeError("module has no attribute 'steps'")

    def load():
        raise error

    installed.entry_points[:] = [FakeEntryPoint("http", load, "pkg:steps")]
    # Left as an AttributeError, it would make hasattr() report a missing step.
    with pytest.raises(ImportError) as info:
        hasattr(Given, "http")
    assert str(info.value) == (
        "entry point 'http' (pkg:steps) in group 'safetydance.steps' failed to load: "
        "module has no attribute 'steps'"
    )
    assert info.value.__cause__ is error


def test_missing_attribute_in_an_entry_point_value_raises_import_error(installed):
    bad = importlib.metadata.EntryPoint("bad", f"{MODULE}:missing", ENTRY_POINT_GROUP)
    installed.entry_points[:] = [bad]
    with pytest.raises(ImportError, match=rf"'bad' \({MODULE}:missing\).*failed to load"):
        Given.http
    assert isinstance(sys.modules[MODULE], type(sys))


def test_broken_unrelated_entry_point_raises_import_error_naming_it(installed):
    def load():
        raise AttributeError("no such attribute")

    installed.entry_points.append(FakeEntryPoint("other", load, "other_pkg:steps"))
    with pytest.raises(ImportError, match=r"entry point 'other' \(other_pkg:steps\)"):
        Given.http.get
    # Still broken, so it raises again rather than leaving a partial registry.
    with pytest.raises(ImportError, match="'other'"):
        Given.http.get


def test_other_load_errors_propagate_with_a_note(installed):
    error = ValueError("bad config")

    def load():
        raise error

    installed.entry_points[:] = [FakeEntryPoint("http", load, "pkg.steps")]
    for _ in range(2):
        with pytest.raises(ValueError) as info:
            Given.http
        assert info.value is error
    if sys.version_info >= (3, 11):
        # The same exception raised twice is noted once.
        assert error.__notes__ == [
            "While loading entry point 'http' (pkg.steps) in group 'safetydance.steps'"
        ]


def registry_state():
    registry = _steps._registry
    return registry._explicit, registry._default, registry._children


def flaky_source(marker=None, error="boom"):
    """A steps module that registers, then fails (until ``marker`` exists)."""
    check = "True" if marker is None else f"not os.path.exists({str(marker)!r})"
    # The module-level list is made afresh on each import and is unhashable, so
    # each import's steps are distinct steps.
    return (
        "import os\n"
        "from safetydance import register_steps, step_namespace\n"
        "items = []\n"
        "@step_namespace('flaky')\n"
        "def fetch():\n"
        "    return items\n"
        "def pick():\n"
        "    return items\n"
        "register_steps(pick, namespace='chosen')\n"
        f"if {check}:\n"
        f"    raise ImportError({error!r})\n"
    )


def test_failed_load_removes_what_it_registered(installed, add_entry_point):
    installed.entry_points.clear()
    add_entry_point("flaky", "sd_ep_flaky", flaky_source())
    with pytest.raises(ImportError, match="boom"):
        Given.flaky
    assert "sd_ep_flaky" not in sys.modules
    assert registry_state() == ({}, {}, {})
    assert dir(Given) == []


def test_failed_load_keeps_steps_other_threads_register(installed, add_entry_point):
    installed.entry_points.clear()
    flaky = add_entry_point("flaky", "sd_ep_flaky", flaky_source())
    import_flaky = flaky._load

    def register_elsewhere():
        # Its module is not imported, so the step stays only because another
        # thread's registrations are not recorded as the load's.
        step_namespace("elsewhere")(make_step_in("sd_ep_elsewhere", "ping"))

    def load():
        thread = threading.Thread(target=register_elsewhere, daemon=True)
        thread.start()
        thread.join(timeout=5)
        return import_flaky()

    flaky._load = load
    with pytest.raises(ImportError, match="boom"):
        Given.flaky
    _, default, _ = registry_state()
    assert ("elsewhere", "ping") in default
    assert ("flaky", "fetch") not in default


def make_step_in(module, name):
    """A function whose __module__ is ``module``, a module that is not imported."""
    namespace = {"__name__": module}
    exec(f"def {name}():\n    return {name!r}\n", namespace)
    return namespace[name]


def test_steps_of_a_module_not_imported_are_rolled_back_from_other_loads(installed):
    # The rule is by module: a step whose module is not in sys.modules goes.
    def load():
        step_namespace("ghost")(make_step_in("sd_ep_never_imported", "ping"))
        raise ImportError("boom")

    installed.entry_points[:] = [FakeEntryPoint("ghost", load)]
    with pytest.raises(ImportError, match="boom"):
        Given.ghost
    assert registry_state() == ({}, {}, {})


def test_retry_after_a_failed_load_registers_cleanly(installed, add_entry_point, tmp_path):
    installed.entry_points.clear()
    marker = tmp_path / "flaky-may-load"
    flaky = add_entry_point("flaky", "sd_ep_flaky", flaky_source(marker, "transient"))
    with pytest.raises(ImportError, match="transient"):
        Given.flaky
    marker.touch()
    # Without the rollback, the second import's steps would conflict with the first.
    assert Given.chosen.pick() == []
    assert Given.flaky.fetch() is Given.chosen.pick()
    assert Given.fetch is Given.flaky.fetch
    assert flaky.loads == 2


@pytest.fixture
def package(installed, tmp_path):
    """A package whose __init__ imports a good submodule, then fails until fixed."""
    marker = tmp_path / "pkg-may-load"
    root = tmp_path / "sd_ep_pkg"
    root.mkdir()
    (root / "good.py").write_text(
        "from safetydance import step_namespace\n"
        "@step_namespace('good')\n"
        "def ping():\n"
        "    return 'good'\n"
    )
    (root / "__init__.py").write_text(
        "import os\n"
        "from safetydance import step_namespace\n"
        "from sd_ep_pkg import good\n"
        "@step_namespace('own')\n"
        "def run():\n"
        "    return 'own'\n"
        f"if not os.path.exists({str(marker)!r}):\n"
        "    raise ImportError('pkg fails')\n"
    )
    importlib.invalidate_caches()
    entry_point = FakeEntryPoint("pkg", lambda: importlib.import_module("sd_ep_pkg"))
    installed.entry_points[:] = [entry_point]
    yield entry_point, marker
    for module in ["sd_ep_pkg", "sd_ep_pkg.good"]:
        sys.modules.pop(module, None)


def test_failed_load_keeps_steps_from_modules_that_imported(package):
    entry_point, marker = package
    with pytest.raises(ImportError, match="pkg fails"):
        Given.good
    # The import system dropped the failed package but kept its good submodule,
    # which will not run again, so only the package's own step is rolled back.
    assert "sd_ep_pkg" not in sys.modules
    assert "sd_ep_pkg.good" in sys.modules
    _, default, _ = registry_state()
    assert ("good", "ping") in default
    assert ("own", "run") not in default
    marker.touch()
    assert Given.good.ping() == "good"
    assert Given.ping() == "good"
    assert Given.own.run() == "own"
    _, default, _ = registry_state()
    assert len(default[("ping",)]) == 1
    assert len(default[("run",)]) == 1
    assert entry_point.loads == 2


def test_failed_load_keeps_steps_of_a_nested_load_that_succeeded(add_entry_point, tmp_path):
    # The outer module loads the inner entry point through a lookup, then fails
    # until the marker file exists.
    marker = tmp_path / "outer-may-load"
    outer = add_entry_point(
        "outer",
        "sd_ep_outer",
        "import os\n"
        "from safetydance import step_namespace\n"
        "from safetydance._steps import Given\n"
        "@step_namespace('outer')\n"
        "def run():\n"
        "    return 'outer'\n"
        "Given.inner.find\n"
        f"if not os.path.exists({str(marker)!r}):\n"
        "    raise ImportError('outer fails')\n",
    )
    inner = add_entry_point(
        "inner",
        "sd_ep_inner",
        "from safetydance import step_namespace\n"
        "@step_namespace('inner')\n"
        "def find():\n"
        "    return 'inner'\n",
    )
    with pytest.raises(ImportError, match="outer fails"):
        Given.inner
    _, default, _ = registry_state()
    # The inner entry point counts as loaded, so its steps must stay.
    assert ("inner", "find") in default
    assert ("outer", "run") not in default
    marker.touch()
    assert Given.inner.find() == "inner"
    assert Given.outer.run() == "outer"
    assert (outer.loads, inner.loads) == (2, 1)


def test_entry_point_listing_that_fails_is_read_again(installed):
    good = list(installed.entry_points)
    reads = []

    def read():
        reads.append(None)
        if len(reads) == 1:
            raise OSError("metadata unavailable")
        return good

    _steps._registry = _steps._Registry(read_entry_points=read)
    with pytest.raises(OSError, match="metadata unavailable"):
        Given.http
    assert Given.http.get() == "ep"
    assert len(reads) == 2


def test_lookup_during_a_load_loads_the_other_entry_points(add_entry_point):
    # The first entry point's module looks up a step of the second, which loads it
    # from inside the first load; the outer loop must then skip the second.
    first = add_entry_point(
        "first",
        "sd_ep_first",
        "from safetydance import step_namespace\n"
        "from safetydance._steps import Given\n"
        "SECOND = Given.second.ping()\n"
        "@step_namespace('first')\n"
        "def ping():\n"
        "    return 'first'\n",
    )
    second = add_entry_point(
        "second",
        "sd_ep_second",
        "from safetydance import step_namespace\n"
        "@step_namespace('second')\n"
        "def ping():\n"
        "    return 'second'\n",
    )
    assert Given.first.ping() == "first"
    assert sys.modules["sd_ep_first"].SECOND == "second"
    assert (first.loads, second.loads) == (1, 1)


def test_circular_lookups_during_loads_do_not_deadlock(add_entry_point):
    # Each module looks up the other's step while it is imported.
    a = add_entry_point(
        "a",
        "sd_ep_a",
        "from safetydance import step_namespace\n"
        "from safetydance._steps import Given\n"
        "@step_namespace('a')\n"
        "def ping():\n"
        "    return 'a'\n"
        "B = Given.b.ping()\n",
    )
    b = add_entry_point(
        "b",
        "sd_ep_b",
        "from safetydance import step_namespace\n"
        "from safetydance._steps import Given\n"
        "@step_namespace('b')\n"
        "def ping():\n"
        "    return 'b'\n"
        "A = Given.a.ping()\n",
    )
    result = []
    thread = threading.Thread(target=lambda: result.append(Given.a.ping()), daemon=True)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert result == ["a"]
    assert sys.modules["sd_ep_a"].B == "b"
    assert sys.modules["sd_ep_b"].A == "a"
    assert (a.loads, b.loads) == (1, 1)


FACTORY_SOURCE = """\
class Client:
    def get(self):
        return "client"


def make():
    items = []

    def fetch():
        return items

    return fetch
"""

FLAKY_LIBRARY_USER_SOURCE = """\
import os
from safetydance import register_steps, step_namespace
from sd_ep_factory import Client, make

client = Client()
register_steps(client.get, namespace="api")
step_namespace("fac")(make())
if not os.path.exists({marker!r}):
    raise ImportError("transient")
"""


def test_retry_after_a_failed_load_that_registered_library_steps(
    installed, add_entry_point, tmp_path
):
    # The failing module registers a bound method and a factory closure whose
    # __module__ is a library that imported fine; both belong to the failed import.
    (tmp_path / "sd_ep_factory.py").write_text(FACTORY_SOURCE)
    marker = tmp_path / "library-user-may-load"
    installed.entry_points.clear()
    user = add_entry_point(
        "user", "sd_ep_library_user", FLAKY_LIBRARY_USER_SOURCE.format(marker=str(marker))
    )
    try:
        with pytest.raises(ImportError, match="transient"):
            Given.api
        assert "sd_ep_factory" in sys.modules
        assert registry_state() == ({}, {}, {})
        marker.touch()
        # Without attributing them to the failed module, the retry's new objects
        # would conflict with the first attempt's ("already registered").
        assert Given.api.get() == "client"
        assert Given.fac.fetch() == []
        assert Given.fetch is Given.fac.fetch
        assert user.loads == 2
    finally:
        sys.modules.pop("sd_ep_factory", None)


def test_registration_outside_a_load_walks_no_frames(monkeypatch):
    # A lookup or plain registration never inspects the stack.
    monkeypatch.setattr(_steps, "_registry", _steps._Registry(read_entry_points=lambda: ()))

    def no_frames():
        raise AssertionError("walked the stack outside a load")

    monkeypatch.setattr(_steps, "currentframe", no_frames)

    @step_namespace("plain")
    def ping():
        return "plain"

    register_steps(ping, namespace="bound")
    assert Given.plain.ping() == "plain"
    assert Given.bound.ping() == "plain"


def test_failed_load_keeps_a_step_registered_before_it(installed):
    # A failing module re-registers a step registered before the load, whose
    # captured value it rebinds first; the rollback must not remove that step.
    def factory():
        box = object()

        def get():
            return box

        def rebind():
            nonlocal box
            box = object()

        return get, rebind

    get, rebind = factory()

    def load():
        namespace = {"__name__": "sd_ep_rebinder", "get": get, "rebind": rebind, "step": step}
        exec("rebind()\nstep(get)\nraise RuntimeError('boom')\n", namespace)

    installed.entry_points[:] = [FakeEntryPoint("rebinder", load)]
    step(get)
    with pytest.raises(RuntimeError, match="boom"):
        Given.get
    _, default, _ = registry_state()
    assert [g for g in default[("get",)].values()] == [get]
    assert any(g is get for g in default[tuple(get.__module__.split(".")) + ("get",)].values())


def make_rebinding_load(tier):
    """A load whose module registers a step, rebinds its value, re-registers it, fails."""
    from safetydance._steps import register_steps as bind

    def factory():
        box = object()

        def get():
            return box

        def rebind():
            nonlocal box
            box = object()

        return get, rebind

    get, rebind = factory()
    register = "step(get)" if tier == "default" else "bind(get, namespace='chosen')"

    def load():
        namespace = {
            "__name__": "sd_ep_rebinder",
            "get": get,
            "rebind": rebind,
            "step": step,
            "bind": bind,
        }
        exec(f"{register}\nrebind()\n{register}\nraise RuntimeError('boom')\n", namespace)

    return get, load


@pytest.mark.parametrize("tier", ["default", "explicit"])
def test_failed_load_removes_a_step_it_registered_and_re_registered(installed, tier):
    get, load = make_rebinding_load(tier)
    installed.entry_points[:] = [FakeEntryPoint("rebinder", load)]
    with pytest.raises(RuntimeError, match="boom"):
        Given.get
    explicit, default, children = registry_state()
    assert not any(g is get for registered in default.values() for g in registered.values())
    assert not any(bound is get for _, bound in explicit.values())
    assert (explicit, default, children) == ({}, {}, {})


def make_rebindable_step():
    box = object()

    def get():
        return box

    def rebind():
        nonlocal box
        box = object()

    return get, rebind


REGISTER = {"default": "step(f)", "explicit": "register_steps(f, namespace='chosen')"}


def run_module(name, source, env, monkeypatch=None):
    """Exec ``source`` as module ``name``; it stays imported when ``monkeypatch`` is given."""
    module = types.ModuleType(name)
    module.__dict__.update(env, step=step, register_steps=register_steps, Given=Given)
    if monkeypatch is not None:
        monkeypatch.setitem(sys.modules, name, module)
    exec(compile(source, f"{name}.py", "exec"), module.__dict__)


def paths_holding(f):
    explicit, default, _ = registry_state()
    held = [p for p, registered in default.items() if any(g is f for g in registered.values())]
    return held + [p for p, (_, bound) in explicit.items() if bound is f]


@pytest.mark.parametrize("tier", ["default", "explicit"])
def test_failed_load_keeps_a_step_an_imported_module_registered_in_it(installed, monkeypatch, tier):
    # In one load, sd_ep_ok registers f and stays imported; sd_ep_bad then rebinds
    # f's value, registers f again and fails. sd_ep_ok will never run again, so f
    # must stay: the re-registration is still sd_ep_ok's record, not sd_ep_bad's.
    f, rebind = make_rebindable_step()
    env = {"f": f, "rebind": rebind}

    def load():
        run_module("sd_ep_ok", REGISTER[tier] + "\n", env, monkeypatch)
        run_module("sd_ep_bad", f"rebind()\n{REGISTER[tier]}\nraise RuntimeError('boom')\n", env)

    installed.entry_points[:] = [FakeEntryPoint("mixed", load)]
    with pytest.raises(RuntimeError, match="boom"):
        Given.get
    assert paths_holding(f) == (
        [tuple(f.__module__.split(".")) + ("get",), ("get",)]
        if tier == "default"
        else [("chosen", "get")]
    )


@pytest.mark.parametrize("tier", ["default", "explicit"])
def test_failed_nested_load_that_re_registers_a_step_rolls_it_back(installed, tier):
    # Entry point a's module registers f, then a lookup loads entry point b, whose
    # module rebinds f's value, registers f again and fails, so a fails too. f was
    # recorded by a's load, which must remove it under its new key.
    f, rebind = make_rebindable_step()
    env = {"f": f, "rebind": rebind}

    def load_a():
        run_module("sd_ep_a_mod", f"{REGISTER[tier]}\nGiven.anything\n", env)

    def load_b():
        run_module("sd_ep_b_mod", f"rebind()\n{REGISTER[tier]}\nraise RuntimeError('b fails')\n", env)

    a, b = FakeEntryPoint("a", load_a), FakeEntryPoint("b", load_b)
    installed.entry_points[:] = [a, b]
    with pytest.raises(RuntimeError, match="b fails"):
        Given.get
    assert paths_holding(f) == []
    assert registry_state() == ({}, {}, {})
    assert (a.loads, b.loads) == (1, 1)
