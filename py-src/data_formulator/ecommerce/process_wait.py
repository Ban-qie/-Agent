"""Bounded subprocess communication with cooperative server cancellation checks."""
import subprocess
import time


def communicate(process, payload, deadline, checkpoint):
    first = True
    while True:
        checkpoint()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, 0)
        try:
            result = process.communicate(payload if first else None, timeout=min(.1, remaining))
            checkpoint()
            return result
        except subprocess.TimeoutExpired:
            first = False
