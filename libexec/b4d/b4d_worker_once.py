#!/usr/bin/env python3
"""B4D R02 VM130 operator-invoked one-job worker. No scheduler.

Only --once may claim a real job. --self-test has no remote access.
"""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from b4d_transport import B4DError, MAX_COMPRESSED, process_once

CAPTURE_TIMEOUT_SECONDS = 160


def capture_live() -> bytes:
    # Preserve the original standalone v2c --live collector and its own
    # non-root /home/ops output boundary. No custom SSH probes are added here.
    if os.geteuid() == 0:
        raise B4DError("ROOT_NOT_ALLOWED")
    root = Path('/home/ops')
    if not root.is_dir():
        raise B4DError("OPS_HOME_MISSING")
    with tempfile.TemporaryDirectory(prefix='.b4d-once-', dir=root) as tmp:
        cmd = [sys.executable, str(Path(__file__).resolve().with_name('b4_v2c_capture.py')),
               '--live', '--output', tmp]
        try:
            run = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 timeout=CAPTURE_TIMEOUT_SECONDS, check=False)
        except subprocess.TimeoutExpired:
            raise  # B4D transport classifies timeout as COLLECTOR_TIMEOUT
        if run.returncode:
            raise B4DError('COLLECTOR_FAILED')
        # Do not trust user-controlled path in collector stdout.
        paths=list(Path(tmp).glob('WG_PAID_MGTS_DIAGNOSTICS_*.zip'))
        if len(paths)!=1 or not paths[0].is_file():
            raise B4DError('COLLECTOR_OUTPUT_INVALID')
        size=paths[0].stat().st_size
        if not 0<size<=MAX_COMPRESSED:
            raise B4DError('COLLECTOR_SIZE_INVALID')
        return paths[0].read_bytes()


def main(argv: list[str]) -> int:
    if os.geteuid() == 0:
        print('RESULT=STOP_B4D_R02_ROOT_FORBIDDEN', file=sys.stderr)
        return 2
    if argv == ['--self-test']:
        # Inert: no SSH, no collector, no queue access.
        print('RESULT=PASS_B4D_R02_INERT_SELF_TEST')
        return 0
    if argv != ['--once']:
        print('RESULT=STOP_B4D_R02_EXPLICIT_ONCE_REQUIRED', file=sys.stderr)
        return 2
    try:
        result = process_once(capture_live)
    except B4DError as exc:
        # Safe enumerated adapter reason only; never echo remote stderr/ZIP.
        print('RESULT=STOP_B4D_R02_WORKER REASON=' + str(exc), file=sys.stderr)
        return 1
    if result == 'NO_PENDING_JOB':
        print('RESULT=PASS_B4D_R02_NO_PENDING_JOB')
    elif result in ('PASS_READY', 'PASS_PARTIAL'):
        print('RESULT=PASS_B4D_R02_' + result)
    elif result.startswith('FAIL_'):
        print('RESULT=STOP_B4D_R02_' + result, file=sys.stderr)
        return 1
    else:
        print('RESULT=STOP_B4D_R02_UNEXPECTED_RESULT', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
