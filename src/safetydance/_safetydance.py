import logging
import sys
from collections.abc import MutableMapping
from contextvars import ContextVar, copy_context
from functools import wraps
from inspect import (
    currentframe,
    isasyncgenfunction,
    iscoroutinefunction,
    isgeneratorfunction,
)
from logging import Logger
from types import FrameType
from typing import Any, Callable, Generic, TypeVar

if sys.version_info >= (3, 14):
    from annotationlib import Format, get_annotations

    def _own_annotations(cls: type) -> dict:
        # Annotations are lazy on 3.14+; FORWARDREF tolerates names defined later.
        return get_annotations(cls, format=Format.FORWARDREF)

else:
    from inspect import get_annotations as _own_annotations


T = TypeVar("T")


class ContextProperty(Generic[T]):
    name: str
    description: str
    initializer: Callable[["Context"], T]
    declaring_cls: type

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
        return self._get(get_context(), currentframe().f_back)

    @value.setter
    def value(self, new_value: T):
        self._set(get_context(), new_value, currentframe().f_back)

    def __call__(self, context: "Context") -> T:
        return self._get(context, currentframe().f_back)

    def __setitem__(self, context: "Context", new_value: T):
        self._set(context, new_value, currentframe().f_back)

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
            raise KeyError(f"ContextData {self} not found in Context")
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
        self.parent = None
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
    Automatically retrieve and optionally create the context for use with step
    functions.
    """
    current = _CURRENT_CONTEXT.get()
    if current is None:
        raise Exception("No context set! Did you forget to use the @context decorator?")
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


def _in_new_context(func: Callable, make_context: Callable[[dict], Context]):
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


def context(
    func: Callable | None = None,
    tracing_log_level: int | None = None,
    tracing_logger: Logger | None = None,
):
    make_context = _context_factory(tracing_log_level, tracing_logger, False)

    def decorator(func):
        return _in_new_context(func, make_context)

    return decorator(func) if func else decorator


script = context


def test_context(
    func: Callable | None = None,
    tracing_log_level: int | None = None,
    tracing_logger: Logger | None = None,
):
    """Grab the test fixtures and put them into the Context."""
    make_context = _context_factory(tracing_log_level, tracing_logger, True)

    def decorator(func):
        return _in_new_context(func, make_context)

    return decorator(func) if func else decorator


class ContextPropertyDescriptor:
    def __init__(self, name, context_data):
        self.name = name
        self.context_data = context_data

    def __get__(self, instance, owner=None):
        return self.context_data.value

    def __set__(self, instance, value):
        self.context_data.value = value


class ContextData(type):
    def __new__(cls, name, bases, namespace):
        # Annotations are read from the finished class: on 3.14+ the namespace holds
        # a lazy __annotate__ function rather than an __annotations__ dict.
        ret = super().__new__(cls, name, bases, namespace)
        for field_name, type_hint in _own_annotations(ret).items():
            initializer = namespace.get(field_name, None)
            context_data = ContextProperty[type_hint](
                field_name, initializer=initializer, declaring_cls=ret
            )
            # Bypass ContextData.__setattr__, which would route to the descriptor.
            type.__setattr__(
                ret, field_name, ContextPropertyDescriptor(field_name, context_data)
            )
        return ret

    def __setattr__(self, name, value):
        if name in self.__dict__:
            return self.__dict__[name].__set__(self, value)
        else:
            # Fall back to normal attribute setting for attributes not managed by ContextData
            return super().__setattr__(name, value)


def context_data(cls):
    """
    Class decorator that sets the metaclass of the decorated class to ContextData.

    This allows classes to be defined without explicitly specifying the metaclass,
    making the API more user-friendly.

    Usage:
        @context_data
        class MyData:
            field1: str
            field2: int = 42
    """
    # Create a new namespace dictionary from the class's __dict__
    namespace = {}
    for key, value in cls.__dict__.items():
        if key not in ("__dict__", "__weakref__", "__annotate_func__", "__annotations_cache__"):
            namespace[key] = value

    # Carry the annotations over explicitly (3.14+ stores them lazily)
    namespace["__annotations__"] = _own_annotations(cls)

    # Create a new class with the same name, bases, and namespace, but with ContextData as metaclass
    return ContextData(cls.__name__, cls.__bases__, namespace)
