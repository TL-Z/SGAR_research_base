"""Run the fixed synthetic Qwen retention comparison with exact validators."""

from __future__ import annotations

import argparse
import base64
import json
import struct
import sys
import time
import zlib
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sgar_mvp.scripts.check_model_health import (
    load_env_value,
    run_with_infrastructure_retries,
    write_json,
)
from sgar_mvp.scripts.run_bounded_candidate_health import EvidenceTransport, utc_now
from sgar_mvp.src.model_accounting import ModelCostPolicy, ModelPricingCatalog, RunCostLedger
from sgar_mvp.src.model_transport import create_model_transport_bundle
from sgar_mvp.src.pipeline_control import canonical_sha256


GENERALIST = ("model.qwen3_5_35b_a3b.v1", "qwen3.5-35b-a3b")
CODER = ("model.qwen3_coder_next.v1", "qwen3-coder-next")
VISION = ("model.qwen3_vl_235b_a22b_instruct.v1", "qwen3-vl-235b-a22b-instruct")


FONT = {
    "1": ("010", "110", "010", "010", "010", "010", "111"),
    "2": ("110", "001", "001", "010", "100", "100", "111"),
    "3": ("110", "001", "001", "010", "001", "001", "110"),
    "4": ("101", "101", "101", "111", "001", "001", "001"),
    "7": ("111", "001", "001", "010", "010", "100", "100"),
    "8": ("010", "101", "101", "010", "101", "101", "010"),
    "9": ("010", "101", "101", "011", "001", "001", "010"),
    "A": ("010", "101", "101", "111", "101", "101", "101"),
    "B": ("110", "101", "101", "110", "101", "101", "110"),
    "C": ("011", "100", "100", "100", "100", "100", "011"),
    "D": ("110", "101", "101", "101", "101", "101", "110"),
    "E": ("111", "100", "100", "110", "100", "100", "111"),
    "F": ("111", "100", "100", "110", "100", "100", "100"),
    "G": ("011", "100", "100", "101", "101", "101", "011"),
    "H": ("101", "101", "101", "111", "101", "101", "101"),
    "I": ("111", "010", "010", "010", "010", "010", "111"),
    "L": ("100", "100", "100", "100", "100", "100", "111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("1001", "1101", "1101", "1011", "1011", "1001", "1001"),
    "O": ("010", "101", "101", "101", "101", "101", "010"),
    "P": ("110", "101", "101", "110", "100", "100", "100"),
    "Q": ("010", "101", "101", "101", "111", "011", "001"),
    "R": ("110", "101", "101", "110", "101", "101", "101"),
    "T": ("111", "010", "010", "010", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "101", "101", "010"),
    "X": ("101", "101", "101", "010", "101", "101", "101"),
    "Y": ("101", "101", "101", "010", "010", "010", "010"),
    " ": ("0", "0", "0", "0", "0", "0", "0"),
}


class Canvas:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.pixels = bytearray(b"\xff\xff\xff" * width * height)

    def rect(self, left: int, top: int, right: int, bottom: int, color: tuple[int, int, int]) -> None:
        for y_coord in range(max(0, top), min(self.height, bottom)):
            for x_coord in range(max(0, left), min(self.width, right)):
                offset = (y_coord * self.width + x_coord) * 3
                self.pixels[offset:offset + 3] = bytes(color)

    def line(self, left: int, top: int, right: int, bottom: int, color: tuple[int, int, int], width: int = 2) -> None:
        if top == bottom:
            self.rect(left, top, right, top + width, color)
        elif left == right:
            self.rect(left, top, left + width, bottom, color)

    def text(self, left: int, top: int, value: str, scale: int = 5) -> None:
        cursor = left
        for character in value.upper():
            glyph = FONT[character]
            glyph_width = len(glyph[0])
            for row, bits in enumerate(glyph):
                for column, bit in enumerate(bits):
                    if bit == "1":
                        self.rect(
                            cursor + column * scale,
                            top + row * scale,
                            cursor + (column + 1) * scale,
                            top + (row + 1) * scale,
                            (0, 0, 0),
                        )
            cursor += (glyph_width + 1) * scale

    def png(self) -> bytes:
        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack("!I", len(data)) + kind + data + struct.pack(
                "!I", zlib.crc32(kind + data) & 0xFFFFFFFF
            )

        rows = b"".join(
            b"\x00" + bytes(self.pixels[y_coord * self.width * 3:(y_coord + 1) * self.width * 3])
            for y_coord in range(self.height)
        )
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack("!2I5B", self.width, self.height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows))
            + chunk(b"IEND", b"")
        )


