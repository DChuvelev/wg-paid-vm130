#!/usr/bin/env python3
"""B4D VM130 transport for accepted VM121 queue.

Fixed CLI actions only; remote commands are not sourced from jobs.
Only stdlib. Does not execute arbitrary remote user commands.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import io
import json
import re
import shlex
import subprocess
import uuid
import zipfile
from typing import Callable

MAX_COMPRESSED = 1_048_576
MAX_UNCOMPRESSED = 2_000_000
MAX_MEMBER = 200_000
REQUIRED = frozenset({"README.md", "report.md", "snapshot.json", "events.jsonl", "manifest.json"})
ERROR_CODES = frozenset({"COLLECTOR_FAILED", "COLLECTOR_TIMEOUT", "ZIP_INVALID", "SOURCE_UNAVAILABLE"})
CLAIM_RE = re.compile(r"^RESULT=CLAIMED JOB_ID=([0-9a-f-]{36}) LEASE=([0-9a-f-]{36})$")
SSH = ("ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8", "vm121")
BASE = ("docker", "exec", "-i", "wgaccess-backend", "python", "-m", "app.cli.admin_diagnostics")


class B4DError(RuntimeError):
    """Safe reason only. No SSH stderr or captured diagnostic content."""


@dataclass(frozen=True)
class Claim:
    job: str
    lease: str


def _uuid(s: str) -> str:
    parsed = uuid.UUID(s)
    if str(parsed) != s:
        raise B4DError("NONCANONICAL_UUID")
    return s


def _remote(action: str, *values: str, payload: bytes = b"", timeout: int = 30,
            runner: Callable = subprocess.run) -> str:
    if action not in ("claim", "complete", "fail"):
        raise B4DError("ACTION_NOT_ALLOWED")
    if action == "claim" and values:
        raise B4DError("CLAIM_ARGUMENT_INVALID")
    if action == "complete" and len(values) != 2:
        raise B4DError("COMPLETE_ARGUMENT_INVALID")
    if action == "fail" and (len(values) != 3 or values[2] not in ERROR_CODES):
        raise B4DError("FAIL_ARGUMENT_INVALID")
    if action != "claim":
        for arg in values[:2]:
            _uuid(arg)
    if action != "complete" and payload:
        raise B4DError("UNEXPECTED_STDIN")
    if action == "complete" and not (0 < len(payload) <= MAX_COMPRESSED):
        raise B4DError("ZIP_SIZE_INVALID")
    args = BASE + (action,) + tuple(values)
    remote_cmd = shlex.join(args)
    try:
        completed = runner([*SSH, remote_cmd], input=payload, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise B4DError("SSH_TIMEOUT") from exc
    except OSError as exc:
        raise B4DError("SSH_EXEC_FAILURE") from exc
    if completed.returncode != 0:
        # Deliberately do not echo remote stderr (could disclose sensitive data).
        raise B4DError("REMOTE_COMMAND_FAILED")
    try:
        out = completed.stdout.decode("ascii", errors="strict").strip()
    except UnicodeError as exc:
        raise B4DError("NON_ASCII_REMOTE_RESULT") from exc
    if len(out) > 256 or "\n" in out:
        raise B4DError("REMOTE_RESULT_MALFORMED")
    return out


def claim(*, runner: Callable = subprocess.run) -> Claim | None:
    response = _remote("claim", runner=runner, timeout=20)
    if response == "RESULT=NO_PENDING_JOB":
        return None
    match = CLAIM_RE.fullmatch(response)
    if not match:
        raise B4DError("CLAIM_RESPONSE_INVALID")
    return Claim(*(_uuid(item) for item in match.groups()))


def validate_zip(payload: bytes) -> str:
    """Mirror the exact B4D R01 ZIP acceptance contract, without SQLAlchemy."""
    if not 0 < len(payload) <= MAX_COMPRESSED:
        raise B4DError("ZIP_SIZE_INVALID")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
            if len(members) != len(REQUIRED) or {m.filename for m in members} != REQUIRED:
                raise B4DError("ZIP_MEMBER_ALLOWLIST")
            if any(m.is_dir() or m.flag_bits & 1 or m.file_size > MAX_MEMBER
                   or m.compress_size > MAX_COMPRESSED for m in members):
                raise B4DError("ZIP_MEMBER_INVALID")
            if sum(m.file_size for m in members) > MAX_UNCOMPRESSED:
                raise B4DError("ZIP_UNCOMPRESSED_LIMIT")
            content = {m.filename: archive.read(m) for m in members}
            manifest = json.loads(content["manifest.json"])
            snapshot = json.loads(content["snapshot.json"])
    except (OSError, RuntimeError, UnicodeError, ValueError, zipfile.BadZipFile, KeyError) as exc:
        if isinstance(exc, B4DError):
            raise
        raise B4DError("ZIP_CONTENT_INVALID") from exc
    if not isinstance(manifest, dict) or not isinstance(snapshot, dict):
        raise B4DError("ZIP_SCHEMA_INVALID")
    checks = manifest.get("files")
    if not isinstance(checks, dict) or set(checks) != REQUIRED - {"manifest.json"}:
        raise B4DError("ZIP_MANIFEST_INVALID")
    for name in REQUIRED - {"manifest.json"}:
        datum = checks[name]
        if not isinstance(datum, dict) or datum.get("sha256") != sha256(content[name]).hexdigest() or datum.get("bytes") != len(content[name]):
            raise B4DError("ZIP_HASH_MISMATCH")
    if snapshot.get("synthetic_fixture") is True:
        raise B4DError("ZIP_SYNTHETIC_REFUSED")
    if not all(isinstance(obj.get("schema_version"), str) and obj["schema_version"].startswith("wg-paid-b4-") for obj in (snapshot, manifest)):
        raise B4DError("ZIP_SCHEMA_UNEXPECTED")
    capture_status = snapshot.get("capture_status")
    if capture_status not in ("complete", "partial", "complete_for_review_scope"):
        raise B4DError("ZIP_STATUS_INVALID")
    return capture_status


def _fail(job: Claim, code: str, runner: Callable) -> None:
    result = _remote("fail", job.job, job.lease, code, runner=runner, timeout=20)
    if result != "RESULT=PASS_FAIL_RECORDED":
        raise B4DError("FAIL_ACK_INVALID")


def process_once(capture: Callable[[], bytes], *, runner: Callable = subprocess.run) -> str:
    """Consume one job, no loops/timers/retries.

    `capture` is an *injected offline tested function*, not authorized to run on
    production until R05 and v2c source are inspected and an exact adapter is frozen.
    On ambiguous `complete` transport failure, do not invoke `fail`: remote DB may
    have committed the ZIP. Expiration eventually cleans up stale leases.
    """
    job = claim(runner=runner)
    if job is None:
        return "NO_PENDING_JOB"
    try:
        payload = capture()
    except subprocess.TimeoutExpired:
        _fail(job, "COLLECTOR_TIMEOUT", runner)
        return "FAIL_COLLECTOR_TIMEOUT"
    except (OSError, RuntimeError, ValueError, TypeError):
        _fail(job, "COLLECTOR_FAILED", runner)
        return "FAIL_COLLECTOR_FAILED"
    try:
        capture_status = validate_zip(payload)
    except (B4DError, TypeError):
        _fail(job, "ZIP_INVALID", runner)
        return "FAIL_ZIP_INVALID"
    result = _remote("complete", job.job, job.lease, payload=payload, runner=runner, timeout=30)
    expect = "ready" if capture_status == "complete" else "partial"
    if result != f"RESULT=PASS_COMPLETE STATUS={expect}":
        raise B4DError("COMPLETE_ACK_INVALID")
    return f"PASS_{expect.upper()}"
