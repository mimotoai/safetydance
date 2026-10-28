import logging
import sys
from collections.abc import MutableMapping
from contextvars import ContextVar, copy_context
from functools import wraps
from inspect import isasyncgenfunction, iscoroutinefunction, isgeneratorfunction
from logging import Logger
from types import FrameType
from typing import Any, Callable, Generic, ParamSpec, TypeVar, overload

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
    """Raised when a Context is needed but none is active."""


class ContextDataNotSet(KeyError, AttributeError):
    """
    Raised when a context value is read before it was set. It is also an
    AttributeError so that hasattr() and getattr(default) work on context_data fields.
    """


T = TypeVar("T")
P = ParamSpec("P")
R = TypeVar("R")


class ContextProperty(Generic[T]):
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
        return self._get(get_context(), sys._getframe(1))

    @value.setter
    def value(self, new_value: T):
        self._set(get_context(), new_value, sys._getframe(1))

    def __call__(self, context: "Context") -> T:
        return self._get(context, sys._getframe(1))

    def __setitem__(self, context: "Context", new_value: T):
        self._set(context, new_value, sys._getframe(1))

    # Values are stored under the property itself, so same-named properties never
    # collide. The name is only a read fallback, which lets values supplied by name
    # (e.g. test fixtures via test_context) bind to the property.
    def _get(self, context: "Context", frame: FrameType) -> T:
        found = True
        if self in context:
            value = context[self]
        elif self.name and self.name in context:
            value = context[self.name]
        elif self.initializer is not None:
            value = self.initializer(context)
            context[self] = value
            context._trace(self, "initialized", frame)
        else:
            found = False
        context._trace(self, "retrieved", frame)
        if not found:
            raise ContextDataNotSet(f"ContextData {self} not found in Context")
        return value

    def _set(self, context: "Context", new_value: T, frame: FrameType):
        context._trace(self, "set", frame)
        context[self] = new_value

    def __repr__(self) -> str:
        if self.name and self.declaring_cls:
            return f"{self.name} of {self.declaring_cls}"
        else:
            return super().__repr__()

    @property
    def is_set(self):
        context = get_context()
        return (
            (self.name and self.name in context)
            or self in context
            or self.initializer is not None
        )


class Context(MutableMapping[ContextProperty | str, Any]):
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
        self.parent: Context | None = None
        self._data = dict(*args, **kwargs)

    # Every Context owns its storage: a nested Context (and so a nested script) never
    # reads or writes its parent's values. To share a context, call a step (a plain
    # function) rather than a script. `parent` is the Context that was active on
    # entry; it is restored on exit and receives trace messages while nested.
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

    def __enter__(self) -> "Context":
        parent = _CURRENT_CONTEXT.get()
        ancestor = parent
        while ancestor is not None:
            if ancestor is self:
                raise RuntimeError(f"{self!r} is already active")
            ancestor = ancestor.parent
        self.parent = parent
        _CURRENT_CONTEXT.set(self)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        _CURRENT_CONTEXT.set(self.parent)
        self.parent = None

    def _trace(self, context_data: ContextProperty, op: str, frame: FrameType):
        if self.tracing:
            filename = frame.f_code.co_filename
            lineno = frame.f_lineno
            self.tracing_logger.log(
                self.tracing_log_level,
                f"ContextData {context_data.name} from {context_data.declaring_cls} {op} by {filename}:{lineno}",
            )
        if self.parent is not None:
            self.parent._trace(context_data, op, frame)


# A ContextVar rather than a thread-local: each asyncio task gets its own copy, so
# concurrent tasks on one thread never see each other's active Context.
_CURRENT_CONTEXT: ContextVar[Context | None] = ContextVar(
    "safetydance_context", default=None
)


def get_context() -> Context:
    """
    Return the active Context, for use by step functions.

    Raises NoActiveContext when called outside a @script (or @context) function or a
    `with Context():` block.
    """
    current = _CURRENT_CONTEXT.get()
    if current is None:
        raise NoActiveContext(
            "No active context: call this from a @script (or @context) function, "
            "or inside `with Context():`."
        )
    return current


class _GeneratorInContext:
    """Drives a generator with every step run inside the given contextvars Context."""

    def __init__(self, run: Callable, gen):
        self._run = run
        self._gen = gen

    def __iter__(self):
        return self

    def __next__(self):
        return self._run(next, self._gen)

    def send(self, value):
        return self._run(self._gen.send, value)

    def throw(self, *args):
        return self._run(self._gen.throw, *args)

    def close(self):
        return self._run(self._gen.close)


def _in_new_context(
    func: Callable[..., Any], make_context: Callable[[dict], Context]
) -> Callable[..., Any]:
    """Wrap func so that each call runs in a fresh Context."""
    if isasyncgenfunction(func):
        raise TypeError(
            f"{func.__qualname__}: async generator functions are not supported"
        )

    if iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            with make_context(kwargs):
                return await func(*args, **kwargs)

        return async_wrapper

    if isgeneratorfunction(func):

        @wraps(func)
        def generator_wrapper(*args, **kwargs):
            # A generator body runs in whichever contextvars context resumes it, so
            # bind the Context to a private copy that is active only during each step.
            run = copy_context().run
            new_context = make_context(kwargs)
            run(new_context.__enter__)
            try:
                gen = func(*args, **kwargs)
                return (yield from _GeneratorInContext(run, gen))
            finally:
                run(new_context.__exit__, None, None, None)

        return generator_wrapper

    @wraps(func)
    def wrapper(*args, **kwargs):
        with make_context(kwargs):
            return func(*args, **kwargs)

    return wrapper


