"""genie.core.orchestration.expand — the SweepSpec expander (the sweep
driver's pure piece; never sent over the wire).

Expands a base SimConfig JSON + SweepSpec into per-point **resolved**
configs:

- Pure expansion, zero scheduling: parameter substitution, product/zip
  combination, per-point deep copy + per-point re-resolution;
- name-axis semantics: ``name`` must be inside the base
  ``parameters`` key set (if not → SweepExpandError listing the available
  names); expansion = per-point ``parameters[name]=v`` → re-resolve via
  ``resolve_parameters`` — every expression slot referencing that
  parameter is re-evaluated in lockstep, point by point;
- Per-point errors carry **which point (1-based) + the axis name** and
  hint at the number of valid prefix points;
- Returns concrete numeric configs that can be POSTed to /api/jobs
  directly.

The public entry is the dict-of-sequences verb ``f.add_param_sweep`` /
``f.run_param_sweep`` (worker); ``SweepSpec`` is internal — the verbs
construct it from plain sequences. Migrated verbatim from
``core/sweep.py`` (Q10): behavior preserved word for word.
"""
from __future__ import annotations

import copy
import itertools
import math
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from genie_protocol.expr import ExprError, valid_param_name
from genie_protocol.resolve import resolve_parameters_detailed, validate_resolved

__all__ = ["SweepSpec", "SweepAxisSpec", "SweepExpandError",
           "expand_sweep", "validate_sweep_config"]

MAX_SWEEP_POINTS = 512           # cap on expanded sweep points (SDK-local constant)
MAX_SWEEP_AXES = 32              # cap on sweep parameter axes


class _StrictModel(BaseModel):
    """Wire-format discipline: unknown fields are always rejected."""

    model_config = ConfigDict(extra="forbid")


class SweepAxisSpec(_StrictModel):
    """A single-parameter sweep axis (**addressed by parameter name**).

    - ``name``: a **parameter name** — it must be a name inside the base
      config's ``parameters`` key set (if not → 422 listing the available
      names); expansion semantics = re-resolve after per-point
      ``parameters[name]=v`` (every expression slot referencing that
      parameter is re-evaluated in lockstep, point by point);
    - Values: exactly one of three mutually exclusive choices: ``values``
      (an explicit list; str members are evaluated per point as
      **expression strings** — not enum-style, an undefined name surfaces
      as unknown_name during per-point resolution) | ``linspace`` =
      [start, stop, n] (numeric, endpoints included, n>=2) | ``logspace``
      = [start, stop, n] (10^x, same rules); more than one given / all
      missing → a curation error pointing the way; start==stop for
      ``linspace/logspace`` is rejected (for a single-value axis use
      values=[x] directly — degenerate ranges are a breeding ground for
      typos; reject rather than let them through).
    """

    name: str = Field(min_length=1, max_length=64)
    values: Optional[list[Union[float, int, str, list[float]]]] = None
    linspace: Optional[tuple[float, float, int]] = None
    logspace: Optional[tuple[float, float, int]] = None

    @field_validator("name", mode="after")
    @classmethod
    def _check_name(cls, v):
        if not valid_param_name(v):
            raise ValueError(
                f"axis name {v!r} is invalid: must be a parameter name "
                f"(identifier, <= 64 chars) that exists in the base "
                f"config's parameters keys")
        return v

    @model_validator(mode="after")
    def _check(self):
        given = [x for x in (self.values, self.linspace, self.logspace)
                 if x is not None]
        if len(given) != 1:
            raise ValueError(
                "exactly one of values / linspace / logspace must be "
                f"given (got {len(given)})")
        for name in ("linspace", "logspace"):
            sp = getattr(self, name)
            if sp is None:
                continue
            start, stop, n = sp
            if not (math.isfinite(start) and math.isfinite(stop)):
                raise ValueError(
                    f"{name} start/stop must be finite, got {sp[:2]!r}")
            if n < 2:
                raise ValueError(f"{name}.n must be an integer >= 2, got {n}")
            if start == stop:
                raise ValueError(
                    f"{name} start==stop=={start} is rejected "
                    f"(use values=[x] for a single-value axis)")
        if self.values is not None and len(self.values) == 0:
            raise ValueError(
                "values must be non-empty (a single-element list [x] is "
                "valid)")
        return self

    def value_list(self) -> list:
        """This axis's sequence of values (values as-is; linspace/logspace
        expanded by formula).

        logspace expanded values are checked for finiteness (start=400 →
        10**400=inf is rejected). linspace does not check numeric sanity —
        the correctness of input numbers is the user's responsibility."""
        if self.values is not None:
            return list(self.values)
        start, stop, n = self.linspace if self.linspace else self.logspace
        if self.linspace is not None:
            return [start + i * (stop - start) / (n - 1)
                    for i in range(n)]
        vals = []
        for i in range(n):
            try:
                vals.append(10 ** (start + i * (stop - start) / (n - 1)))
            except OverflowError:   # 10**400 raises OverflowError directly
                raise ValueError(
                    f"logspace [{start}, {stop}] produces values out of "
                    f"float range (10^x overflow) — narrow the range"
                ) from None
        if not all(math.isfinite(x) for x in vals):
            raise ValueError(
                f"logspace [{start}, {stop}] produces non-finite values "
                f"(10^x overflow) — narrow the range")
        return vals


