# Migrating from 0.3

0.4 is a new API and is not compatible with 0.3. If you cannot migrate yet, pin
`safetydance<0.4`.

| 0.3 | 0.4 |
| --- | --- |
| `@step` function | a plain function |
| `step_data`, `ContextKey` | `ContextProperty` and `@context_data` |
| `script`, `scripted_test` | `@script` and `@test_context` |
| implicit context variables, AST rewriting | gone: read and write context data explicitly |
| nested scripts shared the outer context | every script call is isolated; call a step to share |
| `safetydance.extensions`, `file_util`, `jupyter` | removed |

## What to change

1. Remove `@step`. A step is now any function that uses context data.
2. Replace `step_data` classes and `ContextKey`s with `@context_data` classes
   (or `ContextProperty` objects). Use `initializer(...)` for per-context
   defaults.
3. Replace `scripted_test` with `@test_context`; `script` stays `@script`.
4. If a script relied on seeing its caller's context, turn it into a step
   and wrap it with `script(step)` where it is also run on its own.
5. Use `has_value` to check for a value, and `ContextDataNotSet` or
   `NoActiveContext` where you handled errors.

See the [concepts](concepts.md) for the details and the full list of changes
in the [changelog](https://gitlab.com/dcharbon/safetydance/-/blob/main/CHANGELOG.rst).
