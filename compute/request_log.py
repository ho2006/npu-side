from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

_METHOD = re.compile(r"[A-Z]{1,16}")
_SEGMENT = re.compile(r"[A-Za-z0-9_-]{1,64}")
_ERROR_TYPE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LOG_NAME = re.compile(r"[0-9a-f]{32}\.json")

FIELDS = (
    "request_id",
    "started_at",
    "method",
    "path",
    "experiment_id",
    "bytes_in",
    "phase",
    "status",
    "duration_ms",
    "bytes_out",
    "detail",
    "error_type",
)


def path_parts(path: str) -> tuple[str, ...]:
    if not isinstance(path, str):
        return ()
    return tuple(part for part in urlparse(path).path.split("/") if part)


def experiment_id_from_path(path: str) -> str | None:
    parts = path_parts(path)
    if len(parts) >= 2 and parts[0] == "experiments" and _SEGMENT.fullmatch(parts[1]):
        return parts[1]
    return None


def elapsed_ms(started_ns: int) -> int:
    if type(started_ns) is not int or started_ns < 0:
        raise ValueError("started_ns")
    delta = time.monotonic_ns() - started_ns
    if delta < 0:
        return 0
    return delta // 1_000_000


class Sent:
    __slots__ = ("status", "bytes_out", "detail", "error_type")

    def __init__(self, status: int, bytes_out: int, detail: str | None, error_type: str | None) -> None:
        self.status = status
        self.bytes_out = bytes_out
        self.detail = detail
        self.error_type = error_type


@dataclass(frozen=True, slots=True)
class RequestId:
    hex: str

    def __post_init__(self) -> None:
        if not isinstance(self.hex, str) or re.fullmatch(r"[0-9a-f]{32}", self.hex) is None:
            raise ValueError("request id")

    @staticmethod
    def mint() -> RequestId:
        return RequestId(uuid.uuid4().hex)


@dataclass(frozen=True, slots=True)
class RequestRecord:
    request_id: RequestId
    started_at: str
    method: str
    path: str
    experiment_id: str | None
    bytes_in: int
    phase: Literal["open", "closed"]
    status: int | None
    duration_ms: int | None
    bytes_out: int | None
    detail: str | None
    error_type: str | None

    def __post_init__(self) -> None:
        if type(self.request_id) is not RequestId:
            raise ValueError("request id")
        parsed = datetime.fromisoformat(self.started_at)
        if parsed.tzinfo is None:
            raise ValueError("started_at")
        if _METHOD.fullmatch(self.method) is None:
            raise ValueError("method")
        if not self.path.startswith("/") or "\x00" in self.path or len(self.path) > 65536:
            raise ValueError("path")
        if self.experiment_id is not None and _SEGMENT.fullmatch(self.experiment_id) is None:
            raise ValueError("experiment id")
        if type(self.bytes_in) is not int or self.bytes_in < 0:
            raise ValueError("bytes_in")
        finish = (self.status, self.duration_ms, self.bytes_out, self.detail, self.error_type)
        if self.phase == "open":
            if finish != (None, None, None, None, None):
                raise ValueError("open record has a finish field")
            return
        if self.phase != "closed":
            raise ValueError("phase")
        if type(self.status) is not int or not 100 <= self.status <= 599:
            raise ValueError("status")
        if type(self.duration_ms) is not int or self.duration_ms < 0:
            raise ValueError("duration_ms")
        if type(self.bytes_out) is not int or self.bytes_out < 0:
            raise ValueError("bytes_out")
        if 200 <= self.status < 300:
            if self.detail is not None or self.error_type is not None:
                raise ValueError("2xx carries an error")
            return
        if type(self.detail) is not str or self.detail == "":
            raise ValueError("detail")
        if self.status == 500:
            if type(self.error_type) is not str or _ERROR_TYPE.fullmatch(self.error_type) is None:
                raise ValueError("error_type")
            return
        if self.error_type is not None:
            raise ValueError("error_type")

    @classmethod
    def started(cls, method: str, path: str, bytes_in: int) -> RequestRecord:
        if type(bytes_in) is not int or bytes_in < 0:
            raise ValueError("bytes_in")
        safe_method = method if isinstance(method, str) and _METHOD.fullmatch(method) else "UNKNOWN"
        safe_path = "/"
        if isinstance(path, str) and path.startswith("/") and "\x00" not in path and len(path) <= 65536:
            safe_path = path
        return cls(
            request_id=RequestId.mint(),
            started_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            method=safe_method,
            path=safe_path,
            experiment_id=experiment_id_from_path(safe_path),
            bytes_in=bytes_in,
            phase="open",
            status=None,
            duration_ms=None,
            bytes_out=None,
            detail=None,
            error_type=None,
        )

    def with_bytes_in(self, bytes_in: int) -> RequestRecord:
        if self.phase != "open":
            raise ValueError("phase")
        if type(bytes_in) is not int or bytes_in < 0:
            raise ValueError("bytes_in")
        return replace(self, bytes_in=bytes_in)

    def with_experiment(self, experiment_id: str) -> RequestRecord:
        if self.phase != "open":
            raise ValueError("phase")
        if not isinstance(experiment_id, str) or _SEGMENT.fullmatch(experiment_id) is None:
            return self
        return replace(self, experiment_id=experiment_id)

    def finished(
        self,
        *,
        status: int,
        bytes_out: int,
        duration_ms: int,
        detail: str | None,
        error_type: str | None,
    ) -> RequestRecord:
        if self.phase != "open":
            raise ValueError("phase")
        return replace(
            self,
            phase="closed",
            status=status,
            bytes_out=bytes_out,
            duration_ms=duration_ms,
            detail=detail,
            error_type=error_type,
        )

    def to_dict(self) -> dict[str, object]:
        body = {
            "request_id": self.request_id.hex,
            "started_at": self.started_at,
            "method": self.method,
            "path": self.path,
            "experiment_id": self.experiment_id,
            "bytes_in": self.bytes_in,
            "phase": self.phase,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "bytes_out": self.bytes_out,
            "detail": self.detail,
            "error_type": self.error_type,
        }
        if tuple(body) != FIELDS:
            raise ValueError("fields")
        return body


