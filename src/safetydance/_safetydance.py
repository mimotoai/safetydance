import logging
import re
import sys
import typing
from collections.abc import MutableMapping
from contextvars import ContextVar, copy_context
from enum import EnumMeta
from functools import cached_property, partialmethod, wraps
from inspect import (
    isasyncgenfunction,
    iscoroutinefunction,
    isgeneratorfunction,
    isroutine,
    unwrap,
)
from logging import Logger
from types import FrameType, MemberDescriptorType
from typing import Any, Callable, ClassVar, Generic, ParamSpec, TypeVar, get_origin, overload

if sys.version_info >= (3, 14):
    from annotationlib import Format, get_annotations

    def _own_annotations(cls: type) -> dict:
        # Annotations are lazy on 3.14+; FORWARDREF tolerates names defined later.
        return get_annotations(cls, format=Format.FORWARDREF)

else:
    from inspect import get_annotations as _own_annotations


__all__ = [
    "Context",
    "ContextData",
    "ContextDataNotSet",
    "ContextProperty",
    "NoActiveContext",
    "context",
    "context_data",
    "get_context",
    "initializer",
    "script",
    "test_context",
]


class NoActiveContext(RuntimeError):
    """
    Raised when a Context is needed but none is active.

    ``get_context()`` and ``ContextProperty.value`` raise it when called outside a
    ``@script`` (or ``@context``) function and outside a ``with Context():`` block.
    """


class ContextDataNotSet(KeyError, AttributeError):
    """
    Raised when a context value is read before it was set.

    It is both a ``KeyError`` and an ``AttributeError``, so ``hasattr()`` and
    ``getattr(obj, name, default)`` work on ``context_data`` fields. Reading a field
    outside any context raises it too.
    """


T = TypeVar("T")
P = ParamSpec("P")
R = TypeVar("R")


class ContextProperty(Generic[T]):
    """
    A typed key into a Context. Each field of a ``context_data`` class is one.

    Read and write the value in the active context with ``prop.value``, or in an
    explicit context with ``prop(ctx)`` and ``prop[ctx] = new_value``::

        prop.value = 3
        prop.value             # 3
        prop(other_ctx)        # read in other_ctx
        prop[other_ctx] = 4    # write in other_ctx

    A read finds, in order, the value stored for this property, a value supplied by
    ``name`` (such as a pytest fixture given to ``test_context``), and then the
    result of the initializer, which is stored. Otherwise it raises
    ``ContextDataNotSet``.

    :param name: the field name; also the key used to find values supplied by name.
    :param description: free-form text describing the property.
    :param initializer: called with the Context on the first read to produce a value.
    :param declaring_cls: the class that declares the property.
    """

    name: str | None
    description: str | None
    initializer: Callable[["Context"], T] | None
    declaring_cls: type | None

    def __init__(
        self,
        name: str | None = None,
        description: str | None = None,
        initializer: Callable[["Context"], T] | None = None,
        declaring_cls: type | None = None,
    ):
        self.name = name
        self.description = description
        self.initializer = initializer
        self.declaring_cls = declaring_cls

    @property
    def value(self) -> T:
        """
        The value in the active context, readable and writable.

        Raises ``NoActiveContext`` when no context is active and ``ContextDataNotSet``
        when there is no value to read.
        """
        return self._get(get_context(), sys._getframe(1))

    @value.setter
    def value(self, new_value: T):
        self._set(get_context(), new_value, sys._getframe(1))

    def __call__(self, context: "Context") -> T:
        """Read the value in ``context`` instead of the active context."""
        return self._get(context, sys._getframe(1))

    def __setitem__(self, context: "Context", new_value: T):
        """Write the value in ``context`` instead of the active context: ``prop[ctx] = v``."""
        self._set(context, new_value, sys._getframe(1))

    # Values are stored under the property itself, so same-named properties never
    # collide. The name is only a read fallback, which lets values supplied by name
    # (e.g. test fixtures via test_context) bind to the property.
    def _get(self, context: "Context", frame: FrameType) -> T:
        if self in context:
            value = context[self]
        elif self.name and self.name in context:
            value = context[self.name]
        elif self.initializer is not None:
            # setdefault is atomic, so threads sharing a Context that initialize at
            # the same time all get the value stored first.
            initialized = self.initializer(context)
            value = context._data.setdefault(self, initialized)
            if value is initialized:
                context._trace(self, "initialized", frame)
        else:
            context._trace(self, "not found", frame)
            raise ContextDataNotSet(f"ContextData {self} not found in Context")
        context._trace(self, "retrieved", frame)
        return value

    def _set(self, context: "Context", new_value: T, frame: FrameType):
        context._trace(self, "set", frame)
        context[self] = new_value

    def _unset(self, context: "Context", frame: FrameType):
        context._trace(self, "deleted", frame)
        context.pop(self, None)

    def __repr__(self) -> str:
        if self.name and self.declaring_cls:
            return f"{self.name} of {self.declaring_cls}"
        else:
            return super().__repr__()

    @property
    def has_value(self) -> bool:
        """
        Whether a read in the active context would produce a value.

        True if a value is stored for the property, one is supplied by name, or an
        initializer or default can produce one. Nothing is initialized by asking.
        Raises ``NoActiveContext`` when no context is active.
        """
        context = get_context()
        return bool(
            (self.name and self.name in context)
            or self in context
            or self.initializer is not None
        )