class SweepSpec(_StrictModel):
    """The top-level parameter sweep **definition** (purely local values).

    - ``parameters``: 1..32 axes (SweepAxisSpec);
    - ``mode``: ``product`` (default, Cartesian product) | ``zip``
      (equal-length per-point pairing across axes; unequal lengths →
      error);
    - Cap of 512 on the expanded point count (exceeding it reports the
      point count / the cap).

    Execution knobs stay out of the spec — SweepSpec is a pure definition.
    """

    parameters: list[SweepAxisSpec] = Field(min_length=1,
                                            max_length=MAX_SWEEP_AXES)
    mode: Literal["product", "zip"] = "product"

    @model_validator(mode="after")
    def _check(self):
        lens = [len(a.value_list()) for a in self.parameters]
        if self.mode == "zip":
            if len(set(lens)) != 1:
                raise ValueError(
                    f"zip mode requires all axes to have equal lengths, "
                    f"got {lens}")
            n_points = lens[0] if lens else 0
        else:
            n_points = 1
            for L in lens:
                n_points *= L
        if n_points > MAX_SWEEP_POINTS:
            raise ValueError(
                f"expanded point count {n_points} exceeds the limit "
                f"{MAX_SWEEP_POINTS} (axis lengths {lens}; reduce the "
                f"number of points)")
        return self


class SweepExpandError(ValueError):
    """Expansion-time error (axis name not in the parameter table /
    per-point resolution validation failed).

    Payload shape/value errors, not resource guards.
    """


def expand_sweep(base: dict, sweep: SweepSpec) -> list[dict]:
    """Expand: each point = deepcopy(base) + each axis's value written
    into parameters[name].

    product (default) = Cartesian product, with axis order as the
    outermost loop; zip = equal-length per-point pairing across axes.
    Pure function: does not modify base, **does not resolve expressions**
    (resolution and per-point validation belong to validate_sweep_config).
    """
    axes = [(a.name, a.value_list()) for a in sweep.parameters]
    if sweep.mode == "zip":
        combos = zip(*(vals for _, vals in axes))
    else:
        combos = itertools.product(*(vals for _, vals in axes))
    out = []
    for combo in combos:
        point = copy.deepcopy(base)
        params = point.setdefault("parameters", {})
        for (name, _), value in zip(axes, combo):
            params[name] = copy.deepcopy(value)
        out.append(point)
    return out


def _check_axis_names(sweep: SweepSpec, base: dict) -> None:
    known = base.get("parameters") or {}
    if not isinstance(known, dict):
        # A malformed base such as parameters=5 would otherwise raise a
        # bare TypeError at sorted(known), escaping the SweepExpandError
        # error contract (expand_sweep's setdefault would crash the same
        # way — this guard is the earliest gate).
        raise SweepExpandError(
            f"base config 'parameters' must be a mapping of parameter "
            f"names, got {type(known).__name__}")
    available = sorted(known)
    for a in sweep.parameters:
        if a.name not in known:
            raise SweepExpandError(
                f"axis name {a.name!r} not in base config parameters"
                + (f" (available: {available})" if available
                   else " (base config has no parameters section)"))


def _first_validation_msg(e: ValidationError) -> str:
    first = e.errors()[0]
    loc = ".".join(str(x) for x in first["loc"])
    return f"{loc}: {first['msg']}"


def validate_sweep_config(sweep: SweepSpec, base: dict) -> list[dict]:
    """Submission-side validation: axis-name checks + expansion +
    per-point evaluation/resolution + two-stage validation.

    Any invalid point → SweepExpandError carrying **which point (1-based)
    + axis names + reason** (the message includes a "the first N-1 points
    were valid" hint). All valid → returns the list of **resolved**
    per-point configs (directly POSTable to /api/jobs).
    """
    _check_axis_names(sweep, base)
    axis_names = [a.name for a in sweep.parameters]
    points = expand_sweep(base, sweep)
    resolved_points: list[dict] = []
    for i, point in enumerate(points, start=1):
        try:
            resolved, expr_at = resolve_parameters_detailed(point)
            validate_resolved(resolved, expr_at)
        except (ExprError, ValidationError) as e:
            msg = (f"{getattr(e, 'msg', '')}" if isinstance(e, ExprError)
                   else _first_validation_msg(e))
            raise SweepExpandError(
                f"point {i} of {len(points)} (axes {axis_names}) is "
                f"invalid: {msg}; the first {i - 1} points were valid"
            ) from e
        resolved_points.append(resolved)
    return resolved_points
