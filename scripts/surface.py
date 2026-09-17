# SPDX-License-Identifier: Apache-2.0
"""What SimMirror 1.x promises, written down once, and whether the code still keeps it.

`docs/stability.md` says what is stable from 1.0. This makes that checkable: it describes the surface -- the names on
`sim_mirror.api` with their parameters and attributes, the protocol's schemas, constants and close codes, the agent
tools' arguments, the settings, the command line, and the viewer package's exports, element, events, parts, custom
properties and option types -- and compares a description of the code now with the one committed for the major version,
`compat/surface-v1.json`.

A difference is a break only when something written against the promise stops working: a name, a parameter, a field, a
flag or an enum member gone; a parameter or a field a caller now has to give; a seam a host implements gaining a method;
a value a client sends refused, or one it reads no longer always sent. Everything else is an addition, and passes.

The committed file is written once per major version and never regenerated to make a break pass: a break is a new
major version. What it holds is as of 1.0; the additions of 1.x are promised too, and the changelog records them.

    uv run python scripts/surface.py                                 # exit 1, naming each break
    uv run python scripts/surface.py --write compat/surface-v1.json  # only when a major version starts
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import inspect
import json
import re
import sys
import textwrap
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from types import ModuleType
from typing import IO, Any

from _repo import REPO_ROOT

PROMISE = REPO_ROOT / "compat" / "surface-v1.json"

#: Made by a factory (`Runtime.build`, `SimConfig.defaults`), never from their fields.
NOT_CONSTRUCTED = frozenset({"Runtime", "SimConfig"})
#: What SimMirror's own tests reach, and a host does not: left out of the promise.
UNPROMISED = {
    "Runtime": frozenset(
        {"tool_context", "relay", "stop_builds_not_allowed"}
        | {"config", "state", "policy", "copy", "registry", "manager", "actions", "tools", "builds", "reaper", "sleep"}
    ),
    "Runtime.build": frozenset(
        {"registry", "claims", "tools", "builds", "xcrun", "keyboard_is_us", "hierarchy", "vision", "clock", "sleep"}
        | {"platform"}
    ),
}
#: What a client sends and a server reads: it may accept more, never less. The rest a server sends and a client reads.
INBOUND: dict[str, frozenset[str] | None] = {
    "client-input.schema.json": None,
    "hello.schema.json": frozenset({"ClientHello"}),
    "settings.schema.json": frozenset({"SettingsChange"}),
}
#: Schema words that say nothing a program relies on.
PROSE = frozenset({"description", "$comment", "title", "examples", "example"})
#: The viewer's interfaces, by who writes them: a host gives options, implements a transport, and reads the rest.
INTERFACES = {
    "ViewerOptions": "given",
    "HttpTransportOptions": "given",
    "SimMirrorTransport": "implemented",
    "ViewerState": "read",
    "ViewHandle": "read",
}

Description = dict[str, Any]


# -- the Python API ---------------------------------------------------------------------------------------------------


def _params(
    function: Callable[..., Any], *, drop_first: bool = False, unpromised: Iterable[str] = ()
) -> list[list[Any]]:
    kinds = {
        inspect.Parameter.POSITIONAL_ONLY: "positional",
        inspect.Parameter.POSITIONAL_OR_KEYWORD: "either",
        inspect.Parameter.KEYWORD_ONLY: "keyword",
        inspect.Parameter.VAR_POSITIONAL: "*args",
        inspect.Parameter.VAR_KEYWORD: "**kwargs",
    }
    params = list(inspect.signature(function).parameters.values())[1 if drop_first else 0 :]
    skip = set(unpromised)
    return [
        [param.name, kinds[param.kind], param.default is not inspect.Parameter.empty]
        for param in params
        if param.name not in skip
    ]


def _own(cls: type) -> list[type]:
    """The class and the bases it shares a package with: what a host's code sees of it, less Python's own."""
    root = cls.__module__.split(".")[0]
    return [klass for klass in cls.__mro__ if klass.__module__.split(".")[0] == root]


def _assigned(cls: type) -> set[str]:
    """The public attributes an ``__init__`` of the class or its own bases sets on ``self``."""
    found: set[str] = set()
    for klass in _own(cls):
        init = vars(klass).get("__init__")
        if init is None or not inspect.isfunction(init):
            continue
        try:
            tree = ast.parse(textwrap.dedent(inspect.getsource(init)))
        except OSError:  # a dataclass's generated __init__ has no source: its fields are read instead
            continue
        for node in ast.walk(tree):
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
                if isinstance(node, ast.AnnAssign)
                else []
            )
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    found.add(target.attr)
    return {name for name in found if not name.startswith("_")}


def describe_name(name: str, value: Any) -> Description:
    unpromised = UNPROMISED.get(name, frozenset())
    if not inspect.isclass(value):
        return {"kind": "function", "params": _params(value)}
    members: dict[str, Any] = {}
    for klass in reversed(_own(value)):
        for member, raw in vars(klass).items():
            if member.startswith("_") or member in unpromised:
                continue
            if isinstance(raw, property):
                members[member] = "property"
            elif isinstance(raw, (staticmethod, classmethod)) or inspect.isfunction(raw):
                bound = getattr(value, member)
                drop = inspect.isfunction(raw)
                members[member] = _params(bound, drop_first=drop, unpromised=UNPROMISED.get(f"{name}.{member}", ()))
    if getattr(value, "_is_protocol", False):
        return {"kind": "seam", "methods": dict(sorted(members.items()))}
    fields = {field.name for field in dataclasses.fields(value)} if dataclasses.is_dataclass(value) else set()
    attributes = {attr for attr in (fields | _assigned(value)) if not attr.startswith("_")} - unpromised
    described: Description = {
        "kind": "class",
        "attributes": sorted(attributes),
        "methods": dict(sorted(members.items())),
    }
    if name not in NOT_CONSTRUCTED:
        try:
            described["init"] = _params(value)
        except ValueError:  # an exception with Python's own constructor, which has no signature to read
            described["init"] = [["args", "*args", False]]
    return described


def describe_api(module: ModuleType) -> Description:
    return {name: describe_name(name, getattr(module, name)) for name in sorted(module.__all__)}


def _call_breaks(where: str, old: list[list[Any]], new: list[list[Any]]) -> list[str]:
    """What a call written against `old` could no longer do with `new`."""
    found: list[str] = []
    positional = ("positional", "either")
    old_order = [name for name, kind, _ in old if kind in positional]
    new_order = [name for name, kind, _ in new if kind in positional]
    # Only when each is still positional: a parameter gone or made keyword-only is named on its own, below.
    if set(old_order) <= set(new_order) and new_order[: len(old_order)] != old_order:
        found.append(f"{where}: its positional parameters were {', '.join(old_order)}; now {', '.join(new_order)}")
    now = {name: (kind, default) for name, kind, default in new}
    kinds = {kind for _, kind, _ in new}
    for name, kind, default in old:
        if kind in ("*args", "**kwargs"):
            if kind not in kinds:
                found.append(f"{where}: no longer takes {kind}")
            continue
        if name not in now:
            found.append(f"{where}: lost the parameter {name}")
            continue
        new_kind, new_default = now[name]
        if kind in positional and new_kind == "keyword":
            found.append(f"{where}: {name} can no longer be passed by position")
        if kind in ("either", "keyword") and new_kind == "positional":
            found.append(f"{where}: {name} can no longer be passed by name")
        if default and not new_default:
            found.append(f"{where}: {name} must now be given")
    promised = {name for name, _, _ in old}
    for name, kind, default in new:
        if name not in promised and kind not in ("*args", "**kwargs") and not default:
            found.append(f"{where}: the new parameter {name} must be given")
    return found


def api_breaks(promised: Description, now: Description) -> list[str]:
    found: list[str] = []
    for name, old in promised.items():
        new = now.get(name)
        where = f"api {name}"
        if new is None:
            found.append(f"{where}: is no longer on sim_mirror.api")
            continue
        if new["kind"] != old["kind"]:
            found.append(f"{where}: was a {old['kind']}, now a {new['kind']}")
            continue
        if old["kind"] == "function":
            found += _call_breaks(where, old["params"], new["params"])
        elif old["kind"] == "seam":
            for method in sorted(set(new["methods"]) - set(old["methods"])):
                found.append(f"{where}: a host implementing it must now write {method} too")
            for method, params in old["methods"].items():
                if method in new["methods"] and new["methods"][method] != params:
                    found.append(f"{where}.{method}: what SimMirror calls it with changed, and a host's has not")
        else:
            found += _class_breaks(where, old, new)
    return found


def _class_breaks(where: str, old: Description, new: Description) -> list[str]:
    found: list[str] = []
    if "init" in old:
        if "init" not in new:
            found.append(f"{where}: can no longer be made directly")
        else:
            found += _call_breaks(where, old["init"], new["init"])
    for attribute in old["attributes"]:
        if attribute not in new["attributes"] and new["methods"].get(attribute) != "property":
            found.append(f"{where}.{attribute}: is gone")
    for method, params in old["methods"].items():
        now = new["methods"].get(method)
        if now is None:
            if not (params == "property" and method in new["attributes"]):
                found.append(f"{where}.{method}: is gone")
        elif (params == "property") != (now == "property"):
            found.append(f"{where}.{method}: was {'a property' if params == 'property' else 'a method'}, now not")
        elif params != "property":
            found += _call_breaks(f"{where}.{method}", params, now)
    return found


# -- the protocol and the tools' arguments ----------------------------------------------------------------------------


def _plain(schema: Any) -> Any:
    """A schema without its prose."""
    if isinstance(schema, dict):
        return {key: _plain(value) for key, value in schema.items() if key not in PROSE}
    if isinstance(schema, list):
        return [_plain(item) for item in schema]
    return schema


def describe_protocol(folder: Path) -> Description:
    schemas = {path.name: _plain(json.loads(path.read_text())) for path in sorted(folder.glob("*.schema.json"))}
    constants = {key: value for key, value in json.loads((folder / "constants.json").read_text()).items()}
    codes = json.loads((folder / "close-codes.json").read_text())["close_codes"]
    return {
        "schemas": schemas,
        "constants": {key: value for key, value in constants.items() if not key.startswith("$")},
        "close_codes": {key: entry["code"] for key, entry in codes.items()},
    }


def _types(node: Mapping[str, Any]) -> set[str]:
    kind = node.get("type")
    return set(kind) if isinstance(kind, list) else {kind} if isinstance(kind, str) else set()


def _refs(node: Any) -> set[str]:
    if isinstance(node, dict):
        own = {node["$ref"]} if isinstance(node.get("$ref"), str) else set()
        return own.union(*(_refs(value) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(_refs(item) for item in node))
    return set()


Part = tuple[str, str]


def _part(schemas: Mapping[str, Any], file: str, name: str) -> Any:
    """A file's root without its definitions (``name`` empty), or one of its definitions."""
    schema = schemas.get(file) or {}
    return {k: v for k, v in schema.items() if k != "$defs"} if not name else (schema.get("$defs") or {}).get(name)


