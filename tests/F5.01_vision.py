"""FB1/FB2/FB3/FB4: real decoding + HTTP, schema gates, explicit opt-in and cancellation."""

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(Path(__file__).parent)]
from blocs.vision import logic
from blocs.vision.schema import validate_schema, envelope, validate_result
from blocs.vision.block import VisionBlock
from bloxsmith_app.block_api import BlockRuntimeContext
from vision_fixture import image, provider, response, SECRET, REF
from PIL import Image


def refused(call):
    try:
        call()
    except (ValueError, OSError):
        return
    raise AssertionError("Invalid input was accepted")


def main():
    for cfg in ({"api_key_ref": SECRET}, {"api_key": SECRET}, {"remote_enabled": 1}, {"model": "arbitrary"},
        {"max_bytes": True}, {"timeout_sec": 0}, {"result_schema": {"$ref": "https://invalid/schema"}}, {"instruction": "\ud800"}):
        refused(lambda: logic.configuration(cfg))
    for schema in ({"type": "object", "properties": {"x": {"type": "string"}}},
        {"type": "object", "properties": {"x": {"type": "string", "pattern": ".*"}}, "required": ["x"], "additionalProperties": False}):
        refused(lambda: validate_schema(schema))
    for value in ("https://image.invalid/x", "data:image/png;base64,x", {"path": "x", "api_key": SECRET},
                  '{"path":"a","path":"b"}', {"path": "a", "request_id": "../bad"}):
        refused(lambda: logic.request(value, "Describe"))
    with TemporaryDirectory() as temporary, provider() as api, patch.object(logic, "ENDPOINT", api.url):
        root = Path(temporary); picture = image(root); original = picture.read_bytes()
        cfg = {"remote_enabled": True, "api_key_ref": REF, "allowed_root": str(root), "max_side": 256}
        resolver = Mock(return_value=SECRET)
        refused(lambda: logic.analyze(str(picture), "Describe", {}, root, resolver))
        assert not resolver.called and not api.requests
        result = logic.analyze({"path": str(picture), "request_id": "image-1", "task": {"instruction": "Check blue pixels"}},
                               "Ignored fallback", cfg, root, resolver)
        assert result["result"]["request_id"] == "image-1"
        assert result["result"]["data"]["summary"] == "Synthetic blue panel"
        assert result["evidence"]["verified"] is False
        assert result["result"]["source"]["sha256"] == hashlib.sha256(original).hexdigest()
        sent = api.requests[-1]
        assert sent["path"] == "/v1/responses" and sent["authorization"] == "Bearer " + SECRET
        body = sent["body"]
        assert body["store"] is False and body["stream"] is False and "tools" not in body and "previous_response_id" not in body
        assert body["text"]["format"]["strict"] is True and body["input"][0]["content"][0]["text"] == "Check blue pixels"
        sanitized = base64.b64decode(body["input"][0]["content"][1]["image_url"].split(",", 1)[1])
        assert b"PRIVATE_METADATA" not in sanitized and SECRET not in json.dumps(result)
        with Image.open(io.BytesIO(sanitized)) as prepared:
            assert prepared.size == (128, 256) and not prepared.getexif()
        assert picture.read_bytes() == original
        before = len(api.requests)
        for path in ("../escape.jpg", "https://invalid/x.jpg"):
            refused(lambda: logic.analyze(path, "Describe", cfg, root, resolver))
        link = root / "link.jpg"; link.symlink_to(picture)
        refused(lambda: logic.analyze(str(link), "Describe", cfg, root, resolver))
        multi = root / "multi.tiff"
        Image.new("RGB", (40, 40)).save(multi, save_all=True, append_images=[Image.new("RGB", (40, 40))])
        refused(lambda: logic.analyze(str(multi), "Describe", cfg, root, resolver))
        refused(lambda: logic.analyze(str(picture), "Describe", {**cfg, "max_pixels": 65536}, root, resolver))
        assert len(api.requests) == before
        for mode in ("rate", "redirect", "incomplete", "bad_schema", "refusal", "large"):
            api.mode = mode
            before = len(api.requests)
            refused(lambda: logic.analyze(str(picture), "Describe", cfg, root, resolver))
            assert len(api.requests) == before + 1, "No retry or redirect is allowed"
        api.mode = "hold"; before = time.monotonic(); requests_before = len(api.requests)
        refused(lambda: logic.analyze(str(picture), "Describe", cfg, root, resolver,
            lambda: len(api.requests) > requests_before and time.monotonic()-before > 0.3))
        assert time.monotonic()-before < 1.5
        assert len(api.requests) == requests_before+1, "Cancellation must cover a submitted HTTP request"
        api.release.set(); api.mode = "ok"
        # Public runtime context tracks updated values by name, never visual port order.
        ports = lambda names: tuple(SimpleNamespace(id=index+1, name=name) for index, name in enumerate(names))
        ctx = BlockRuntimeContext(kind="vision", config=cfg, root_dir=root, input_ports=ports(["image", "task"])[::-1],
            output_ports=ports(["result", "evidence", "issues"])[::-1], services={"resolve_secret": resolver})
        block = VisionBlock()
        before = len(api.requests)
        block.prepare_runtime(ctx)
        assert len(api.requests) == before
        ctx.input_attribute("task").update("Look for blue")
        assert block.execute_runtime(ctx).status == "skipped"
        ctx.mark_inputs_consumed()
        ctx.input_attribute("image").update(str(picture))
        executed = block.execute_runtime(ctx)
        assert {o.port_name for o in executed.outputs} == {"result", "evidence", "issues"}
        assert api.requests[-1]["body"]["input"][0]["content"][0]["text"] == "Look for blue"
        ctx.mark_inputs_consumed(); before = len(api.requests)
        ctx.input_attribute("task").update("Changed task")
        assert block.execute_runtime(ctx).status == "skipped" and len(api.requests) == before
        ctx.mark_inputs_consumed(); ctx.input_attribute("image").update(json.dumps({"path": str(picture), "request_id": "failed-image"}))
        ctx.services["resolve_secret"] = Mock(side_effect=RuntimeError(SECRET))
        error = block.execute_runtime(ctx)
        assert [o.port_name for o in error.outputs] == ["issues"] and SECRET not in str(error)
        assert json.loads(error.outputs[0].value)["request_id"] == "failed-image"
        assert len(api.requests) == before
    print("[ok] Vision real JPEG preparation/HTTP, opt-in, wallet boundary, structured errors and cancellation")


if __name__ == "__main__":
    main()
