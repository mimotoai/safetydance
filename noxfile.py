import nox

nox.options.default_venv_backend = "uv"

PYPROJECT = nox.project.load_toml("pyproject.toml")


@nox.session(python=["3.10", "3.11", "3.12", "3.13", "3.14"])
def test(session):
    session.install(".", *nox.project.dependency_groups(PYPROJECT, "test"))
    session.run("pytest")


@nox.session(python="3.14")
def typecheck(session):
    session.install(".", *nox.project.dependency_groups(PYPROJECT, "typing"))
    session.run("mypy", "src", "tests/typecheck")