class Context(MutableMapping[ContextProperty | str, Any]):
    """
    A mapping that holds the values of ContextProperty keys (or plain string names).

    Every Context owns its storage; a nested Context never reads or writes its
    parent's values. Use it as a context manager to make it the active context::

        with Context() as ctx:
            Cart.items = ["apple"]

    Entering a Context that is already active in the current thread or task raises
    ``RuntimeError``; one Context may be active in several threads or tasks at once.
    Exiting a Context that is not the innermost one raises ``RuntimeError``; if it
    is active, the contexts entered after it are exited with it.
    Contexts compare by identity and are hashable.

    :param tracing: log each read, write and delete of context data.
    :param tracing_logger: the logger to use (default: the root logger).
    :param tracing_log_level: the level to log at (default: ``logging.INFO``).

    Other positional and keyword arguments initialize the mapping like ``dict()``.
    """

    def __init__(
        self,
        *args,
        tracing: bool = False,
        tracing_logger: Logger | None = None,
        tracing_log_level: int = logging.INFO,
        **kwargs,
    ):
        self.tracing = tracing
        self.tracing_logger = tracing_logger or logging.getLogger()
        self.tracing_log_level = tracing_log_level
        self._data = dict(*args, **kwargs)

    # Every Context owns its storage: a nested Context (and so a nested script) never
    # reads or writes its parent's values. To share a context, call a step (a plain
    # function) rather than a script.
    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value

    def __delitem__(self, key):
        del self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __repr__(self) -> str:
        return f"Context({self._data!r})"

    # Identity, not Mapping's value equality: a Context is a scope, and it must stay
    # hashable so it can key dicts and sets.
    def __eq__(self, other: object) -> bool:
        return self is other

    __hash__ = object.__hash__

    # The active stack lives in a ContextVar, never on the Context, so one Context can
    # be active on several threads or tasks at once.
    def __enter__(self) -> "Context":
        stack = _ACTIVE_CONTEXTS.get()
        if any(active is self for active in stack):
            raise RuntimeError(f"{self!r} is already active")
        _ACTIVE_CONTEXTS.set(stack + (self,))
        return self

    # Contexts left open above this one (an __enter__ without its __exit__) are dropped
    # along with it before raising, so the thread or task is not stuck with them.
    def __exit__(self, exc_type, exc_value, traceback):
        stack = _ACTIVE_CONTEXTS.get()
        for index in range(len(stack) - 1, -1, -1):
            if stack[index] is self:
                _ACTIVE_CONTEXTS.set(stack[:index])
                if index == len(stack) - 1:
                    return
                break
        raise RuntimeError(f"{self!r} is not the active context")

    def _trace(self, context_data: ContextProperty, op: str, frame: FrameType):
        # Logged once: by this context if it traces, else by the innermost active
        # context that does, so tracing a script also traces the scripts it calls.
        # The stack is scanned on each access rather than caching the tracer at
        # __enter__, because a context's tracing flag may change while it is active.
        tracer = self if self.tracing else None
        if tracer is None:
            for active in reversed(_ACTIVE_CONTEXTS.get()):
                if active.tracing:
                    tracer = active
                    break
        if tracer is not None:
            filename = frame.f_code.co_filename
            lineno = frame.f_lineno
            tracer.tracing_logger.log(
                tracer.tracing_log_level,
                f"ContextData {context_data.name} from {context_data.declaring_cls} {op} by {filename}:{lineno}",
            )


