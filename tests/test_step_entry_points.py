import importlib
import sys
import threading
import time

import pytest

from safetydance import _steps
from safetydance._steps import AmbiguousStep, Given, StepNotFound, step

MODULE = "sd_ep_steps"

STEPS_SOURCE = """\
from safetydance import step_namespace
step = step_namespace("http")

@step
def get():
    return "ep"
"""


class FakeEntryPoint:
    def __init__(self, name, load):
        self.name, self._load, self.loads = name, load, 0

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
    monkeypatch.setattr(_steps, "_registry", _steps._Registry(entry_points=entry_points))
    yield entry_points
    sys.modules.pop(MODULE, None)


@pytest.fixture
def add_entry_point(installed, tmp_path):
    """Install one more entry point, whose module has the given source."""
    modules = []

    def add(name, module, source):
        (tmp_path / f"{module}.py").write_text(source)
        modules.append(module)
        entry_point = FakeEntryPoint(name, lambda: importlib.import_module(module))
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


def test_named_root_lookup_stays_lazy(installed):
    unrelated = FakeEntryPoint("other", lambda: None)
    installed.entry_points.append(unrelated)
    assert Given.http.get() == "ep"
    assert installed.entry_points[0].loads == 1
    assert unrelated.loads == 0


def test_root_ambiguity_triggers_load_all_and_retry(add_entry_point):
    # A local step named http makes the root both a step and a namespace once the
    # "http" entry point loads; a later entry point binds http explicitly.
    @step
    def http():
        return "local"

    chooser = add_entry_point(
        "chooser",
        "sd_ep_chooser",
        "from safetydance import register_steps\n"
        "def http():\n"
        "    return 'chosen'\n"
        "register_steps(http)\n",
    )
    assert Given.http() == "chosen"
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
    def load():
        time.sleep(0.05)
        return import_steps()

    slow = FakeEntryPoint("http", load)
    installed.entry_points[:] = [slow]
    barrier = threading.Barrier(8)
    results, errors = [], []

    def look_up():
        try:
            barrier.wait()
            results.append(Given.http.get())
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=look_up) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert results == ["ep"] * 8
    assert slow.loads == 1


def test_installed_entry_points_reads_the_group(monkeypatch):
    calls = []

    def entry_points(**kwargs):
        calls.append(kwargs)
        return ["ep"]

    monkeypatch.setattr(_steps, "entry_points", entry_points)
    assert list(_steps._installed_entry_points()) == ["ep"]
    assert calls == [{"group": "safetydance.steps"}]
