======================
safetydance Change Log
======================

.. current developments

v0.4.0
====================

A new API, incompatible with 0.3: steps are plain functions that share a
context, and ``@script`` runs them in a fresh one. Functions are no longer
rewritten.

**Added:**

* ``ContextProperty`` declares a context variable, and ``@context_data``
  classes declare several; ``initializer(...)`` marks a default computed from
  the context on first read. ``prop(ctx)`` and ``prop[ctx] = value`` read and
  write a given context.
* ``@script`` (also ``@context``) runs a function in a fresh ``Context``;
  generator and ``async`` functions can be scripts, and concurrent asyncio
  tasks each see their own context. ``@test_context`` puts pytest fixtures in
  the context by name.
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



v0.2.1
====================



v0.1.5
====================



v0.1.4
====================



v0.1.3
====================



v0.1.2
====================



v0.1.1
====================



v0.1.0
====================


