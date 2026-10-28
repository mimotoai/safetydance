import keyword
import sys
import threading
from importlib.metadata import entry_points
from inspect import currentframe, ismethod
from types import CodeType
from typing import Any, Callable, Iterable, TypeVar

__all__ = [
    "AmbiguousStep",
    "And",
    "Given",
    "StepNotFound",
    "StepPrefix",
    "Then",
    "When",
    "register_steps",
    "step",
    "step_namespace",
    "unregister_steps",
]

F = TypeVar("F", bound=Callable[..., Any])

ENTRY_POINT_GROUP = "safetydance.steps"

_Path = tuple[str, ...]
_Key = tuple[Any, ...]
# One registration, (tier, path, key, module), as recorded while an entry point
# loads; the module is the one whose import made the registration.
_Record = tuple[str, _Path, _Key, str]


class StepNotFound(AttributeError):
    """
    Raised when no step is registered at the name looked up on a prefix.

    It is an ``AttributeError``, so ``hasattr(Given, name)`` is false and
    ``getattr(Given, name, default)`` returns ``default``. ``AmbiguousStep`` is a
    separate ``AttributeError``, not a kind of ``StepNotFound``.
    """


class AmbiguousStep(AttributeError):
    """
    Raised when a name looked up on a prefix could mean more than one thing.

    That is, several distinct steps share the name, or the name is both a step and
    a namespace. A step's full module path is unique, except for steps made by one
    factory (closures) and bound methods: give those an explicit registration with
    ``register_steps``, or a short namespace.

    It is an ``AttributeError`` but not a ``StepNotFound``, so ``hasattr`` and
    ``getattr(Given, name, default)`` treat an ambiguous name as absent too.
    """


def _installed_entry_points() -> Iterable[Any]:
    """The installed entry points whose modules register steps when loaded."""
    return entry_points(group=ENTRY_POINT_GROUP)


def _is_segment(name: str) -> bool:
    return name.isidentifier() and not keyword.iskeyword(name) and not name.startswith("_")


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


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


def _layers(f: Callable[..., Any]) -> list[Any]:
    """``f`` and each object it wraps through ``__wrapped__``, stopping at a cycle."""
    # The list keeps every layer alive, so no id in seen can be reused.
    layers: list[Any] = []
    seen: set[int] = set()
    layer: Any = f
    while layer is not None and id(layer) not in seen:
        if len(layers) >= sys.getrecursionlimit():
            # As inspect.unwrap does for a chain that never ends.
            raise ValueError(f"wrapper loop when unwrapping {f!r}")
        layers.append(layer)
        seen.add(id(layer))
        layer = getattr(layer, "__wrapped__", None)
    return layers


def _function_key(f: Any, wrapped: Any) -> _Key:
    code: CodeType = f.__code__
    # A pseudo-file such as "<string>" does not identify the source, so the module
    # tells apart code exec'd in different modules.
    module = getattr(f, "__module__", None) if code.co_filename.startswith("<") else None
    kwdefaults = getattr(f, "__kwdefaults__", None) or {}
    cells = []
    for cell in getattr(f, "__closure__", None) or ():
        try:
            contents = cell.cell_contents
        except ValueError:
            cells.append(("<empty>",))
            continue
        if wrapped is not None and contents is wrapped:
            # A wrapper captures the function it wraps, which the next layer keys.
            cells.append(("<wrapped>",))
        else:
            cells.append(_value_key(contents))
    return (
        code.co_filename,
        code.co_firstlineno,
        getattr(f, "__qualname__", None),
        # Code compares by value, so different code at one location stays distinct.
        code,
        module,
        tuple(_value_key(v) for v in getattr(f, "__defaults__", None) or ()),
        tuple((name, _value_key(v)) for name, v in sorted(kwdefaults.items())),
        tuple(cells),
    )


