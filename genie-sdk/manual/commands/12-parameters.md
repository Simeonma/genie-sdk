# 12 · Parameters, expressions & units

> Every numeric slot in the configuration accepts either a number or an
> **expression string**, evaluated against the global parameter table set by
> `f.parameters()`. Evaluation is safe: a whitelisted-AST walker with zero
> `eval`/`exec` — attributes, comparisons, lambdas and string constants are
> rejected outright. Sources of truth: `genie.models.expr` (evaluator),
> `genie.models.resolve` (two-stage config resolution), `genie.core.script`
> (script emission).

## Parameter table & expression slots

`f.parameters(table)` sets/merges the global parameter table,
COMSOL-style: `{name: number | expression-string}`.

```python
f.parameters({'d': 1e-6})
f.parameters({'d': '500[nm]', 'pitch': '3*d'})   # merges, not replaces
f.mesh.dx = 'd / 4'                               # any numeric slot
```

| Rule | Description |
|---|---|
| Merge, not replace | Repeated calls **accumulate**: later tables update matching names, earlier names survive — the table is never wholesale-replaced |
| Declaration order irrelevant | A parameter's value may reference any other parameter (`'3*d'` before `d` is declared is fine); resolution is topological |
| Cycles rejected | `a → b → a` fails with `kind=cycle`; the error message carries the full chain |
| Name rules | Identifier (`[A-Za-z_][A-Za-z0-9_]*`), ≤64 characters — the same name space used by expression slots and sweep axes |
| Slots | **Any numeric slot** accepts an expression string: scalar fields, interval endpoints, tensor/tuple elements (incl. nested 3×3), dict values |
| Reference syntax | Expressions use **bare parameter names**: `'d/4'`. Braces — `'{d}/4'` — are a Python set literal and are **rejected** (the FAQ warning bears repeating) |

Non-numeric slots (string fields, bools, literals) are never touched;
with no expression strings anywhere, resolution is the identity map.

## Whitelist & unit suffixes

The expression grammar is deliberately tiny:

| Allowed | Forms |
|---|---|
| Constants | Numeric literals only (`int`/`float`) |
| Names | Parameter lookup in the environment — nothing else |
| Binary ops | `+ - * / % **` |
| Unary ops | `+ -` |
| Calls | `abs(x)`, `sqrt(x)` — exactly 1 arg; `min(a, b)`, `max(a, b)` — 1 or 2 args |
| Unit suffix | `primary[unit]` — binds tightest: `2*50[nm]` == `2*(50[nm])` |

Everything else — `Attribute`/`Subscript` access (the main injection
channel), `Compare`, `BoolOp`, `Lambda`, `IfExp`, string constants,
keyword arguments, any other function name — is rejected as
`illegal_node`. Evaluation is a pure AST walk; there is no `eval`.

**Unit suffixes**: `50[nm]` means `50 × 1e-9` — a pure numeric scale
factor, **no dimension checking**. A registered unit in the wrong
dimension sails through: `'50[Hz]'` in a length slot is just the number
50. Units are a conversion convenience, not a type system.

| Family | Unit → scale |
|---|---|
| Length | `pm` 1e-12 · `nm` 1e-9 · `um` 1e-6 · `µm` 1e-6 · `mm` 1e-3 · `cm` 1e-2 · `m` 1 |
| Time | `fs` 1e-15 · `ps` 1e-12 · `ns` 1e-9 · `us` 1e-6 · `µs` 1e-6 · `ms` 1e-3 · `s` 1 |
| Frequency | `Hz` 1 · `kHz` 1e3 · `MHz` 1e6 · `GHz` 1e9 · `THz` 1e12 · `PHz` 1e15 |

Unit names are NFKC-normalized before lookup (mirroring Python
identifier rules), so the Unicode micro sign µ (U+00B5) and the Greek
mu μ (U+03BC) both hit the same `µm`/`µs` alias (scale 1e-6, same as
`um`/`us`).

## Error kinds

All expression/parameter failures raise one error type with a `kind`
discriminator, surfaced as HTTP 422 at the submission boundary:

| kind | Trigger | Example |
|---|---|---|
| `syntax` | Empty/whitespace expression, unparseable text, NUL bytes; also an invalid parameter *name* (not an identifier or >64 chars) | `'d /*'`; parameter named `'2d'` |
| `unknown_name` | A name in the expression is not in the environment; the message lists available names | `'d/4'` with no `d` declared |
| `unknown_unit` | Bracket suffix is not a registered unit; the message lists the registry | `'50[kg]'` — `kg` is not in the 20-unit table |
| `illegal_node` | Any non-whitelisted AST node, operator, or function form | `'d.x'`, `'d < 1'`, `'sin(d)'`, `'{d}'` |
| `math` | Division/modulo by zero, overflow, non-finite result, `sqrt` of a negative number | `'1/0'`, `'sqrt(0-d)'` with `d>0` |
| `nonatomic` | Non-numeric constant (string/bool), keyword arguments, wrong arity in a call | `'"d"'`, `'min(x=1)'`, `'min(1,2,3)'` |
| `cycle` | Parameter table contains a reference cycle; message carries the full chain | `a='b'`, `b='a'` → `参数环: a→b→a` |
| `guard` | The expression itself evaluated fine, but its **result** was caught by a SimConfig value guard | `'d/4'` resolving to a non-positive dx |

Slot-level errors carry the in-config path (e.g.
`source[0].freq 处表达式 'd/4' 求值失败`), so a 422 pinpoints the field.

**One honest caveat**: expression slots are **not** evaluated locally — the
SDK captures configs verbatim, strings included. Expression errors only
surface at the submission boundary (local precheck / server 422).

**Common pitfalls**

- Writing `'{d}/4'` out of template-string habit — braces are a Python
  set literal, rejected as `illegal_node`; use bare names.
- Expecting units to catch a physics mix-up — a registered unit in the
  wrong dimension (`30[Hz]` in a length slot) passes through as the
  number 30; only *unregistered* names (`50[kg]`) are rejected.
- Debugging expressions without submitting — slots are not evaluated
  locally; the local precheck (on `run()`) or the server 422 is where
  they surface.

At script-emission time (`f.to_script()`), unit suffixes become
multiplication (`50[nm]` → `50*1e-9`) and each parameter line carries a
`# 原文 <json>` comment with the original value — the golden tests are
the rendering canon.
