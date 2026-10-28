import importlib
import sys
import threading
import time

import pytest

from safetydance import _steps
from safetydance._steps import Given, StepNotFound

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


def test_bare_miss_loads_every_entry_point_and_retries(installed):
    assert Given.get() == "ep"


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


def test_installed_entry_points_reads_the_group():
    entry_points = _steps._installed_entry_points()
    for entry_point in entry_points:
        assert entry_point.group == "safetydance.steps"
