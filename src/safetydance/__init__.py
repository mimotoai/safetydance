# -*- coding: utf-8 -*-
from importlib.metadata import PackageNotFoundError, version

try:
    # Change here if project is renamed and does not equal the package name
    __version__ = version(__name__)
except PackageNotFoundError:
    __version__ = "unknown"
finally:
    del version, PackageNotFoundError

from ._safetydance import *