def _reached(schemas: Mapping[str, Any], roots: Iterable[Part]) -> set[Part]:
    """The parts `roots` name, and every part their references reach."""
    todo, seen = list(roots), set()
    while todo:
        file, name = todo.pop()
        if (file, name) in seen or _part(schemas, file, name) is None:
            continue
        seen.add((file, name))
        for ref in _refs(_part(schemas, file, name)):
            target, _, pointer = ref.partition("#")
            todo.append((target or file, pointer.removeprefix("/$defs/")))
    return seen


def _sent_by_client(file: str, name: str) -> bool:
    if file not in INBOUND:
        return False
    names = INBOUND[file]
    return names is None or name in names


def directions(schemas: Mapping[str, Any]) -> tuple[set[Part], set[Part]]:
    """What a client sends -- `INBOUND` and all it references -- and what a server sends: every other part, and all
    that references. A definition both use, such as a share of the screen, is checked both ways."""
    parts: list[Part] = []
    for file, schema in schemas.items():
        parts += [(file, ""), *((file, name) for name in (schema.get("$defs") or {}))]
    inbound = _reached(schemas, (part for part in parts if _sent_by_client(*part)))
    return inbound, _reached(schemas, (part for part in parts if part not in inbound))


LOWER_BOUNDS = ("minimum", "exclusiveMinimum", "minLength", "minItems")
UPPER_BOUNDS = ("maximum", "exclusiveMaximum", "maxLength", "maxItems")


