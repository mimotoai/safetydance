API reference
=============

Every public name in ``safetydance``. The `guide </safetydance/>`_ explains how they
fit together.

.. currentmodule:: safetydance

Scripts and contexts
--------------------

.. autofunction:: script
.. autofunction:: context
.. autofunction:: test_context
.. autofunction:: get_context
.. autoclass:: Context

Context data
------------

.. autofunction:: context_data
.. autofunction:: initializer
.. autoclass:: ContextData
.. autoclass:: ContextProperty
   :members: value, has_value
   :special-members: __call__, __setitem__

Errors
------

.. autoexception:: NoActiveContext
.. autoexception:: ContextDataNotSet
