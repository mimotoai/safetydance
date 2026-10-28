import safetydance
from safetydance import _steps


def test_star_import_exports_only_the_public_api():
    namespace = {}
    exec("from safetydance import *", namespace)
    namespace.pop("__builtins__")
    assert set(namespace) == {
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
    }


def test_importing_test_context_does_not_add_a_pytest_test(pytester):
    pytester.makepyfile(
        """
        from safetydance import test_context

        @test_context
        def test_uses_fixture(tmp_path):
            assert tmp_path.exists()
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)


def test_star_import_brings_prefixes(pytester, monkeypatch):
    # pytester runs in this process, so give its steps a registry of their own.
    monkeypatch.setattr(_steps, "_registry", _steps._Registry())
    pytester.makepyfile(
        """
        from safetydance import *

        @step
        def star_import_adds(a, b):
            return a + b

        @test_context
        def test_prefixes_work_after_a_star_import():
            assert Given.star_import_adds(1, 2) == 3
            assert When.star_import_adds(1, 2) == 3
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)
