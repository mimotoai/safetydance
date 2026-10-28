======================
safetydance Change Log
======================

v0.5.0
====================

**Added:**

* ``Given``, ``When``, ``Then`` and ``And`` look steps up by name. ``@step``
  and ``step_namespace(...)`` register steps, found by full module path, short
  namespace or bare name; ``register_steps(...)`` binds steps explicitly and
  always wins, to settle collisions. ``register_steps()`` with no steps raises
  ``TypeError``.
* ``unregister_steps(*steps)`` removes steps from every path they are
  registered at, e.g. before redefining one in a notebook.
* Steps modules can be exposed through ``safetydance.steps`` entry points. The
  first lookup imports every installed one, so results never depend on lookup
  order. A loaded entry point is not loaded again; a failed one is retried on
  later lookups, with its partial registrations rolled back, and an
  ``AttributeError`` raised while importing it becomes an ``ImportError``
  naming it. Only registrations made by modules whose import failed are rolled
  back. One broken plugin's ``ImportError`` surfaces from lookups until it is
  fixed.
* A step's identity covers its definition location, wrapper chain and default
  and captured values. Module-level functions and classes in a default or
  captured value are compared by file and name, so a module imported twice is
  one step unless a value compares by identity (an ``object()`` sentinel, an
  instance, a ``functools.partial`` or an ``Enum`` member), when each copy is a
  different step and its bare name is ambiguous.
  Error messages suggest a full module path only when it can be typed.
* ``StepNotFound`` and ``AmbiguousStep`` are sibling ``AttributeError`` s
  (neither is a kind of the other) that report a missing name or one that could
  mean several steps; ``hasattr`` and ``getattr(..., default)`` treat both as
  absent.
* Single-underscore module segments (``pkg._impl.steps``) are reachable below
  the root. The root rejects ``_`` names and dunder segments are unreachable,
  so steps in a script run as ``__main__`` are found by bare name or short
  namespace.
* ``dir(Given)`` lists only registered names and loads nothing, so before the
  first lookup it shows only steps from modules already imported. Attributes
  cannot be set on ``Given``.

**Changed:**

* ``@step`` is back as an optional registration decorator; it never rewrites
  functions.
* The ``Given``, ``When``, ``Then`` and ``And`` prefixes now live in
  ``safetydance``, superseding the ``safetydance-test`` and
  ``type_extensions`` packages, which are no longer needed. See the migration
  guide.

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