def make_images(output_dir: Path) -> list[dict[str, str]]:
    output_dir.mkdir(parents=True)
    images: list[tuple[str, str, str, Canvas]] = []

    first = Canvas(640, 320)
    first.text(40, 30, "ITEM QTY", 6)
    first.text(90, 130, "A", 7)
    first.text(470, 130, "2", 7)
    first.text(90, 225, "B", 7)
    first.text(470, 225, "7", 7)
    for x_coord in (25, 350, 610):
        first.line(x_coord, 20, x_coord, 305, (0, 0, 0), 4)
    for y_coord in (20, 105, 205, 305):
        first.line(25, y_coord, 614, y_coord, (0, 0, 0), 4)
    images.append(("ocr_table_1", "A=2;B=7", "Read the quantities for rows A and B. Use answer format A=<qty>;B=<qty>.", first))

    second = Canvas(640, 300)
    second.text(45, 50, "CODE X7", 7)
    second.text(45, 170, "TOTAL 19", 7)
    second.line(25, 140, 615, 140, (0, 0, 0), 4)
    images.append(("ocr_table_2", "X7;19", "Read the CODE and TOTAL. Use answer format <code>;<total>.", second))

    third = Canvas(640, 280)
    third.rect(90, 90, 220, 220, (220, 20, 30))
    third.rect(420, 90, 550, 220, (30, 80, 220))
    images.append(("spatial_1", "red_left_of_blue", "State the red square's horizontal relation to the blue square using red_left_of_blue or red_right_of_blue.", third))

    fourth = Canvas(640, 320)
    for left, top in ((60, 50), (250, 50), (440, 50)):
        fourth.rect(left, top, left + 90, top + 90, (220, 20, 30))
    for left in (155, 345):
        fourth.rect(left, 195, left + 90, 285, (30, 80, 220))
    images.append(("spatial_2", "red=3;blue=2", "Count red and blue squares. Use answer format red=<count>;blue=<count>.", fourth))

    fifth = Canvas(720, 280)
    for index, value in enumerate(("2", "4", "8")):
        left = 20 + index * 235
        fifth.line(left, 20, left, 250, (0, 0, 0), 4)
        fifth.line(left + 215, 20, left + 215, 250, (0, 0, 0), 4)
        fifth.line(left, 20, left + 219, 20, (0, 0, 0), 4)
        fifth.line(left, 246, left + 219, 246, (0, 0, 0), 4)
        fifth.text(left + 85, 90, value, 12)
    images.append(("sequence_1", "2,4,8", "Read the three panels from left to right. Return the comma-separated values with no spaces.", fifth))

    sixth = Canvas(840, 280)
    for index, value in enumerate(("LEFT", "MIDDLE", "RIGHT")):
        left = 15 + index * 275
        sixth.line(left, 20, left, 250, (0, 0, 0), 4)
        sixth.line(left + 255, 20, left + 255, 250, (0, 0, 0), 4)
        sixth.line(left, 20, left + 259, 20, (0, 0, 0), 4)
        sixth.line(left, 246, left + 259, 246, (0, 0, 0), 4)
        sixth.text(left + 25, 105, value, 5)
    images.append(("sequence_2", "LEFT,MIDDLE,RIGHT", "Read the three panel labels from left to right. Return uppercase comma-separated labels with no spaces.", sixth))

    result = []
    for name, answer, question, canvas in images:
        path = output_dir / f"{name}.png"
        path.write_bytes(canvas.png())
        result.append({"name": name, "answer": answer, "question": question, "path": str(path)})
    return result


