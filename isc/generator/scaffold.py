"""Create a user module by selecting a platform-managed operation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from isc.generator.generate import HookTypes, SignatureError, parse_signature, render_starter, strip_comments
from isc.manifest import Manifest
from isc.operations import (
    OperationSpec, canonical_profile_id, get_operation, get_profile, profiles,
)
from isc.workspace import Workspace

_VALID_ID = re.compile(r"^[a-z][a-z0-9_]*$")


def _check_module_id(module_id: str) -> None:
    if not _VALID_ID.match(module_id):
        raise ValueError(
            f"invalid module id {module_id!r}: use lowercase letters, digits and "
            "underscores, starting with a letter"
        )


def create_module(workspace: Workspace, module_id: str, *, operation_id: str,
                  name: str | None = None,
                  parameters: dict[str, Any] | None = None) -> Path:
    _check_module_id(module_id)
    operation = get_operation(operation_id)
    resolved_parameters = operation.resolve_parameters(parameters)
    destination = workspace.modules_dir / module_id
    if destination.exists():
        raise FileExistsError(f"module already exists: {destination}")

    manifest: dict[str, Any] = {
        "module": {
            "id": module_id,
            "name": name or module_id,
            "version": "0.1.0",
        },
        "operation": {
            "id": operation.id,
            "version": operation.version,
        },
    }
    if resolved_parameters:
        manifest["parameters"] = resolved_parameters
    destination.mkdir(parents=True)
    (destination / "module.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)
    )
    (destination / "README.md").write_text(
        _module_readme(module_id, operation, resolved_parameters)
    )
    return destination


def render_profile_starter(profile: OperationSpec, in_type: str = "u32",
                           out_type: str | None = None) -> str:
    """The starter file for a profile, with the chosen element types filled in."""
    template = (profile.directory / str(profile.hook_spec["template"])).read_text()
    return render_starter(template, profile.hook_spec, profile.id, in_type, out_type)


def _types_of(source: str, profile: OperationSpec) -> HookTypes | None:
    try:
        return parse_signature(source, profile.hook_spec)
    except SignatureError:
        return None


def create_hook_module(workspace: Workspace, module_id: str, *, profile_id: str,
                       name: str | None = None, logic: str | None = None,
                       origin: Path | None = None, in_type: str = "u32",
                       out_type: str | None = None) -> Path:
    """Create the one editable source file required by a managed hook profile.

    `logic` replaces the generated starter when the user already wrote the code;
    `origin` is the file it came from, recorded in the README. `in_type` and
    `out_type` choose the element types of the starter.
    """
    _check_module_id(module_id)
    profile = get_profile(profile_id)
    destination = workspace.modules_dir / module_id
    if destination.exists():
        raise FileExistsError(f"module already exists: {destination}")
    if logic is None:
        logic = render_profile_starter(profile, in_type, out_type)  # 잘못된 타입이면 여기서 실패

    manifest = {
        "module": {
            "id": module_id,
            "name": name or module_id,
            "version": "0.1.0",
        },
        "hook": {
            "profile": profile.id,
            "version": profile.version,
            "source": "logic.hpp",
        },
    }
    destination.mkdir(parents=True)
    (destination / "module.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)
    )
    (destination / "logic.hpp").write_text(logic)
    (destination / "README.md").write_text(
        _hook_readme(module_id, profile, _types_of(logic, profile), origin)
    )
    return destination


@dataclass(frozen=True)
class LogicModule:
    """A module made from the user's logic file, and what happened to it."""
    module_id: str
    path: Path
    profile_id: str
    action: str  # "created", "updated" or "unchanged"

    def to_dict(self) -> dict[str, str]:
        return {
            "module": self.module_id, "path": str(self.path),
            "profile": self.profile_id, "action": self.action,
        }


def _read_cpp_body(path: Path) -> str:
    return path.read_text()


# 로직 파일의 형태마다 읽는 방법이 있고, 확장자가 그것을 고른다. 읽는 일은 파일을 HLS C++
# 소스로 바꾸는 데까지다. 프로파일 판별, 모듈 생성, 검사, 빌드는 형태와 무관하게 같으므로
# 새 형태는 여기에 읽는 함수 하나를 더하면 된다.
_LOGIC_READERS: dict[str, Callable[[Path], str]] = {".hpp": _read_cpp_body}


def is_logic_file(name: str | Path) -> bool:
    return Path(name).suffix.lower() in _LOGIC_READERS


def _read_logic(path: Path) -> str:
    reader = _LOGIC_READERS.get(path.suffix.lower())
    if reader is None:
        raise ValueError(
            f"unsupported logic file {path.name!r}: "
            f"supported types are {', '.join(sorted(_LOGIC_READERS))}"
        )
    return reader(path)


def _detect_profile(source: str, profile_id: str | None) -> OperationSpec:
    """Pick the hook profile whose entry-point signature the source defines."""
    code = strip_comments(source)
    wanted = canonical_profile_id(profile_id) if profile_id else None
    candidates = [profile for profile in profiles() if wanted in (None, profile.id)]
    matches = [
        profile for profile in candidates
        if re.search(rf"\b{re.escape(str(profile.hook_spec['symbol']))}\s*\(", code)
    ]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise ValueError(
            f"logic file matches several profiles ({', '.join(p.id for p in matches)}); "
            "choose one with --profile"
        )
    supported = "; ".join(
        f"{profile.id}: {profile.hook_spec['signature']}"
        for profile in (candidates or profiles())
    )
    raise ValueError(f"no hook profile matches this logic file (supported: {supported})")


