======================
safetydance Change Log
======================

v0.5.0
====================

**Added:**

* ``Given``, ``When``, ``Then`` and ``And`` look steps up by name. They are one
  ``StepPrefix`` instance; the four names only change how a test reads.
* ``step`` registers a function under its own name, ``step_namespace("a.b")``
  makes a decorator that registers under a namespace, and
  ``register_steps(*fns, namespace=None)`` registers from the caller's side. Each
  registration binds one path.
* ``unregister_steps(*steps)`` removes a step from every path it is registered
  at, and namespaces left empty disappear.
* ``StepNotFound``, an ``AttributeError``, is raised for a name nothing is
  registered at. Names starting with ``_`` raise a plain ``AttributeError``.
* Conflicts raise ``ValueError`` at registration, naming both steps. A module
  imported twice under different names gets a hint saying so. Nothing is ever
  replaced silently.

**Changed:**

* ``@step`` is back as an optional registrar; it never rewrites functions.
* The prefixes now live in ``safetydance``, superseding the ``safetydance-test``
  and ``type_extensions`` packages, which are no longer needed. See the
  migration guide.

v0.4.0
====================

Releases before 0.4.0 (0.3.x and earlier) are not listed here.

A new API, incompatible with 0.3: steps are plain functions that share a
context, and ``@script`` runs them in a fresh one. Functions are no longer
rewritten.

**Added:**

* ``ContextProperty`` declares a context variable, and ``@context_data``
  classes declare several; ``initializer(...)`` marks a default computed from
  the context on first read. ``prop(ctx)`` and ``prop[ctx] = value`` read and
  write a given context. ``@context_data`` classes are final: subclassing one
  raises ``TypeError``.
* ``@script`` (also ``@context``) runs a function in a fresh ``Context``;
  generator and ``async`` functions can be scripts, and concurrent asyncio
  tasks each see their own context. ``@test_context`` puts pytest fixtures in
  the context by name.
* ``ContextProperty.has_value`` tells whether a read would produce a value.
  ``NoActiveContext`` is raised by ``get_context()`` and
  ``ContextProperty.value`` outside any context, and ``ContextDataNotSet``
  (a ``KeyError`` and an ``AttributeError``) for unset values, so ``hasattr``
  and ``getattr`` with a default work.
* A documentation site with a user guide and API reference, including a
  migration guide from 0.3: https://dcharbon.gitlab.io/safetydance/migrating/
* Python 3.14 support. Type hints are shipped (``py.typed``) and decorated
  scripts keep their signatures for type checkers.

**Changed:**

* Every script call owns its context: a script called from another script does
  not see or change the caller's context. Call a step to share it.
* Requires Python 3.10 or later.

**Removed:**

* ``step``, ``step_data``, ``ContextKey``, ``Step``, ``Script``,
  ``NestingContext`` and the AST rewriting behind them, and the
  ``safetydance.extensions``, ``safetydance.file_util`` and
  ``safetydance.jupyter`` modules.

**Fixed:**

* The package imports without setuptools installed (``pkg_resources`` is no
  longer used).
