"""Everything the platform builds around the user's one function.

All of it is text in, text out, so it is testable without a device: read the
element types out of the function's signature, render the wrapper kernel for those
types, render the starter file a user begins from.
"""
from __future__ import annotations

import re
import string
from dataclasses import dataclass
from typing import Any

from isc.operations.elements import ELEMENTS, TypeUse, lookup, spellings_for

TODO_MARKER = "ISC-TODO"

_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_TYPE = r"[A-Za-z_][\w:]*(?:\s*<\s*\d+\s*>)?"


class SignatureError(ValueError):
    """The source does not define the profile's function in a supported form."""


@dataclass(frozen=True)
class HookTypes:
    inp: TypeUse
    out: TypeUse | None   # None when the profile's output is not a column of elements

    def adapter_view(self) -> dict[str, Any]:
        """What the host adapter needs to pack and unpack bytes."""
        view: dict[str, Any] = {
            "in": self.inp.element.name,
            "in_bytes": self.inp.element.size,
            "in_fmt": self.inp.element.fmt,
        }
        if self.out is not None:
            view.update({
                "out": self.out.element.name,
                "out_bytes": self.out.element.size,
                "out_fmt": self.out.element.fmt,
            })
        return view

    def fingerprint_view(self) -> dict[str, str]:
        """The spellings that reach the generated source (they key the build cache)."""
        view = {"in": self.inp.spelling}
        if self.out is not None:
            view["out"] = self.out.spelling
        return view

    def to_dict(self) -> dict[str, Any]:
        def one(use: TypeUse) -> dict[str, Any]:
            return {"element": use.element.name, "spelling": use.spelling,
                    "bytes": use.element.size}

        result: dict[str, Any] = {"in": one(self.inp)}
        if self.out is not None:
            result["out"] = one(self.out)
        return result


def strip_comments(source: str) -> str:
    return _COMMENTS.sub(" ", source)


def _declaration(symbol: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?:\b(?:inline|static)\s+)*(?P<ret>{_TYPE})\s+{re.escape(symbol)}"
        rf"\s*\(\s*(?:const\s+)?(?P<arg>{_TYPE})\s+value\s*\)"
    )


def _use(text: str, allowed: list[str], where: str) -> TypeUse:
    use = lookup(text)
    if use is None or use.element.name not in allowed:
        raise SignatureError(
            f"unsupported type `{' '.join(text.split())}` for the {where}; "
            f"supported: {', '.join(spellings_for(allowed))}"
        )
    return use


def parse_signature(source: str, hook_spec: dict[str, Any]) -> HookTypes:
    """Read the element types out of the user's function.

    `hook_spec` is the `hook:` section of a profile's operation.yaml.
    """
    symbol = str(hook_spec["symbol"])
    wanted = str(hook_spec["signature"])
    allowed = list(hook_spec["element_types"])
    code = strip_comments(source)

    if not re.search(rf"\b{re.escape(symbol)}\s*\(", code):
        raise SignatureError(
            f"source does not define `{symbol}`; declare it as `{wanted}`"
        )
    match = _declaration(symbol).search(code)
    if match is None:
        raise SignatureError(
            f"`{symbol}` is declared in an unsupported form; use `{wanted}` "
            "(a by-value parameter named `value`, optional `inline`/`static` and `const`)"
        )

    argument = _use(match.group("arg"), allowed, f"parameter of `{symbol}`")
    if hook_spec["returns"] == "bool":
        if match.group("ret") != "bool":
            raise SignatureError(f"`{symbol}` must return bool: `{wanted}`")
        output = argument if hook_spec["output_element"] == "input" else None
    else:
        output = _use(match.group("ret"), allowed, f"return type of `{symbol}`")
    return HookTypes(inp=argument, out=output)


def _kernel_values(types: HookTypes) -> dict[str, str]:
    return {
        "IN_T": types.inp.spelling,
        "OUT_T": types.out.spelling if types.out is not None else "",
        "ACC_T": "int64_t" if types.inp.element.signed else "uint64_t",
    }


def render_kernel(template_text: str, types: HookTypes) -> str:
    """The platform-owned wrapper kernel, specialised to the user's element types."""
    return string.Template(template_text).substitute(_kernel_values(types))


def _element_name(name: str, allowed: list[str], option: str) -> str:
    if name not in ELEMENTS or name not in allowed:
        raise SignatureError(
            f"unsupported {option} {name!r}; choose one of: {', '.join(allowed)}"
        )
    return name


def render_starter(template_text: str, hook_spec: dict[str, Any], profile_id: str,
                   in_name: str, out_name: str | None = None) -> str:
    """The file a user starts from, with the chosen element types filled in."""
    allowed = list(hook_spec["element_types"])
    inp = _element_name(in_name, allowed, "--type")
    if hook_spec["returns"] == "element":
        out: str | None = _element_name(out_name or inp, allowed, "--out-type")
    elif out_name is not None:
        raise SignatureError(f"profile `{profile_id}` has no output type; drop --out-type")
    else:
        out = None

    kind = "float" if ELEMENTS[inp].is_float else "int"
    names = {"in": inp, "out": out or ""}
    examples = "\n".join(
        ("// 예)  " if index == 0 else "//      ") + line
        for index, line in enumerate(hook_spec["examples"][kind])
    )
    return string.Template(template_text).substitute({
        "PROFILE": profile_id,
        "IN_T": ELEMENTS[inp].standard, "IN_NAME": inp,
        "OUT_T": ELEMENTS[out].standard if out else "", "OUT_NAME": out or "",
        "INPUT_DESC": str(hook_spec["input_description"]).format(**names),
        "OUTPUT_DESC": str(hook_spec["output_description"]).format(**names),
        "EXAMPLES": examples,
    })