def exact_text_sample(
    send: Callable[..., Any], model_id: str, prompt: Any, expected: str, source: str,
) -> str:
    if isinstance(prompt, str):
        prompt = "Reply with only the requested value and no explanation or formatting. " + prompt
    else:
        prompt = list(prompt)
        prompt[0] = dict(prompt[0])
        prompt[0]["text"] = "Reply with only the requested value and no explanation or formatting. " + str(prompt[0]["text"])
    response = send(
        model=model_id,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=4096,
    )
    value = str(response.choices[0].message.content or "").strip()
    if value != expected:
        raise ValueError("comparison_answer_mismatch")
    return value


def tool_sample(
    send: Callable[..., Any], model_id: str, prompt: str, tools: list[dict[str, Any]],
    expected_name: str, expected_arguments: dict[str, Any],
) -> dict[str, Any]:
    response = send(
        model=model_id,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=512,
        tools=tools,
        tool_choice="auto",
    )
    calls = response.choices[0].message.tool_calls or []
    if len(calls) != 1:
        raise ValueError("comparison_tool_call_count_invalid")
    call = calls[0]
    arguments = json.loads(call.function.arguments)
    if call.function.name != expected_name or arguments != expected_arguments:
        raise ValueError("comparison_tool_call_invalid")
    return {"tool": call.function.name, "arguments": arguments}


