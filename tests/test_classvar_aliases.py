# String annotations: ClassVar is recognised however it was imported.
from __future__ import annotations

import typing
import typing as t
from typing import ClassVar
from typing import ClassVar as CV

from safetydance import context_data


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
