from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from core.test_case_dedup import prepare_generated_test_cases


app = FastAPI(title="Web Test Agent Visualizer Backend")

DEFAULT_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
]


def _configured_cors_origins() -> list[str]:
    value = os.environ.get("WEBAGENT_CORS_ORIGINS", "")
    origins = [item.strip() for item in value.split(",") if item.strip()]
    return origins or DEFAULT_CORS_ORIGINS


app.add_middleware(
    CORSMiddleware,
    allow_origins=_configured_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
RUNTIME_LOG_PATH = BASE_DIR / "runtime.log"
DEFAULT_TARGET_URL = "https://demo.4gaboards.com/"
DEFAULT_MANUAL_DIR = "./manual"
DEFAULT_TEST_CASES_FILE = "test_cases_manual.json"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_current_process: subprocess.Popen | None = None
_current_run_started_at: float | None = None


class _PollingAccessLogFilter(logging.Filter):
    """Keep high-frequency UI polling endpoints out of uvicorn access logs."""

    _POLLING_PATHS = (
        "GET /api/logs",
        "GET /api/screenshots",
        "GET /api/run/status",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(path in message for path in self._POLLING_PATHS)


def _install_access_log_filter() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    if any(isinstance(item, _PollingAccessLogFilter) for item in access_logger.filters):
        return
    access_logger.addFilter(_PollingAccessLogFilter())


_install_access_log_filter()


def _json_default(default: Any) -> Any:
    return [] if default is None else default


def _read_json_file(path: Path, default: Any = None) -> Any:
    if path is None:
        return _json_default(default)
    if not path.is_file():
        return _json_default(default)
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        logging.getLogger(__name__).warning(
            "Unable to read JSON file %s: %s", path, exc
        )
        return _json_default(default)


def _write_json_file_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(tmp_path, path)


def _relative_path(path: Path, root: Path = BASE_DIR) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _static_output_url(path: Path, output_dir: Path = OUTPUT_DIR) -> str:
    relative = path.relative_to(output_dir).as_posix()
    return f"/static/output/{quote(relative, safe='/')}"


def _resolve_public_output_image(relative_path: str, output_dir: Path = OUTPUT_DIR) -> Path:
    candidate = (output_dir / relative_path).resolve()
    output_root = output_dir.resolve()
    try:
        candidate.relative_to(output_root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc

    if not candidate.is_file() or candidate.suffix.lower() not in IMAGE_EXTENSIONS:
        raise HTTPException(status_code=404, detail="File not found")
    return candidate


def _file_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": _relative_path(path),
            "exists": False,
            "size": 0,
            "mtime": None,
        }
    stat = path.stat()
    return {
        "path": _relative_path(path),
        "exists": True,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }


def _find_test_cases_path(output_dir: Path = OUTPUT_DIR) -> Path | None:
    candidates: list[Path] = [
        output_dir / DEFAULT_TEST_CASES_FILE,
        output_dir / "test_cases.json",
    ]

    candidates.extend(
        sorted(
            (
                path for path in output_dir.glob("test_cases*.json")
                if path.name != "test_cases_with_sources.json"
                and path not in candidates
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    )

    sources_path = output_dir / "test_cases_with_sources.json"
    candidates.append(sources_path)

    for path in candidates:
        if _is_valid_test_cases_file(path):
            return path
    return None


def _is_valid_test_cases_file(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    raw = _read_json_file(path, None)
    return isinstance(raw, list)


def _find_sources_path(output_dir: Path = OUTPUT_DIR) -> Path | None:
    path = output_dir / "test_cases_with_sources.json"
    return path if path.is_file() else None


def _document_name_from_citation(citation: dict[str, Any]) -> str | None:
    title = str(citation.get("title") or "").strip()
    if title:
        return title

    source = str(citation.get("source") or "").strip()
    if not source:
        return None

    return Path(source).stem or Path(source).name


def _citation_from_raw(citation: dict[str, Any]) -> dict[str, Any]:
    return dict(citation)


def _citations_from_case(test_case: dict[str, Any]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for citation in test_case.get("citations") or []:
        if isinstance(citation, dict):
            citations.append(_citation_from_raw(citation))
    return citations


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _document_names_from_citations(citations: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for citation in citations:
        name = _document_name_from_citation(citation)
        if name:
            names.append(name)
    return _unique_strings(names)


def _document_names_from_case(test_case: dict[str, Any]) -> list[str]:
    return _document_names_from_citations(_citations_from_case(test_case))


def _source_case_citations(output_dir: Path = OUTPUT_DIR) -> dict[str, list[dict[str, Any]]]:
    sources_path = _find_sources_path(output_dir)
    source_cases = _read_json_file(sources_path, []) if sources_path else []
    if not isinstance(source_cases, list):
        return {}

    citations: dict[str, list[dict[str, Any]]] = {}
    for case in source_cases:
        if not isinstance(case, dict):
            continue
        scenario_id = str(case.get("scenario_id") or "").strip()
        if not scenario_id:
            continue
        citations[scenario_id] = _citations_from_case(case)
    return citations


def _sanitize_test_case(test_case: dict[str, Any], citations_by_scenario: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    scenario_id = str(test_case.get("scenario_id") or "").strip()
    sanitized = dict(test_case)
    own_citations = _citations_from_case(test_case)
    citations = own_citations or citations_by_scenario.get(scenario_id, [])
    sanitized["citations"] = citations
    sanitized["documents"] = _document_names_from_citations(citations)
    return sanitized


def _load_test_cases(output_dir: Path = OUTPUT_DIR) -> tuple[list[dict[str, Any]], Path | None]:
    cases_path = _find_test_cases_path(output_dir)
    raw_cases = _read_json_file(cases_path, []) if cases_path else []
    if not isinstance(raw_cases, list):
        return [], cases_path

    citations_by_scenario = _source_case_citations(output_dir)
    cases = [
        _sanitize_test_case(case, citations_by_scenario)
        for case in raw_cases
        if isinstance(case, dict)
    ]
    return cases, cases_path


def _load_verification_results(output_dir: Path = OUTPUT_DIR) -> dict[str, dict[str, Any]]:
    raw = _read_json_file(output_dir / "verification_results.json", {})
    if not isinstance(raw, dict):
        return {}
    return {
        str(scenario_id): value
        for scenario_id, value in raw.items()
        if isinstance(value, dict)
    }


def _case_text(test_case: dict[str, Any]) -> str:
    if not isinstance(test_case, dict):
        return ""
    return " ".join([
        str(test_case.get("scenario_id", "")),
        str(test_case.get("feature_id", "")),
        str(test_case.get("scenario_name", "")),
        " ".join(str(step) for step in test_case.get("steps", [])),
        " ".join(str(exp) for exp in test_case.get("expectations", [])),
    ]).lower()


def _is_ignorable_external_registration_failure(
    test_case: dict[str, Any],
    verification: dict[str, Any],
) -> bool:
    """Return whether a verification result was explicitly marked ignorable.

    The UI summary should not infer ignore status from scenario names or page
    text. Classification belongs to the verification result itself, where it can
    be produced by semantic validation or an upstream structured rule.
    """
    if verification.get("ignored") is True or verification.get("effective_status") == "ignored":
        return True
    return False


def _verification_summary(
    cases: list[dict[str, Any]],
    verification_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    case_by_id = {
        str(case.get("scenario_id") or ""): case
        for case in cases
        if isinstance(case, dict)
    }
    items: list[dict[str, Any]] = []
    passed_count = 0
    failed_count = 0
    ignored_count = 0
    raw_passed_count = 0
    raw_failed_count = 0

    for scenario_id, result in verification_results.items():
        case = case_by_id.get(scenario_id, {})
        ignored = _is_ignorable_external_registration_failure(case, result)
        passed = result.get("passed") is True
        if passed:
            raw_passed_count += 1
        else:
            raw_failed_count += 1
        if ignored:
            status = "ignored"
            ignored_count += 1
        elif passed:
            status = "passed"
            passed_count += 1
        else:
            status = "failed"
            failed_count += 1

        items.append(
            {
                "scenario_id": scenario_id,
                "scenario_name": case.get("scenario_name", ""),
                "status": status,
                "effective_status": status,
                "raw_status": "passed" if passed else "failed",
                "ignored": ignored,
                "passed": passed,
                "reason": result.get("reason", ""),
            }
        )

    effective_total = passed_count + failed_count
    verified_count = len(verification_results)
    expected_count = len(cases)
    pass_rate = round((passed_count / effective_total) * 100, 2) if effective_total else None
    return {
        "expected_count": expected_count,
        "verified_count": verified_count,
        "unverified_count": max(expected_count - verified_count, 0),
        "passed_count": passed_count,
        "failed_count": failed_count,
        "ignored_count": ignored_count,
        "raw_passed_count": raw_passed_count,
        "raw_failed_count": raw_failed_count,
        "pass_rate": pass_rate,
        "items": sorted(items, key=lambda item: item["scenario_id"]),
    }


def _load_document_index(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    cases, cases_path = _load_test_cases(output_dir)
    by_name: dict[str, dict[str, Any]] = {}
    by_scenario: list[dict[str, Any]] = []

    for case in cases:
        scenario_id = str(case.get("scenario_id") or "")
        scenario_name = str(case.get("scenario_name") or "")
        documents = list(case.get("documents") or [])
        citations = list(case.get("citations") or [])
        by_scenario.append(
            {
                "scenario_id": scenario_id,
                "scenario_name": scenario_name,
                "documents": documents,
                "citations": citations,
            }
        )
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            name = _document_name_from_citation(citation)
            if not name:
                continue
            entry = by_name.setdefault(name, {"name": name, "scenario_ids": set(), "citations": []})
            entry["scenario_ids"].add(scenario_id)
            entry["citations"].append(
                {
                    "scenario_id": scenario_id,
                    "scenario_name": scenario_name,
                    **citation,
                }
            )

    documents = [
        {
            "name": entry["name"],
            "scenario_ids": sorted(entry["scenario_ids"]),
            "citations": entry["citations"],
        }
        for _, entry in sorted(by_name.items())
    ]
    return {
        "test_cases_path": _relative_path(cases_path) if cases_path else None,
        "count": len(documents),
        "documents": documents,
        "by_scenario": by_scenario,
    }


def _parse_screenshot_metadata(path: Path) -> dict[str, Any]:
    stem = path.stem
    scenario_id = None
    status = None
    name = stem

    for marker, value in (("_成功_", "成功"), ("_失败_", "失败")):
        if marker in stem:
            scenario_id, name = stem.split(marker, 1)
            status = value
            break

    if scenario_id is None:
        match = re.match(r"(?P<scenario_id>TS_[A-Za-z0-9]+_[A-Za-z0-9]+|TS_REG_\d+|TS_SETUP_[A-Za-z0-9]+)_(?P<name>.+)", stem)
        if match:
            scenario_id = match.group("scenario_id")
            name = match.group("name")

    step_match = re.search(r"(?:^|_)step_(?P<step>\d+)(?:_|$)", name)
    return {
        "scenario_id": scenario_id,
        "status": status,
        "name": name,
        "step": int(step_match.group("step")) if step_match else None,
    }


def _list_screenshot_items(
    output_dir: Path = OUTPUT_DIR,
    scenario_id: str | None = None,
    since: float | None = None,
) -> list[dict[str, Any]]:
    if not output_dir.is_dir():
        return []

    items: list[dict[str, Any]] = []
    for path in output_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        stat = path.stat()
        if since is not None and stat.st_mtime < since:
            continue
        metadata = _parse_screenshot_metadata(path)
        if scenario_id and metadata.get("scenario_id") != scenario_id:
            continue
        items.append(
            {
                "filename": path.name,
                "path": _relative_path(path),
                "url": _static_output_url(path, output_dir),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                **metadata,
            }
        )

    return _sort_screenshot_items(_dedupe_screenshot_items(items))


def _annotate_screenshot_case_names(
    items: list[dict[str, Any]],
    output_dir: Path = OUTPUT_DIR,
) -> list[dict[str, Any]]:
    cases, _ = _load_test_cases(output_dir)
    names_by_id = {
        str(case.get("scenario_id") or ""): str(case.get("scenario_name") or "")
        for case in cases
        if isinstance(case, dict)
    }
    annotated = []
    for item in items:
        copied = dict(item)
        scenario_id = str(copied.get("scenario_id") or "")
        copied["scenario_name"] = names_by_id.get(scenario_id, "")
        annotated.append(copied)
    return annotated


def _dedupe_screenshot_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in items:
        key = _screenshot_dedupe_key(item)
        current = by_key.get(key)
        if current is None or _screenshot_keep_score(item) > _screenshot_keep_score(current):
            by_key[key] = item
    return list(by_key.values())


def _screenshot_dedupe_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("scenario_id"),
        item.get("step"),
        _screenshot_base_name(
            str(item.get("name") or item.get("filename") or ""),
            item.get("step"),
        ),
        item.get("size"),
        _file_content_hash(BASE_DIR / str(item.get("path") or "")),
    )


def _screenshot_keep_score(item: dict[str, Any]) -> tuple[int, int, float, str]:
    filename = str(item.get("filename") or "")
    has_status = 1 if item.get("status") else 0
    is_numbered_copy = 1 if _is_screenshot_numbered_copy(filename, item.get("step")) else 0
    return (
        has_status,
        -is_numbered_copy,
        float(item.get("mtime") or 0),
        filename,
    )


def _screenshot_base_name(name: str, step: Any = None) -> str:
    stem = Path(str(name)).stem
    if step is not None:
        try:
            step_number = int(step)
        except (TypeError, ValueError):
            step_number = None
        if step_number is not None:
            return re.sub(
                rf"((?:^|_)step_{step_number})_\d+$",
                r"\1",
                stem,
            )
    return re.sub(r"_\d+$", "", stem)


def _is_screenshot_numbered_copy(filename: str, step: Any = None) -> bool:
    stem = Path(str(filename)).stem
    if step is not None:
        try:
            step_number = int(step)
        except (TypeError, ValueError):
            step_number = None
        if step_number is not None:
            return re.search(rf"(?:^|_)step_{step_number}_\d+$", stem) is not None
    return re.search(r"_\d+$", stem) is not None


def _file_content_hash(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def _sort_screenshot_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            item.get("mtime") or 0,
            item.get("filename") or "",
        ),
    )


def _read_log_chunk(path: Path = RUNTIME_LOG_PATH, offset: int = 0) -> dict[str, Any]:
    if not path.is_file():
        return {
            "content": "",
            "offset": 0,
            "exists": False,
            "path": _relative_path(path),
            "size": 0,
        }

    size = path.stat().st_size
    if offset < 0 or offset > size:
        offset = 0

    with path.open("rb") as file:
        file.seek(offset)
        data = file.read()

    return {
        "content": data.decode("utf-8", errors="replace"),
        "offset": size,
        "exists": True,
        "path": _relative_path(path),
        "size": size,
    }


def _runtime_log_activity(now: float | None = None) -> dict[str, Any]:
    info = _file_info(RUNTIME_LOG_PATH)
    mtime = info.get("mtime")
    idle_seconds = None
    if isinstance(mtime, (int, float)):
        idle_seconds = max(0.0, (time.time() if now is None else now) - float(mtime))
    return {
        "runtime_log": info,
        "log_size": info.get("size", 0),
        "log_mtime": mtime,
        "log_idle_seconds": idle_seconds,
    }


def _reset_runtime_log(path: Path = RUNTIME_LOG_PATH) -> None:
    """Clear the previous run log before a new agent process starts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8"):
        pass


def _reset_run_outputs(output_dir: Path = OUTPUT_DIR) -> None:
    """Clear per-run status so UI progress belongs to the new run."""
    _write_json_file_atomic(output_dir / "verification_results.json", {})


def _run_status() -> dict[str, Any]:
    global _current_process
    log_activity = _runtime_log_activity()
    if _current_process is None:
        return {
            "status": "idle",
            "pid": None,
            "returncode": None,
            "started_at": _current_run_started_at,
            **log_activity,
        }

    returncode = _current_process.poll()
    if returncode is None:
        return {
            "status": "running",
            "pid": _current_process.pid,
            "returncode": None,
            "started_at": _current_run_started_at,
            **log_activity,
        }

    status = "completed" if returncode == 0 else "failed"
    return {
        "status": status,
        "pid": _current_process.pid,
        "returncode": returncode,
        "started_at": _current_run_started_at,
        **log_activity,
    }


def _prepare_selected_cases_file(case_ids: list[Any], output_dir: Path = OUTPUT_DIR) -> str:
    selected_ids = [str(case_id).strip() for case_id in case_ids if str(case_id).strip()]
    if not selected_ids:
        return DEFAULT_TEST_CASES_FILE

    cases, _ = _load_test_cases(output_dir)
    by_id = {str(case.get("scenario_id") or ""): case for case in cases}
    selected_cases = [by_id[case_id] for case_id in selected_ids if case_id in by_id]
    if not selected_cases:
        raise HTTPException(status_code=400, detail="Selected test cases were not found.")

    selected_cases, removed_duplicates = prepare_generated_test_cases(selected_cases)
    if removed_duplicates:
        logging.getLogger(__name__).info(
            "Removed duplicate selected test cases: %s",
            ", ".join(str(case.get("scenario_id") or "") for case in removed_duplicates),
        )

    selected_path = output_dir / "selected_test_cases.json"
    _write_json_file_atomic(selected_path, selected_cases)
    return selected_path.name


def _build_agent_command(options: dict[str, Any]) -> list[str]:
    command = [
        sys.executable,
        str(BASE_DIR / "main.py"),
        "--url",
        str(options.get("url") or DEFAULT_TARGET_URL),
        "--manual-dir",
        str(options.get("manual_dir") or DEFAULT_MANUAL_DIR),
    ]

    mode = str(options.get("mode") or "resume").strip().lower()
    if mode != "full":
        command.extend([
            "--resume",
            "--test-cases",
            str(options.get("test_cases") or DEFAULT_TEST_CASES_FILE),
        ])

    model = options.get("model")
    if model:
        command.extend(["--model", str(model)])

    max_retries = options.get("max_retries")
    if max_retries is not None:
        command.extend(["--max-retries", str(max_retries)])

    if options.get("headless"):
        command.append("--headless")
    if options.get("stub"):
        command.append("--stub")

    return command


def _start_agent_process(options: dict[str, Any]) -> subprocess.Popen:
    command = _build_agent_command(options)
    return subprocess.Popen(command, cwd=BASE_DIR)


@app.post("/api/start-test")
async def start_test(request: Request):
    global _current_process, _current_run_started_at

    status = _run_status()
    if status["status"] == "running":
        return {"status": "running", "message": "Agent pipeline is already running.", **status}

    try:
        options = await request.json()
    except Exception:
        options = {}
    if not isinstance(options, dict):
        options = {}

    if isinstance(options.get("cases"), list) and options.get("mode") != "full":
        options["test_cases"] = _prepare_selected_cases_file(options["cases"])

    _current_run_started_at = time.time()
    _reset_runtime_log()
    _reset_run_outputs()
    _current_process = _start_agent_process(options)
    return {
        "status": "success",
        "message": "Agent pipeline started successfully.",
        "pid": _current_process.pid,
        "started_at": _current_run_started_at,
        "log_reset": True,
        "mode": str(options.get("mode") or "resume"),
        "test_cases": options.get("test_cases") or DEFAULT_TEST_CASES_FILE,
    }


@app.get("/api/run/status")
async def get_run_status():
    return _run_status()


@app.get("/api/logs")
async def get_logs(offset: int = 0):
    return _read_log_chunk(offset=offset)


@app.get("/static/output/{relative_path:path}")
async def get_output_image(relative_path: str):
    return FileResponse(_resolve_public_output_image(relative_path))


@app.get("/api/test-cases")
async def get_test_cases():
    cases, path = _load_test_cases()
    return {
        "path": _relative_path(path) if path else None,
        "count": len(cases),
        "test_cases": cases,
    }


@app.get("/api/cases")
async def get_cases():
    cases, path = _load_test_cases()
    return {
        "path": _relative_path(path) if path else None,
        "count": len(cases),
        "cases": cases,
    }


@app.get("/api/test-cases/{scenario_id}")
async def get_test_case(scenario_id: str):
    cases, path = _load_test_cases()
    for case in cases:
        if case.get("scenario_id") == scenario_id:
            return {
                "path": _relative_path(path) if path else None,
                "test_case": case,
            }
    raise HTTPException(status_code=404, detail=f"Test case not found: {scenario_id}")


@app.get("/api/documents")
async def get_documents():
    return _load_document_index()


@app.get("/api/screenshots")
async def get_screenshots(scenario_id: str | None = None, since: float | None = None):
    items = _annotate_screenshot_case_names(
        _list_screenshot_items(scenario_id=scenario_id, since=since)
    )
    return {
        "count": len(items),
        "screenshots": [item["url"] for item in items],
        "items": items,
    }


@app.get("/api/summary")
async def get_summary():
    cases, cases_path = _load_test_cases()
    verification_results = _load_verification_results()
    screenshots = _list_screenshot_items()
    documents = _load_document_index()
    reports = sorted(OUTPUT_DIR.glob("report_*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {
        "run": _run_status(),
        "runtime_log": _file_info(RUNTIME_LOG_PATH),
        "test_cases": {
            "path": _relative_path(cases_path) if cases_path else None,
            "count": len(cases),
        },
        "documents": {
            "count": documents["count"],
        },
        "verification": _verification_summary(cases, verification_results),
        "screenshots": {
            "count": len(screenshots),
        },
        "latest_report": _file_info(reports[0]) if reports else None,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
