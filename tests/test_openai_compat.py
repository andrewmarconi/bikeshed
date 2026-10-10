import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aiswarm.config import OpenAIBackend
from aiswarm.models.base import ModelError
from aiswarm.models.openai_compat import OpenAICompatClient

CHAT_REPLY = {
    "id": "x", "object": "chat.completion", "created": 0, "model": "qwen-test",
    "choices": [{
        "index": 0, "finish_reason": "stop",
        "message": {"role": "assistant", "content": '{"action": {"type": "pass"}}', "reasoning_content": "thinking..."},
    }],
    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
}
MODELS_REPLY = {"object": "list", "data": [{"id": "qwen-test", "object": "model", "created": 0, "owned_by": "omlx"}]}


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
        self._send(CHAT_REPLY)

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