def _node_breaks(where: str, old: Any, new: Any, *, inbound: bool, outbound: bool) -> list[str]:
    if not isinstance(old, dict):
        return [] if old == new else [f"{where}: was {json.dumps(old)}, now {json.dumps(new)}"]
    if not isinstance(new, dict):
        return [f"{where}: is gone"]
    found: list[str] = []
    for key in ("$ref", "const", "x-constant", "pattern", "format"):
        if key in old and old[key] != new.get(key):
            found.append(f"{where}: its {key} was {json.dumps(old[key])}, now {json.dumps(new.get(key))}")
    if "type" in old:
        before, after = _types(old), _types(new)
        if inbound and not before <= after:
            found.append(f"{where}: no longer accepts {', '.join(sorted(before - after))}")
        if outbound and not after <= before:
            found.append(f"{where}: may now be {', '.join(sorted(after - before))}")
    if "enum" in old and "enum" in new:
        gone = [value for value in old["enum"] if value not in new["enum"]]
        if gone:
            found.append(f"{where}: lost {', '.join(map(str, gone))}")
    old_properties, new_properties = old.get("properties") or {}, new.get("properties") or {}
    for key, sub in old_properties.items():
        if key not in new_properties:
            found.append(f"{where}.{key}: is gone")
        else:
            found += _node_breaks(f"{where}.{key}", sub, new_properties[key], inbound=inbound, outbound=outbound)
    before_required, after_required = set(old.get("required") or ()), set(new.get("required") or ())
    if outbound and not before_required <= after_required:
        found.append(f"{where}: no longer always sends {', '.join(sorted(before_required - after_required))}")
    if inbound and not after_required <= before_required:
        found.append(f"{where}: now requires {', '.join(sorted(after_required - before_required))}")
    if isinstance(old.get("items"), dict):
        found += _node_breaks(f"{where}[]", old["items"], new.get("items"), inbound=inbound, outbound=outbound)
    for key in ("prefixItems", "oneOf", "anyOf", "allOf"):
        before_list, after_list = old.get(key) or [], new.get(key) or []
        if len(after_list) < len(before_list):
            found.append(f"{where}: its {key} had {len(before_list)}, now {len(after_list)}")
        for index, (sub, now) in enumerate(zip(before_list, after_list, strict=False)):
            found += _node_breaks(f"{where}.{key}[{index}]", sub, now, inbound=inbound, outbound=outbound)
    if inbound:
        for key in LOWER_BOUNDS:
            if key in new and (key not in old or new[key] > old[key]):
                found.append(f"{where}: its {key} tightened to {new[key]}")
        for key in UPPER_BOUNDS:
            if key in new and (key not in old or new[key] < old[key]):
                found.append(f"{where}: its {key} tightened to {new[key]}")
        if old.get("additionalProperties") is not False and new.get("additionalProperties") is False:
            found.append(f"{where}: no longer accepts properties it does not describe")
    return found