# A ContextVar rather than a thread-local: each asyncio task gets its own copy, so
# concurrent tasks on one thread never see each other's active stack. The stack is an
# immutable tuple so that copy_context() snapshots stay independent.
_ACTIVE_CONTEXTS: ContextVar[tuple[Context, ...]] = ContextVar(
    "safetydance_active_contexts", default=()
)


def get_context() -> Context:
    """
    Return the innermost active Context of the current thread or task.

    Steps call this to reach the context of the script that called them.

    Raises ``NoActiveContext`` when called outside a ``@script`` (or ``@context``)
    function and outside a ``with Context():`` block.
    """
    stack = _ACTIVE_CONTEXTS.get()
    if not stack:
        raise NoActiveContext(
            "No active context: call this from a @script (or @context) function, "
            "or inside `with Context():`."
        )
    return stack[-1]


def _classify(func: Callable[..., Any], check: Callable[[Any], bool]) -> bool:
    # Callable objects are judged by their __call__ unless they are routines
    # themselves or carry their own marker (partial, AsyncMock, markcoroutinefunction).
    return check(func) or (
        not isroutine(func) and check(getattr(type(func), "__call__", None))
    )


def _in_new_context(
    func: Callable[..., Any], make_context: Callable[[dict], Context]
) -> Callable[..., Any]:
    """Wrap func so that each call runs in a fresh Context."""
    if _classify(func, isasyncgenfunction):
        raise TypeError(
            f"{getattr(func, '__qualname__', type(func).__qualname__)}: "
            "async generator functions are not supported"
        )

    if _classify(func, iscoroutinefunction):

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            with make_context(kwargs):
                return await func(*args, **kwargs)

        return async_wrapper

    if _classify(func, isgeneratorfunction):

        @wraps(func)
        def generator_wrapper(*args, **kwargs):
            # A generator body runs in whichever contextvars context resumes it, so
            # bind the Context to a private copy that is active only during each step.
            run = copy_context().run
            new_context = make_context(kwargs)
            run(new_context.__enter__)
            try:
                gen = func(*args, **kwargs)
                # Hand-rolled yield from: it forwards send/throw/close through `run`
                # and, unlike yield from, keeps a value returned while closing.
                try:
                    produced = run(next, gen)
                except StopIteration as stop:
                    return stop.value
                while True:
                    try:
                        sent = yield produced
                    except BaseException as exc:
                        step, arg = gen.throw, exc
                    else:
                        step, arg = gen.send, sent
                    try:
                        produced = run(step, arg)
                    except StopIteration as stop:
                        return stop.value
                    # A thrown exception's traceback holds this frame; don't keep it
                    # alive while suspended.
                    arg = None
            finally:
                run(new_context.__exit__, None, None, None)

        return generator_wrapper

    @wraps(func)
    def wrapper(*args, **kwargs):
        with make_context(kwargs):
            return func(*args, **kwargs)

    return wrapper


def _require_callable(names: str, func: Any) -> None:
    if func is not None and not callable(func):
        raise TypeError(
            f"{names}: got {func!r} as the function to decorate; the tracing "
            "options are keyword-only: pass tracing_log_level=... or tracing_logger=..."
        )


# The body behind context/script and test_context. Each keeps its own overloads,
# which type checkers need per public name, and its own docstring.
def _script_decorator(
    names: str,
    func: Callable[..., Any] | None,
    tracing_log_level: int | None,
    tracing_logger: Logger | None,
    with_fixtures: bool,
) -> Any:
    _require_callable(names, func)
    tracing = tracing_log_level is not None or tracing_logger is not None

    def make_context(kwargs: dict) -> Context:
        return Context(
            kwargs if with_fixtures else {},
            tracing=tracing,
            tracing_logger=tracing_logger,
            tracing_log_level=(
                logging.INFO if tracing_log_level is None else tracing_log_level
            ),
        )

    def decorator(func):
        return _in_new_context(func, make_context)

    return decorator(func) if func is not None else decorator


@overload
def context(
    func: Callable[P, R],
    *,
    tracing_log_level: int | None = None,
    tracing_logger: Logger | None = None,
) -> Callable[P, R]: ...


@overload
def context(
    func: None = None,
    *,
    tracing_log_level: int | None = None,
    tracing_logger: Logger | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]: ...


