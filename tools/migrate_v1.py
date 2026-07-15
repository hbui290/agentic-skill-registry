import json
import os
from pathlib import Path


MACROS = (
    "ai-and-data", "andruia", "business-and-finance", "devops-and-security",
    "engineering", "marketing-and-seo", "product-and-design",
    "productivity-and-content", "workflows-and-management",
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    os.replace(temp, path)


def migrate(root: Path) -> list[str]:
    source_manifest = root / ".antigravity-install-manifest.json"
    payload = json.loads(source_manifest.read_text())
    original_entries = payload["entries"]
    catalog = root / "catalog"
    catalog.mkdir(exist_ok=True)
    for macro in MACROS:
        source = root / macro
        if source.exists():
            source.rename(catalog / macro)
    migrated = sorted(f"catalog/{entry}" for entry in original_entries)
    write_json(root / "registry/migration/legacy-manifest.json", {
        "schema_version": 1,
        "source_commit": "a3f3ac3bb434884b9847cf6df43a534ec00a6d71",
        "entries": migrated,
    })
    return migrated


if __name__ == "__main__":
    result = migrate(Path(__file__).resolve().parents[1])
    print(f"migrated {len(result)} legacy records")
