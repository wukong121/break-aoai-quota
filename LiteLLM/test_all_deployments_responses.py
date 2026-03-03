#!/usr/bin/env python3
"""Test all Azure OpenAI deployments via LiteLLM proxy.

Text models   → Responses API  (/v1/responses, /openai/v1/responses)
Image models  → Images API     (/v1/images/generations, /openai/deployments/{model}/images/generations)
"""
import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request

# Deployment names that should be tested via the Images API instead of Responses API
DEFAULT_IMAGE_DEPLOYMENTS = "gpt-image-1"


@dataclass
class TestCase:
    style: str
    deployment_name: str
    model_alias: str
    api_type: str = "responses"  # "responses" or "images"


@dataclass
class TestResult:
    case: TestCase
    ok: bool
    status_code: int
    output_text: str
    raw_response: str


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Test all deployments via Responses API and Images API routes.")
    parser.add_argument("--config", default=str(root / "azure-openai.json"), help="Path to azure-openai.json")
    parser.add_argument("--base-url", default="http://127.0.0.1:4000", help="LiteLLM proxy base URL")
    parser.add_argument("--api-key", default=None, help="LiteLLM master key; falls back to LITELLM_MASTER_KEY env")
    parser.add_argument("--prompt", default="请只回复: ok", help="Prompt used for text model tests")
    parser.add_argument("--image-prompt", default="a simple white circle on a solid black background",
                        help="Prompt used for image model tests")
    parser.add_argument("--image-size", default="1024x1024", help="Image size for generation (default: 1024x1024)")
    parser.add_argument("--max-output-tokens", type=int, default=128)
    parser.add_argument("--timeout", type=int, default=90, help="HTTP timeout in seconds (text models)")
    parser.add_argument("--image-timeout", type=int, default=180, help="HTTP timeout in seconds (image models, usually slower)")
    parser.add_argument("--model-prefix", default="aoai-", help="Prefix used to build LiteLLM model alias from deployment_name")
    parser.add_argument("--azure-api-version", default=None, help="Optional api-version for Azure-style route")
    parser.add_argument(
        "--skip-deployments",
        default="",
        help="Comma-separated deployment names to skip (default: none)",
    )
    parser.add_argument(
        "--image-deployments",
        default=DEFAULT_IMAGE_DEPLOYMENTS,
        help="Comma-separated deployment names that use Images API instead of Responses API (default: gpt-image-1)",
    )
    return parser.parse_args()


def load_deployments(config_path: Path) -> list[dict[str, Any]]:
    with config_path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    deployments = cfg.get("deployment_list")
    if not isinstance(deployments, list) or len(deployments) == 0:
        raise ValueError("deployment_list is empty or invalid")
    validated: list[dict[str, Any]] = []
    for item in deployments:
        deployment_name = item.get("deployment_name")
        if not isinstance(deployment_name, str) or not deployment_name.strip():
            raise ValueError(f"invalid deployment_name in item: {item}")
        validated.append(item)
    return validated


def build_responses_url(base_url: str, style: str, azure_api_version: str | None) -> str:
    base_url = base_url.rstrip("/")
    if style == "openai":
        return f"{base_url}/v1/responses"
    if style == "azure":
        url = f"{base_url}/openai/v1/responses"
        if azure_api_version:
            return f"{url}?{parse.urlencode({'api-version': azure_api_version})}"
        return url
    raise ValueError(f"unknown style: {style}")


def build_images_url(base_url: str, style: str, model_alias: str, azure_api_version: str | None) -> str:
    """Build URL for image generation endpoint.

    OpenAI style:  /v1/images/generations
    Azure style:   /openai/deployments/{model}/images/generations
    """
    base_url = base_url.rstrip("/")
    if style == "openai":
        return f"{base_url}/v1/images/generations"
    if style == "azure":
        url = f"{base_url}/openai/deployments/{model_alias}/images/generations"
        if azure_api_version:
            return f"{url}?{parse.urlencode({'api-version': azure_api_version})}"
        return url
    raise ValueError(f"unknown style: {style}")


def extract_output_text(response_obj: dict[str, Any]) -> str:
    output_text = response_obj.get("output_text")
    if isinstance(output_text, str) and output_text:
        return output_text

    output = response_obj.get("output")
    if isinstance(output, list):
        text_parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
        if text_parts:
            return "\n".join(text_parts)

    return ""


def call_responses_api(
    url: str,
    api_key: str,
    model_alias: str,
    prompt: str,
    max_output_tokens: int,
    timeout: int,
) -> TestResult:
    payload = {
        "model": model_alias,
        "input": prompt,
        "max_output_tokens": max_output_tokens,
    }
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url=url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    status_code = 0
    raw_body = ""
    output_text = ""
    ok = False

    try:
        with request.urlopen(req, timeout=timeout) as resp:
            status_code = int(resp.status)
            raw_body = resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as e:
        status_code = int(e.code)
        raw_body = e.read().decode("utf-8", errors="replace")
    except Exception as e:
        raw_body = str(e)

    if 200 <= status_code < 300:
        try:
            response_obj = json.loads(raw_body)
            output_text = extract_output_text(response_obj)
            ok = True
        except Exception:
            output_text = ""
            ok = False

    return TestResult(
        case=TestCase(style="", deployment_name="", model_alias=model_alias),
        ok=ok,
        status_code=status_code,
        output_text=output_text,
        raw_response=raw_body,
    )


