import keyword
import threading
from importlib.metadata import entry_points
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
    """The installed entry points whose modules register steps when loaded."""
    return entry_points(group=ENTRY_POINT_GROUP)


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
        # Held while entry points load. Never acquired while _lock is held: a loading
        # module registers steps, which takes _lock. Reentrant, so a module may look
        # steps up while it is being imported. Known limit: if a module does a root
        # lookup during its import while another thread loads an entry point that
        # imports the same module, the two threads deadlock; once every entry point
        # has loaded, the lock-free path rules this out.
        self._load_lock = threading.RLock()
        self._unloaded: list[Any] | None = None
        self._loading: set[int] = set()
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

    def add_explicit(self, f: Callable[..., Any], namespace: _Path) -> None:
        self.add_explicit_all([f], namespace)

    def add_explicit_all(self, steps: list[Callable[..., Any]], namespace: _Path) -> None:
        bindings: dict[_Path, list[tuple[_Key, Callable[..., Any]]]] = {}
        for f in steps:
            path = (*namespace, _step_name(f))
            bindings.setdefault(path, []).append((_step_key(f), f))
        with self._lock:
            # Check every conflict before binding anything, so a failure binds nothing.
            for path, items in bindings.items():
                first = self._explicit.get(path) or items[0]
                for key, f in items:
                    if key != first[0]:
                        raise ValueError(
                            f"'{_dotted(path)}' is already registered to "
                            f"{_full_name(first[1])}; cannot also register "
                            f"{_full_name(f)} there."
                        )
            for path, items in bindings.items():
                self._explicit[path] = items[-1]
                self._add_prefixes(path)

    def _load_entry_points(self, name: str | None) -> None:
        """Load every not-yet-loaded entry point called ``name``, or all of them."""
        # The list only shrinks, and only after a load returns, so empty means done.
        if self._unloaded == []:
            return
        with self._load_lock:
            if self._unloaded is None:
                self._unloaded = list(self._entry_points())
            for ep in list(self._unloaded):
                # Skip one this thread is already loading, as a circular import would.
                if (name is not None and ep.name != name) or id(ep) in self._loading:
                    continue
                # A lookup made while an earlier one was loading may have loaded it.
                if not any(ep is pending for pending in self._unloaded):
                    continue
                self._loading.add(id(ep))
                try:
                    ep.load()
                finally:
                    self._loading.discard(id(ep))
                # Only once load() returns, so a failed load raises again next time.
                self._unloaded = [pending for pending in self._unloaded if pending is not ep]

    def resolve(self, path: _Path) -> Any:
        if len(path) > 1:
            return self._resolve(path)
        self._load_entry_points(path[0])
        try:
            return self._resolve(path)
        except StepNotFound:
            pass
        self._load_entry_points(None)
        return self._resolve(path)

    def _resolve(self, path: _Path) -> Any:
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
                "Use the full module path, a short namespace, or "
                "register_steps(..., namespace=...) to choose one."
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


def register_steps(*steps: Callable[..., object], namespace: str | None = None) -> None:
    """
    Bind ``steps`` explicitly, ahead of every step registered with ``step``.

    Each step is bound at its bare name, or under ``namespace`` when given, and
    only there. Use it to settle a collision between steps from different
    libraries::

        register_steps(other_lib.get, namespace="other")

        Given.other.get("/x")

    The steps need not have been decorated with ``step``. Raises ``TypeError`` for
    something that is not a named callable, and ``ValueError`` for an invalid
    namespace, a name starting with ``_``, or a different step already bound at the
    same path. Every argument is checked first, so a failed call registers nothing.
    """
    path = () if namespace is None else _namespace_path(namespace)
    for f in steps:
        if not callable(f):
            raise TypeError(f"A step must be callable; got {f!r}.")
    _registry.add_explicit_all(list(steps), path)


Given = When = Then = And = StepPrefix()

_registry: _Registry = _Registry()
