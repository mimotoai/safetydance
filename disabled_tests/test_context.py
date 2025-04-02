import pytest
from dataclasses import dataclass
from safetydance import script, ContextProperty


@dataclass
class Foo:
    bar: int


arg1 = ContextProperty[int](description="Hi, I'm arg1")
arg2 = ContextProperty[dict](description="Hi, I'm arg3")
One = ContextProperty[int]()
Two = ContextProperty[int](description="Here's a description for ya!")
SomeFoo = ContextProperty[Foo]()


def step_one():  # def step_one(context: Context):
    One.value = 1
    Two.value = 2
    arg1.value = 7
    arg2.value = {"one": 1, "three": 3.333, "thirty": 30}
    SomeFoo.value = Foo(42)

def step_with_args(arg1, arg2, arg3=None):
    """
    General state coming into this function should be that arg1 is unassigned,
    arg2 is passed an int in the call, and arg3 is passed a str
    steps_globally arg1 is an int, and arg2 is a dict
    """
    arg1 = SomeFoo.value
    assert isinstance(arg1.value, Foo)
    assert isinstance(arg2.value, float)
    assert isinstance(arg3.value, str)


@script
def my_script():
    step_one()
    assert One.value == 1
    assert Two.value == 2
    step_with_args(
        "keyword arg provided", arg2.value["three"], arg3="this is the keyword arg"
    )
    # arg1 and arg2 were declared as step_data with type int and dict, assigned values in step_one
    # step_with_args changes the class type locally as arg1:Foo, arg2:float
    # these should not affect step_gloabl, and below should pass as arg1:int, arg2:dict
    assert isinstance(arg1.value, int)
    assert isinstance(arg2.value, dict)


def test_context_access():
    my_script()
