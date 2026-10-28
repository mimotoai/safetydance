from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version(__name__)
except PackageNotFoundError:
    __version__ = "unknown"
finally:
    del version, PackageNotFoundError

from ._safetydance import *
from ._steps import *
from . import _safetydance, _steps

__all__ = [*_safetydance.__all__, *_steps.__all__]
