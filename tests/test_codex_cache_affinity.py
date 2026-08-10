#!/usr/bin/env python3
"""Measure Codex prompt-cache hits through a LiteLLM session-affinity route."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class TurnUsage:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int


@dataclass
class TurnMeasurement:
    round_number: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    total_input_cache_rate: float
    previous_prefix_coverage: float | None


@dataclass
class ScenarioResult:
    requested_payload_tokens: int
    estimated_payload_tokens: int
    payload_characters: int
    thread_id: str
    turns: list[TurnMeasurement]
    final_turn_cache_rate: float
    steady_state_cache_rate: float
    aggregate_cache_rate: float
    prefix_continuity_rate: float
    backend_affinity_verified: bool | None
    backend_record_count: int
    backend_unique_model_ids: int | None
    backend_transitions: int | None
    requested_rounds: int
    attempted_rounds: int
    completed_rounds: int
    task_completed: bool
    failure_round: int | None
    failure_message: str | None
    passed: bool
    marker: str
    started_at: str
    completed_at: str


TASK_ROUNDS = [
    "Extract the five most important requirements and unresolved assumptions.",
    "Identify technical, security, and operational risks; rank the top five.",
    "Propose the target architecture and explain the main component boundaries.",
    "Define identity, access control, secret handling, and audit requirements.",
    "Define reliability objectives, failure modes, retries, and recovery behavior.",
    "Analyze cost drivers and propose measurable cost controls.",
    "Create a focused functional, integration, load, and failure test strategy.",
    "Create a staged rollout, rollback, and production monitoring plan.",
    "Review prior answers for contradictions, missing dependencies, and weak evidence.",
    "Produce the final release-readiness checklist with owners and acceptance criteria.",
]


class CodexCommandError(RuntimeError):
    def __init__(self, message: str, output: str, events: list[dict[str, Any]]):
        super().__init__(message)
        self.output = output
        self.events = events


def parse_positive_sizes(value: str) -> list[int]:
    sizes: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        size = int(item)
        if size <= 0:
            raise argparse.ArgumentTypeError("input token sizes must be positive")
        if size not in sizes:
            sizes.append(size)
    if not sizes:
        raise argparse.ArgumentTypeError("at least one input token size is required")
    return sizes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Simulate one multi-round Codex engineering task per input size and "
            "compare prompt-cache continuity across LiteLLM routing modes."
        )
    )
    parser.add_argument("--model", default="gpt-5.6-terra", help="LiteLLM model group")
    parser.add_argument(
        "--sizes",
        type=parse_positive_sizes,
        default=parse_positive_sizes("1024,4096,8192"),
        help="Comma-separated approximate payload token sizes",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=10,
        help="Conversation rounds in each simulated task (default: 10)",
    )
    parser.add_argument(
        "--routing-mode",
        choices=("simple-shuffle", "affinity"),
        default="affinity",
        help="Label and pass criteria for the active LiteLLM router configuration",
    )
    parser.add_argument(
        "--provider",
        default="litellm",
        help="Codex model_provider name from config.toml",
    )
    parser.add_argument(
        "--api-key-env",
        default="LITELLM_API_KEY",
        help="Environment variable referenced by the Codex provider",
    )
    parser.add_argument(
        "--codex-command",
        default="codex",
        help="Codex executable name or path",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Timeout in seconds for each Codex turn",
    )
    parser.add_argument(
        "--chars-per-token",
        type=float,
        default=4.0,
        help="Approximation used only to construct payloads; actual usage comes from Codex",
    )
    parser.add_argument(
        "--min-final-hit-rate",
        "--min-hit-rate",
        dest="min_final_hit_rate",
        type=float,
        default=0.85,
        help="Minimum final-round cached/input ratio required in affinity mode",
    )
    parser.add_argument(
        "--prefix-match-threshold",
        type=float,
        default=0.90,
        help="Coverage required for a round to count as preserving the prior prefix",
    )
    parser.add_argument(
        "--min-prefix-continuity",
        type=float,
        default=0.90,
        help="Minimum fraction of resumed rounds preserving the prior prefix",
    )
    parser.add_argument(
        "--steady-state-turns",
        type=int,
        default=3,
        help="Number of final rounds averaged for steady-state cache rate",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        help="Optional path for machine-readable results",
    )
    parser.add_argument(
        "--compare-json",
        type=Path,
        help="Optional result JSON from the other routing mode to compare",
    )
    parser.add_argument(
        "--keep-jsonl",
        type=Path,
        help="Optional directory for raw, credential-free Codex JSONL output",
    )
    parser.add_argument(
        "--verify-backend-affinity",
        action="store_true",
        help=(
            "Query LiteLLM Spend Logs through kubectl, correlate all task rounds by "
            "cache_key, and report model_id transitions"
        ),
    )
    parser.add_argument("--kube-namespace", default="litellm")
    parser.add_argument("--kube-deployment", default="litellm-mi-proxy")
    parser.add_argument(
        "--spend-log-wait",
        type=int,
        default=30,
        help="Seconds to wait for both Spend Log rows when backend verification is enabled",
    )
    return parser.parse_args()


def build_payload(target_tokens: int, chars_per_token: float, marker: str) -> str:
    target_characters = max(1, round(target_tokens * chars_per_token))
    lines: list[str] = []
    index = 0
    while sum(len(line) + 1 for line in lines) < target_characters:
        lines.append(
            f"{marker}-segment-{index:06d}: stable LiteLLM session affinity cache data."
        )
        index += 1
    payload = "\n".join(lines)
    return payload[:target_characters]


def extract_json_events(output: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def extract_thread_id(events: list[dict[str, Any]]) -> str:
    for event in events:
        if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
            return event["thread_id"]
    raise RuntimeError("Codex output did not contain thread.started/thread_id")


def extract_usage(events: list[dict[str, Any]]) -> TurnUsage:
    for event in reversed(events):
        if event.get("type") != "turn.completed":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        return TurnUsage(
            input_tokens=int(usage.get("input_tokens") or 0),
            cached_input_tokens=int(usage.get("cached_input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        )
    errors = [
        item.get("message", "unknown Codex error")
        for event in events
        if event.get("type") == "item.completed"
        and isinstance((item := event.get("item")), dict)
        and item.get("type") == "error"
    ]
    suffix = f": {'; '.join(errors)}" if errors else ""
    raise RuntimeError(f"Codex output did not contain turn.completed usage{suffix}")


def run_codex(command: list[str], timeout: int) -> tuple[str, list[dict[str, Any]]]:
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    output = completed.stdout
    events = extract_json_events(output)
    if completed.returncode != 0:
        tail = "\n".join(output.splitlines()[-20:])
        raise CodexCommandError(
            f"Codex exited with {completed.returncode}: {' '.join(command[:4])}\n{tail}",
            output,
            events,
        )
    return output, events


def task_round_prompt(round_number: int, marker: str) -> str:
    task = TASK_ROUNDS[(round_number - 1) % len(TASK_ROUNDS)]
    cycle = (round_number - 1) // len(TASK_ROUNDS) + 1
    return (
        f"Continue the same release-readiness task ({marker}), round {round_number}, "
        f"review cycle {cycle}. {task} Keep the answer under 100 words and finish "
        f"with ROUND-{round_number}-{marker}."
    )


def build_turn_measurements(usages: list[TurnUsage]) -> list[TurnMeasurement]:
    measurements: list[TurnMeasurement] = []
    for index, usage in enumerate(usages):
        total_rate = (
            usage.cached_input_tokens / usage.input_tokens
            if usage.input_tokens
            else 0.0
        )
        previous_coverage: float | None = None
        if index > 0 and usages[index - 1].input_tokens:
            previous_coverage = min(
                usage.cached_input_tokens / usages[index - 1].input_tokens,
                1.0,
            )
        measurements.append(
            TurnMeasurement(
                round_number=index + 1,
                input_tokens=usage.input_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                output_tokens=usage.output_tokens,
                total_input_cache_rate=total_rate,
                previous_prefix_coverage=previous_coverage,
            )
        )
    return measurements


def run_scenario(
    codex: str,
    provider: str,
    model: str,
    requested_tokens: int,
    rounds: int,
    chars_per_token: float,
    routing_mode: str,
    min_final_hit_rate: float,
    prefix_match_threshold: float,
    min_prefix_continuity: float,
    steady_state_turns: int,
    timeout: int,
    keep_jsonl: Path | None,
) -> ScenarioResult:
    started_at = datetime.now(timezone.utc).isoformat()
    marker = f"CACHE-{requested_tokens}-{uuid.uuid4().hex[:10]}"
    payload = build_payload(requested_tokens, chars_per_token, marker)
    estimated_payload_tokens = round(len(payload) / chars_per_token)
    warm_prompt = (
        "We are starting one continuous release-readiness engineering task. Treat "
        "the reference block as stable project context and retain it for every later "
        f"round. Task ID: {marker}.\n\nREFERENCE BLOCK:\n{payload}\n\n"
        + task_round_prompt(1, marker)
    )
    first_command = [
        codex,
        "exec",
        "-c",
        f'model_provider="{provider}"',
        "-m",
        model,
        "--sandbox",
        "read-only",
        "--json",
        warm_prompt,
    ]
    first_output, first_events = run_codex(first_command, timeout)
    thread_id = extract_thread_id(first_events)
    usages = [extract_usage(first_events)]
    outputs = [first_output]
    attempted_rounds = 1
    failure_round: int | None = None
    failure_message: str | None = None

    for round_number in range(2, rounds + 1):
        command = [
            codex,
            "exec",
            "resume",
            thread_id,
            "-c",
            f'model_provider="{provider}"',
            "-m",
            model,
            "--json",
            task_round_prompt(round_number, marker),
        ]
        attempted_rounds = round_number
        try:
            output, events = run_codex(command, timeout)
        except CodexCommandError as exc:
            outputs.append(exc.output)
            failure_round = round_number
            error_events = [
                str(event.get("message"))
                for event in exc.events
                if event.get("type") in {"error", "turn.failed"}
                and event.get("message")
            ]
            failure_message = (
                error_events[-1][:1000]
                if error_events
                else str(exc).splitlines()[-1][:1000]
            )
            break
        usages.append(extract_usage(events))
        outputs.append(output)
    completed_at = datetime.now(timezone.utc).isoformat()

    if keep_jsonl is not None:
        keep_jsonl.mkdir(parents=True, exist_ok=True)
        for round_number, output in enumerate(outputs, 1):
            (keep_jsonl / f"{requested_tokens}-round{round_number}.jsonl").write_text(
                output, encoding="utf-8"
            )

    measurements = build_turn_measurements(usages)
    resumed_turns = measurements[1:]
    steady_count = min(steady_state_turns, len(resumed_turns))
    steady_turns = resumed_turns[-steady_count:]
    steady_rate = (
        sum(turn.total_input_cache_rate for turn in steady_turns) / steady_count
        if steady_count
        else 0.0
    )
    total_resumed_input = sum(turn.input_tokens for turn in resumed_turns)
    aggregate_rate = (
        sum(turn.cached_input_tokens for turn in resumed_turns) / total_resumed_input
        if total_resumed_input
        else 0.0
    )
    continuity_rate = (
        sum(
            1
            for turn in resumed_turns
            if (turn.previous_prefix_coverage or 0.0) >= prefix_match_threshold
        )
        / len(resumed_turns)
        if resumed_turns
        else 0.0
    )
    final_rate = measurements[-1].total_input_cache_rate
    task_completed = len(measurements) == rounds
    metric_passed = task_completed and (
        final_rate >= min_final_hit_rate
        and continuity_rate >= min_prefix_continuity
    )
    return ScenarioResult(
        requested_payload_tokens=requested_tokens,
        estimated_payload_tokens=estimated_payload_tokens,
        payload_characters=len(payload),
        thread_id=thread_id,
        turns=measurements,
        final_turn_cache_rate=final_rate,
        steady_state_cache_rate=steady_rate,
        aggregate_cache_rate=aggregate_rate,
        prefix_continuity_rate=continuity_rate,
        backend_affinity_verified=None,
        backend_record_count=0,
        backend_unique_model_ids=None,
        backend_transitions=None,
        requested_rounds=rounds,
        attempted_rounds=attempted_rounds,
        completed_rounds=len(measurements),
        task_completed=task_completed,
        failure_round=failure_round,
        failure_message=failure_message,
        passed=metric_passed if routing_mode == "affinity" else True,
        marker=marker,
        started_at=started_at,
        completed_at=completed_at,
    )


def query_backend_affinity(
    result: ScenarioResult,
    model: str,
    namespace: str,
    deployment: str,
    expected_rounds: int,
    wait_seconds: int,
) -> tuple[bool, int, int | None, int | None]:
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        raise RuntimeError("kubectl is required for --verify-backend-affinity")
    query_script = r'''
import asyncio
import json
import os
from datetime import datetime
from prisma import Prisma

async def main():
    db = Prisma(datasource={"url": os.environ["DATABASE_URL"]})
    await db.connect()
    rows = await db.litellm_spendlogs.find_many(
        where={
            "model_group": os.environ["AFFINITY_MODEL"],
            "startTime": {
                "gte": datetime.fromisoformat(os.environ["AFFINITY_START"]),
                "lte": datetime.fromisoformat(os.environ["AFFINITY_END"]),
            },
        },
        order={"startTime": "asc"},
    )
    groups = {}
    for row in rows:
        if row.cache_key:
            groups.setdefault(row.cache_key, []).append(row)
    expected = int(os.environ["AFFINITY_EXPECTED_ROUNDS"])
    candidates = [group for group in groups.values() if len(group) >= expected]
    selected = max(candidates, key=lambda group: group[-1].startTime) if candidates else []
    model_ids = {row.model_id for row in selected if row.model_id}
    sequence = [row.model_id for row in selected if row.model_id]
    transitions = sum(
        1 for previous, current in zip(sequence, sequence[1:]) if previous != current
    )
    print(json.dumps({
        "record_count": len(selected),
        "unique_model_ids": len(model_ids),
        "transitions": transitions,
        "complete": len(selected) >= expected,
        "verified": len(selected) >= expected and len(model_ids) == 1,
    }))
    await db.disconnect()

asyncio.run(main())
'''
    deadline = time.monotonic() + max(0, wait_seconds)
    last_data: dict[str, Any] = {}
    while True:
        env_args = [
            "AFFINITY_START=" + result.started_at,
            "AFFINITY_END=" + result.completed_at,
            "AFFINITY_MODEL=" + model,
            "AFFINITY_EXPECTED_ROUNDS=" + str(expected_rounds),
        ]
        completed = subprocess.run(
            [
                kubectl,
                "exec",
                "-i",
                "-n",
                namespace,
                f"deployment/{deployment}",
                "--",
                "env",
                *env_args,
                "python",
                "-",
            ],
            input=query_script,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        for line in reversed(completed.stdout.splitlines()):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and "verified" in parsed:
                last_data = parsed
                break
        if last_data.get("complete") or time.monotonic() >= deadline:
            break
        time.sleep(2)
    if not last_data:
        raise RuntimeError(
            "Could not parse Spend Logs affinity result from kubectl output:\n"
            + completed.stdout[-2000:]
        )
    return (
        bool(last_data.get("verified")),
        int(last_data.get("record_count") or 0),
        int(last_data["unique_model_ids"])
        if last_data.get("unique_model_ids") is not None
        else None,
        int(last_data["transitions"])
        if last_data.get("transitions") is not None
        else None,
    )


def print_turn_curve(result: ScenarioResult) -> None:
    print(f"\nPayload {result.requested_payload_tokens} token task curve:")
    print("round  input_tokens  cached_tokens  total_rate  previous_prefix")
    print("-----  ------------  -------------  ----------  ---------------")
    for turn in result.turns:
        previous = (
            f"{turn.previous_prefix_coverage:.2%}"
            if turn.previous_prefix_coverage is not None
            else "WARM"
        )
        print(
            f"{turn.round_number:<5}  {turn.input_tokens:<12}  "
            f"{turn.cached_input_tokens:<13}  "
            f"{turn.total_input_cache_rate:<10.2%}  {previous}"
        )
    if not result.task_completed:
        print(
            f"Task interrupted at round {result.failure_round}: "
            f"{result.failure_message or 'unknown error'}"
        )


def print_results(results: list[ScenarioResult], routing_mode: str) -> None:
    headers = (
        "payload",
        "rounds",
        "final_rate",
        "steady_rate",
        "aggregate",
        "continuity",
        "backends",
        "transitions",
        "status",
    )
    rows = [
        (
            str(result.requested_payload_tokens),
            f"{result.completed_rounds}/{result.requested_rounds}",
            f"{result.final_turn_cache_rate:.2%}",
            f"{result.steady_state_cache_rate:.2%}",
            f"{result.aggregate_cache_rate:.2%}",
            f"{result.prefix_continuity_rate:.2%}",
            str(result.backend_unique_model_ids or "SKIP"),
            str(result.backend_transitions if result.backend_transitions is not None else "SKIP"),
            (
                "INTERRUPTED"
                if not result.task_completed
                else "MEASURED"
                if routing_mode == "simple-shuffle"
                else "PASS"
                if result.passed
                else "FAIL"
            ),
        )
        for result in results
    ]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]
    print("  ".join(value.ljust(widths[index]) for index, value in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def print_comparison(current: dict[str, Any], baseline_path: Path) -> None:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_by_size = {
        int(item["requested_payload_tokens"]): item
        for item in baseline.get("results", [])
    }
    print(
        f"\nComparison: {baseline.get('routing_mode', 'unknown')} -> "
        f"{current.get('routing_mode', 'unknown')}"
    )
    print("payload  final_delta  steady_delta  continuity_delta  backend_delta")
    print("-------  -----------  ------------  ----------------  -------------")
    for item in current.get("results", []):
        size = int(item["requested_payload_tokens"])
        other = baseline_by_size.get(size)
        if other is None:
            continue
        backend_current = item.get("backend_unique_model_ids")
        backend_baseline = other.get("backend_unique_model_ids")
        backend_delta = (
            str(int(backend_current) - int(backend_baseline))
            if backend_current is not None and backend_baseline is not None
            else "N/A"
        )
        print(
            f"{size:<7}  "
            f"{item['final_turn_cache_rate'] - other['final_turn_cache_rate']:+.2%}      "
            f"{item['steady_state_cache_rate'] - other['steady_state_cache_rate']:+.2%}       "
            f"{item['prefix_continuity_rate'] - other['prefix_continuity_rate']:+.2%}           "
            f"{backend_delta}"
        )


def main() -> int:
    args = parse_args()
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")
    if args.chars_per_token <= 0:
        raise ValueError("--chars-per-token must be positive")
    if args.rounds < 2:
        raise ValueError("--rounds must be at least 2")
    if args.steady_state_turns <= 0:
        raise ValueError("--steady-state-turns must be positive")
    if args.spend_log_wait < 0:
        raise ValueError("--spend-log-wait cannot be negative")
    for name in (
        "min_final_hit_rate",
        "prefix_match_threshold",
        "min_prefix_continuity",
    ):
        if not 0 <= getattr(args, name) <= 1:
            raise ValueError(f"--{name.replace('_', '-')} must be between 0 and 1")
    if not os.environ.get(args.api_key_env):
        raise RuntimeError(
            f"{args.api_key_env} is not set. Set a LiteLLM Virtual Key before running."
        )

    codex = shutil.which(args.codex_command)
    if codex is None:
        candidate = Path(args.codex_command)
        if not candidate.exists():
            raise RuntimeError(f"Codex executable not found: {args.codex_command}")
        codex = str(candidate)

    started_at = datetime.now(timezone.utc).isoformat()
    results: list[ScenarioResult] = []
    print(f"Codex executable: {codex}")
    print(f"Provider/model: {args.provider}/{args.model}")
    print(f"Routing mode label: {args.routing_mode}")
    print(f"Rounds per task: {args.rounds}")
    print(f"Approximate payload sizes: {args.sizes}")
    print("Actual input and cached token counts are read from Codex turn.completed events.\n")

    for requested_tokens in args.sizes:
        print(f"Running payload size {requested_tokens} tokens...", flush=True)
        result = run_scenario(
            codex=codex,
            provider=args.provider,
            model=args.model,
            requested_tokens=requested_tokens,
            rounds=args.rounds,
            chars_per_token=args.chars_per_token,
            routing_mode=args.routing_mode,
            min_final_hit_rate=args.min_final_hit_rate,
            prefix_match_threshold=args.prefix_match_threshold,
            min_prefix_continuity=args.min_prefix_continuity,
            steady_state_turns=args.steady_state_turns,
            timeout=args.timeout,
            keep_jsonl=args.keep_jsonl,
        )
        if args.verify_backend_affinity:
            verified, record_count, unique_model_ids, transitions = query_backend_affinity(
                result=result,
                model=args.model,
                namespace=args.kube_namespace,
                deployment=args.kube_deployment,
                expected_rounds=result.attempted_rounds,
                wait_seconds=args.spend_log_wait,
            )
            result.backend_affinity_verified = verified
            result.backend_record_count = record_count
            result.backend_unique_model_ids = unique_model_ids
            result.backend_transitions = transitions
            if args.routing_mode == "affinity":
                result.passed = result.passed and verified
        results.append(result)
        print_turn_curve(result)

    print()
    print_results(results, args.routing_mode)

    output = {
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "provider": args.provider,
        "model": args.model,
        "routing_mode": args.routing_mode,
        "rounds": args.rounds,
        "minimum_final_hit_rate": args.min_final_hit_rate,
        "prefix_match_threshold": args.prefix_match_threshold,
        "minimum_prefix_continuity": args.min_prefix_continuity,
        "results": [asdict(result) for result in results],
    }
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nJSON results: {args.output_json}")
    if args.compare_json is not None:
        print_comparison(output, args.compare_json)

    failures = [result for result in results if not result.passed]
    if failures:
        print(
            "\nCache validation failed for payload sizes: "
            + ", ".join(str(result.requested_payload_tokens) for result in failures),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)