def invoke(
    *, name: str, family: str, model: tuple[str, str], operation: Callable[[str], Any],
    retries: int, delay: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    value, failure, attempts = run_with_infrastructure_retries(
        lambda: operation(model[1]), retries=retries, delay_seconds=delay,
    )
    result = {
        "sample": name,
        "family": family,
        "resource_id": model[0],
        "api_model_id": model[1],
        "attempt_count": attempts,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    if failure is not None:
        return {**result, "status": "failed", "failure": failure}
    return {**result, "status": "passed", "value": value}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=ROOT / "Pool/resources/json/models.json")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-delay-sec", type=float, default=10.0)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit("qwen_comparison_output_dir_already_exists")
    args.output_dir.mkdir(parents=True)
    key = load_env_value("LLM_API_KEY")
    if not key:
        raise SystemExit("qwen_comparison_api_key_missing")
    pricing = ModelPricingCatalog.from_manifest_file(
        args.catalog, required_model_refs=[GENERALIST[0], CODER[0], VISION[0]],
    )
    ledger = RunCostLedger(
        catalog=pricing,
        policy=ModelCostPolicy(
            mode="stop_after_limit", warning_usd=Decimal("10"), limit_usd=Decimal("100")
        ),
        output_dir=args.output_dir / "accounting",
    )
    bundle = create_model_transport_bundle(
        api_key=key,
        base_url=args.base_url,
        credential_environment_variable="LLM_API_KEY",
        timeout_seconds=90,
    )
    evidence = EvidenceTransport(
        base=bundle.sync,
        ledger=ledger,
        output_dir=args.output_dir,
        resource_by_api_id={model_id: resource_id for resource_id, model_id in (GENERALIST, CODER, VISION)},
    )
    send = evidence.port.send
    results: list[dict[str, Any]] = []

    strict_cases = [
        (
            "code_reasoning_1",
            "repository_code_reasoning",
            "A Python repository contains `def average(values): return sum(values) / (len(values) - 1)`. Return the exact corrected denominator expression.",
            "len(values)",
        ),
        (
            "code_reasoning_2",
            "repository_code_reasoning",
            "A JavaScript loop is `for (let i = 0; i <= items.length; i++)`. Return only the corrected comparison operator that prevents the out-of-bounds iteration.",
            "<",
        ),
        (
            "recovery_1",
            "failed_tool_recovery",
            "A prior read_file tool result is `FileNotFoundError: /work/src/app.py`. The repository root exists. Choose the next diagnostic label from inspect_parent_directory, retry_same_path, or claim_fixed. Do not claim another tool was already called.",
            "inspect_parent_directory",
        ),
        (
            "recovery_2",
            "failed_tool_recovery",
            "A prior test tool result is `AssertionError: expected 4, got 5 at counter.py:12`. Choose the next diagnostic label from inspect_counter_line_12, rerun_unchanged, or claim_fixed. Do not claim a fix was applied.",
            "inspect_counter_line_12",
        ),
    ]
    read_tools = [
        {"type": "function", "function": {"name": "read_file", "description": "Read one repository file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}},
        {"type": "function", "function": {"name": "write_file", "description": "Write one repository file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False}}},
    ]
    test_tools = [
        {"type": "function", "function": {"name": "run_test", "description": "Run one named test.", "parameters": {"type": "object", "properties": {"test": {"type": "string"}}, "required": ["test"], "additionalProperties": False}}},
        {"type": "function", "function": {"name": "apply_patch", "description": "Apply an already prepared patch.", "parameters": {"type": "object", "properties": {"patch": {"type": "string"}}, "required": ["patch"], "additionalProperties": False}}},
    ]
    tool_cases = [
        ("tool_selection_1", "Call the appropriate tool to inspect src/config.py before any edit.", read_tools, "read_file", {"path": "src/config.py"}),
        ("tool_selection_2", "Call the appropriate tool to execute tests/test_counter.py before changing code.", test_tools, "run_test", {"test": "tests/test_counter.py"}),
    ]
    for name, family, prompt, expected in strict_cases:
        for model in (GENERALIST, CODER):
            results.append(invoke(
                name=name,
                family=family,
                model=model,
                operation=lambda model_id, p=prompt, e=expected, n=name: exact_text_sample(send, model_id, p, e, n),
                retries=args.retries,
                delay=args.retry_delay_sec,
            ))
    for name, prompt, tools, expected_name, expected_arguments in tool_cases:
        for model in (GENERALIST, CODER):
            results.append(invoke(
                name=name,
                family="structured_multi_tool_selection",
                model=model,
                operation=lambda model_id, p=prompt, t=tools, en=expected_name, ea=expected_arguments: tool_sample(send, model_id, p, t, en, ea),
                retries=args.retries,
                delay=args.retry_delay_sec,
            ))

    images = make_images(args.output_dir / "images")
    vision_family = {
        "ocr_table_1": "dense_ocr_layout",
        "ocr_table_2": "dense_ocr_layout",
        "spatial_1": "spatial_grounding",
        "spatial_2": "spatial_grounding",
        "sequence_1": "multi_panel_sequence",
        "sequence_2": "multi_panel_sequence",
    }
    for image in images:
        raw = Path(image["path"]).read_bytes()
        prompt = [
            {"type": "text", "text": image["question"] + " Return it in the response schema."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(raw).decode("ascii")}},
        ]
        for model in (GENERALIST, VISION):
            results.append(invoke(
                name=image["name"],
                family=vision_family[image["name"]],
                model=model,
                operation=lambda model_id, p=prompt, e=image["answer"], n=image["name"]: exact_text_sample(send, model_id, p, e, n),
                retries=args.retries,
                delay=args.retry_delay_sec,
            ))
        ledger.write_summary()
        write_json(args.output_dir / "comparison.partial.json", results)

    ledger.write_summary()
    by_model = {
        resource_id: {
            "passed": sum(row["status"] == "passed" and row["resource_id"] == resource_id for row in results),
            "failed": sum(row["status"] != "passed" and row["resource_id"] == resource_id for row in results),
        }
        for resource_id, _model_id in (GENERALIST, CODER, VISION)
    }
    report = {
        "schema_version": "sgar-qwen-retention-comparison-v1",
        "generated_at": utc_now(),
        "endpoint_identity_sha256": bundle.endpoint_identity.identity_sha256,
        "normal_physical_sends": 24,
        "max_physical_sends": 24 * (args.retries + 1),
        "policy": {
            "temperature": 0,
            "max_tokens": 4096,
            "sdk_retries": 0,
            "infrastructure_retries": args.retries,
            "retry_delay_seconds": args.retry_delay_sec,
            "answer_validation": "exact_text_after_outer_whitespace",
        },
        "results": results,
        "summary": by_model,
        "request_count": evidence.sequence,
        "request_index": "request_index.json",
        "accounting_summary": "accounting/cost_summary.json",
    }
    report["report_sha256"] = canonical_sha256(report)
    write_json(args.output_dir / "request_index.json", evidence.requests)
    write_json(args.output_dir / "comparison.json", report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
