# String annotations: ClassVar is recognised however it was imported.
from __future__ import annotations

import typing
import typing as t
from typing import ClassVar
from typing import ClassVar as CV

from safetydance import context_data, script
from safetydance._safetydance import ContextPropertyDescriptor


@context_data
class Registry:
    plain: ClassVar[dict] = {}
    qualified: typing.ClassVar[dict] = {}
    module_alias: t.ClassVar[dict] = {}
    name_alias: CV[dict] = {}
    bare: ClassVar = 0


def test_classvar_aliases_stay_class_attributes():
    for name in ("plain", "qualified", "module_alias", "name_alias"):
        assert Registry.__dict__[name] == {}
    assert Registry.bare == 0


@context_data
class StringFields:
    maybe: int | None = None
    sequence: collections.abc.Sequence = ()  # noqa: F821 - never evaluated
    other_module: foo.ClassVar[int] = 3  # noqa: F821 - not typing's ClassVar
    lookalike: ClassVarLike = 4  # noqa: F821


def test_other_string_annotations_are_fields():
    for name in ("maybe", "sequence", "other_module", "lookalike"):
        assert isinstance(StringFields.__dict__[name], ContextPropertyDescriptor)

    @script
    def run():
        StringFields.maybe = 5
        return (
            StringFields.maybe,
            StringFields.sequence,
            StringFields.other_module,
            StringFields.lookalike,
        )

    assert run() == (5, (), 3, 4)