class _Ref:
    """An identity key: holds ``obj`` alive and never runs its ``__eq__`` or ``__hash__``."""

    # Held, not just its id, so the address cannot be freed and reused by another
    # object that would then key equal.
    __slots__ = ("obj",)

    def __init__(self, obj: object):
        self.obj = obj

    def __eq__(self, other: object) -> bool:
        return type(other) is _Ref and self.obj is other.obj

    def __hash__(self) -> int:
        return id(self.obj)


# Exactly these types (not subclasses) compare equal across two imports of one
# module without running user code; tuples and frozensets of them do too.
_PLAIN_TYPES = (int, bool, float, complex, str, bytes, type(None))


def _plain_key(value: Any) -> Any:
    """The key of a builtin ``value`` that is safe to compare by value, else None."""
    # type() reads no attribute, so a lazy proxy is not evaluated, and identity
    # checks run no __eq__ or __hash__, not even a metaclass's.
    cls = type(value)
    if cls is float:
        # hex() tells apart 0.0 and -0.0, and makes NaN equal to itself.
        return (cls, value.hex())
    if cls is complex:
        return (cls, value.real.hex(), value.imag.hex())
    if any(cls is plain for plain in _PLAIN_TYPES):
        return (cls, value)
    if cls is tuple or cls is frozenset:
        # Element by element, so that (1,) and (1.0,) stay apart.
        items = [_plain_key(item) for item in value]
        if any(item is None for item in items):
            return None
        return (cls, tuple(items) if cls is tuple else frozenset(items))
    return None


def _value_key(value: Any) -> Any:
    """
    Key a default or captured ``value`` of a step: builtins by value, all else by id.

    So a module imported twice dedupes only steps whose values are builtins. Any
    other value tells the copies apart, loudly (AmbiguousStep), rather than risk
    silently merging two different steps.
    """
    key = _plain_key(value)
    return ("<id>", _Ref(value)) if key is None else key


def _layer_key(layer: Any, wrapped: Any) -> _Key:
    if ismethod(layer):
        # A bound method forwards __code__, so methods of different instances share it.
        return ("<method>", _Ref(layer.__self__), _layer_key(layer.__func__, wrapped))
    if isinstance(getattr(layer, "__code__", None), CodeType):
        return _function_key(layer, wrapped)
    return ("<object>", _Ref(layer))


def _step_key(f: Callable[..., Any]) -> _Key:
    # Keyed by each layer's code and by its defaults and captured values: builtins
    # by value, so one module imported under two names (e.g. through pytest's
    # rootdir) is one step, and anything else by identity. Every wrapper in a
    # functools.wraps chain counts.
    layers = _layers(f)
    return tuple(
        _layer_key(layer, layers[i + 1] if i + 1 < len(layers) else None)
        for i, layer in enumerate(layers)
    )


def _full_path(f: Callable[..., Any]) -> _Path:
    return (*f.__module__.split("."), f.__name__)


def _full_name(f: Callable[..., Any]) -> str:
    return f"{f.__module__}.{f.__name__}"


def _reachable(path: _Path) -> bool:
    """Whether ``path`` can be typed as attribute lookups on a prefix."""
    return (
        not path[0].startswith("_")
        and all(s.isidentifier() and not keyword.iskeyword(s) for s in path)
        and not any(_is_dunder(s) for s in path)
    )


def _advisable(full: _Path, path: _Path) -> bool:
    # A full module path through the path being looked up is just as ambiguous.
    return _reachable(full) and full[: len(path)] != path


def _candidate_names(steps: list[Callable[..., Any]]) -> list[str]:
    names = [_full_name(f) for f in steps]
    labels = []
    for name, f in zip(names, steps):
        code = getattr(_layers(f)[-1], "__code__", None)
        # A full name alone cannot tell apart candidates that share it.
        if isinstance(code, CodeType) and names.count(name) > 1:
            name = f"{name} ({code.co_filename}:{code.co_firstlineno})"
        labels.append(name)
    return sorted(labels)


def _dotted(path: _Path) -> str:
    return ".".join(path)


