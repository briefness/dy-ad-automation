from __future__ import annotations

import multiprocessing as mp
import os
import signal
import time
import uuid
from pathlib import Path

from .worker import run_worker


class JobManager:
    def __init__(self, process_factory=None, queue_factory=None):
        self.job = None
        self.last_preflight = None
        self.process_factory = process_factory or mp.Process
        self.queue_factory = queue_factory or mp.Queue

    def start(self, kind: str, payload: dict) -> dict:
        self.poll()
        if self.job and self.job["process"].is_alive(): raise RuntimeError("已有任务正在运行")
        if kind == "run":
            expected = payload.get("preflight_key")
            if not expected or not self.last_preflight or self.last_preflight.get("key") != expected:
                raise RuntimeError("请先完成与当前素材和产品信息匹配的预检")
        queue = self.queue_factory()
        try:
            process = self.process_factory(target=run_worker, args=(kind, payload, queue), daemon=True)
        except TypeError:
            process = self.process_factory(target=run_worker, args=(kind, payload, queue))
        if hasattr(process, "daemon"):
            process.daemon = True
        process.start();
        try: os.setpgid(process.pid, process.pid)
        except (OSError, AttributeError): pass
        self.job = {"id": uuid.uuid4().hex, "kind": kind, "process": process, "queue": queue, "stage": "queued", "progress": 0, "logs": [], "result": None, "error": None, "started_at": time.time(), "cancelled": False}
        return self.public_status()

    def poll(self) -> dict:
        if not self.job: return {"active": False}
        job = self.job
        while True:
            try: message = job["queue"].get_nowait()
            except Exception: break
            if message["kind"] == "log": job["logs"].append(message); job["logs"] = job["logs"][-100:]
            elif message["kind"] == "stage":
                progress = max(job["progress"], int(message.get("progress", job["progress"])))
                if progress >= job["progress"]:
                    job["stage"] = message.get("stage", job["stage"])
                job["progress"] = progress
            elif message["kind"] == "result":
                job["result"] = message["result"]; job["stage"] = "completed"; job["progress"] = 100
                if job["kind"] == "preflight": self.last_preflight = job["result"]
            elif message["kind"] == "error": job["error"] = message["error"]; job["stage"] = "cancelled" if job["cancelled"] else "failed"
        if not job["process"].is_alive() and job["stage"] not in {"completed", "failed", "cancelled"}: job["stage"] = "failed"; job["error"] = job["error"] or "worker unexpectedly exited"
        return self.public_status()

    def cancel(self) -> dict:
        self.poll()
        if not self.job: return {"active": False}
        job = self.job; job["cancelled"] = True; process = job["process"]
        if not process.is_alive() or job["stage"] in {"completed", "failed", "cancelled"}:
            job["cancelled"] = job["stage"] == "cancelled"
            return self.public_status()
        if process.is_alive():
            try: os.killpg(process.pid, signal.SIGTERM)
            except (OSError, AttributeError): process.terminate()
            process.join(3)
            if process.is_alive():
                try: os.killpg(process.pid, signal.SIGKILL)
                except (OSError, AttributeError): process.kill()
                process.join(2)
        job["stage"] = "cancelled"; job["progress"] = 0
        return self.public_status()

    def shutdown(self): self.cancel()

    def public_status(self) -> dict:
        if not self.job: return {"active": False}
        job = self.job; return {k: job[k] for k in ("id", "kind", "stage", "progress", "logs", "result", "error", "started_at", "cancelled") } | {"active": job["process"].is_alive() and job["stage"] not in {"completed", "failed", "cancelled"}}
