from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from .jobs import JobManager
from .services import environment_check, normalize_product
from .validation import AssetPathError, is_safe_artifact

STATIC_DIR = Path(__file__).with_name("static")
ARTIFACT_SUFFIXES = {".mp4", ".json", ".txt", ".html", ".md", ".jpg", ".jpeg", ".png"}
PRESENTATION_SUFFIXES = {".mp4", ".jpg", ".jpeg", ".png", ".json", ".html"}
TEXT_SUFFIXES = {".txt", ".json", ".html", ".md"}


def output_dir() -> Path:
    from config import OUTPUT_DIR
    return Path(OUTPUT_DIR).resolve()


def cache_roots() -> list[Path]:
    from config import LOCAL_ASSET_INDEX_PATH
    return [Path(LOCAL_ASSET_INDEX_PATH).resolve()]


def artifact_label(path: Path) -> str:
    name = path.name.lower()
    if path.suffix.lower() == ".mp4": return "最终成片" if name.endswith(("_final.mp4", "_enhanced.mp4")) else "视频"
    if path.suffix.lower() in {".jpg", ".jpeg", ".png"}: return "封面图"
    if name.endswith("_script.json"): return "脚本"
    if name.endswith("_run_manifest.json"): return "运行清单"
    if name.endswith("_edit_decision_report.json"): return "剪辑报告"
    if name.endswith("_timeline_review.html"): return "时间线报告"
    if name.endswith("_frame_evidence_report.html"): return "画面报告"
    return path.stem


def is_presentable_artifact(path: Path) -> bool:
    name = path.name.lower()
    if path.suffix.lower() not in PRESENTATION_SUFFIXES: return False
    if path.suffix.lower() in {".mp4", ".jpg", ".jpeg", ".png"}: return True
    return name.endswith(("_script.json", "_run_manifest.json", "_edit_decision_report.json", "_timeline_review.html", "_frame_evidence_report.html"))


def artifacts_for(path: Path, prefix: str | None = None) -> list[dict[str, str]]:
    root = output_dir()
    files = sorted(root.rglob("*"), key=lambda item: item.stat().st_mtime if item.is_file() else 0, reverse=True)
    prefix = prefix or path.stem.removesuffix("_final")
    candidates = [item for item in files if item.is_file() and is_presentable_artifact(item) and (item == path or item.name.startswith(prefix))]
    videos = [item for item in candidates if item.suffix.lower() == ".mp4"]
    preferred_video = next((item for item in videos if item.name.endswith("_final.mp4")), None) or next((item for item in videos if item.name.endswith("_enhanced.mp4")), None) or next(iter(videos), None)
    candidates = [item for item in candidates if item.suffix.lower() != ".mp4" or item == preferred_video]
    return [{"name": item.name, "label": artifact_label(item), "path": str(item.relative_to(root)), "url": "/api/artifact?path=" + quote(str(item.relative_to(root)), safe="")} for item in candidates]


