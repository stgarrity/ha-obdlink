"""Build the HACS release ZIP without caches or local instance files."""

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    integration = root / "custom_components" / "obdlink_ble"
    manifest = json.loads((integration / "manifest.json").read_text())
    if manifest["domain"] != "obdlink_ble":
        raise ValueError("Unexpected integration domain.")
    files = sorted(
        path for path in integration.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
        and path.suffix in {".py", ".json", ".yaml", ".png"}
    )
    output = root / "dist" / "obdlink_ble.zip"
    output.parent.mkdir(exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(integration))
    print(f"Built HACS integration {manifest['version']}: {output.name}")


if __name__ == "__main__":
    main()