def protocol_breaks(promised: Description, now: Description) -> list[str]:
    found: list[str] = []
    inbound, outbound = directions(promised["schemas"])
    for file, old in promised["schemas"].items():
        new = now["schemas"].get(file)
        if new is None:
            found.append(f"protocol {file}: is gone")
            continue
        parts: dict[str, tuple[Any, Any]] = {
            "": (_part(promised["schemas"], file, ""), _part(now["schemas"], file, ""))
        }
        new_defs = new.get("$defs") or {}
        parts |= {name: (sub, new_defs.get(name)) for name, sub in (old.get("$defs") or {}).items()}
        for name, (before, after) in parts.items():
            where = f"protocol {file}" + (f" {name}" if name else "")
            if after is None:
                found.append(f"{where}: is gone")
                continue
            part = (file, name)
            found += _node_breaks(where, before, after, inbound=part in inbound, outbound=part in outbound)
    for area in ("constants", "close_codes"):
        for key, value in promised[area].items():
            if now[area].get(key) != value:
                found.append(f"protocol {area} {key}: was {value}, now {now[area].get(key)}")
    return found


def describe_tools(
    schemas: Mapping[str, tuple[str, Mapping[str, Any]]], steps: Mapping[str, Iterable[str]]
) -> Description:
    """Each tool's arguments, and what each kind of `sim_act` step may say -- which its schema leaves to the tool."""
    return {
        "arguments": {name: _plain(dict(schema)) for name, (_, schema) in sorted(schemas.items())},
        "steps": {kind: sorted(options) for kind, options in sorted(steps.items())},
    }


def tool_breaks(promised: Description, now: Description) -> list[str]:
    found: list[str] = []
    for name, old in promised["arguments"].items():
        if name not in now["arguments"]:
            found.append(f"tool {name}: is gone")
        else:
            found += _node_breaks(f"tool {name}", old, now["arguments"][name], inbound=True, outbound=False)
    for kind, options in promised["steps"].items():
        if kind not in now["steps"]:
            found.append(f"tool sim_act step {kind}: is gone")
        else:
            found += [
                f"tool sim_act step {kind}: no longer takes {option}"
                for option in options
                if option not in now["steps"][kind]
            ]
    return found


