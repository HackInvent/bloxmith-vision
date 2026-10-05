"""Public BloxSmith integration: image events trigger work, task events only update context."""

import json
from bloxsmith_app.block_api import BlockDefinition, BlockRuntimeOutput, BlockRuntimePreparation, BlockRuntimeResult
from . import logic, ui


# FB1 - Fresh image event triggers one task-bound request; task-only events never replay an image.
# FB2 - Explicit cloud opt-in, wallet-only secrets and stable bounded local image sanitization.
# FB3 - Strict structured responses separate result, model-proposed evidence and uncertainties.
# FB4 - Bounded cancellation/no retry and content-free recoverable error outputs.
# FB5 - Public runtime/package contracts, translated responsive UI and exact draft persistence.
class VisionBlock(BlockDefinition):
    kind = "vision"

    def ui_assets(self, surface="modal"):
        return list(self.model.get("ui_assets", {}).get(surface, []))

    def prepare_runtime(self, context):
        logic.configuration(context.config)
        return BlockRuntimePreparation()

    def execute_runtime(self, context):
        request_id = None
        try:
            cfg = logic.configuration(context.config)
            by_name = {port.name: port.id for port in context.input_ports}
            if context.input_events:
                images = [event.value for event in context.input_events if event.input_port_id == by_name.get("image")]
            else:
                attribute = context.input_attribute("image", "1")
                images = [attribute.value] if attribute and attribute.status == "updated" else []
            if not images:
                return BlockRuntimeResult(status="skipped", outputs=[])
            if len(images) != 1:
                raise logic.VisionError("Send one image per activation.")
            instruction = context.input_value("task", "2") if context.has_input_value("task", "2") else cfg["instruction"]
            path, instruction, request_id = logic.request(images[0], instruction)
            values = logic.analyze({"path": path, "task": instruction, "request_id": request_id}, instruction, cfg, context.root_dir,
                context.services.get("resolve_secret"), context.services.get("cancel_requested", lambda: False))
        except (ValueError, TypeError, RuntimeError, OSError) as exc:
            detail = str(exc) if isinstance(exc, logic.VisionError) else "Analysis unavailable; check the input, dependencies and connection."
            values = {"issues": {"request_id": request_id, "code": "cancelled" if isinstance(exc, logic.Cancelled) else "error",
                "items": [detail], "human_review_required": True}}
        outputs = [BlockRuntimeOutput(port_id=int(port.id), port_name=port.name,
            value=json.dumps(values[port.name], ensure_ascii=True, allow_nan=False), content_type="application/json")
            for port in context.output_ports if port.name in values]
        return BlockRuntimeResult(status="success", outputs=outputs,
            metadata={self.kind: {"code": values["issues"]["code"]}}, logs=["[vision] " + values["issues"]["code"]])

    def handle_ui_action(self, *, node, action, values, payload=None):
        if action in {"save_settings", "modal_update_fields", "inspector_update_fields"}:
            patch = (values or {}).get("node_patch") or {}
            if "config" in patch:
                try:
                    logic.configuration({**self.default_config(), **(node.get("config") or {}), **patch["config"]})
                except (ValueError, TypeError) as exc:
                    return {"error": self.translate("block.vision.error", {"detail": str(exc)}, fallback="Invalid settings: {detail}")}
        return ui.replace_config(super().handle_ui_action(node=node,
            action="modal_update_fields" if action == "save_settings" else action, values=values, payload=payload), node)

    def render_modal(self, *, node, payload=None):
        return ui.modal(self, node, payload)

    def render_inspector_panel(self, *, node, payload=None):
        return ui.inspector(self, node, payload)

    def render_node_card(self, *, node, payload=None):
        cfg = {**self.default_config(), **(node.get("config") or {})}
        key = "enabled" if cfg.get("remote_enabled") else "disabled"
        return ui.card(self, node, self.translate("block.vision." + key, fallback=key))