def _parse_external(payload: object) -> RequestRecord:
    if not isinstance(payload, dict) or tuple(payload) != FIELDS:
        raise ValueError("fields")
    raw_id = payload["request_id"]
    if not isinstance(raw_id, str):
        raise ValueError("request id")
    try:
        return RequestRecord(
            request_id=RequestId(raw_id),
            started_at=payload["started_at"],
            method=payload["method"],
            path=payload["path"],
            experiment_id=payload["experiment_id"],
            bytes_in=payload["bytes_in"],
            phase=payload["phase"],
            status=payload["status"],
            duration_ms=payload["duration_ms"],
            bytes_out=payload["bytes_out"],
            detail=payload["detail"],
            error_type=payload["error_type"],
        )
    except (TypeError, AttributeError) as error:
        raise ValueError("record") from error


def _existing_record(path: Path) -> RequestRecord | None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    try:
        record = _parse_external(payload)
    except ValueError:
        return None
    if record.request_id.hex != path.stem:
        return None
    return record


def _same_open_request(existing: RequestRecord, record: RequestRecord) -> bool:
    return (
        existing.request_id == record.request_id
        and existing.started_at == record.started_at
        and existing.method == record.method
        and existing.path == record.path
    )


def write_request(root: Path, record: RequestRecord) -> None:
    if type(record) is not RequestRecord:
        raise ValueError("record")
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"{record.request_id.hex}.json"
    existing = _existing_record(destination)
    if existing is not None:
        if existing.phase == "closed":
            return
        if not _same_open_request(existing, record):
            raise ValueError("open record")
    temporary = root / f"{record.request_id.hex}.json.tmp"
    text = json.dumps(record.to_dict(), ensure_ascii=False, indent=2) + "\n"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        # A kill after replace must still find this record, not an empty page-cache file.
        os.fsync(handle.fileno())
    os.replace(temporary, destination)


def read_requests(root: Path) -> tuple[RequestRecord, ...]:
    if not root.exists():
        return ()
    if not root.is_dir():
        raise ValueError("log directory")
    found: list[RequestRecord] = []
    for child in root.iterdir():
        if _LOG_NAME.fullmatch(child.name) is None:
            continue
        try:
            payload = json.loads(child.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(child.name) from error
        record = _parse_external(payload)
        if record.request_id.hex != child.stem:
            raise ValueError("request id")
        found.append(record)
    found.sort(key=lambda item: (item.started_at, item.request_id.hex))
    return tuple(found)