def _context_factory(
    tracing_log_level: int | None,
    tracing_logger: Logger | None,
    with_fixtures: bool,
) -> Callable[[dict], Context]:
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

    return make_context


@overload
def context(
    func: Callable[P, R],
    tracing_log_level: int | None = None,
    tracing_logger: Logger | None = None,
) -> Callable[P, R]: ...


@overload
def context(
    func: None = None,
    tracing_log_level: int | None = None,
    tracing_logger: Logger | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]: ...


def context(
    func: Callable[..., Any] | None = None,
    tracing_log_level: int | None = None,
    tracing_logger: Logger | None = None,
) -> Any:
    make_context = _context_factory(tracing_log_level, tracing_logger, False)

    def decorator(func):
        return _in_new_context(func, make_context)

    return decorator(func) if func else decorator


script = context


@overload
def test_context(
    func: Callable[P, R],
    tracing_log_level: int | None = None,
    tracing_logger: Logger | None = None,
) -> Callable[P, R]: ...


@overload
def test_context(
    func: None = None,
    tracing_log_level: int | None = None,
    tracing_logger: Logger | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]: ...


def test_context(
    func: Callable[..., Any] | None = None,
    tracing_log_level: int | None = None,
    tracing_logger: Logger | None = None,
) -> Any:
    """Grab the test fixtures and put them into the Context."""
    make_context = _context_factory(tracing_log_level, tracing_logger, True)

    def decorator(func):
        return _in_new_context(func, make_context)

    return decorator(func) if func else decorator


# The name starts with "test", so stop pytest collecting it wherever it is imported.
test_context.__test__ = False  # type: ignore[attr-defined]


class ContextPropertyDescriptor:
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


class _Initializer:
    def __init__(self, fn: Callable[["Context"], Any]):
        self.fn = fn


def initializer(fn: Callable[["Context"], T]) -> T:
    """
    Mark a context_data default as an initializer, called with the Context on first
    read. Typed as returning T so that `cart: list[str] = initializer(...)` checks.
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


class ContextData(type):
    def __new__(cls, name, bases, namespace):
        # Annotations are read from the finished class: on 3.14+ the namespace holds
        # a lazy __annotate__ function rather than an __annotations__ dict.
        ret = super().__new__(cls, name, bases, namespace)
        for field_name, type_hint in _own_annotations(ret).items():
            initializer = None
            if field_name in namespace:
                initializer = _initializer_for(field_name, namespace[field_name])
            context_data = ContextProperty[type_hint](
                field_name, initializer=initializer, declaring_cls=ret
            )
            # Bypass ContextData.__setattr__, which would route to the descriptor.
            type.__setattr__(
                ret, field_name, ContextPropertyDescriptor(field_name, context_data)
            )
        return ret

    def __setattr__(self, name, value):
        # Resolve the name the way attribute lookup does, so fields inherited from a
        # base class are set in the context rather than shadowed on the subclass.
        for klass in self.__mro__:
            if name in klass.__dict__:
                attr = klass.__dict__[name]
                if isinstance(attr, ContextPropertyDescriptor):
                    # Called directly so the trace frame is the user's assignment.
                    return attr.context_data._set(
                        get_context(), value, sys._getframe(1)
                    )
                break
        # Fall back to normal attribute setting for attributes not managed by ContextData
        return super().__setattr__(name, value)


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
        else:
            funcs = [attr]
        for func in funcs:
            code = getattr(func, "__code__", None)
            if code is None or "__class__" not in code.co_freevars:
                continue
            cell = (func.__closure__ or ())[code.co_freevars.index("__class__")]
            if cell.cell_contents is old_cls:
                cell.cell_contents = new_cls


def context_data(cls):
    """
    Class decorator that sets the metaclass of the decorated class to ContextData.

    This allows classes to be defined without explicitly specifying the metaclass,
    making the API more user-friendly.

    A default wrapped in initializer() is called with the Context on first read; any
    other default is a constant. Mutable defaults are rejected, as with dataclasses.

    Usage:
        @context_data
        class MyData:
            field1: str
            field2: int = 42
            field3: list = initializer(lambda ctx: [])
    """
    # Create a new namespace dictionary from the class's __dict__
    namespace = {}
    for key, value in cls.__dict__.items():
        if key not in ("__dict__", "__weakref__", "__annotate_func__", "__annotations_cache__"):
            namespace[key] = value

    # Carry the annotations over explicitly (3.14+ stores them lazily)
    namespace["__annotations__"] = _own_annotations(cls)
    namespace["__qualname__"] = cls.__qualname__

    # Create a new class with the same name, bases, and namespace, but with ContextData as metaclass
    new_cls = ContextData(cls.__name__, cls.__bases__, namespace)
    _retarget_class_cells(cls, new_cls)
    return new_cls
