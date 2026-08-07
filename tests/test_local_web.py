from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
import socket
from pathlib import Path

import pytest

from local_web_app.jobs import JobManager
from local_web_app.services import environment_check, normalize_product, parse_list, preflight_fingerprint
from local_web_app.validation import AssetPathError, is_safe_artifact, validate_asset_directory


def test_asset_path_validation_allows_any_readable_directory_but_rejects_output(tmp_path):
    assets = tmp_path / "campaign"; assets.mkdir()
    (assets / "clip.mp4").write_bytes(b"video")
    output = tmp_path / "output"; output.mkdir()
    assert validate_asset_directory(assets, output_dir=output) == assets.resolve()
    with pytest.raises(AssetPathError): validate_asset_directory(output, output_dir=output)


def test_asset_path_validation_requires_readable_videos(tmp_path):
    root = tmp_path / "videos"; root.mkdir(); assets = root / "campaign"; assets.mkdir()
    clip = assets / "clip.mp4"; clip.write_bytes(b"video")
    output = tmp_path / "output"; output.mkdir()
    clip.chmod(0)
    try:
        with pytest.raises(AssetPathError, match="不可读"):
            validate_asset_directory(assets, root, output)
    finally:
        clip.chmod(0o600)


def test_product_lists_normalize():
    assert parse_list("苹果，梨, 香蕉") == ["苹果", "梨", "香蕉"]
    product = normalize_product({"name": "茶", "verified_claims": "无添加，烘焙", "ingredients": ["茶叶"]})
    assert product["verified_claims"] == ["无添加", "烘焙"]
    assert product["ingredients"] == ["茶叶"]
    assert product["production_process"] == []


def test_artifact_protection(tmp_path):
    output = tmp_path / "output"; output.mkdir(); good = output / "final.mp4"; good.write_bytes(b"x")
    assert is_safe_artifact(good, output)
    assert not is_safe_artifact(tmp_path / "secret.txt", output)


def test_presented_artifacts_exclude_intermediate_text(tmp_path, monkeypatch):
    import local_web_app.server as server_module
    monkeypatch.setattr(server_module, "output_dir", lambda: tmp_path)
    final = tmp_path / "茶咖_abc_final.mp4"; final.write_bytes(b"video")
    (tmp_path / "茶咖_abc_发布文案.txt").write_text("标题", encoding="utf-8")
    (tmp_path / "茶咖_abc_script.json").write_text("{}", encoding="utf-8")
    (tmp_path / "茶咖_abc_debug.json").write_text("{}", encoding="utf-8")
    artifacts = server_module.artifacts_for(final, prefix="茶咖_abc")
    assert [item["label"] for item in artifacts] == ["脚本", "最终成片"]


def test_run_requires_matching_preflight():
    manager = JobManager()
    with pytest.raises(RuntimeError, match="预检"):
        manager.start("run", {"preflight_key": "stale"})


def test_run_accepts_only_matching_preflight(monkeypatch):
    manager = JobManager()
    manager.last_preflight = {"key": "ready"}
    import local_web_app.jobs as jobs_module
    monkeypatch.setattr(jobs_module.os, "setpgid", lambda *_args: None)
    monkeypatch.setattr(manager, "process_factory", lambda **kwargs: type("Process", (), {
        "daemon": False, "pid": 12345, "start": lambda self: None, "is_alive": lambda self: False,
    })())
    status = manager.start("run", {"preflight_key": "ready"})
    assert status["kind"] == "run"


def test_environment_reports_api_key_state(monkeypatch):
    import config
    monkeypatch.setattr(config, "VISION_API_KEY", "")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    state = environment_check(output_dir="/tmp")
    assert state["vision_api_key"]["ok"] is False
    assert state["llm_api_key"]["ok"] is False


def test_port_falls_back_when_default_is_busy():
    import local_web_app.server as server_module
    occupied = socket.socket()
    try:
        occupied.bind(("127.0.0.1", 8765))
    except OSError:
        occupied.close()
        pytest.skip("8765 已被其他本地服务占用")
    occupied.listen(1)
    server = server_module.create_server(port=8765)
    try:
        assert server.server_port != 8765
    finally:
        server.server_close(); occupied.close()


def test_http_smoke(monkeypatch, tmp_path):
    import local_web_app.server as server_module
    monkeypatch.setattr(server_module, "output_dir", lambda: tmp_path)
    (tmp_path / "final").mkdir()
    server = server_module.create_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        body = json.loads(urllib.request.urlopen(base + "/api/environment").read())
        assert "asset_root" not in body
        assert urllib.request.urlopen(base + "/").status == 200
        (tmp_path / "final.mp4").write_bytes(b"video")
        (tmp_path / "caption.txt").write_text("中文", encoding="utf-8")
        (tmp_path / "secret.py").write_text("secret", encoding="utf-8")
        assert urllib.request.urlopen(base + "/api/artifact?path=final.mp4").status == 200
        caption = urllib.request.urlopen(base + "/api/artifact?path=caption.txt")
        assert caption.headers["Content-Type"] == "text/plain; charset=utf-8"
        with pytest.raises(urllib.error.HTTPError) as forbidden:
            urllib.request.urlopen(base + "/api/artifact?path=secret.py")
        assert forbidden.value.code == 403
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_completed_run_render_is_deduplicated():
    source = Path("local_web_app/static/app.js").read_text(encoding="utf-8")
    assert "status.id !== completedRunId" in source
