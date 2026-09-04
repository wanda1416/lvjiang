"""Run data-only test matrices as one pytest item.

Unlike ``pytest.mark.parametrize``, this helper keeps sample rows inside one
test invocation.  It is intended for homogeneous input/output tables where
separate fixture setup and separate reporting add noise without adding
coverage.
"""

from __future__ import annotations

from functools import wraps
from inspect import Signature, signature
from typing import Any, Callable, Iterable


def case_matrix(
    argnames: str | tuple[str, ...],
    argvalues: Iterable[Any],
    **_display_options: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Execute every parameter row inside a single collected test."""
    names = (
        tuple(part.strip() for part in argnames.split(","))
        if isinstance(argnames, str)
        else tuple(argnames)
    )
    rows = tuple(argvalues)

    def decorate(test: Callable[..., Any]) -> Callable[..., Any]:
        original_signature = signature(test)
        visible_parameters = [
            parameter
            for name, parameter in original_signature.parameters.items()
            if name not in names
        ]

        @wraps(test)
        def run_matrix(*args: Any, **kwargs: Any) -> None:
            fixtures = original_signature.bind_partial(*args, **kwargs).arguments
            failures: list[str] = []
            for index, row in enumerate(rows):
                values = (row,) if len(names) == 1 else tuple(row)
                case = dict(zip(names, values, strict=True))
                try:
                    test(**fixtures, **case)
                except Exception as exc:  # report every bad row together
                    failures.append(f"row {index} {case!r}: {exc!r}")
            if failures:
                raise AssertionError("matrix failures:\n" + "\n".join(failures))

        run_matrix.__signature__ = Signature(visible_parameters)  # type: ignore[attr-defined]
        return run_matrix

    return decorate
