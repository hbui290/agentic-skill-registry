import os
import json

def main():
    skills_dir = "/Users/winston/.agents/skills"
    manifest_path = os.path.join(skills_dir, ".antigravity-install-manifest.json")
    
    if not os.path.exists(manifest_path):
        print(f"Error: Manifest file not found at {manifest_path}")
        return

    with open(manifest_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    entries = data.get("entries", [])
    print(f"Manifest has {len(entries)} entries.")

    # Check for duplicates
    if len(entries) != len(set(entries)):
        print("ERROR: Duplicate entries found in manifest.")
        seen = set()
        dupes = []
        for x in entries:
            if x in seen:
                dupes.append(x)
            seen.add(x)
        print("Duplicates:", dupes)
    else:
        print("SUCCESS: No duplicate entries in manifest.")

    # Verify each exists on disk
    missing = []
    for entry in entries:
        path = os.path.join(skills_dir, entry)
        if not os.path.exists(path) or not os.path.isdir(path):
            missing.append(entry)

    if missing:
        print(f"ERROR: {len(missing)} manifest entries do not exist on disk!")
        print("Missing:", missing[:10])
    else:
        print("SUCCESS: Every manifest entry exists on disk!")

if __name__ == "__main__":
    main()
