"""FB1/FB2/FB3/FB4/FB5: actual framework + wallet + loopback HTTP across both runtimes/origins."""

import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(Path(__file__).parent)]
from blocs.vision.block import VisionBlock
from block_test_packages import install_test_package, prepare_release_run, surface_payload
from vision_fixture import image, provider, prepare_http_fixture, SECRET, REF
from ui_smoke_common import (create_project_api, create_run_api, data_edge, graph_payload, http_json,
    isolated_server, stop_run_api, text_node, wait_for_run_terminal, wait_for_run_predicate)


def main():
    with provider() as api:
        for mode in ("centralized", "zeromq_active"):
            for origin in (None, "managed", "linked"):
                with isolated_server(prepare=lambda root: prepare_http_fixture(root, api)) as server:
                    picture = image(server.root_dir)
                    node = VisionBlock().build_node_payload(node_id="vision", position={"x": 450, "y": 150},
                        config_overrides={"remote_enabled": True, "api_key_ref": REF, "allowed_root": str(server.root_dir)})
                    node["inputs"].reverse(); node["outputs"].reverse()
                    if origin:
                        model = install_test_package(server, "vision", origin=origin)
                        node["block_version"] = model["version"]
                        for surface in ("modal", "inspector_panel", "node_card"):
                            rendered = surface_payload(server, model, node, surface)["html"]
                            assert "{{" not in rendered and SECRET not in rendered
                    http_json(server.base_url, "/api/application/secrets/init", method="POST", payload={"password": "temporary-vision-test"})
                    http_json(server.base_url, "/api/application/secrets", method="POST", payload={"name": "vision_fixture", "value": SECRET})
                    atomic = {"path": str(picture), "request_id": "first-image", "task": "Inspect synthetic blue pixels"}
                    document = graph_payload("Controlled Vision", [text_node("source", "Image", json.dumps(atomic) if mode == "centralized" else "", 70, 100),
                        text_node("task", "Task", "", 70, 340), node],
                        [data_edge("image", "source", 1, "vision", 1), data_edge("task", "task", 1, "vision", 2)])
                    project = create_project_api(server, document=document)["project"]
                    before = len(api.requests)
                    if mode == "centralized":
                        run = create_run_api(server, document, project_id=project["graph_id"], runtime_mode=mode)
                        result = wait_for_run_terminal(server, run["run_id"], timeout_sec=30)
                        assert result["status"] == "success", result.get("logs")
                        assert len(api.requests) == before+1
                    else:
                        rid = prepare_release_run(server, project["graph_id"], document)["run_id"]
                        assert len(api.requests) == before, "Preparation must never transmit an image"
                        http_json(server.base_url, f"/api/runs/{rid}/play", method="POST", payload={})
                        def value(result, port):
                            raw = result.get("output_values", {}).get("vision:" + str(port), {}).get("value")
                            return json.loads(raw) if raw else {}
                        def send(source, value):
                            http_json(server.base_url, f"/api/runs/{rid}/active/control", method="POST", payload={"action": "publish_output",
                                "node_id": source, "port_id": 1, "value": json.dumps(value), "content_type": "application/json"})
                        def wait(predicate):
                            result = wait_for_run_predicate(server, rid, lambda data: data.get("status") == "failed" or predicate(data),
                                "Vision did not emit the expected result", timeout_sec=25)
                            assert result["status"] != "failed", result.get("logs")
                            return result
                        try:
                            send("source", atomic)
                            result = wait(lambda data: value(data, 1).get("request_id") == "first-image")
                            assert len(api.requests) == before+1
                            send("task", {"instruction": "Updated task only"})
                            time.sleep(0.3)
                            assert len(api.requests) == before+1, "Task-only activation replayed an old image"
                            send("source", {"path": str(picture), "request_id": "second-image"})
                            result = wait(lambda data: value(data, 1).get("request_id") == "second-image")
                            assert api.requests[-1]["body"]["input"][0]["content"][0]["text"] == "Updated task only"
                            api.mode = "hold"; api.release.clear(); requests_before = len(api.requests)
                            send("source", {"path": str(picture), "request_id": "stop-pending"})
                            deadline = time.monotonic()+15
                            while len(api.requests) == requests_before and time.monotonic() < deadline:
                                time.sleep(0.03)
                            assert len(api.requests) == requests_before+1, "The HTTP request must be in flight before Stop"
                        finally:
                            started = time.monotonic()
                            stop_run_api(server, rid)
                            final = wait_for_run_terminal(server, rid, timeout_sec=15)
                            assert time.monotonic()-started < 5
                            assert value(final, 1).get("request_id") != "stop-pending", "Stop emitted an obsolete result"
                            api.release.set(); api.mode = "ok"
                    values = result["output_values"]
                    assert json.loads(values["vision:1"]["value"])["data"]["summary"] == "Synthetic blue panel", result.get("logs")
                    assert json.loads(values["vision:2"]["value"])["verified"] is False
                    assert json.loads(values["vision:3"]["value"])["code"] == "analyzed"
                    assert SECRET not in json.dumps(result)
                print("[ok] Vision real HTTP + wallet runtime " + mode + " " + str(origin), flush=True)


if __name__ == "__main__":
    main()
