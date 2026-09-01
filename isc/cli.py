"""The `isc` command line: create -> build -> run -> validate -> record -> reuse."""
from __future__ import annotations

import argparse
import json
import platform
import sys
from typing import Any

from isc import record
from isc.backends import backend_names, get_backend
from isc.build import build as build_module
from isc.check import check_module
from isc.lifecycle import revalidate, run as run_module
from isc.manifest import ManifestError
from isc.operations import (
    get_operation, operations as catalog_operations, parse_parameter_assignments,
)
from isc.scaffold import create_module
from isc.workspace import Workspace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="isc", description="ISC module lifecycle platform")
    parser.add_argument("--workspace", help="workspace root (default: nearest parent with modules/)")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("doctor", help="check the local environment")
    commands.add_parser("devices", help="list devices visible to each backend")
    commands.add_parser("list", help="list modules in this workspace")
    commands.add_parser("operations", help="list platform-supported operations")

    describe = commands.add_parser("describe", help="describe one supported operation")
    describe.add_argument("operation_id")

    new = commands.add_parser("new", help="create a module from a supported operation")
    new.add_argument("module_id")
    new.add_argument("--operation", help="operation shown by `isc operations`")
    new.add_argument("--name")
    new.add_argument(
        "--set", action="append", dest="settings", metavar="NAME=VALUE",
        help="set an operation parameter (repeatable)",
    )

    inspect = commands.add_parser("inspect", help="show a module's manifest")
    inspect.add_argument("module_id")

    check = commands.add_parser("check", help="static checks, no device or toolchain needed")
    check.add_argument("module_id")

    build = commands.add_parser("build", help="build a module for a target")
    build.add_argument("module_id")
    build.add_argument("--target")
    build.add_argument("--force", action="store_true", help="ignore the build cache")
    build.add_argument("--timeout", type=float)

    run = commands.add_parser("run", help="build if needed, execute, validate and record")
    run.add_argument("module_id")
    run.add_argument("--input", required=True, help="input data file")
    run.add_argument("--output", help="where to write the result (default: artifacts/outputs/)")
    run.add_argument("--target")
    run.add_argument("--device", type=int, help="device index")
    run.add_argument("--force-build", action="store_true")
    run.add_argument("--no-validate", action="store_true")
    run.add_argument("--timeout", type=float)

    validate = commands.add_parser("validate", help="re-check an existing output against its input")
    validate.add_argument("module_id")
    validate.add_argument("--input", required=True)
    validate.add_argument("--output", required=True)
    validate.add_argument("--target", required=True, help="the target that produced --output")
    validate.add_argument("--device", type=int, help="device index")
    validate.add_argument("--timeout", type=float)

    records = commands.add_parser("records", help="list past runs")
    records.add_argument("--module")
    records.add_argument("--limit", type=int, default=20)
    records.add_argument("--csv", action="store_true")

    show = commands.add_parser("show", help="show one run record")
    show.add_argument("run_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = Workspace.discover(args.workspace)
    emit = _json_printer if args.json else None
    try:
        return _dispatch(args, workspace, emit)
    except (LookupError, ValueError, ManifestError, FileExistsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace, workspace: Workspace, emit) -> int:
    command = args.command

    if command == "doctor":
        report = _doctor(workspace)
        if emit:
            emit(report)
        else:
            print(f"workspace : {report['workspace']}")
            print(f"python    : {report['python']}")
            for name, status in report["backends"].items():
                print(f"backend {name}: {'ok' if status['available'] else 'unavailable'} -- {status['reason']}")
                for device in status["devices"]:
                    print(f"    [{device['index']}] {device['name']}  {device['address']}")
            print(f"modules   : {', '.join(report['modules']) or 'none'}")
        return 0 if any(s["available"] for s in report["backends"].values()) else 1

    if command == "devices":
        found = [vars(device) for name in backend_names() for device in _devices(name)]
        if emit:
            emit(found)
        elif not found:
            print("no devices found")
        else:
            for device in found:
                print(f"[{device['index']}] {device['name']}  {device['address']}  ({device['backend']})")
        return 0 if found else 1

    if command == "list":
        modules = []
        for module_id in workspace.module_ids():
            try:
                manifest = workspace.load(module_id)
                modules.append({
                    "id": manifest.id, "name": manifest.name, "version": manifest.version,
                    "operation": manifest.operation_id,
                    "targets": list(manifest.build.targets),
                })
            except ManifestError as exc:
                modules.append({"id": module_id, "error": str(exc)})
        if emit:
            emit(modules)
        elif not modules:
            print(f"no modules in {workspace.modules_dir}")
        else:
            for entry in modules:
                if "error" in entry:
                    print(f"{entry['id']:<16} INVALID: {entry['error']}")
                else:
                    print(f"{entry['id']:<16} {entry['version']:<8} {entry['operation']:<12} "
                          f"{','.join(entry['targets']):<14} {entry['name']}")
        return 0

    if command == "operations":
        entries = [operation.to_dict() for operation in catalog_operations()]
        if emit:
            emit(entries)
        elif not entries:
            print("no operations installed")
        else:
            print(f"{'OPERATION':<14} {'CATEGORY':<14} DESCRIPTION")
            for entry in entries:
                print(f"{entry['id']:<14} {entry['category']:<14} {entry['description']}")
        return 0

    if command == "describe":
        operation = get_operation(args.operation_id)
        if emit:
            emit(operation.to_dict())
        else:
            print(f"operation   : {operation.id}")
            print(f"name        : {operation.name}")
            print(f"version     : {operation.version}")
            print(f"category    : {operation.category}")
            print(f"description : {operation.description}")
            print(f"input       : {_contract_summary(operation.input_contract)}")
            print(f"output      : {_contract_summary(operation.output_contract)}")
            print("parameters  :")
            if not operation.parameter_specs:
                print("  none")
            for name, spec in operation.parameter_specs.items():
                default = f" (default: {spec['default']})" if "default" in spec else ""
                print(f"  {name:<12} {spec.get('type', 'string')}{default}")
                if spec.get("description"):
                    print(f"               {spec['description']}")
            print("example     :")
            print(f"  isc new mymodule --operation {operation.id}")
        return 0

    if command == "new":
        if not args.operation:
            available = ", ".join(operation.id for operation in catalog_operations()) or "none"
            raise ValueError(
                "an operation is required; use --operation <name> "
                f"(available: {available}). Run `isc operations` for details"
            )
        parameters = parse_parameter_assignments(args.settings)
        path = create_module(
            workspace, args.module_id, operation_id=args.operation,
            name=args.name, parameters=parameters,
        )
        report = check_module(path)
        if emit:
            emit({"created": str(path), "check": report.to_dict()})
        else:
            print(f"created {path}")
            for item in sorted(p.relative_to(path) for p in path.rglob("*") if p.is_file()):
                print(f"  {item}")
            print(f"\noperation: {args.operation}")
            if not report.passed:
                print("setup required before build:")
                for problem in report.problems:
                    print(f"  {problem}")
            print(f"next: isc check {args.module_id}")
        # Creation succeeded. Environment-dependent readiness belongs to
        # `isc check` and must not make `isc new` look like a scaffold failure.
        return 0

    if command == "inspect":
        manifest = workspace.load(args.module_id)
        if emit:
            emit(manifest.to_dict())
        else:
            _print_tree(manifest.to_dict())
        return 0

    if command == "check":
        report = check_module(workspace.modules_dir / args.module_id)
        if emit:
            emit(report.to_dict())
        else:
            print(f"{args.module_id}: {'PASS' if report.passed else 'FAIL'}")
            for problem in report.problems:
                print(f"  problem: {problem}")
            for note in report.notes:
                print(f"  note   : {note}")
        return 0 if report.passed else 1

    if command == "build":
        manifest = workspace.load(args.module_id)
        target = args.target or manifest.build.targets[0]
        result = build_module(workspace, manifest, target, force=args.force,
                              timeout_seconds=args.timeout)
        if emit:
            emit(result.to_dict())
        else:
            print(f"{result.module_id} [{result.target}] {'cache hit' if result.cached else 'built'}")
            print(f"  fingerprint : {result.fingerprint}")
            print(f"  binary      : {result.binary}")
            if result.log_path:
                print(f"  log         : {result.log_path}")
        return 0

    if command == "run":
        manifest = workspace.load(args.module_id)
        target = args.target or manifest.build.targets[0]
        result = run_module(
            workspace, args.module_id, args.input, target,
            device_index=args.device, output_path=args.output,
            force_build=args.force_build, timeout_seconds=args.timeout,
            skip_validation=args.no_validate,
        )
        if emit:
            emit(result.to_dict())
        else:
            _print_run(result)
        return 0 if result.ok else 1

    if command == "validate":
        result = revalidate(workspace, args.module_id, args.input, args.output, args.target,
                            device_index=args.device, timeout_seconds=args.timeout)
        if emit:
            emit(result.to_dict())
        else:
            print(f"{args.module_id}: {'PASS' if result.passed else 'FAIL'} ({result.mode})")
            print(f"  {result.detail}")
        return 0 if result.passed else 1

    if command == "records":
        records = record.read_all(workspace, args.module, args.limit)
        if args.csv:
            print(record.to_csv(records), end="")
        elif emit:
            emit(records)
        elif not records:
            print("no runs recorded yet")
        else:
            for entry in records:
                validation = (entry.get("validation") or {}).get("passed")
                metrics = entry.get("metrics") or {}
                print(f"{entry['run_id']}  {entry['module']:<10} {entry['target']:<7} "
                      f"{entry['status']:<9} validation={validation} "
                      f"{metrics.get('total_ms', '-')}ms")
        return 0

    if command == "show":
        entry = record.get(workspace, args.run_id)
        if entry is None:
            raise LookupError(f"unknown run: {args.run_id}")
        if emit:
            emit(entry)
        else:
            _print_tree(entry)
        return 0

    raise ValueError(f"unhandled command: {command}")


def _devices(backend_name: str):
    try:
        return get_backend(backend_name).devices()
    except Exception:
        return []


def _doctor(workspace: Workspace) -> dict[str, Any]:
    backends: dict[str, Any] = {}
    for name in backend_names():
        backend = get_backend(name)
        try:
            available, reason = backend.available()
        except Exception as exc:
            available, reason = False, str(exc)
        backends[name] = {
            "available": available, "reason": reason,
            "devices": [vars(device) for device in _devices(name)],
        }
    return {
        "workspace": str(workspace.root),
        "python": platform.python_version(),
        "backends": backends,
        "modules": workspace.module_ids(),
    }


def _print_run(result) -> None:
    print(f"{result.module} [{result.target}] {result.status}  run_id={result.run_id}")
    if result.device:
        print(f"  device     : [{result.device['index']}] {result.device['name']}"
              f"{'  (emulated)' if result.device.get('emulated') else ''}")
    if result.build:
        print(f"  build      : {'cache hit' if result.build.get('cached') else 'built'}"
              f"  {result.build.get('fingerprint', '')}")
    if result.output_path:
        print(f"  input      : {result.input_bytes} bytes  {result.input_path}")
        print(f"  output     : {result.output_bytes} bytes  {result.output_path}")
    for key, value in (result.metrics or {}).items():
        print(f"  {key:<11}: {value}")
    if result.validation:
        print(f"  validation : {'PASS' if result.validation.get('passed') else 'FAIL'}"
              f"  ({result.validation.get('mode')}) {result.validation.get('detail')}")
    if result.error:
        print(f"  error      : {result.error}", file=sys.stderr)


def _print_tree(data: Any, indent: int = 0) -> None:
    pad = "  " * indent
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)) and value:
                print(f"{pad}{key}:")
                _print_tree(value, indent + 1)
            else:
                print(f"{pad}{key}: {value}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                _print_tree(item, indent)
            else:
                print(f"{pad}- {item}")
    else:
        print(f"{pad}{data}")


def _json_printer(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str, ensure_ascii=False))


def _contract_summary(contract: dict[str, Any]) -> str:
    representation = contract.get("representation", "unspecified")
    formats = contract.get("formats")
    if formats == ["*"]:
        return f"{representation}, any file format"
    if formats:
        return f"{representation}, formats={','.join(str(item) for item in formats)}"
    if contract.get("format"):
        return f"{representation}, format={contract['format']}"
    return str(representation)


if __name__ == "__main__":
    raise SystemExit(main())