def context(
    func: Callable[..., Any] | None = None,
    *,
    tracing_log_level: int | None = None,
    tracing_logger: Logger | None = None,
) -> Any:
    """
    Decorator that runs each call of a function in a fresh, isolated Context.

    Usable bare or with options::

        @script
        def checkout():
            add_item("apple")      # a step: shares checkout's context

        @script(tracing_log_level=logging.DEBUG)
        def traced():
            ...

    Each call gets its own Context, which is not shared with any other script, even
    one it calls or is called from. Steps (plain functions) run in their caller's
    context and reach it with ``get_context()`` or context data. Generator
    functions, async functions, and callable objects whose ``__call__`` is one are
    supported; the context stays active while a generator is stepped. Async
    generator functions raise ``TypeError``.

    ``tracing_logger`` and ``tracing_log_level`` are keyword-only; giving either
    turns on tracing of context data access. A non-callable positional argument
    raises ``TypeError``. ``script`` is an alias of ``context``.
    """
    return _script_decorator(
        "script/context", func, tracing_log_level, tracing_logger, with_fixtures=False
    )


script = context


@overload
def test_context(
    func: Callable[P, R],
    *,
    tracing_log_level: int | None = None,
    tracing_logger: Logger | None = None,
) -> Callable[P, R]: ...


@overload
def test_context(
    func: None = None,
    *,
    tracing_log_level: int | None = None,
    tracing_logger: Logger | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]: ...


def test_context(
    func: Callable[..., Any] | None = None,
    *,
    tracing_log_level: int | None = None,
    tracing_logger: Logger | None = None,
) -> Any:
    """
    Like ``script``, for tests: the call's keyword arguments are put into the Context.

    The arguments are typically pytest fixtures. Each is stored by name, so a
    ``context_data`` field with the same name reads it, and ``get_context()["name"]``
    finds it::

        @test_context
        def test_cart(tmp_path):
            assert Storage.tmp_path == tmp_path

    The tracing options and the rules for generator, async and callable-object
    functions are the same as for ``script``. ``test_context`` itself is not
    collected by pytest.
    """
    return _script_decorator(
        "test_context", func, tracing_log_level, tracing_logger, with_fixtures=True
    )


# The name starts with "test", so stop pytest collecting it wherever it is imported.
test_context.__test__ = False  # type: ignore[attr-defined]


class ContextPropertyDescriptor:
    """Class attribute that reads and writes a field in the active context."""

    def __init__(self, name, context_data):
        self.name = name
        self.context_data = context_data

    # Pass the caller's frame so traces name the user's code, not this descriptor.
    def __get__(self, instance, owner=None):
        try:
            context = get_context()
        except NoActiveContext as e:
            raise ContextDataNotSet(
                f"ContextData {self.context_data} read outside a context"
            ) from e
        return self.context_data._get(context, sys._getframe(1))

    def __set__(self, instance, value):
        self.context_data._set(get_context(), value, sys._getframe(1))

    def __delete__(self, instance):
        self.context_data._unset(get_context(), sys._getframe(1))


class _Initializer:
    def __init__(self, fn: Callable[["Context"], Any]):
        self.fn = fn


def initializer(fn: Callable[["Context"], T]) -> T:
    """
    Mark a context_data default as computed rather than constant.

    ``fn`` is called with the Context on the first read in each context, and the
    result is stored there. It must be the default of an annotated attribute of a
    ``context_data`` class, otherwise ``TypeError`` is raised::

        @context_data
        class Cart:
            items: list[str] = initializer(lambda ctx: [])

    It is typed as returning ``T`` so that ``cart: list[str] = initializer(...)``
    type checks.
    """
    return _Initializer(fn)  # type: ignore[return-value]


def _initializer_for(field_name: str, default: Any) -> Callable[["Context"], Any]:
    """An initializer() default is called on first read; anything else is a constant."""
    if isinstance(default, _Initializer):
        return default.fn
    if type(default).__hash__ is None:
        # Like dataclasses: one mutable object would be shared by every context.
        raise ValueError(
            f"mutable default {type(default).__name__} for field {field_name!r} is "
            f"not allowed: use `initializer(lambda ctx: {default!r})`"
        )
    return lambda ctx: default


# The module of an annotation's prefix ("t" in "t.ClassVar[int]"), or the object a bare
# name ("CV" from `from typing import ClassVar as CV`) refers to, is looked up in the
# defining module's globals, as dataclasses does for string annotations.
_CLASSVAR_TEXT = re.compile(r"^\s*(?:(\w+)\s*\.\s*)?(\w+)\s*(?:\[|$)")


