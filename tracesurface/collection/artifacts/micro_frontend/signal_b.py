from __future__ import annotations

import json

from tracesurface.jsast import walk_pre_iter_json

AppConfig = dict[str, object]

_QIANKUN_REQUIRED_KEYS = ("name", "entry", "activeRule")


def _is_qiankun_app_config(obj: dict[str, object]) -> bool:
    if not all(k in obj for k in _QIANKUN_REQUIRED_KEYS):
        return False
    name = obj.get("name")
    entry = obj.get("entry")
    if not isinstance(name, str) or not name:
        return False
    if not (isinstance(entry, str) or isinstance(entry, dict)):
        return False
    return True


def match_qiankun_schema_in_json(body: str) -> list[AppConfig]:
    try:
        root = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return []

    found: list[AppConfig] = []
    seen: set[str] = set()

    for node in walk_pre_iter_json(root):
        if not isinstance(node, dict):
            continue
        if _is_qiankun_app_config(node):
            name = node["name"]

            if name not in seen:
                seen.add(name)
                found.append(
                    {
                        "name": name,
                        "entry": node["entry"],
                        "activeRule": node.get("activeRule"),
                    }
                )
    return found


def match_qiankun_bodies(bodies: tuple[str, ...]) -> list[AppConfig]:
    found: list[AppConfig] = []
    for body in bodies:
        found.extend(match_qiankun_schema_in_json(body))
    return found
