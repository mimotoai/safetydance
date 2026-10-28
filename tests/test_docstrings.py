import importlib.metadata
import inspect

import pytest
from docutils.core import publish_doctree

import safetydance
import safetydance._safetydance as impl


def _documented_members():
    yield "ContextProperty.value", impl.ContextProperty.value
    yield "ContextProperty.has_value", impl.ContextProperty.has_value
    yield "ContextProperty.__call__", impl.ContextProperty.__call__
    yield "ContextProperty.__setitem__", impl.ContextProperty.__setitem__


@pytest.mark.parametrize("name", safetydance.__all__)
def test_every_public_name_has_a_docstring(name):
    obj = getattr(safetydance, name)
    assert inspect.getdoc(obj), name
    if inspect.isclass(obj):
        assert obj.__dict__.get("__doc__"), f"{name} inherits its docstring"


@pytest.mark.parametrize("name,member", list(_documented_members()))
def test_key_members_have_a_docstring(name, member):
    assert inspect.getdoc(member), name


def _all_docstrings():
    for name in safetydance.__all__:
        yield name, getattr(safetydance, name)
    yield from _documented_members()


@pytest.mark.parametrize("name,obj", list(_all_docstrings()))
def test_docstrings_are_valid_rst(name, obj):
    doc = inspect.getdoc(obj)
    assert doc, name
    publish_doctree(
        doc, settings_overrides={"report_level": 2, "halt_level": 2}
    )


def test_package_exports_all():
    assert safetydance.__all__ == impl.__all__


def test_version_matches_metadata():
    assert safetydance.__version__ == importlib.metadata.version("safetydance")
