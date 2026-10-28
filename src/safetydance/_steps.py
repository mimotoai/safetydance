import keyword
import threading
from inspect import unwrap
from typing import Any, Callable, Iterable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

ENTRY_POINT_GROUP = "safetydance.steps"

_Path = tuple[str, ...]
_Key = tuple[Any, ...]


class StepNotFound(AttributeError):
    """
    Raised when no step is registered at the name looked up on a prefix.

    It is an ``AttributeError``, so ``hasattr(Given, name)`` and
    ``getattr(Given, name, default)`` work.
    """


class AmbiguousStep(StepNotFound):
    """
    Raised when a name looked up on a prefix could mean more than one thing.

    That is, several distinct steps share the name, or the name is both a step and
    a namespace. Look the step up by its full module path instead.
    """


def _installed_entry_points() -> Iterable[Any]:
    return ()


def _is_segment(name: str) -> bool:
    return name.isidentifier() and not keyword.iskeyword(name) and not name.startswith("_")


def _namespace_path(namespace: str) -> _Path:
    path = tuple(namespace.split("."))
    if not all(_is_segment(segment) for segment in path):
        raise ValueError(
            f"Invalid step namespace {namespace!r}: each dotted segment must be an "
            "identifier that is not a keyword and does not start with '_'."
        )
    return path


def _step_name(f: Callable[..., Any]) -> str:
    name = getattr(f, "__name__", None)
    if not isinstance(name, str) or not isinstance(getattr(f, "__module__", None), str):
        raise TypeError(f"A step needs a __name__ and a __module__; got {f!r}.")
    if not _is_segment(name):
        raise ValueError(
            f"Invalid step name {name!r}: it must be an identifier that is not a "
            "keyword and does not start with '_'."
        )
    return name


def _step_key(f: Callable[..., Any]) -> _Key:
    # Keyed by where the code is defined, not by object identity, so one module
    # imported under two names (e.g. through pytest's rootdir) is one step.
    target = unwrap(f)
    qualname = getattr(target, "__qualname__", None)
    code = getattr(target, "__code__", None)
    if code is None:
        return ("<object>", id(f), qualname)
    key = (code.co_filename, code.co_firstlineno, qualname)
    # Closures from one factory share their code but may capture different values.
    if getattr(target, "__closure__", None):
        return (*key, id(f))
    return key


def _full_name(f: Callable[..., Any]) -> str:
    return f"{f.__module__}.{f.__name__}"


def _candidate_names(steps: list[Callable[..., Any]]) -> list[str]:
    names = [_full_name(f) for f in steps]
    labels = []
    for name, f in zip(names, steps):
        code = getattr(unwrap(f), "__code__", None)
        # A full name alone cannot tell apart candidates that share it.
        if code is not None and names.count(name) > 1:
            name = f"{name} ({code.co_filename}:{code.co_firstlineno})"
        labels.append(name)
    return sorted(labels)


def _dotted(path: _Path) -> str:
    return ".".join(path)


