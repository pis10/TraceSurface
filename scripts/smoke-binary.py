"""Exercise the packaged report and AI path without external model services or keys."""

from __future__ import annotations

import json
import os
import signal
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import Request, urlopen


class ModelHandler(BaseHTTPRequestHandler):
    calls: list[str] = []

    def log_message(self, *_):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).calls.append(self.path)
        assert not request.get("tools"), "Analysis must not call tools"
        result = {
            "results": [
                {
                    "replay_id": 1,
                    "suspected": False,
                    "reason": "Login required",
                    "recommendation": "",
                }
            ]
        }
        if self.path == "/v1/chat/completions":
            response = {
                "id": "smoke",
                "object": "chat.completion",
                "created": 1,
                "model": request["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps(result)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 10, "total_tokens": 22},
            }
        else:
            assert self.path == "/v1beta/models/smoke-model:generateContent", self.path
            assert request.get("contents"), "Gemini request contents missing"
            response = {
                "candidates": [{
                    "content": {"role": "model", "parts": [{"text": json.dumps(result)}]},
                    "finishReason": "STOP",
                    "index": 0,
                }],
                "usageMetadata": {
                    "promptTokenCount": 12,
                    "candidatesTokenCount": 10,
                    "totalTokenCount": 22,
                },
                "modelVersion": "smoke-model",
            }
        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    binary = Path(sys.argv[1]).resolve()
    subprocess.run([str(binary), "--version"], check=True)
    subprocess.run([str(binary), "--help"], check=True, stdout=subprocess.DEVNULL)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    mock = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    thread = threading.Thread(target=mock.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="tracesurface-smoke-") as directory:
            env = {**os.environ, "TRACESURFACE_HOME": directory}
            with open(Path(directory) / "server.log", "w+") as log:
                process = subprocess.Popen(
                    [str(binary), "serve", "--port", str(port)],
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=os.name != "nt",
                )
                base = f"http://127.0.0.1:{port}"
                try:
                    deadline = time.monotonic() + 120
                    while True:
                        if process.poll() is not None:
                            raise RuntimeError("Packaged report exited during startup")
                        try:
                            with urlopen(
                                base + "/api/ai/catalog", timeout=3
                            ) as response:
                                catalog = json.load(response)
                            break
                        except (URLError, TimeoutError):
                            if time.monotonic() >= deadline:
                                raise TimeoutError(
                                    "Packaged report did not become ready"
                                )
                            time.sleep(0.5)
                    assert {"deepseek", "moonshotai", "zai", "anthropic", "google"} <= {
                        p["id"] for p in catalog["providers"]
                    }
                    assert catalog["source"].startswith("Pydantic AI ")
                    with urlopen(base, timeout=5) as response:
                        assert b"/static/assets/" in response.read(), (
                            "Frontend assets missing"
                        )
                    config = {
                        "mode": "custom",
                        "provider_id": "openai-chat",
                        "model": "smoke-model",
                        "api_base": f"http://127.0.0.1:{mock.server_port}/v1",
                        "api_key": "smoke-only",
                        "context_window": 32000,
                        "max_output_tokens": 1024,
                    }
                    for provider, suffix in (("openai-chat", "/v1"), ("google", "")):
                        config["provider_id"] = provider
                        config["api_base"] = f"http://127.0.0.1:{mock.server_port}{suffix}"
                        request = Request(
                            base + "/api/ai/test",
                            data=json.dumps(config).encode(),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urlopen(request, timeout=70) as response:
                            result = json.load(response)
                        assert result.get("ok") is True, (provider, result)
                    assert ModelHandler.calls == [
                        "/v1/chat/completions", "/v1beta/models/smoke-model:generateContent",
                    ], ModelHandler.calls
                    print(
                        "Packaged report, model catalog, OpenAI and Gemini requests: OK", flush=True
                    )
                except BaseException:
                    log.flush()
                    log.seek(0)
                    print(log.read(), file=sys.stderr)
                    raise
                finally:
                    if process.poll() is None:
                        if os.name == "nt":
                            # A onefile binary launches a child process holding the database.
                            subprocess.run(
                                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                check=False,
                            )
                        else:
                            os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
    finally:
        mock.shutdown()
        mock.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