class _Registry:
    def __init__(self, read_entry_points: Callable[[], Iterable[Any]] = _installed_entry_points):
        # Not called here: entry points are read lazily, on the first lookup.
        self._read_entry_points = read_entry_points
        self._lock = threading.Lock()
        # Held while entry points load. Never acquired while _lock is held: a loading
        # module registers steps, which takes _lock. Reentrant, so a module may look
        # steps up while it is being imported. Known limit: if a module does a lookup
        # during its import while another thread loads an entry point that imports
        # the same module, the two threads deadlock; once every entry point has
        # loaded, the lock-free path rules this out.
        self._load_lock = threading.RLock()
        self._unloaded: list[Any] | None = None
        self._loading: set[int] = set()
        # Per thread: what each entry point the thread is loading has registered,
        # innermost last, so that a failed load can remove it.
        self._local = threading.local()
        self._explicit: dict[_Path, tuple[_Key, Callable[..., Any]]] = {}
        self._default: dict[_Path, dict[_Key, Callable[..., Any]]] = {}
        # Every proper prefix of a registered path, mapped to its child names; the
        # root () included, so a namespace check is a single dict lookup.
        self._children: dict[_Path, set[str]] = {}

    def _add_prefixes(self, path: _Path) -> None:
        for i in range(len(path)):
            self._children.setdefault(path[:i], set()).add(path[i])

    def _rebuild_children(self) -> None:
        self._children = {}
        for path in (*self._default, *self._explicit):
            self._add_prefixes(path)

    def _journals(self) -> list[list[_Record]]:
        journals: list[list[_Record]] | None = getattr(self._local, "journals", None)
        if journals is None:
            journals = self._local.journals = []
        return journals

    def _record(self, tier: str, path: _Path, key: _Key, module: str) -> None:
        # Thread-local, so another thread's registrations never count as the load's.
        journals = self._journals()
        if journals:
            journals[-1].append((tier, path, key, module))

    def _importer(self) -> str | None:
        """
        The module whose import is registering steps, while an entry point loads.

        That is the nearest module-level frame above the caller and below this
        thread's innermost load. A failing module often registers steps defined
        elsewhere, such as a library's bound method or factory closure.
        """
        if not self._journals():
            return None
        frame = currentframe()
        while frame is not None and frame.f_code is not _LOAD_CODE:
            if frame.f_code.co_name == "<module>":
                name = frame.f_globals.get("__name__")
                if isinstance(name, str):
                    return name
            frame = frame.f_back
        return None

    def add_default(self, f: Callable[..., Any], namespace: _Path | None) -> None:
        name = _step_name(f)
        key = _step_key(f)
        paths = [_full_path(f), (name,)]
        if namespace is not None:
            paths.append((*namespace, name))
        importer = self._importer() or f.__module__
        # Defaults and captured values are keyed as builtins or _Refs, so comparing
        # keys under _lock runs none of their __eq__ or __hash__; names such as
        # __qualname__ are used as given.
        with self._lock:
            for path in paths:
                registered = self._default.setdefault(path, {})
                # The same object under an older key (a default or captured value
                # rebound since) is one step, not two.
                stale = [k for k, g in registered.items() if g is f and k != key]
                for old in stale:
                    del registered[old]
                # A replaced entry was registered before, so a failed load that
                # replaces it must not roll it back; the explicit tier does the same.
                if key not in registered and not stale:
                    self._record("default", path, key, importer)
                # The same step registered again replaces the stored object.
                registered[key] = f
                self._add_prefixes(path)

    def add_explicit_all(self, steps: list[Callable[..., Any]], namespace: _Path) -> None:
        bindings: dict[_Path, list[tuple[_Key, Callable[..., Any]]]] = {}
        for f in steps:
            path = (*namespace, _step_name(f))
            bindings.setdefault(path, []).append((_step_key(f), f))
        importer = self._importer()
        with self._lock:
            # Check every conflict before binding anything, so a failure binds nothing.
            for path, items in bindings.items():
                self._check_explicit(path, items)
            for path, items in bindings.items():
                if path not in self._explicit:
                    module = importer or items[-1][1].__module__
                    self._record("explicit", path, items[-1][0], module)
                self._explicit[path] = items[-1]
                self._add_prefixes(path)

    def _check_explicit(self, path: _Path, items: list[tuple[_Key, Callable[..., Any]]]) -> None:
        where = f"Cannot register {_full_name(items[0][1])} at '{_dotted(path)}'"
        if path in self._children:
            raise ValueError(f"{where}: it is already a namespace of other steps.")
        for i in range(1, len(path)):
            bound = self._explicit.get(path[:i])
            if bound is not None:
                raise ValueError(
                    f"{where}: '{_dotted(path[:i])}' is already registered to "
                    f"{_full_name(bound[1])}."
                )
        existing = self._explicit.get(path)
        # Every step must be the same as the one already bound and the one that will
        # be bound, by key or, though a rebound value changed its key, as an object.
        bindings = [existing, items[-1]] if existing is not None else [items[-1]]
        for key, f in items:
            for other_key, other in bindings:
                if key != other_key and f is not other:
                    raise ValueError(
                        f"'{_dotted(path)}' is already registered to "
                        f"{_full_name(other)}; cannot also register "
                        f"{_full_name(f)} there."
                    )

    def remove(self, steps: tuple[Callable[..., object], ...]) -> None:
        keys = {_step_key(f) for f in steps}

        def matches(key: _Key, stored: Callable[..., Any]) -> bool:
            # By object too, so a step whose values were rebound since still goes.
            return key in keys or any(stored is f for f in steps)

        with self._lock:
            for path in list(self._default):
                registered = self._default[path]
                for key in [k for k, g in registered.items() if matches(k, g)]:
                    del registered[key]
                if not registered:
                    del self._default[path]
            for path, (key, stored) in list(self._explicit.items()):
                if matches(key, stored):
                    del self._explicit[path]
            self._rebuild_children()

    def _roll_back(self, records: list[_Record]) -> None:
        with self._lock:
            for tier, path, key, module in records:
                # A module that imported along the way stays imported, so a retry
                # would not register its steps again; only failed modules' go.
                if module in sys.modules:
                    continue
                if tier == "explicit":
                    bound = self._explicit.get(path)
                    if bound is not None and bound[0] == key:
                        del self._explicit[path]
                    continue
                registered = self._default.get(path)
                if registered is not None:
                    registered.pop(key, None)
                    if not registered:
                        del self._default[path]
            self._rebuild_children()

    def _load(self, ep: Any) -> None:
        journals = self._journals()
        records: list[_Record] = []
        journals.append(records)
        self._loading.add(id(ep))
        try:
            ep.load()
        except BaseException as e:
            # So that a retry registers the failed modules' steps afresh.
            self._roll_back(records)
            where = f"entry point {ep.name!r} ({ep.value}) in group {ENTRY_POINT_GROUP!r}"
            if isinstance(e, AttributeError):
                # Raised out of __getattr__, it would read as a missing step.
                raise ImportError(f"{where} failed to load: {e}") from e
            note = f"While loading {where}"
            if hasattr(e, "add_note") and note not in getattr(e, "__notes__", ()):
                e.add_note(note)
            raise
        finally:
            journals.pop()
            self._loading.discard(id(ep))

    def _load_entry_points(self) -> None:
        """
        Load every entry point not loaded yet.

        Everything installed loads before the first lookup resolves, so no result
        depends on which lookups came first.
        """
        # The list only shrinks, and only after a load returns, so empty means done.
        if self._unloaded == []:
            return
        with self._load_lock:
            if self._unloaded is None:
                self._unloaded = list(self._read_entry_points())
            for ep in list(self._unloaded):
                # Skip one this thread is already loading, as a circular import would,
                # and one that a lookup made during an earlier load has loaded.
                if id(ep) in self._loading or not any(ep is p for p in self._unloaded):
                    continue
                self._load(ep)
                # Only once load() returns, so a failed load raises again next time.
                self._unloaded = [p for p in self._unloaded if p is not ep]

    def resolve(self, path: _Path, node: object) -> Any:
        self._load_entry_points()
        with self._lock:
            explicit = self._explicit.get(path)
            defaults = list(self._default.get(path, {}).values())
            is_namespace = path in self._children
        if explicit is not None and is_namespace:
            raise _lookup_error(
                AmbiguousStep,
                f"'{_dotted(path)}' is both a step and a namespace; register_steps "
                f"bound {_full_name(explicit[1])} there, so bind it under a namespace "
                "of its own with register_steps(..., namespace=...) instead",
                path,
                node,
            )
        if explicit is not None:
            return explicit[1]
        if len(defaults) > 1:
            candidates = ", ".join(_candidate_names(defaults))
            full_paths = {_full_path(f) for f in defaults}
            if len(full_paths) == len(defaults) and all(
                _advisable(full, path) for full in full_paths
            ):
                hint = "Use the full module path, a short namespace, or"
            else:
                hint = "Give each a short namespace with step_namespace, or use"
            raise _lookup_error(
                AmbiguousStep,
                f"Step '{_dotted(path)}' is ambiguous; it could be any of: {candidates}. "
                f"{hint} register_steps(..., namespace=...) to choose one",
                path,
                node,
            )
        if defaults and is_namespace:
            found = defaults[0]
            if _advisable(_full_path(found), path):
                hint = f"use the step's full module path, {_full_name(found)}, to call it"
            else:
                hint = (
                    "give the step a short namespace with step_namespace, or bind it "
                    "with register_steps(..., namespace=...)"
                )
            raise _lookup_error(
                AmbiguousStep, f"'{_dotted(path)}' is both a step and a namespace; {hint}", path, node
            )
        if defaults:
            return defaults[0]
        if is_namespace:
            return _StepNamespace(path)
        raise _lookup_error(
            StepNotFound,
            f"No step named '{_dotted(path)}'. Steps are found only once their module "
            f"is imported or exposed through a {ENTRY_POINT_GROUP!r} entry point",
            path,
            node,
        )

    def child_names(self, path: _Path) -> list[str]:
        with self._lock:
            names = self._children.get(path, set())
            # Only names a lookup can reach: the root refuses every name starting
            # with "_", a namespace only dunders (full module paths have __main__).
            if not path:
                return sorted(name for name in names if not name.startswith("_"))
            return sorted(name for name in names if not _is_dunder(name))