def recent_history(limit: int = 12) -> list[dict]:
    root = output_dir() / "final"
    if not root.exists(): return []
    entries = []
    for manifest in sorted(root.glob("*_run_manifest.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        try: data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): data = {}
        stem = manifest.name.removesuffix("_run_manifest.json")
        candidates = sorted(root.glob(f"{stem}*.mp4"))
        final = next((item for item in candidates if item.name.endswith("_final.mp4")), None) or next((item for item in candidates if item.name.endswith("_enhanced.mp4")), None) or next(iter(candidates), None)
        entries.append({"name": stem, "created_at": manifest.stat().st_mtime, "manifest": str(manifest.relative_to(output_dir())), "video": str(final.relative_to(output_dir())) if final else None, "artifacts": artifacts_for(final or manifest, prefix=stem)})
    return entries


class LocalWebHandler(SimpleHTTPRequestHandler):
    server_version = "LocalMixWorkbench/1.0"

    @property
    def manager(self) -> JobManager: return self.server.manager

    def log_message(self, format, *args): pass

    def _json(self, payload, status=HTTPStatus.OK):
        content = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Cache-Control", "no-store"); self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Content-Length", str(len(content))); self.end_headers(); self.wfile.write(content)

    def _body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("请求内容过大")
        raw = self.rfile.read(length)
        return json.loads(raw or b"{}")

    def _trusted_origin(self) -> bool:
        host = self.headers.get("Host", "")
        allowed_hosts = {
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        }
        origin = self.headers.get("Origin")
        allowed_origins = {f"http://{value}" for value in allowed_hosts}
        return host in allowed_hosts and (not origin or origin in allowed_origins)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/environment": return self._json(environment_check())
        if parsed.path in {"/api/status", "/api/jobs/current"}:
            status = self.manager.poll()
            result = status.get("result") or {}
            final_path = Path(str(result.get("final_path") or ""))
            if final_path.is_file() and is_safe_artifact(final_path, output_dir()):
                relative = str(final_path.resolve().relative_to(output_dir()))
                result["video_url"] = "/api/artifact?path=" + quote(relative, safe="")
                result["artifacts"] = artifacts_for(final_path)
                status["result"] = result
            return self._json(status)
        if parsed.path == "/api/history": return self._json({"runs": recent_history()})
        if parsed.path == "/api/artifact": return self._serve_artifact(unquote(parse_qs(parsed.query).get("path", [""])[0]))
        if parsed.path in {"/", "/index.html"}: return self._serve_static("index.html")
        if parsed.path in {"/styles.css", "/app.js"}: return self._serve_static(parsed.path.lstrip("/"))
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if not self._trusted_origin():
                return self._json({"error": "请求来源不受信任"}, HTTPStatus.FORBIDDEN)
            body = self._body()
            if parsed.path == "/api/preflight":
                self.manager.poll()
                if self.manager.public_status().get("active"): raise RuntimeError("已有任务正在运行")
                payload = {"asset_path": body["asset_path"], "product_payload": normalize_product(body.get("product") or body), "requested_duration": body.get("target_duration")}
                return self._json(self.manager.start("preflight", payload), HTTPStatus.ACCEPTED)
            if parsed.path in {"/api/jobs", "/api/run"}:
                self.manager.poll()
                env = environment_check()
                required = ("ffmpeg", "ffprobe", "vision", "llm", "output_writable")
                if body.get("voiceover", True): required = required + ("tts",)
                missing = [env[key]["label"] for key in required if not env[key]["ok"]]
                if missing: raise RuntimeError("缺少必需环境：" + "、".join(missing))
                from .services import preflight_fingerprint
                product = normalize_product(body.get("product") or body)
                payload = {"asset_path": body["asset_path"], "product": product, "preflight_key": preflight_fingerprint(body["asset_path"], product, output_dir=output_dir()), "target_duration": body.get("target_duration"), "video_style": body.get("video_style", "auto"), "rhythm_style": body.get("rhythm_style", "moderate"), "voiceover": body.get("voiceover", True), "voice": body.get("voice", "auto"), "stickers": body.get("stickers", "auto"), "output_name": body.get("output_name"), "resume": body.get("resume", True), "output_dir": str(output_dir())}
                return self._json(self.manager.start("run", payload), HTTPStatus.ACCEPTED)
            if parsed.path in {"/api/cancel", "/api/jobs/cancel", "/api/heartbeat"}:
                return self._json(self.manager.cancel() if parsed.path != "/api/heartbeat" or body.get("closing") else self.manager.poll())
            if parsed.path == "/api/open-output":
                self._open_output(); return self._json({"ok": True})
            self.send_error(HTTPStatus.NOT_FOUND)
        except (KeyError, ValueError, AssetPathError, RuntimeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _serve_static(self, name: str):
        path = STATIC_DIR / name
        if not path.is_file(): return self.send_error(HTTPStatus.NOT_FOUND)
        content = path.read_bytes(); self.send_response(HTTPStatus.OK); self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream"); self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; script-src 'self'"); self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Content-Length", str(len(content))); self.end_headers(); self.wfile.write(content)

    def _serve_artifact(self, relative: str):
        if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            return self.send_error(HTTPStatus.FORBIDDEN)
        target = (output_dir() / relative).resolve()
        if target.suffix.lower() not in ARTIFACT_SUFFIXES or not is_safe_artifact(target, output_dir()): return self.send_error(HTTPStatus.FORBIDDEN)
        size = target.stat().st_size; start, end = 0, size - 1; status = HTTPStatus.OK
        match = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if match:
            start = int(match.group(1) or 0); end = min(int(match.group(2) or size - 1), size - 1)
            if start > end or start >= size: return self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            status = HTTPStatus.PARTIAL_CONTENT
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix.lower() in TEXT_SUFFIXES: content_type += "; charset=utf-8"
        self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Accept-Ranges", "bytes"); self.send_header("Content-Length", str(end - start + 1))
        if status == HTTPStatus.PARTIAL_CONTENT: self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with target.open("rb") as handle: handle.seek(start); self.wfile.write(handle.read(end - start + 1))

    def _open_output(self):
        target = str(output_dir())
        if os.name == "nt": os.startfile(target)
        elif os.uname().sysname == "Darwin": subprocess.Popen(["open", target])
        else: subprocess.Popen(["xdg-open", target])


class LocalWebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(self, address=("127.0.0.1", 8765)):
        super().__init__(address, LocalWebHandler); self.manager = JobManager()
    def server_close(self):
        if hasattr(self, "manager"):
            self.manager.shutdown()
        super().server_close()


def create_server(host="127.0.0.1", port=8765) -> LocalWebServer:
    if host != "127.0.0.1": raise ValueError("仅允许绑定 127.0.0.1")
    try: return LocalWebServer((host, port))
    except OSError:
        if port != 8765: raise
        return LocalWebServer((host, 0))


def main():
    parser = argparse.ArgumentParser(description="本地素材混剪工作台")
    parser.add_argument("--port", type=int, default=8765); args = parser.parse_args()
    server = create_server(port=args.port)
    print(f"本地工作台：http://127.0.0.1:{server.server_port}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
