import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bikeshed.config import OpenAIBackend
from bikeshed.models.base import ModelError
from bikeshed.models.openai_compat import OpenAICompatClient

CHAT_REPLY = {
    "id": "x", "object": "chat.completion", "created": 0, "model": "qwen-test",
    "choices": [{
        "index": 0, "finish_reason": "stop",
        "message": {"role": "assistant", "content": '{"action": {"type": "pass"}}', "reasoning_content": "thinking..."},
    }],
    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
}
MODELS_REPLY = {"object": "list", "data": [{"id": "qwen-test", "object": "model", "created": 0, "owned_by": "omlx"}]}
EMBED_REPLY = {"object": "list", "model": "e", "data": [
    {"object": "embedding", "index": 1, "embedding": [0.0, 1.0]},
    {"object": "embedding", "index": 0, "embedding": [1.0, 0.0]},
], "usage": {"prompt_tokens": 2, "total_tokens": 2}}


class _Handler(BaseHTTPRequestHandler):
    def _send(self, payload):
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append((self.path, body))
        self._send(EMBED_REPLY if self.path.endswith("/embeddings") else CHAT_REPLY)

    def do_GET(self):
        self._send(MODELS_REPLY)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.requests = []
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def backend(srv, **kwargs):
    return OpenAIBackend(kind="openai", model="qwen-test", base_url=f"http://127.0.0.1:{srv.server_port}/v1", **kwargs)


async def test_chat_sends_system_messages_and_schema(server):
    client = OpenAICompatClient(backend(server))
    result = await client.chat("SYS", [{"role": "user", "content": "hi"}], json_schema={"type": "object"})
    path, body = server.requests[0]
    assert path == "/v1/chat/completions"
    assert body["model"] == "qwen-test"
    assert body["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == {"type": "object"}
    assert result.text == '{"action": {"type": "pass"}}'
    assert result.reasoning == "thinking..."
    assert result.model == "qwen-test"
    assert result.input_tokens == 10


async def test_schema_omitted_when_disabled(server):
    client = OpenAICompatClient(backend(server, json_schema=False))
    await client.chat("S", [], json_schema={"type": "object"})
    assert "response_format" not in server.requests[0][1]


async def test_list_models(server):
    assert await OpenAICompatClient(backend(server)).list_models() == ["qwen-test"]


async def test_unreachable_server_raises_model_error():
    client = OpenAICompatClient(OpenAIBackend(kind="openai", model="m", base_url="http://127.0.0.1:9/v1", timeout_s=2))
    with pytest.raises(ModelError):
        await client.chat("S", [])


async def test_thinking_left_to_server_by_default(server):
    await OpenAICompatClient(backend(server)).chat("S", [])
    assert "chat_template_kwargs" not in server.requests[0][1]


@pytest.mark.parametrize("thinking", [False, True])
async def test_thinking_sent_as_chat_template_kwarg(server, thinking):
    await OpenAICompatClient(backend(server, thinking=thinking)).chat("S", [])
    assert server.requests[0][1]["chat_template_kwargs"] == {"enable_thinking": thinking}


async def test_thinking_drops_the_schema_because_oMLX_skips_thinking_under_one(server):
    await OpenAICompatClient(backend(server, thinking=True)).chat("S", [], json_schema={"type": "object"})
    assert "response_format" not in server.requests[0][1]


class _AwkwardHandler(BaseHTTPRequestHandler):
    """Mimics oMLX under memory pressure: keepalive bytes, or an error body after a 200."""

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps({"error": {"message": "Request aborted: process memory limit exceeded"}}).encode()
        trickle = self.server.trickle_s
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body) + int(trickle * 20)))
        self.end_headers()
        try:
            for _ in range(int(trickle * 20)):
                self.wfile.write(b" ")
                self.wfile.flush()
                time.sleep(0.05)
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


def awkward_server(trickle_s):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _AwkwardHandler)
    srv.trickle_s = trickle_s
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


async def test_error_body_inside_a_200_is_a_model_error():
    srv = awkward_server(0)
    try:
        with pytest.raises(ModelError, match="memory limit"):
            await OpenAICompatClient(backend(srv)).chat("S", [])
    finally:
        srv.shutdown()
        srv.server_close()


async def test_timeout_is_a_total_deadline_despite_keepalive_bytes():
    srv = awkward_server(3)
    try:
        started = time.monotonic()
        with pytest.raises(ModelError, match="timed out"):
            await OpenAICompatClient(backend(srv, timeout_s=0.5)).chat("S", [])
        assert time.monotonic() - started < 2
    finally:
        srv.shutdown()
        srv.server_close()


async def test_embed_returns_vectors_in_input_order(server):
    vectors = await OpenAICompatClient(backend(server)).embed(["first", "second"])
    path, body = server.requests[0]
    assert path == "/v1/embeddings" and body["input"] == ["first", "second"] and body["model"] == "qwen-test"
    assert vectors == [[1.0, 0.0], [0.0, 1.0]]