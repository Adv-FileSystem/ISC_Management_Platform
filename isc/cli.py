"""The `isc` command line.

Two arcs share one workspace:

    create -> check -> build -> run -> validate -> record -> reuse
    plan   -> bench  -> report

The first arc is about one module doing one job correctly. The second is about
deciding whether that job belongs on the device at all.
"""
from __future__ import annotations

import argparse
import json
import platform
import signal
import sys
from pathlib import Path
from typing import Any

from isc import bench as bench_module
from isc import benchdata
from isc import experiment as experiment_module
from isc import record
from isc import report as report_module
from isc.backends import backend_names, get_backend
from isc.check import check_module
from isc.devices import DeviceManager, WaitInfo, describe_wait
from isc.events import (
    describe_event, events_path, follow, format_clock, list_runs, read_events, summarize,
)
from isc.experiment import ExperimentError
from isc.lifecycle import revalidate, run as run_module, run_build
from isc.manifest import ManifestError
from isc.operations import (
    get_operation, get_profile, operations as catalog_operations,
    parse_parameter_assignments, profiles as catalog_profiles,
)
from isc.generator.scaffold import (
    create_hook_module, create_module, create_module_from_logic, is_logic_file,
    render_profile_starter,
)
from isc.workspace import Workspace


PROGRESS_HELP = "print each stage and its progress to stderr while it runs"


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
    commands.add_parser("profiles", help="list user-code hook profiles")

    describe = commands.add_parser("describe", help="describe one supported operation")
    describe.add_argument("operation_id")

    describe_profile = commands.add_parser(
        "describe-profile", help="describe one user-code hook profile")
    describe_profile.add_argument("profile_id")

    new = commands.add_parser("new", help="create a module from a supported operation")
    new.add_argument("module_id")
    new.add_argument("--operation", help="operation shown by `isc operations`")
    new.add_argument("--name")
    new.add_argument(
        "--set", action="append", dest="settings", metavar="NAME=VALUE",
        help="set an operation parameter (repeatable)",
    )

    template = commands.add_parser(
        "template", help="write a starter file for a hook profile (no module is created)")
    template.add_argument("profile_id")
    template.add_argument("--type", default="u32",
                          help="element type of the input values "
                               "(u8 u16 u32 u64 i8 i16 i32 i64 f32; default u32)")
    template.add_argument("--out-type", dest="out_type",
                          help="element type of the output values (map only)")
    template.add_argument("-o", "--output",
                          help="write the starter here (default: standard output)")

    new_custom = commands.add_parser(
        "new-custom", help="create a module with one constrained user-code hook")
    new_custom.add_argument("module_id", nargs="?",
                            help="module id (with --from: defaults to the logic file's name)")
    new_custom.add_argument("--profile", help="profile shown by `isc profiles`")
    new_custom.add_argument("--from", dest="logic_file", metavar="FILE",
                            help="make the module from a logic file you already wrote (.hpp)")
    new_custom.add_argument("--type", dest="in_type",
                            help="element type of the starter's input values (default u32)")
    new_custom.add_argument("--out-type", dest="out_type",
                            help="element type of the starter's output values (map only)")
    new_custom.add_argument("--name")

    inspect = commands.add_parser("inspect", help="show a module's manifest")
    inspect.add_argument("module_id")

    check = commands.add_parser("check", help="static checks, no device or toolchain needed")
    check.add_argument("module_id")

    build = commands.add_parser("build", help="build a module for a target")
    build.add_argument("module_id")
    build.add_argument("--target")
    build.add_argument("--force", action="store_true", help="ignore the build cache")
    build.add_argument("--timeout", type=float)
    build.add_argument("--progress", action="store_true", help=PROGRESS_HELP)

    run = commands.add_parser("run", help="build if needed, execute, validate and record")
    run.add_argument("module_id",
                     help="module id, or a logic file (.hpp) to make the module from first")
    run.add_argument("--input", required=True, help="input data file")
    run.add_argument("--output", help="where to write the result (default: artifacts/outputs/)")
    run.add_argument("--target")
    run.add_argument("--device", type=int, help="device index")
    run.add_argument("--force-build", action="store_true")
    run.add_argument("--no-validate", action="store_true")
    run.add_argument("--timeout", type=float)
    run.add_argument("--device-timeout", type=float, metavar="SEC",
                     help="give up after SEC seconds of waiting for the device (default: wait)")
    run.add_argument("--progress", action="store_true", help=PROGRESS_HELP)

    validate = commands.add_parser("validate", help="re-check an existing output against its input")
    validate.add_argument("module_id")
    validate.add_argument("--input", required=True)
    validate.add_argument("--output", required=True)
    validate.add_argument("--target", required=True, help="the target that produced --output")
    validate.add_argument("--device", type=int, help="device index")
    validate.add_argument("--timeout", type=float)

    plan = commands.add_parser(
        "plan", help="define a benchmark experiment (modules x input sizes)")
    plan.add_argument("experiment_id")
    plan.add_argument("--modules", required=True,
                      help="comma-separated module ids to compare")
    plan.add_argument("--sizes", required=True,
                      help="comma-separated input sizes, e.g. 4KB,1MB,16MB")
    plan.add_argument("--name")
    plan.add_argument("--target", default=experiment_module.DEFAULT_TARGET,
                      help="target to measure on (default: hw; *_emu is not "
                           "performance data)")
    plan.add_argument("--repeat", type=int, default=experiment_module.DEFAULT_REPEAT)
    plan.add_argument("--warmup", type=int, default=experiment_module.DEFAULT_WARMUP)
    plan.add_argument("--fill", default="text", choices=list(benchdata.FILL_MODES),
                      help="how generated input is filled (default: text)")

    commands.add_parser("experiments", help="list benchmark experiments")

    bench = commands.add_parser(
        "bench", help="measure the experiment matrix against a CPU baseline")
    bench.add_argument("experiment_id")
    bench.add_argument("--device", type=int, help="device index")
    bench.add_argument("--timeout", type=float)
    bench.add_argument("--device-timeout", type=float, metavar="SEC",
                       help="give up after SEC seconds of waiting for the device (default: wait)")
    bench.add_argument("--progress", action="store_true", help=PROGRESS_HELP)
    bench.add_argument("--dry-run", action="store_true",
                       help="show what would be measured, measure nothing")

    report = commands.add_parser(
        "report", help="speedup, crossover point and an acceleration verdict")
    report.add_argument("experiment_id")
    report.add_argument("--csv", action="store_true")
    report.add_argument("--file", help="analyse this benchmark file instead of the latest")

    records = commands.add_parser("records", help="list past runs")
    records.add_argument("--module")
    records.add_argument("--limit", type=int, default=20)
    records.add_argument("--csv", action="store_true")

    show = commands.add_parser("show", help="show one run record")
    show.add_argument("run_id")

    status = commands.add_parser("status", help="recent runs and where they are")
    status.add_argument("--limit", type=int, default=10)

    watch = commands.add_parser("watch", help="follow one run's events until it ends")
    watch.add_argument("run_id")
    return parser


