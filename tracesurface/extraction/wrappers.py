from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

_SPLIT_CALL = re.compile(
    r"\.[A-Za-z_$]*(?:post|get|put|del)[A-Za-z_$]*\(\s*(?:\{[^{}]*\}\s*,\s*)?"
    r'"([A-Za-z][\w\-]*(?:/[\w\-]+){0,3})"\s*,\s*"[\w\-/.]+"'
)


def gateways_in_calls(source: str) -> set[str]:
    return {m.group(1).lower() for m in _SPLIT_CALL.finditer(source)}


def infixes_for(source: str, gateways: Iterable[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for gw in gateways:
        if gw not in source:
            continue

        pat = re.compile(
            r'["\'`]/?' + re.escape(gw) + r"/([A-Za-z][\w\-]{0,11})/[\w\-]"
        )
        found = {m.group(1).lower() for m in pat.finditer(source)}
        if found:
            out.setdefault(gw, set()).update(found)
    return out


def finalize_wrapper_prefixes(
    gw_infixes: Mapping[str, set[str]],
    all_gateways: Iterable[str] = (),
) -> dict[str, str]:
    unambiguous = {
        gw: next(iter(inf)) for gw, inf in gw_infixes.items() if len(inf) == 1
    }

    infix_gws: dict[str, set[str]] = {}
    for gw, inf in unambiguous.items():
        infix_gws.setdefault(inf, set()).add(gw)
    structural = {inf for inf, gws in infix_gws.items() if len(gws) >= 2}
    result = {gw: inf for gw, inf in unambiguous.items() if inf in structural}

    family_infix: dict[str, str] = {}
    fam_counts: dict[str, dict[str, int]] = {}

    for gw, inf in result.items():
        top = gw.split("/", 1)[0]
        fam_counts.setdefault(top, {}).setdefault(inf, 0)
        fam_counts[top][inf] += 1

    for top, counts in fam_counts.items():
        family_infix[top] = max(counts.items(), key=lambda kv: kv[1])[0]

    for gw in all_gateways:
        gw = gw.lower()
        if gw in result:
            continue
        top = gw.split("/", 1)[0]
        if top in family_infix:
            result[gw] = family_infix[top]
    return result
