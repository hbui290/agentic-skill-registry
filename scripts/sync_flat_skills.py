import os
import json
import shutil
import sys

skills_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
flat_dir = os.path.abspath(os.path.join(skills_dir, "..", "flat-skills"))
manifest_path = os.path.join(skills_dir, ".antigravity-install-manifest.json")

def clean_dir(target_dir):
    ok = True
    if os.path.exists(target_dir):
        for name in os.listdir(target_dir):
            path = os.path.join(target_dir, name)
            try:
                if os.path.islink(path) or os.path.isfile(path):
                    os.unlink(path)
                elif os.path.isdir(path):
                    shutil.rmtree(path)
            except Exception as e:
                print(f"Error removing {path}: {e}")
                ok = False
    else:
        os.makedirs(target_dir, exist_ok=True)
    return ok

def sync_skill(src_path, dest_skill_dir):
    os.makedirs(dest_skill_dir, exist_ok=True)
    ok = True
    # Symlink all contents of src_path into dest_skill_dir
    for item in os.listdir(src_path):
        src_item = os.path.join(src_path, item)
        dest_item = os.path.join(dest_skill_dir, item)
        if os.path.exists(dest_item) or os.path.islink(dest_item):
            try:
                if os.path.islink(dest_item) or os.path.isfile(dest_item):
                    os.unlink(dest_item)
                elif os.path.isdir(dest_item):
                    shutil.rmtree(dest_item)
            except Exception as e:
                print(f"Error cleaning {dest_item}: {e}")
                ok = False
                continue
        try:
            os.symlink(src_item, dest_item)
        except Exception as e:
            print(f"Error symlinking {src_item} -> {dest_item}: {e}")
            ok = False
    if not ok:
        shutil.rmtree(dest_skill_dir, ignore_errors=True)
    return ok

def _remove_path(path):
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)

def sweep_sync_dirs():
    parent = os.path.dirname(flat_dir)
    name = os.path.basename(flat_dir)
    stage = os.path.join(parent, f".{name}.tmp-sync")
    backup = os.path.join(parent, f".{name}.bak-sync")
    os.makedirs(parent, exist_ok=True)
    if not os.path.exists(flat_dir) and os.path.exists(backup):
        os.replace(backup, flat_dir)
    elif os.path.exists(flat_dir) and os.path.exists(backup):
        _remove_path(backup)
    if os.path.exists(stage):
        _remove_path(stage)
    return stage, backup

def main():
    if not os.path.exists(manifest_path):
        print("Manifest file not found!")
        return 1
        
    with open(manifest_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    entries = data.get("entries", [])

    try:
        stage, backup = sweep_sync_dirs()
        os.makedirs(stage)
    except Exception as e:
        print(f"Error preparing flat sync: {e}")
        return 1

    # Shared flat-naming rule (same helper the librarian index uses)
    from update_skills import flat_name_map
    fmap = flat_name_map(entries)

    created_flat = 0
    duplicates_resolved = 0

    for entry in entries:
        src_path = os.path.join(skills_dir, entry)
        if not os.path.isdir(src_path):
            continue

        symlink_name = fmap[entry]
        if symlink_name != os.path.basename(entry):
            duplicates_resolved += 1

        # Sync to flat_dir
        dest_flat_dir = os.path.join(stage, symlink_name)
        if sync_skill(src_path, dest_flat_dir):
            created_flat += 1
        else:
            _remove_path(stage)
            print(f"Flat directory: Sync failed after {created_flat} skills; active directory preserved.")
            return 1

    try:
        had_active = os.path.exists(flat_dir)
        if had_active:
            os.replace(flat_dir, backup)
        try:
            os.replace(stage, flat_dir)
        except Exception:
            if had_active and os.path.exists(backup) and not os.path.exists(flat_dir):
                os.replace(backup, flat_dir)
            if os.path.exists(stage):
                _remove_path(stage)
            raise
        if os.path.exists(backup):
            _remove_path(backup)
    except Exception as e:
        print(f"Error replacing flat directory: {e}")
        return 1
            
    print(f"Flat directory: Synced {created_flat} skills.")
    print(f"Resolved {duplicates_resolved} duplicate names.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
