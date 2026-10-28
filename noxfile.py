import nox

nox.options.default_venv_backend = "uv"


def sync(session, group):
    session.run_install(
        "uv", "sync", "--locked", "--no-default-groups", "--group", group,
        env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location},
    )


@nox.session(python=["3.10", "3.11", "3.12", "3.13", "3.14"])
def test(session):
    sync(session, "test")
    session.run("pytest")


@nox.session(python="3.14")
def typecheck(session):
    sync(session, "typing")
    session.run("mypy", "src")
    session.run("mypy", "--strict", "tests/typecheck")


# Needs Node on PATH for the guide (`mise install` provides it, see mise.toml).
@nox.session(python="3.14")
def docs(session):
    sync(session, "docs")
    with session.chdir("docs"):
        session.run(
            "jupyter-book", "build", "--html", "--execute", "--strict", "--ci", "--keep-host",
            env={"HOST": "127.0.0.1", "BASE_URL": "/safetydance"},
        )
    session.run("sphinx-build", "-W", "--keep-going", "-b", "html", "docs/api", "docs/_build/html/api")