# -- settings and the command line ------------------------------------------------------------------------------------


def describe_settings(settings: Iterable[Any]) -> Description:
    return {
        setting.path: {
            "env": setting.env,
            "reach": setting.reach,
            "rule": {key: value for key, value in setting.rule.spec().items() if key != "example"},
        }
        for setting in sorted(settings, key=lambda setting: str(setting.path))
    }


def setting_breaks(promised: Description, now: Description) -> list[str]:
    found: list[str] = []
    for path, old in promised.items():
        new = now.get(path)
        where = f"setting {path}"
        if new is None:
            found.append(f"{where}: is gone")
            continue
        if new["env"] != old["env"]:
            found.append(f"{where}: is set by {new['env']}, no longer {old['env']}")
        if old["reach"] == "scope" and new["reach"] != "scope":
            found.append(f"{where}: can no longer be set for one scope")
        before, after = old["rule"], new["rule"]
        if before.get("kind") != after.get("kind"):
            found.append(f"{where}: was a {before.get('kind')}, now a {after.get('kind')}")
            continue
        for key, value in before.items():
            changed = after.get(key)
            if key == "options":
                gone = [option for option in value if option not in (changed or ())]
                if gone:
                    found.append(f"{where}: no longer accepts {', '.join(gone)}")
            elif key == "low" and (changed is None or changed > value):
                found.append(f"{where}: no longer accepts values below {changed}")
            elif key in ("high", "max_length") and (changed is None or changed < value):
                found.append(f"{where}: no longer accepts values above {changed}")
            elif key == "required" and changed and not value:
                found.append(f"{where}: may no longer be empty")
            elif key not in ("low", "high", "max_length", "required", "options") and changed != value:
                found.append(f"{where}: its {key} was {json.dumps(value)}, now {json.dumps(changed)}")
    return found


def describe_cli(parser: argparse.ArgumentParser, prefix: str = "sim-mirror") -> Description:
    found: Description = {}
    options: dict[str, bool] = {}
    positionals: list[list[Any]] = []
    for action in parser._actions:  # argparse offers no other way to read a parser back
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                found |= describe_cli(sub, f"{prefix} {name}")
        elif isinstance(action, argparse._HelpAction):
            continue
        elif action.option_strings:
            options |= {flag: bool(action.required) for flag in action.option_strings}
        else:
            positionals.append([action.dest, bool(action.required)])
    found[prefix] = {"options": dict(sorted(options.items())), "positionals": positionals}
    return dict(sorted(found.items()))


def cli_breaks(promised: Description, now: Description) -> list[str]:
    found: list[str] = []
    for command, old in promised.items():
        new = now.get(command)
        if new is None:
            found.append(f"cli {command}: is gone")
            continue
        for flag in old["options"]:
            if flag not in new["options"]:
                found.append(f"cli {command} {flag}: is gone")
        for flag, required in new["options"].items():
            if required and not old["options"].get(flag, False):
                found.append(f"cli {command} {flag}: must now be given")
        before = [name for name, _ in old["positionals"]]
        after = [name for name, _ in new["positionals"]]
        if after[: len(before)] != before:
            found.append(f"cli {command}: its arguments were {' '.join(before)}; now {' '.join(after)}")
        for name, required in new["positionals"][len(before) :]:
            if required:
                found.append(f"cli {command}: the new argument {name} must be given")
    return found


# -- the viewer package -----------------------------------------------------------------------------------------------


def _first(pattern: str, text: str) -> str:
    found = re.search(pattern, text)
    if found is None:
        raise ValueError(f"the viewer no longer has {pattern}")
    return found[1]


def _interface(source: str, name: str) -> dict[str, bool] | None:
    """An interface's members, each with whether it may be left out; None when there is no such interface."""
    body = re.search(rf"^export interface {name}\b[^{{]*\{{\n(.*?)^\}}", source, re.MULTILINE | re.DOTALL)
    if body is None:
        return None
    members = re.findall(r"^  (?:readonly )?([A-Za-z_]\w*)(\?)?[(:<]", body[1], re.MULTILINE)
    return {member: bool(optional) for member, optional in members}