def _lookup_error(
    cls: type[AttributeError], message: str, path: _Path, node: object
) -> AttributeError:
    # name and obj let Python add "Did you mean ...?"; it appends that after a
    # period, so no message ends with one.
    return cls(message, name=path[-1], obj=node)


def _no_attribute(obj: object, name: str) -> AttributeError:
    return AttributeError(
        f"{type(obj).__name__!r} object has no attribute {name!r}", name=name, obj=obj
    )


class StepPrefix:
    """
    Looks steps up by name: ``Given``, ``When``, ``Then`` and ``And`` are one instance.

    Each attribute is a registered step, or a namespace that holds steps::

        Given.get("/x")                     # bare name, when only one step has it
        Given.http.get("/x")                # namespace given to step_namespace
        Given.my_package.steps.get("/x")    # full module path

    A missing name raises ``StepNotFound``; a name that could mean more than one
    step raises ``AmbiguousStep``. The full module path is unique, except for steps
    made by one factory (closures) and bound methods, which need an explicit
    registration with ``register_steps`` or a short namespace. The first lookup
    imports every module exposed through a ``safetydance.steps`` entry point. The
    step returned is the registered function itself, so it runs in whatever context
    is active where it is called.

    Names starting with ``_`` are never looked up on a prefix and raise a plain
    ``AttributeError``. Below the prefix only dunder names are refused, so a full
    module path such as ``Given.my_package._impl.steps.get`` works. Attributes
    cannot be set on a prefix.
    """

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        # Tooling (pytest, mock, copy, inspect) probes dunder names; keep those cheap.
        if name.startswith("_"):
            raise _no_attribute(self, name)
        return _registry.resolve((name,), self)

    def __dir__(self) -> list[str]:
        return _registry.child_names(())

    def __repr__(self) -> str:
        return "<step prefix>"