def call_images_api(
    url: str,
    api_key: str,
    model_alias: str,
    prompt: str,
    size: str,
    timeout: int,
) -> TestResult:
    """Call the /images/generations endpoint and return the result."""
    payload: dict[str, Any] = {
        "model": model_alias,
        "prompt": prompt,
        "n": 1,
        "size": size,
    }
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url=url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    status_code = 0
    raw_body = ""
    output_text = ""
    ok = False

    try:
        with request.urlopen(req, timeout=timeout) as resp:
            status_code = int(resp.status)
            raw_body = resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as e:
        status_code = int(e.code)
        raw_body = e.read().decode("utf-8", errors="replace")
    except Exception as e:
        raw_body = str(e)

    if 200 <= status_code < 300:
        try:
            response_obj = json.loads(raw_body)
            image_data = response_obj.get("data", [])
            if isinstance(image_data, list) and len(image_data) > 0:
                first = image_data[0]
                img_url = first.get("url", "")
                b64 = first.get("b64_json", "")
                revised_prompt = first.get("revised_prompt", "")
                if img_url:
                    output_text = f"image_url={img_url[:120]}..."
                    ok = True
                elif b64:
                    b64_len = len(b64)
                    output_text = f"b64_json=({b64_len} chars)"
                    ok = True
                else:
                    output_text = f"data[0] keys: {list(first.keys())}"
                if revised_prompt:
                    output_text += f" | revised_prompt={revised_prompt[:80]}"
            else:
                output_text = "<empty data array>"
        except Exception:
            output_text = ""
            ok = False

    return TestResult(
        case=TestCase(style="", deployment_name="", model_alias=model_alias, api_type="images"),
        ok=ok,
        status_code=status_code,
        output_text=output_text,
        raw_response=raw_body[:2000] if len(raw_body) > 2000 else raw_body,  # truncate large b64 responses
    )


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).resolve()

    api_key = args.api_key
    if not api_key:
        api_key = os.getenv("LITELLM_MASTER_KEY")

    if not api_key:
        print("[ERROR] missing API key: pass --api-key or set LITELLM_MASTER_KEY")
        return 2

    if not config_path.exists():
        print(f"[ERROR] config not found: {config_path}")
        return 2

    try:
        deployments = load_deployments(config_path)
    except Exception as e:
        print(f"[ERROR] invalid config: {e}")
        return 2

    skip_set = {s.strip() for s in (args.skip_deployments or "").split(",") if s.strip()}
    image_set = {s.strip() for s in (args.image_deployments or "").split(",") if s.strip()}

    cases: list[TestCase] = []
    skipped_deployments: list[str] = []
    for item in deployments:
        deployment_name = item["deployment_name"].strip()
        if deployment_name in skip_set:
            skipped_deployments.append(deployment_name)
            continue
        alias = f"{args.model_prefix}{deployment_name}"
        api_type = "images" if deployment_name in image_set else "responses"
        cases.append(TestCase(style="openai", deployment_name=deployment_name, model_alias=alias, api_type=api_type))
        cases.append(TestCase(style="azure", deployment_name=deployment_name, model_alias=alias, api_type=api_type))

    print(f"[INFO] config: {config_path}")
    print(f"[INFO] base_url: {args.base_url.rstrip('/')}")
    print(f"[INFO] deployments: {len(deployments)}")
    if skipped_deployments:
        print(f"[INFO] skipped: {', '.join(skipped_deployments)}")
    if image_set:
        print(f"[INFO] image deployments (Images API): {', '.join(sorted(image_set))}")
    print(f"[INFO] total tests: {len(cases)}")

    results: list[TestResult] = []

    for idx, case in enumerate(cases, start=1):
        if case.api_type == "images":
            url = build_images_url(args.base_url, case.style, case.model_alias, args.azure_api_version)
        else:
            url = build_responses_url(args.base_url, case.style, args.azure_api_version)

        print("\n" + "=" * 88)
        print(f"[TEST {idx}/{len(cases)}] style={case.style} deployment={case.deployment_name} "
              f"model={case.model_alias} api={case.api_type}")
        print(f"[URL] {url}")

        if case.api_type == "images":
            print(f"[PROMPT] {args.image_prompt}")
            result = call_images_api(
                url=url,
                api_key=api_key,
                model_alias=case.model_alias,
                prompt=args.image_prompt,
                size=args.image_size,
                timeout=args.image_timeout,
            )
        else:
            print(f"[PROMPT] {args.prompt}")
            result = call_responses_api(
                url=url,
                api_key=api_key,
                model_alias=case.model_alias,
                prompt=args.prompt,
                max_output_tokens=args.max_output_tokens,
                timeout=args.timeout,
            )
        result.case = case
        results.append(result)

        if result.ok:
            print(f"[STATUS] {result.status_code}")
            if result.output_text:
                print(f"[RESPONSE] {result.output_text}")
            else:
                print("[RESPONSE] <empty output_text>")
        else:
            print(f"[STATUS] {result.status_code if result.status_code else 'request-error'}")
            print(f"[RESPONSE_ERROR] {result.raw_response}")

    passed = sum(1 for r in results if r.ok)
    failed = len(results) - passed

    print("\n" + "#" * 88)
    print(f"[SUMMARY] passed={passed} failed={failed} total={len(results)}")

    if failed > 0:
        print("[FAILED_CASES]")
        for r in results:
            if not r.ok:
                print(
                    f"- style={r.case.style} deployment={r.case.deployment_name} "
                    f"model={r.case.model_alias} status={r.status_code if r.status_code else 'request-error'}"
                )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
