import os
import json
import shutil

skills_dir = "/Users/winston/.agents/skills"
flat_dir = "/Users/winston/.agents/flat-skills"
plugin_skills_dir = "/Users/winston/.claude/plugins/cache/superpowers-marketplace/superpowers/6.0.3/skills"
manifest_path = os.path.join(skills_dir, ".antigravity-install-manifest.json")

# Default 14 skills that come with superpowers
DEFAULT_SKILLS = {
    "brainstorming", "dispatching-parallel-agents", "executing-plans",
    "finishing-a-development-branch", "receiving-code-review", "requesting-code-review",
    "subagent-driven-development", "systematic-debugging", "test-driven-development",
    "using-git-worktrees", "using-superpowers", "verification-before-completion",
    "writing-plans", "writing-skills"
}

def clean_dir(target_dir, keep_defaults=False):
    if os.path.exists(target_dir):
        for name in os.listdir(target_dir):
            if keep_defaults and name in DEFAULT_SKILLS:
                continue
            path = os.path.join(target_dir, name)
            try:
                if os.path.islink(path) or os.path.isfile(path):
                    os.unlink(path)
                elif os.path.isdir(path):
                    shutil.rmtree(path)
            except Exception as e:
                print(f"Error removing {path}: {e}")
    else:
        os.makedirs(target_dir, exist_ok=True)

def sync_skill(src_path, dest_skill_dir):
    os.makedirs(dest_skill_dir, exist_ok=True)
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
                continue
        try:
            os.symlink(src_item, dest_item)
        except Exception as e:
            print(f"Error symlinking {src_item} -> {dest_item}: {e}")

def main():
    if not os.path.exists(manifest_path):
        print("Manifest file not found!")
        return
        
    with open(manifest_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    entries = data.get("entries", [])
    
    # Clean targets
    clean_dir(flat_dir, keep_defaults=False)
    clean_dir(plugin_skills_dir, keep_defaults=True)
    
    # Count occurrences of base names to detect duplicates
    base_names = {}
    for entry in entries:
        base = os.path.basename(entry)
        base_names[base] = base_names.get(base, 0) + 1
        
    created_flat = 0
    created_plugin = 0
    duplicates_resolved = 0
    
    for entry in entries:
        src_path = os.path.join(skills_dir, entry)
        if not os.path.isdir(src_path):
            continue
            
        base = os.path.basename(entry)
        # If duplicated, use slug name
        if base_names[base] > 1:
            symlink_name = entry.replace("/", "-")
            duplicates_resolved += 1
        else:
            symlink_name = base
            
        # Sync to flat_dir
        dest_flat_dir = os.path.join(flat_dir, symlink_name)
        sync_skill(src_path, dest_flat_dir)
        created_flat += 1
        
        # Sync to plugin_skills_dir (if not one of the default 14 skills to avoid overwriting them)
        if symlink_name not in DEFAULT_SKILLS:
            dest_plugin_dir = os.path.join(plugin_skills_dir, symlink_name)
            sync_skill(src_path, dest_plugin_dir)
            created_plugin += 1
            
    print(f"Flat directory: Synced {created_flat} skills.")
    print(f"Plugin directory: Synced {created_plugin} skills.")
    print(f"Resolved {duplicates_resolved} duplicate names.")

if __name__ == "__main__":
    main()