def _cancel_on_signals() -> None:
    """Treat SIGTERM and SIGHUP (`kill`, a service stop, a closed terminal) like Ctrl-C.

    A long job's tools run in their own session, so a signal to `isc` alone would leave Vivado
    running for the rest of its 40 minutes with nobody waiting. As an interrupt, the job is
    cancelled cleanly: its tools are ended and the run is recorded as `cancelled`.
    """
    def interrupt(signum, frame) -> None:
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGHUP"):
        try:
            signal.signal(getattr(signal, name), interrupt)
        except (AttributeError, ValueError, OSError):
            pass                  # not on this platform, or not the main thread


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        _cancel_on_signals()      # a real command line; tests call main([...]) with arguments
    args = build_parser().parse_args(argv)
    workspace = Workspace.discover(args.workspace)
    emit = _json_printer if args.json else None
    try:
        return _dispatch(args, workspace, emit)
    except KeyboardInterrupt:
        try:
            print("interrupted", file=sys.stderr)
        except OSError:
            pass                  # the terminal may be gone (SIGHUP)
        return 130
    except (LookupError, ValueError, ManifestError, ExperimentError,
            FileExistsError, OSError) as exc:
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
        found = [_device_dict(device) for device in DeviceManager(workspace).devices()]
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

    if command == "profiles":
        entries = [profile.to_dict() for profile in catalog_profiles()]
        if emit:
            emit(entries)
        elif not entries:
            print("no hook profiles installed")
        else:
            print(f"{'PROFILE':<20} {'CATEGORY':<14} USER ENTRY POINT")
            for entry, profile in zip(entries, catalog_profiles()):
                print(f"{entry['id']:<20} {entry['category']:<14} "
                      f"{profile.hook_spec['signature']}")
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
                # Changing a build-scoped parameter costs a rebuild; say so
                # here rather than letting the user discover it by waiting.
                rebuild = (
                    "  [rebuild required]"
                    if operation.parameter_scope(name) == "build" else ""
                )
                print(f"  {name:<12} {spec.get('type', 'string')}{default}{rebuild}")
                if spec.get("description"):
                    print(f"               {spec['description']}")
            # 실험을 계획하기 전에 알아야 한다: 기준선이 없으면 speedup 이 나오지
            # 않고, equivalence 가 exact 가 아니면 정확성 대조에 쓸 수 없다.
            if not operation.has_baseline:
                print("baseline    : none — `isc bench` cannot compute a speedup")
            else:
                comparable = ("correctness comparable"
                              if operation.baseline_equivalence == "exact"
                              else "performance comparison only")
                needs = (f", needs `{operation.baseline_requires}`"
                         if operation.baseline_requires else "")
                print(f"baseline    : {operation.baseline_description or 'CPU reference'} "
                      f"({comparable}{needs})")
            print("example     :")
            print(f"  isc new mymodule --operation {operation.id}")
        return 0

    if command == "describe-profile":
        profile = get_profile(args.profile_id)
        if emit:
            emit({**profile.to_dict(), "hook": profile.hook_spec})
        else:
            print(f"profile     : {profile.id}")
            print(f"name        : {profile.name}")
            print(f"category    : {profile.category}")
            print(f"description : {profile.description}")
            print(f"input       : {_contract_summary(profile.input_contract)}")
            print(f"output      : {_contract_summary(profile.output_contract)}")
            print(f"user hook   : {profile.hook_spec['signature']}")
            print(f"types       : {', '.join(profile.hook_spec['element_types'])}")
            if args.profile_id != profile.id:
                print(f"note        : {args.profile_id} is an alias of {profile.id}")
            print("example     :")
            print(f"  isc template {profile.id} -o my_logic.hpp")
            print("  isc run my_logic.hpp --input data.bin")
        return 0

    if command == "template":
        profile = get_profile(args.profile_id)
        text = render_profile_starter(profile, args.type, args.out_type)
        if args.output:
            target = Path(args.output)
            if target.exists():
                raise FileExistsError(f"{target} already exists; choose another path or remove it")
            target.write_text(text)
            if emit:
                emit({"profile": profile.id, "written": str(target)})
            else:
                print(f"wrote {target}")
                print(f"next: edit it, then: isc run {target} --input <file>")
        elif emit:
            emit({"profile": profile.id, "text": text})
        else:
            print(text, end="")
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

    if command == "new-custom":
        logic = None
        if args.logic_file:
            if args.in_type or args.out_type:
                raise ValueError(
                    "--type and --out-type are read from the logic file's signature; "
                    "drop them when using --from"
                )
            logic = create_module_from_logic(
                workspace, args.logic_file, module_id=args.module_id,
                profile_id=args.profile, name=args.name,
            )
            path = logic.path
        elif args.module_id and args.profile:
            path = create_hook_module(
                workspace, args.module_id, profile_id=args.profile, name=args.name,
                in_type=args.in_type or "u32", out_type=args.out_type,
            )
        else:
            raise ValueError(
                "new-custom needs <module_id> and --profile, or --from <logic file>"
            )
        report = check_module(path)
        if emit:
            payload = {"created": str(path), "check": report.to_dict()}
            if logic:
                payload["logic"] = logic.to_dict()
            emit(payload)
        else:
            print(f"{logic.action if logic else 'created'} {path}")
            for item in sorted(p.relative_to(path) for p in path.rglob("*") if p.is_file()):
                print(f"  {item}")
            if not report.passed:
                print("setup required before build:")
                for problem in report.problems:
                    print(f"  {problem}")
            if logic:
                print(f"next: isc run {args.logic_file} --input <파일>")
            else:
                print(f"next: edit {path / 'logic.hpp'}")
                print(f"then: isc check {path.name}")
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
            for warning in report.warnings:
                print(f"  warning: {warning}")
            for note in report.notes:
                print(f"  note   : {note}")
        return 0 if report.passed else 1

    if command == "build":
        manifest = workspace.load(args.module_id)
        target = args.target or manifest.build.targets[0]
        result = run_build(workspace, manifest, target, force=args.force,
                           timeout_seconds=args.timeout,
                           on_event=_echo_event if args.progress else None)
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
        module_id, logic = args.module_id, None
        if is_logic_file(module_id):
            # 로직 파일이 원본이다: 모듈을 만들거나 갱신한다.
            logic = create_module_from_logic(workspace, module_id)
            module_id = logic.module_id
            if not emit:
                _print_logic(logic)  # 빌드는 수십 분 걸릴 수 있으니 시작 전에 먼저 알린다
        # 모듈 id 로 실행하든 로직 파일로 실행하든 같은 정적 검사를 통과해야 빌드가 시작된다.
        # run_module() 도 같은 검사를 하지만, 경고를 긴 빌드 전에 보여 주려고 여기서 먼저 한다.
        manifest = workspace.load(module_id)
        report = check_module(manifest.directory)
        if not report.passed:
            if emit:
                emit({**({"logic": logic.to_dict()} if logic else {}), "check": report.to_dict()})
            else:
                print(f"{module_id}: FAIL")
                for problem in report.problems:
                    print(f"  problem: {problem}")
            return 1
        if not emit:
            for warning in report.warnings:
                print(f"warning: {warning}", flush=True)
        target = args.target or manifest.build.targets[0]
        result = run_module(
            workspace, module_id, args.input, target,
            device_index=args.device, output_path=args.output,
            force_build=args.force_build, timeout_seconds=args.timeout,
            skip_validation=args.no_validate,
            device_timeout_s=args.device_timeout, on_wait=_announce_wait,
            on_event=_echo_event if args.progress else None,
        )
        if emit:
            payload = result.to_dict()
            if logic:
                payload["logic"] = {**logic.to_dict(), "warnings": report.warnings}
            elif report.warnings:
                payload["warnings"] = report.warnings
            emit(payload)
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

    if command == "plan":
        path = experiment_module.create(
            workspace, args.experiment_id,
            modules=_split(args.modules), sizes=_split(args.sizes),
            name=args.name, target=args.target, repeat=args.repeat,
            warmup=args.warmup, input_fill=args.fill,
        )
        definition = experiment_module.Experiment.load(path)
        if emit:
            emit({"created": str(path), "experiment": definition.to_dict()})
        else:
            print(f"created {path / experiment_module.EXPERIMENT_NAME}")
            print(f"  modules : {', '.join(definition.modules)}")
            print(f"  sizes   : "
                  f"{', '.join(benchdata.format_size(s) for s in definition.input_sizes)}")
            print(f"  target  : {definition.target}"
                  f"{'' if definition.is_performance_data else '  (emulation: not performance data)'}")
            print(f"  measure : warmup={definition.warmup} repeat={definition.repeat} "
                  f"fill={definition.input_fill}")
            print(f"\nnext: isc bench {definition.id}")
        return 0

    if command == "experiments":
        entries = []
        for experiment_id in workspace.experiment_ids():
            try:
                definition = experiment_module.load(workspace, experiment_id)
                entries.append(definition.to_dict())
            except (ExperimentError, LookupError) as exc:
                entries.append({"id": experiment_id, "error": str(exc)})
        if emit:
            emit(entries)
        elif not entries:
            print(f"no experiments in {workspace.experiments_dir}")
        else:
            for entry in entries:
                if "error" in entry:
                    print(f"{entry['id']:<16} INVALID: {entry['error']}")
                else:
                    print(f"{entry['id']:<16} {entry['target']:<7} "
                          f"{len(entry['modules'])} modules x "
                          f"{len(entry['input_sizes'])} sizes  {entry['name']}")
        return 0

    if command == "bench":
        definition = experiment_module.load(workspace, args.experiment_id)
        if args.dry_run:
            cells = bench_module.plan(workspace, definition)
            if emit:
                emit({"experiment": definition.to_dict(), "cells": cells})
            else:
                print(f"{definition.id}: {len(cells)} cells, "
                      f"{sum(cell['iterations'] for cell in cells)} device executions")
                for cell in cells:
                    print(f"  {cell['module']:<14}{cell['input']:>8}  "
                          f"x{cell['iterations']}  [{cell['target']}]")
            return 0
        result = bench_module.run(
            workspace, definition, device_index=args.device,
            timeout_seconds=args.timeout, device_timeout_s=args.device_timeout,
            report=None if emit else lambda message: print(message),
            on_event=_echo_event if args.progress else None,
        )
        failed = [cell for cell in result.cells if cell.error]
        if emit:
            emit(result.to_dict())
        else:
            print(f"\nwrote {result.path}")
            for cell in failed:
                print(f"  failed: {cell.module} "
                      f"{benchdata.format_size(cell.input_bytes)}: {cell.error}",
                      file=sys.stderr)
            print(f"next: isc report {definition.id}")
        return 0 if not failed else 1

    if command == "report":
        source = (Path(args.file) if args.file
                  else bench_module.latest(workspace, args.experiment_id))
        analysis = report_module.analyse(source)
        if args.csv:
            print(report_module.to_csv(analysis), end="")
        elif emit:
            emit(analysis.to_dict())
        else:
            print(report_module.render(analysis), end="")
        return 0

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

    if command == "status":
        runs = list_runs(workspace, args.limit)
        if emit:
            emit([summary.to_dict() for summary in runs])
        elif not runs:
            print(f"no runs yet in {workspace.events_dir}")
        else:
            print(f"{'RUN':<26}{'KIND':<7}{'MODULE':<14}{'TARGET':<8}{'STATE':<11}"
                  f"{'STAGE':<12}{'ELAPSED':<9}")
            for summary in runs:
                print((f"{summary.run_id:<26}{summary.kind:<7}{summary.module[:13]:<14}"
                       f"{summary.target:<8}{summary.state:<11}{summary.stage or '-':<12}"
                       f"{format_clock(summary.elapsed_s):<9}{summary.progress}").rstrip())
        return 0

    if command == "watch":
        path = events_path(workspace, args.run_id)
        if not path.exists():
            raise LookupError(f"unknown run: {args.run_id} (no events at {path})")
        try:
            for event in follow(path):
                if emit:
                    print(json.dumps(event, ensure_ascii=False), flush=True)
                    continue
                line = describe_event(event)
                if line is not None:
                    print(f"{str(event.get('ts', ''))[11:19]}  {line}", flush=True)
        except KeyboardInterrupt:
            return 130
        summary = summarize(read_events(path))
        if summary is not None and summary.state == "lost" and not emit:
            print(f"lost: no new events and the process (pid {summary.pid}) is gone")
        return 0 if summary is not None and summary.state == "succeeded" else 1

    raise ValueError(f"unhandled command: {command}")


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _device_dict(device) -> dict[str, Any]:
    """A listed device as JSON: its fields plus the id events and metrics use for it."""
    return {**vars(device), "id": device.id}


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
            "devices": [_device_dict(device) for device in DeviceManager(workspace).devices(name)],
        }
    return {
        "workspace": str(workspace.root),
        "python": platform.python_version(),
        "backends": backends,
        "modules": workspace.module_ids(),
    }


def _echo_event(event: dict[str, Any]) -> None:
    """--progress: an event as one line on stderr, so stdout and --json stay what they were."""
    line = describe_event(event)
    if line is not None:
        print(line, file=sys.stderr, flush=True)


def _announce_wait(info: WaitInfo) -> None:
    """The device is reserved by someone else: say by whom, on stderr so --json stays clean."""
    print(describe_wait(info), file=sys.stderr, flush=True)


def _print_logic(logic) -> None:
    print(f"{logic.module_id}: {logic.action} {logic.path} (profile {logic.profile_id})",
          flush=True)


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
