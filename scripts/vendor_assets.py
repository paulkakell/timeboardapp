"""Copy npm-locked browser assets and their license notices into the image."""
from pathlib import Path
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parents[1]
files = {
    "bootstrap.min.css": "bootstrap/dist/css/bootstrap.min.css",
    "bootstrap.bundle.min.js": "bootstrap/dist/js/bootstrap.bundle.min.js",
    "bootstrap-LICENSE": "bootstrap/LICENSE",
    "fullcalendar.min.js": "fullcalendar/index.global.min.js",
    "fullcalendar-LICENSE.md": "fullcalendar/LICENSE.md",
}
out = ROOT / "app/static/vendor"
out.mkdir(parents=True, exist_ok=True)
manifest = {}
for name, source in files.items():
    data = (ROOT / "node_modules" / source).read_bytes()
    # Do not request absent development source maps from production browsers.
    if name.endswith((".js", ".css")):
        import re
        data = re.sub(rb"(?m)^//# sourceMappingURL=.*$", b"", data)
        data = re.sub(rb"/\*# sourceMappingURL=.*?\*/", b"", data)
    (out / name).write_bytes(data)
    manifest[name] = hashlib.sha256(data).hexdigest()
(out / "SHA256SUMS.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
print(f"Vendored {len(files)} files with licenses and checksums")
