import keyword
import threading
from inspect import unwrap
from types import MethodType
from typing import Any, Callable, TypeVar

__all__ = [
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

_Path = tuple[str, ...]


class StepNotFound(AttributeError):
    """
    Raised when no step or namespace is registered at the name looked up.

    It is an ``AttributeError``, so ``hasattr(Given, name)`` is false and
    ``getattr(Given, name, default)`` returns ``default``. Steps are found only
    once the module that registers them has been imported.
    """


def _dotted(path: _Path) -> str:
    return ".".join(path)


def _same(a: Any, b: Any) -> bool:
    # ``obj.m`` makes a new bound method on every access, so compare its parts.
    # ``type(...) is`` avoids running a user-defined ``__class__`` under the lock.
    return a is b or (
        type(a) is MethodType
        and type(b) is MethodType
        and a.__self__ is b.__self__
        and a.__func__ is b.__func__
    )


def _code(f: Any) -> Any:
    # Unwrap decorators, so a shared ``functools.wraps`` wrapper does not stand for the step.
    f = getattr(f, "__func__", f)
    try:
        f = unwrap(f)
    except Exception:  # a cyclic or raising ``__wrapped__`` must not hide the conflict
        pass
    return getattr(f, "__code__", None)


def _describe(f: Any) -> str:
    name = getattr(f, "__qualname__", None) or getattr(f, "__name__", "?")
    code = _code(f)
    where = f"{code.co_filename}:{code.co_firstlineno}" if code else "unknown location"
    return f"{getattr(f, '__module__', '?')}.{name} ({where})"


def _hint(a: Any, b: Any) -> str:
    if type(a) is MethodType and type(b) is MethodType and a.__func__ is b.__func__:
        if a.__self__ is b.__self__:
            return ""
        return "; a method of a different instance"
    # The same source file imported under two names registers two copies of a step.
    where = [(c.co_filename, c.co_firstlineno) for c in (_code(a), _code(b)) if c]
    mods = getattr(a, "__module__", None), getattr(b, "__module__", None)
    if len(where) == 2 and where[0] == where[1] and mods[0] != mods[1]:
        return (
            f"; the module was imported twice, as {mods[0]} and {mods[1]}; "
            "see the testing guide"
        )
    return ""


def _segment(name: str, what: str) -> str:
    if not name.isidentifier() or keyword.iskeyword(name) or name.startswith("_"):
        raise ValueError(
            f"{what} {name!r} must be a public identifier, not a keyword or _-prefixed"
        )
    return name


def _prefix(namespace: str | None) -> _Path:
    if namespace is None:
        return ()
    if not isinstance(namespace, str):
        raise TypeError(f"a namespace must be a str, not {type(namespace).__name__}")
    return tuple(_segment(part, "namespace segment") for part in namespace.split("."))


def _step_name(f: Any) -> str:
    if not callable(f):
        raise TypeError(f"a step must be callable, not {type(f).__name__}")
    name = getattr(f, "__name__", None)
    if not isinstance(name, str):
        raise TypeError(f"{f!r} has no __name__, so it has no step name")
    return _segment(name, "step name")


class _Registry:
    """Bindings from a path to a step, plus an index of each prefix's children."""

    def __init__(self) -> None:
        # One lock guards both dicts. Nothing user-defined runs while it is held:
        # only identity checks and dict operations.
        self._lock = threading.Lock()
        self._bindings: dict[_Path, Callable[..., Any]] = {}
        self._children: dict[_Path, set[str]] = {}

    def _clash(self, prefix: _Path, name: str, f: Any, pending: dict[_Path, Any]):
        """Why ``f`` cannot be bound at ``prefix + (name,)``, as (reason, other), or None."""
        path = (*prefix, name)
        taken = self._bindings.get(path, pending.get(path))
        if taken is not None and not _same(f, taken):
            return f"'{_dotted(path)}' is already bound to", taken
        # A binding above the path is in ``_bindings``: one call binds one prefix.
        for i in range(1, len(path)):
            above = self._bindings.get(path[:i])
            if above is not None:
                return f"'{_dotted(path[:i])}' is the step", above
        if path in self._children:
            below = path
            while below not in self._bindings:
                below += (min(self._children[below]),)
            return f"'{_dotted(path)}' is a namespace holding", self._bindings[below]
        return None

    def bind(self, prefix: _Path, steps: list[tuple[str, Callable[..., Any]]]) -> None:
        """Bind every step under ``prefix`` or none; raises ``ValueError`` on a conflict."""
        pending: dict[_Path, Any] = {}
        bad = None
        with self._lock:
            for name, f in steps:
                clash = self._clash(prefix, name, f, pending)
                if clash:
                    bad = ((*prefix, name), f, *clash)
                    break
                pending.setdefault((*prefix, name), f)
            else:
                for path, f in pending.items():
                    if path not in self._bindings:
                        self._bindings[path] = f
                        for i in range(len(path)):
                            self._children.setdefault(path[:i], set()).add(path[i])
        if bad:
            path, f, reason, other = bad
            raise ValueError(
                f"{reason} {_describe(other)}; cannot register "
                f"{_describe(f)} at '{_dotted(path)}'{_hint(other, f)}"
            )

    def unbind(self, steps: tuple[Callable[..., Any], ...]) -> None:
        """Remove every path bound to any of ``steps``; unknown steps are ignored."""
        with self._lock:
            for path, bound in list(self._bindings.items()):
                if any(_same(bound, s) for s in steps):
                    del self._bindings[path]
                    # Drop each prefix that no longer holds anything.
                    for i in range(len(path) - 1, -1, -1):
                        kids = self._children[path[:i]]
                        kids.discard(path[i])
                        if kids:
                            break
                        del self._children[path[:i]]

    def resolve(self, path: _Path) -> Any:
        """The step at ``path``, a namespace node if it has children, else None."""
        with self._lock:
            found = self._bindings.get(path)
            is_namespace = path in self._children
        if found is not None:
            return found
        return _StepNamespace(path) if is_namespace else None

    def children(self, path: _Path) -> list[str]:
        with self._lock:
            return sorted(self._children.get(path, ()))


# Read when called, so tests can swap in an isolated registry.
_registry = _Registry()


def _lookup(owner: object, path: _Path) -> Any:
    name = path[-1]
    # Tooling probes dunder and private names; keep those plain and cheap.
    if name.startswith("_"):
        raise AttributeError(
            f"{type(owner).__name__!r} object has no attribute {name!r}",
            name=name,
            obj=owner,
        )
    found = _registry.resolve(path)
    if found is None:
        raise StepNotFound(
            f"No step or namespace at '{_dotted(path)}'; "
            "import the module that defines it",
            name=name,
            obj=owner,
        )
    return found


class StepPrefix:
    """
    Looks steps up by name: ``Given``, ``When``, ``Then`` and ``And`` are one instance.

    Each attribute is a registered step, or a namespace that holds steps::

        Given.get("/x")             # bare name registered with step
        Given.http.get("/x")        # namespace given to step_namespace

    A missing name raises ``StepNotFound``. The step returned is the registered
    function itself. Names starting with ``_`` raise a plain ``AttributeError``.
    """

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        return _lookup(self, (name,))

    def __dir__(self) -> list[str]:
        return _registry.children(())

    def __repr__(self) -> str:
        return "<step prefix>"


class _StepNamespace:
    # Dunder-named, so that it can never collide with a step name.
    __slots__ = ("__sd_path__",)

    def __init__(self, path: _Path):
        self.__sd_path__ = path

    def __getattr__(self, name: str) -> Any:
        # Copy and pickle probe _-names before the slot is filled; never read it then.
        path = () if name.startswith("_") else self.__sd_path__
        return _lookup(self, (*path, name))

    def __dir__(self) -> list[str]:
        return _registry.children(self.__sd_path__)

    def __repr__(self) -> str:
        return f"<step namespace '{_dotted(self.__sd_path__)}'>"


def _bind(steps: tuple[Callable[..., Any], ...], prefix: _Path) -> None:
    _registry.bind(prefix, [(_step_name(f), f) for f in steps])


def step(f: F) -> F:
    """
    Register ``f`` under its own name and return it unchanged.

    ``Given.name(...)`` then calls it. Registering the same step again does
    nothing; a different step at the same name raises ``ValueError`` and
    nothing is replaced. Raises ``TypeError`` if ``f`` is not callable or has no
    ``__name__``, and ``ValueError`` if its name is not a public identifier.
    """
    _bind((f,), ())
    return f


def step_namespace(namespace: str) -> Callable[[F], F]:
    """
    Make a decorator that registers a step under a dotted ``namespace``.

    ``step_namespace("http")`` registers ``get`` as ``Given.http.get``, and
    ``step_namespace("my.pkg")`` as ``Given.my.pkg.get``. The namespace is
    validated now, not when the decorator is applied, and raises ``ValueError``
    if a segment is not a public identifier, and ``TypeError`` if ``namespace`` is
    not a ``str``. The decorated function is returned
    unchanged.
    """
    if namespace is None:
        raise TypeError("a namespace must be a str, not NoneType")
    prefix = _prefix(namespace)

    def decorator(f: F) -> F:
        _bind((f,), prefix)
        return f

    return decorator


def register_steps(*steps: Callable[..., object], namespace: str | None = None) -> None:
    """
    Register each of ``steps`` under its own name, optionally inside ``namespace``.

    ``register_steps(f)`` makes ``Given.f``, and ``register_steps(f, namespace="a.b")``
    makes ``Given.a.b.f``. A step already registered elsewhere is also reachable at
    the new path. Registration is all or nothing: if any step is invalid or
    conflicts, nothing is bound. Registering the same step again does nothing; a
    different step at a taken path raises ``ValueError``. Bound methods are the
    same step when they share ``__self__`` and ``__func__``. Raises ``TypeError``
    if called with no steps, if ``namespace`` is not a ``str`` or ``None``, or if a
    step is not callable or has no ``__name__``.
    """
    if not steps:
        raise TypeError("register_steps() needs at least one step")
    _bind(steps, _prefix(namespace))


def unregister_steps(*steps: Callable[..., object]) -> None:
    """
    Remove every path at which each of ``steps`` is registered.

    Namespaces left empty disappear. A step that is not registered is ignored, and
    a bound method matches by ``__self__`` and ``__func__``. Raises ``TypeError``
    if called with no steps.
    """
    if not steps:
        raise TypeError("unregister_steps() needs at least one step")
    _registry.unbind(steps)


Given = When = Then = And = StepPrefix()