def _is_classvar(type_hint: Any, module_globals: dict[str, Any]) -> bool:
    """Whether an annotation is ClassVar, as an object, a string or a ForwardRef."""
    if type_hint is ClassVar or get_origin(type_hint) is ClassVar:
        return True
    text = type_hint if isinstance(type_hint, str) else getattr(type_hint, "__forward_arg__", None)
    if text is None:
        return False
    match = _CLASSVAR_TEXT.match(text)
    if match is None:
        return False
    prefix, name = match.groups()
    if prefix is None:
        return name == "ClassVar" or module_globals.get(name) is ClassVar
    if prefix == "typing" or module_globals.get(prefix) is typing:
        return name == "ClassVar"
    return False


class ContextData(type):
    """
    Metaclass that turns the annotated attributes of a class into context fields.

    Each field reads and writes a value in the active context, never on the class
    or an instance. ``Cls.field`` reads, ``Cls.field = v`` writes, and
    ``del Cls.field`` clears the stored value in the active context (a value supplied
    by name stays)::

        class Cart(metaclass=ContextData):
            owner: str = "guest"
            items: list[str] = initializer(lambda ctx: [])

    A default wrapped in ``initializer(fn)`` is called with the Context on first
    read; any other default is a constant. A field with no default raises
    ``ContextDataNotSet`` until it is set. A constant is shared by every context,
    so a default whose type isn't hashable (a ``list``, ``dict`` or ``set``) raises
    ``ValueError``; use ``initializer()`` for any mutable default, including one
    nested in a tuple, which is not detected. ``ClassVar`` annotations are ordinary
    class attributes; an ``initializer()`` default on one raises ``TypeError``.

    Classes built with ContextData are final: subclassing one raises ``TypeError``.
    Declare related fields on another class instead. A ContextData class may itself
    have ordinary base classes.
    """

    def __new__(mcls, name, bases, namespace, **kwds):
        for base in bases:
            if isinstance(base, ContextData):
                raise TypeError(
                    f"{name}: {base.__qualname__} is a context_data class, which can't "
                    "be subclassed; declare these fields on a separate context_data "
                    "class"
                )
        # Annotations are read from the finished class: on 3.14+ the namespace holds
        # a lazy __annotate__ function rather than an __annotations__ dict.
        ret = super().__new__(mcls, name, bases, namespace, **kwds)
        annotations = _own_annotations(ret)
        module = sys.modules.get(ret.__module__)
        module_globals = vars(module) if module is not None else {}
        for field_name, value in namespace.items():
            if isinstance(value, _Initializer) and field_name not in annotations:
                raise TypeError(
                    f"initializer() default for {field_name!r} needs a type annotation"
                )
        for field_name, type_hint in annotations.items():
            if _is_classvar(type_hint, module_globals):
                if isinstance(namespace.get(field_name), _Initializer):
                    raise TypeError(
                        f"initializer() default for {field_name!r} can't be used on a "
                        "ClassVar, which is an ordinary class attribute"
                    )
                continue
            initializer = None
            default = namespace.get(field_name, _NO_DEFAULT)
            if isinstance(default, ContextPropertyDescriptor):
                # A namespace copied from a ContextData class: keep that field's default.
                initializer = default.context_data.initializer
            elif default is not _NO_DEFAULT:
                initializer = _initializer_for(field_name, default)
            context_data = ContextProperty[type_hint](
                field_name, initializer=initializer, declaring_cls=ret
            )
            # Bypass ContextData.__setattr__, which would route to the descriptor.
            type.__setattr__(
                ret, field_name, ContextPropertyDescriptor(field_name, context_data)
            )
        return ret

    # Fields are set (or unset) in the context rather than replaced on the class. The
    # property is called directly so the trace frame is the user's statement.
    # Setting a field to its own descriptor is how mock.patch.object and
    # monkeypatch.setattr restore it: that clears the patched value in the active
    # context, if there is one, and leaves the class alone.
    def __setattr__(self, name, value):
        field = _field_descriptor(self, name)
        if field is None:
            return super().__setattr__(name, value)
        if value is field:
            stack = _ACTIVE_CONTEXTS.get()
            if stack:
                field.context_data._unset(stack[-1], sys._getframe(1))
            return None
        return field.context_data._set(get_context(), value, sys._getframe(1))

    def __delattr__(self, name):
        field = _field_descriptor(self, name)
        if field is not None:
            return field.context_data._unset(get_context(), sys._getframe(1))
        return super().__delattr__(name)


