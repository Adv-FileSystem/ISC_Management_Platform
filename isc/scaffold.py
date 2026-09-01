"""Create a user module by selecting a platform-managed operation."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from isc.operations import OperationSpec, get_operation
from isc.workspace import Workspace

_VALID_ID = re.compile(r"^[a-z][a-z0-9_]*$")


def create_module(workspace: Workspace, module_id: str, *, operation_id: str,
                  name: str | None = None,
                  parameters: dict[str, Any] | None = None) -> Path:
    if not _VALID_ID.match(module_id):
        raise ValueError(
            f"invalid module id {module_id!r}: use lowercase letters, digits and "
            "underscores, starting with a letter"
        )
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