def describe_viewer(src: Path) -> Description:
    sources = {path.name: path.read_text() for path in sorted(src.glob("*.ts")) if not path.name.endswith(".test.ts")}
    everything = "\n".join(sources.values())
    exports = set()
    for names in re.findall(r"^export (?:type )?\{([^}]*)\} from", sources["index.ts"], re.MULTILINE):
        exports |= {part.split(" as ")[-1].strip() for part in names.split(",") if part.strip()}
    element = sources["element.ts"]
    interfaces = {}
    for name in INTERFACES:
        found = (_interface(source, name) for source in sources.values())
        interfaces[name] = next((members for members in found if members is not None), {})
    properties = re.findall(r"(--sim-mirror-[a-z0-9]+(?:-[a-z0-9]+)*)", (src / "styles.css").read_text())
    return {
        "exports": sorted(exports),
        "element": _first(r"ELEMENT_NAME = '([^']+)'", element),
        "attributes": re.findall(r"'([a-z-]+)'", _first(r"observedAttributes = \[([^\]]*)\]", element)),
        "events": sorted(set(re.findall(r"#tell\('([a-z-]+)'", element))),
        "parts": sorted(set(re.findall(r'part="([a-z-]+)"', everything))),
        "custom_properties": sorted(set(properties)),
        "interfaces": interfaces,
    }


def viewer_breaks(promised: Description, now: Description) -> list[str]:
    found: list[str] = []
    if now["element"] != promised["element"]:
        found.append(f"viewer element: was <{promised['element']}>, now <{now['element']}>")
    for area in ("exports", "attributes", "events", "parts", "custom_properties"):
        found += [f"viewer {area} {item}: is gone" for item in promised[area] if item not in now[area]]
    for name, members in promised["interfaces"].items():
        role = INTERFACES.get(name, "read")
        current = now["interfaces"].get(name, {})
        for member, optional in members.items():
            if member not in current:
                found.append(f"viewer {name}.{member}: is gone")
            elif role == "read" and not optional and current[member]:
                found.append(f"viewer {name}.{member}: may now be absent")
            elif role != "read" and optional and not current[member]:
                found.append(f"viewer {name}.{member}: must now be given")
        if role != "read":
            found += [
                f"viewer {name}.{member}: is new, and must be given"
                for member, optional in current.items()
                if member not in members and not optional
            ]
    return found


# -- all of it --------------------------------------------------------------------------------------------------------

AREAS: dict[str, Callable[[Description, Description], list[str]]] = {
    "api": api_breaks,
    "protocol": protocol_breaks,
    "tools": tool_breaks,
    "settings": setting_breaks,
    "cli": cli_breaks,
    "viewer": viewer_breaks,
}


def describe(root: Path = REPO_ROOT) -> Description:
    """The surface as the code has it now."""
    from sim_mirror import api
    from sim_mirror.cli.main import parser
    from sim_mirror.config.schema import SETTINGS
    from sim_mirror.core.actions import STEP_OPTIONS
    from sim_mirror.tools.schemas import SCHEMAS

    return {
        "api": describe_api(api),
        "protocol": describe_protocol(root / "protocol" / "v1"),
        "tools": describe_tools(SCHEMAS, STEP_OPTIONS),
        "settings": describe_settings(SETTINGS),
        "cli": describe_cli(parser()),
        "viewer": describe_viewer(root / "viewer" / "src"),
    }


def breaks(promised: Description, now: Description) -> list[str]:
    """Every way `now` no longer keeps `promised`; empty when it keeps all of it."""
    found: list[str] = []
    for area, check in AREAS.items():
        if area not in promised:
            continue
        if area not in now:
            found.append(f"{area}: is gone")
        else:
            found += check(promised[area], now[area])
    return found


def main(
    argv: list[str] | None = None,
    *,
    promise: Path = PROMISE,
    current: Callable[[], Description] = describe,
    out: IO[str] | None = None,
) -> int:
    parsed = argparse.ArgumentParser(description="Check that the code keeps the surface this major version promises.")
    parsed.add_argument("--write", type=Path, metavar="FILE", help="write the surface now, when a major version starts")
    args = parsed.parse_args(argv)
    stream = out or sys.stdout
    if args.write:
        args.write.write_text(json.dumps(current(), indent=2, sort_keys=True) + "\n")
        print(f"wrote {args.write}", file=stream)
        return 0
    found = breaks(json.loads(promise.read_text()), current())
    for line in found:
        print(line, file=stream)
    if found:
        print(f"{len(found)} break(s) of {promise.name}: see docs/stability.md", file=stream)
    return 1 if found else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