class _Registry:
    def __init__(self, entry_points: Callable[[], Iterable[Any]] = _installed_entry_points):
        # Not called here: entry points are read lazily, on lookup.
        self._entry_points = entry_points
        self._lock = threading.Lock()
        self._explicit: dict[_Path, tuple[_Key, Callable[..., Any]]] = {}
        self._default: dict[_Path, dict[_Key, Callable[..., Any]]] = {}
        # Every proper prefix of a registered path, mapped to its child names; the
        # root () included, so a namespace check is a single dict lookup.
        self._children: dict[_Path, set[str]] = {}

    def _add_prefixes(self, path: _Path) -> None:
        for i in range(len(path)):
            self._children.setdefault(path[:i], set()).add(path[i])

    def add_default(self, f: Callable[..., Any], namespace: _Path | None) -> None:
        name = _step_name(f)
        key = _step_key(f)
        paths = [(*f.__module__.split("."), name), (name,)]
        if namespace is not None:
            paths.append((*namespace, name))
        with self._lock:
            for path in paths:
                # The same step registered again replaces the stored object.
                self._default.setdefault(path, {})[key] = f
                self._add_prefixes(path)

    def resolve(self, path: _Path) -> Any:
        with self._lock:
            explicit = self._explicit.get(path)
            defaults = list(self._default.get(path, {}).values())
            is_namespace = path in self._children
        if explicit is not None:
            return explicit[1]
        if len(defaults) > 1:
            candidates = ", ".join(_candidate_names(defaults))
            raise AmbiguousStep(
                f"Step '{_dotted(path)}' is ambiguous; it could be any of: {candidates}. "
                "Use the full module path to choose one."
            )
        if defaults and is_namespace:
            raise AmbiguousStep(
                f"'{_dotted(path)}' is both a step and a namespace; use the step's full "
                f"module path, {_full_name(defaults[0])}, to call the step."
            )
        if defaults:
            return defaults[0]
        if is_namespace:
            return _StepNamespace(path)
        raise StepNotFound(
            f"No step named '{_dotted(path)}'. Steps are found only once their module "
            f"is imported or exposed through a {ENTRY_POINT_GROUP!r} entry point."
        )

    def child_names(self, path: _Path) -> list[str]:
        with self._lock:
            names = self._children.get(path, set())
            # Full module paths can include names like __main__ that no lookup reaches.
            return sorted(name for name in names if not name.startswith("_"))

    def root_names(self) -> list[str]:
        return self.child_names(())


def _no_attribute(obj: object, name: str) -> AttributeError:
    return AttributeError(f"{type(obj).__name__!r} object has no attribute {name!r}")


class StepPrefix:
    """
    Looks steps up by name: ``Given``, ``When``, ``Then`` and ``And`` are one instance.

    Each attribute is a registered step, or a namespace that holds steps::

        Given.get("/x")                     # bare name, when only one step has it
        Given.http.get("/x")                # namespace given to step_namespace
        Given.my_package.steps.get("/x")    # full module path, never ambiguous

    A missing name raises ``StepNotFound``; a name that could mean more than one
    step raises ``AmbiguousStep``. Names starting with ``_`` are never steps and
    raise a plain ``AttributeError``. The step returned is the registered function
    itself, so it runs in whatever context is active where it is called.
    """

    def __getattr__(self, name: str) -> Any:
        # Tooling (pytest, mock, copy, inspect) probes dunder names; keep those cheap.
        if name.startswith("_"):
            raise _no_attribute(self, name)
        return _registry.resolve((name,))

    def __dir__(self) -> list[str]:
        return _registry.root_names()


class _StepNamespace:
    # The path is private so that every public name reaches __getattr__.
    __slots__ = ("_path",)

    def __init__(self, path: _Path):
        self._path = path

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise _no_attribute(self, name)
        return _registry.resolve((*self._path, name))

    def __dir__(self) -> list[str]:
        return _registry.child_names(self._path)

    def __repr__(self) -> str:
        return f"<step namespace '{_dotted(self._path)}'>"


def step(f: F) -> F:
    """
    Register ``f`` as a step and return it unchanged.

    The step is found by its bare name and by its full module path, for example
    ``Given.get`` and ``Given.my_package.steps.get``. To add a short namespace as
    well, use ``step_namespace``. Raises ``ValueError`` if the name starts with
    ``_``.
    """
    _registry.add_default(f, None)
    return f


def step_namespace(namespace: str) -> Callable[[F], F]:
    """
    Return a decorator like ``step`` that also registers steps under ``namespace``.

    ``namespace`` is a dotted name; each segment must be an identifier that is not
    a keyword and does not start with ``_``, or ``ValueError`` is raised at once::

        step = step_namespace("http")

        @step
        def get(*args, **kwargs): ...

        Given.http.get("/x")
    """
    path = _namespace_path(namespace)

    def register(f: F) -> F:
        _registry.add_default(f, path)
        return f

    return register


Given = When = Then = And = StepPrefix()

_registry: _Registry = _Registry()
