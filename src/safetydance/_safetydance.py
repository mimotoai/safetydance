import logging
import threading
from functools import wraps
from inspect import currentframe
from logging import Logger
from types import FrameType
from typing import Any, Callable, Dict, Generic, TypeVar


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
        context = get_context()
        value = context.get(self, None)
        found = False
        if value is None and self.name:
            value = context.get(self.name, None)
            found = self.name in context
        else:
            value = context.get(self, None)
            found = self in context
        if not found and self.initializer is not None:
            value = self.initializer(context)
            context[self.name or self] = value
            context._trace(self, "initialized", currentframe().f_back)
            found = True
        context._trace(self, "retrieved", currentframe().f_back)
        if not found:
            raise KeyError(f"ContextData {self} not found in Context")
        return value

    @value.setter
    def value(self, new_value: T):
        context = get_context()
        context._trace(self, "set", currentframe().f_back)
        context[self.name or self] = new_value

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


class Context(Dict[ContextProperty | str, Any]):
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
        super().__init__(*args, **kwargs)

    def __getitem__(self, key):
        if self.parent:
            return self.parent.__getitem__(key)
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        if self.parent:
            return self.parent.__setitem__(key)
        return super().__setitem__(key, value)

    def __enter__(self) -> "Context":
        global THREAD_LOCALS
        if hasattr(THREAD_LOCALS, "context"):
            self.parent = THREAD_LOCALS.context
        THREAD_LOCALS.context = self
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        global THREAD_LOCALS
        THREAD_LOCALS.context = self.parent

    def _trace(self, context_data: ContextProperty, op: str, frame: FrameType):
        if self.tracing:
            filename = frame.f_code.co_filename
            lineno = frame.f_lineno
            self.tracing_logger.log(
                self.tracing_log_level,
                f"ContextData {context_data.name} from {context_data.declaring_cls} {op} by {filename}:{lineno}",
            )
        if self.parent:
            self.parent._trace(context_data, op, frame)


THREAD_LOCALS = threading.local()


def get_context() -> Context:
    """
    Automatically retrieve and optionally create the context for use with step
    functions.
    """
    global THREAD_LOCALS
    if not hasattr(THREAD_LOCALS, "context") or THREAD_LOCALS.context is None:
        raise Exception("No context set! Did you forget to use the @context decorator?")
    return THREAD_LOCALS.context


def context(
    func: Callable | None = None,
    tracing_log_level: int | None = None,
    tracing_logger: Logger | None = None,
):
    tracing = tracing_log_level is not None or tracing_logger is not None

    if func:

        @wraps(func)
        def direct_wrapper(*args, **kwargs):
            with Context(
                tracing=tracing,
                tracing_logger=tracing_logger,
                tracing_log_level=tracing_log_level,
            ):
                return func(*args, **kwargs)

        return direct_wrapper

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with Context(
                tracing=tracing,
                tracing_logger=tracing_logger,
                tracing_log_level=tracing_log_level,
            ):
                return func(*args, **kwargs)

        return wrapper

    return decorator


script = context


def test_context(
    func: Callable | None = None,
    tracing_log_level: int | None = None,
    tracing_logger: Logger | None = None,
):
    """Grab the test fixtures and put them into the Context."""
    tracing = tracing_log_level is not None or tracing_logger is not None

    if func:

        @wraps(func)
        def direct_wrapper(*args, **kwargs):
            with Context(
                tracing=tracing,
                tracing_logger=tracing_logger,
                tracing_log_level=tracing_log_level,
            ) as context:
                context.update(kwargs)
                return func(*args, **kwargs)

        return direct_wrapper

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with Context(
                tracing=tracing,
                tracing_logger=tracing_logger,
                tracing_log_level=tracing_log_level,
            ) as context:
                context.update(kwargs)
                return func(*args, **kwargs)

        return wrapper

    return decorator


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
        type_hints = namespace["__annotations__"]
        new_namespace = dict(namespace)
        for name, type_hint in type_hints.items():
            initializer = namespace.get(name, None)
            context_data = ContextProperty[type_hint](name, initializer=initializer)
            new_namespace[name] = ContextPropertyDescriptor(name, context_data)
        ret = super().__new__(cls, name, bases, new_namespace)
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
        if key != "__dict__" and key != "__weakref__":
            namespace[key] = value
    
    # Create a new class with the same name, bases, and namespace, but with ContextData as metaclass
    return ContextData(cls.__name__, cls.__bases__, namespace)