class _StepNamespace:
    # A dunder, which lookups refuse, so that every other name reaches __getattr__,
    # including a module path segment such as _path.
    __slots__ = ("__sd_path__",)

    def __init__(self, path: _Path):
        self.__sd_path__ = path

    def __getattr__(self, name: str) -> Any:
        # A module path may have single-underscore segments, like _impl.
        if _is_dunder(name):
            raise _no_attribute(self, name)
        return _registry.resolve((*self.__sd_path__, name), self)

    def __dir__(self) -> list[str]:
        return _registry.child_names(self.__sd_path__)

    def __repr__(self) -> str:
        return f"<step namespace '{_dotted(self.__sd_path__)}'>"


def step(f: F) -> F:
    """
    Register ``f`` as a step and return it unchanged.

    The step is found by its bare name and by its full module path, for example
    ``Given.get`` and ``Given.my_package.steps.get``. To add a short namespace as
    well, use ``step_namespace``. Raises ``ValueError`` if the name is a keyword,
    is not an identifier or starts with ``_``.

    Registering the same step again replaces it rather than making its names
    ambiguous, so a module imported under two names is harmless. Two steps are the
    same when they have the same code, defaults and captured values, through every
    wrapper of a ``functools.wraps`` chain, and, for bound methods, the same
    instance.
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


def _check_callables(steps: tuple[Callable[..., object], ...]) -> None:
    for f in steps:
        if not callable(f):
            raise TypeError(f"A step must be callable; got {f!r}.")


def register_steps(*steps: Callable[..., object], namespace: str | None = None) -> None:
    """
    Bind ``steps`` explicitly, ahead of every step registered with ``step``.

    Each step is bound at its bare name, or under ``namespace`` when given, and
    only there. Use it to settle a collision between steps from different
    libraries::

        register_steps(other_lib.get, namespace="other")

        Given.other.get("/x")

    The steps need not have been decorated with ``step``. Raises ``TypeError`` when
    no step is given, or for something that is not a named callable. Raises
    ``ValueError`` for an invalid namespace or step name, a different step already
    bound at the same path, a path that is already a namespace of other steps, or a
    path under one already bound to a step. Every argument is checked first, so a
    failed call registers nothing.
    """
    if not steps:
        raise TypeError("register_steps() needs at least one step.")
    path = () if namespace is None else _namespace_path(namespace)
    _check_callables(steps)
    _registry.add_explicit_all(list(steps), path)


def unregister_steps(*steps: Callable[..., object]) -> None:
    """
    Remove ``steps`` from every path they are registered at.

    That covers both ``step`` and ``register_steps``: the bare name, the full
    module path, short namespaces and explicit bindings. A registration goes when
    it is the same step as one given, in the sense that ``step`` describes, or is
    the very object given, even if its defaults or captured values have changed
    since. A step that is not registered is ignored. Raises ``TypeError`` for
    something that is not callable; every argument is checked first, so a failed
    call removes nothing. Raises ``TypeError`` when no step is given, as
    ``register_steps`` does.
    """
    if not steps:
        raise TypeError("unregister_steps() needs at least one step.")
    _check_callables(steps)
    _registry.remove(steps)


Given = When = Then = And = StepPrefix()

# Where _Registry._importer stops walking up: the frame of the innermost load.
_LOAD_CODE = _Registry._load.__code__

_registry: _Registry = _Registry()