def _owning_module(workspace: Workspace, source_path: Path) -> str | None:
    """The id of the existing hook module whose own logic file this is, if any."""
    resolved = source_path.resolve()
    for existing_id in workspace.module_ids():
        try:
            manifest = workspace.load(existing_id)
        except (LookupError, ValueError):
            continue
        if manifest.is_hook and manifest.hook_source == resolved:
            return existing_id
    return None


def create_module_from_logic(workspace: Workspace, logic_path: str | Path, *,
                             module_id: str | None = None, profile_id: str | None = None,
                             name: str | None = None) -> LogicModule:
    """Make, or refresh, a hook module from the one file the user wrote.

    The user's file is the source of truth: running this again with an edited file
    rewrites the module's logic, which changes the build fingerprint.
    """
    source_path = Path(logic_path)
    source = _read_logic(source_path)
    owner = _owning_module(workspace, source_path)
    if owner is not None and module_id in (None, "", owner):
        # 이미 어떤 모듈의 로직 파일이다: 파일 이름('logic')으로 새 모듈을 만들지 않는다.
        manifest = workspace.load(owner)
        return LogicModule(owner, manifest.directory, manifest.hook.profile, "unchanged")
    profile = _detect_profile(source, profile_id)
    parse_signature(source, profile.hook_spec)  # 지원하지 않는 타입이면 아무것도 만들기 전에 실패
    derived = not module_id
    module_id = module_id or source_path.stem
    try:
        _check_module_id(module_id)
    except ValueError as error:
        if not derived:
            raise
        raise ValueError(
            f"{error} (taken from the file name {source_path.name!r}; "
            "give another with `isc new-custom <id> --from <file>`)"
        ) from None

    destination = workspace.modules_dir / module_id
    if not destination.exists():
        create_hook_module(workspace, module_id, profile_id=profile.id, name=name,
                           logic=source, origin=source_path.resolve())
        return LogicModule(module_id, destination, profile.id, "created")

    manifest = Manifest.load(destination)
    if not (manifest.is_hook and manifest.hook.profile == profile.id):
        raise FileExistsError(
            f"module {module_id!r} already exists and is not a {profile.id} hook module: "
            f"{destination}"
        )
    current = manifest.hook_source
    if current.read_text() == source:
        return LogicModule(module_id, destination, profile.id, "unchanged")
    current.write_text(source)
    return LogicModule(module_id, destination, profile.id, "updated")


def _module_readme(module_id: str, operation: OperationSpec,
                   parameters: dict[str, Any]) -> str:
    parameter_lines = (
        "\n".join(f"- `{name}`: `{value}`" for name, value in parameters.items())
        if parameters else "- 설정 가능한 파라미터가 없습니다."
    )
    return f"""# {module_id}

이 모듈은 ISC 내장 `{operation.id}` 연산을 사용합니다.

{operation.description}

## 설정

{parameter_lines}

하드웨어 커널, 메모리 인터페이스와 호스트 어댑터는 ISC가 관리합니다. 이 모듈에서는
`module.yaml`에 연산이 허용한 파라미터만 설정하십시오.

## 실행

```bash
isc describe {operation.id}
isc check {module_id}
isc build {module_id} --target hw_emu
isc run {module_id} --input <파일> --target hw_emu
```

`--input` 파일은 원시 바이트로 전달되며 실제 입력 제약은
`isc describe {operation.id}`에서 확인할 수 있습니다.
"""


def _hook_readme(module_id: str, profile: OperationSpec, types: HookTypes | None = None,
                 origin: Path | None = None) -> str:
    signature = profile.hook_spec["signature"]
    names = {
        "in": types.inp.element.name if types else "T",
        "out": types.out.element.name if types and types.out else "U",
    }
    input_line = str(profile.hook_spec["input_description"]).format(**names)
    output_line = str(profile.hook_spec["output_description"]).format(**names)
    origin_note = "" if origin is None else f"""
## 로직 원본

`logic.hpp` 는 `{origin}` 에서 가져왔습니다. `isc run <로직 파일>` 이나
`isc new-custom --from <로직 파일>` 을 다시 실행하면 이 모듈의 `logic.hpp` 가 그 파일
내용으로 갱신됩니다. 로직은 원본 파일에서 고치십시오.
"""
    return f"""# {module_id}

이 모듈은 ISC 사용자 Hook 프로파일 `{profile.id}`를 사용합니다.

## 사용자 작성 범위

`logic.hpp`의 `{signature}` 함수만 수정하십시오. 함수 이름과 인자 이름(`value`)은
바꾸지 마십시오. 인자 타입이 입력 값의 타입을 정하고, 최상위 HLS 커널, 메모리
인터페이스 및 호스트 어댑터는 그 타입에 맞춰 ISC가 생성합니다.

## 데이터 계약

- 입력: {input_line}
- 출력: {output_line}
{origin_note}
## 실행

```bash
isc check {module_id}
isc build {module_id} --target hw_emu
isc run {module_id} --input <파일> --target hw_emu
```
"""
