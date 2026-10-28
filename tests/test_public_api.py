import safetydance

pytest_plugins = ["pytester"]


def test_star_import_exports_only_the_public_api():
    namespace = {}
    exec("from safetydance import *", namespace)
    namespace.pop("__builtins__")
    assert set(namespace) == {
        "Context",
        "ContextData",
        "ContextProperty",
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