_NO_DEFAULT = object()


def _field_descriptor(cls: type, name: str) -> ContextPropertyDescriptor | None:
    """The field named name, if cls declares one."""
    attr = cls.__dict__.get(name)
    return attr if isinstance(attr, ContextPropertyDescriptor) else None


_COMBINED_METACLASSES: dict[type, type] = {}


def _with_context_data(metaclass: type) -> type:
    """A metaclass combining ContextData with the decorated class's own (e.g. ABCMeta)."""
    if metaclass not in _COMBINED_METACLASSES:
        _COMBINED_METACLASSES[metaclass] = type(
            f"ContextData{metaclass.__name__}",
            (ContextData, metaclass),
            {"__module__": __name__},
        )
    return _COMBINED_METACLASSES[metaclass]


def _retarget_class_cells(old_cls: type, new_cls: type):
    """
    Point zero-argument super() at the rebuilt class. Methods compiled in the original
    class body close over a __class__ cell holding the original class.
    """
    for attr in new_cls.__dict__.values():
        funcs: list[Any]
        if isinstance(attr, (classmethod, staticmethod)):
            funcs = [attr.__func__]
        elif isinstance(attr, property):
            funcs = [attr.fget, attr.fset, attr.fdel]
        elif isinstance(attr, (partialmethod, cached_property)):
            funcs = [attr.func]
        else:
            funcs = [attr]
        for func in funcs:
            if not callable(func):
                continue
            # Decorators built with functools.wraps hide the original behind __wrapped__.
            # unwrap stops on cycles and endless chains; an object whose attribute
            # lookup fails is not a method of the class body, so it is left alone.
            try:
                func = unwrap(func)
                code = getattr(func, "__code__", None)
            except Exception:
                continue
            if code is None or "__class__" not in code.co_freevars:
                continue
            cell = (func.__closure__ or ())[code.co_freevars.index("__class__")]
            if cell.cell_contents is old_cls:
                cell.cell_contents = new_cls


def context_data(cls: type[T]) -> type[T]:
    """
    Class decorator that sets the metaclass of the decorated class to ContextData.

    This allows classes to be defined without explicitly specifying the metaclass,
    making the API more user-friendly. See ``ContextData`` for how the annotated
    attributes behave.

    A default wrapped in ``initializer()`` is called with the Context on first read;
    any other default is a constant. A default whose type isn't hashable (a ``list``,
    ``dict`` or ``set``) raises ``ValueError``, as with dataclasses; a mutable object
    nested in a tuple is not detected. The decorated class is final: subclassing it
    raises ``TypeError``.

    The decorator rebuilds the class, so a base class's ``__init_subclass__`` runs
    again and class keywords are not carried over; use ``metaclass=ContextData`` for
    those. A class whose metaclass is not ``type`` (an ``abc.ABC`` subclass, say)
    is rebuilt with a metaclass derived from both. Classes with ``__slots__`` are
    supported.

    Usage::

        @context_data
        class MyData:
            field1: str
            field2: int = 42
            field3: list = initializer(lambda ctx: [])
    """
    if isinstance(cls, ContextData):
        # Already built by the metaclass: the class was decorated twice.
        return cls

    # Rebuild from the class's own namespace. Its slot descriptors are dropped (under
    # their mangled names too) because the rebuilt class creates its own from
    # __slots__.
    skipped = ("__dict__", "__weakref__", "__annotate_func__", "__annotations_cache__")
    namespace = {
        key: value
        for key, value in cls.__dict__.items()
        if key not in skipped
        and not (isinstance(value, MemberDescriptorType) and value.__objclass__ is cls)
    }

    # Carry the annotations over explicitly (3.14+ stores them lazily)
    namespace["__annotations__"] = _own_annotations(cls)
    namespace["__qualname__"] = cls.__qualname__

    metaclass: type = type(cls)
    if issubclass(metaclass, EnumMeta):
        raise TypeError(f"{cls.__qualname__}: an Enum can't be a context_data class")
    metaclass = ContextData if metaclass is type else _with_context_data(metaclass)
    new_cls: type[T] = metaclass(cls.__name__, cls.__bases__, namespace)
    _retarget_class_cells(cls, new_cls)
    return new_cls
