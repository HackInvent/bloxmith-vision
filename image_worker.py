"""Isolated bounded image decoder; emits sanitized JPEG, never original metadata."""

import base64
import io
import json
import resource
import sys


def main():
    resource.setrlimit(resource.RLIMIT_AS, (536870912, 536870912))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16777216, 16777216))
    request = json.loads(sys.stdin.buffer.read(30000000))
    cfg = request["config"]
    resource.setrlimit(resource.RLIMIT_CPU, (cfg["timeout_sec"]+1, cfg["timeout_sec"]+1))
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return {"error": "dependency"}
    binary = base64.b64decode(request["data"], validate=True)
    with Image.open(io.BytesIO(binary), formats=["JPEG", "PNG", "WEBP", "BMP", "TIFF"]) as image:
        if getattr(image, "n_frames", 1) != 1:
            return {"error": "frames"}
        if min(image.size) < 1 or max(image.size) > 16384 or image.width*image.height > cfg["max_pixels"]:
            return {"error": "dimensions"}
        oriented = ImageOps.exif_transpose(image)
        original_size = list(oriented.size)
        oriented.thumbnail((cfg["max_side"], cfg["max_side"]), Image.Resampling.LANCZOS)
        rgba = oriented.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        background.alpha_composite(rgba)
        # A new pixel-only image prevents EXIF, ICC, comments and thumbnails leaking.
        clean = Image.new("RGB", background.size)
        clean.paste(background.convert("RGB"))
        output = io.BytesIO()
        clean.save(output, format="JPEG", quality=90, optimize=False)
        if len(output.getvalue()) > 8388608:
            return {"error": "size"}
        return {"data": base64.b64encode(output.getvalue()).decode("ascii"),
            "width": clean.width, "height": clean.height, "oriented_source_size": original_size,
            "mime_type": "image/jpeg"}


if __name__ == "__main__":
    try:
        result = main()
    except Exception:
        result = {"error": "decode"}
    sys.stdout.write(json.dumps(result))
