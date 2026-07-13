import os
import re
import shutil
import subprocess
import json
import hashlib
import sys
import tempfile
from datetime import datetime, timezone

skills_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
manifest_path = os.path.join(skills_dir, ".antigravity-install-manifest.json")
readme_path = os.path.join(skills_dir, "README.md")
sources_path = os.path.join(skills_dir, "sources.json")
data_dir = os.path.join(skills_dir, "data")
reports_dir = os.path.join(skills_dir, "reports")
origins_path = os.path.join(data_dir, "origins.json")
similars_path = os.path.join(data_dir, "similars.json")
librarian_index_path = os.path.join(skills_dir, "librarian-index.json")

# Naming mappings for classifications
MACRO_CATEGORIES = [
    "ai-and-data", "andruia", "business-and-finance", "devops-and-security",
    "engineering", "marketing-and-seo", "product-and-design",
    "productivity-and-content", "workflows-and-management"
]

def run_cmd(cmd, cwd=None):
    # cmd is an argv list — never a shell string (source URLs are untrusted input).
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        print(f"Error executing: {' '.join(cmd)}")
        print(result.stderr)
        return False, result.stderr
    return True, result.stdout

def find_leaf_skills():
    # A skill dir is the shallowest dir under a macro category that directly
    # contains regular files (handles skills nested at any depth).
    leafs = []
    for macro in MACRO_CATEGORIES:
        macro_path = os.path.join(skills_dir, macro)
        if not os.path.exists(macro_path):
            continue
        for dirpath, dirs, files in os.walk(macro_path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            if dirpath != macro_path and any(not f.startswith('.') for f in files):
                leafs.append(os.path.relpath(dirpath, skills_dir))
                dirs[:] = []  # do not descend into a skill
    return sorted(leafs)

def get_current_skill_mapping():
    # Map from skill_folder_name -> absolute_path of its parent directory,
    # plus names that occur at more than one path and are unsafe to update.
    mapping = {}
    dups = set()
    for rel in find_leaf_skills():
        base = os.path.basename(rel)
        if base in mapping:
            dups.add(base)
        mapping[base] = os.path.join(skills_dir, os.path.dirname(rel))
    if dups:
        print(f"⚠️  duplicate skill basenames are ambiguous: {sorted(dups)}")
    return mapping, dups

def get_update_parent(name, mapping, ambiguous_names):
    if name in ambiguous_names:
        raise ValueError(f"duplicate installed basename '{name}' is ambiguous")
    return mapping.get(name)

def _kw_hit(kws, name, tokens):
    padded = "-" + name.replace("_", "-") + "-"
    for kw in kws:
        if "-" in kw:
            if "-" + kw.strip("-") + "-" in padded:
                return True
        elif any(token == kw or (len(kw) >= 4 and token.startswith(kw))
                 for token in tokens):
            return True
    return False

def auto_classify_skill(skill_name):
    # Classify the bare skill name; a collision's __source suffix is provenance,
    # not a taxonomy signal.
    name = skill_name.split("__")[0].lower()
    tokens = [token for token in re.split(r"[-_]", name) if token]

    def has(*kws):
        return _kw_hit(kws, name, tokens)

    # 1. First, determine the Macro Category using broader keyword checks
    macro = "engineering" # Default macro

    if has("andruia"):
        macro = "andruia"
    elif has("agent", "prompt", "llm", "rag", "ai", "ml", "hugging",
             "claude", "gemini", "memory", "search", "vector", "embed",
             "mcp", "orchestrat", "langgraph", "pydantic", "context",
             "token", "cache"):
        macro = "ai-and-data"
    elif has("docker", "kubernetes", "aws", "azure", "cloud", "ci-cd",
             "security", "pentest", "hacking", "vulnerability", "monitoring",
             "pipeline", "deploy", "dns", "ssl"):
        macro = "devops-and-security"
    elif has("seo", "marketing", "copywrit", "conversion", "cro", "email",
             "social", "linkedin", "ads", "growth"):
        macro = "marketing-and-seo"
    elif has("design", "ui", "ux", "aesthetics", "figma", "3d", "motion",
             "animation", "radix", "tailwind", "css", "theme"):
        macro = "product-and-design"
    elif has("finance", "trading", "business", "hr", "odoo", "legal",
             "compliance", "startup", "sales", "audit", "billing", "revenue"):
        macro = "business-and-finance"
    elif has("office", "slide", "document", "excel", "word", "health",
             "fitness", "wellness", "educat", "coach", "scientific", "math",
             "video", "transcribe", "youtube"):
        macro = "productivity-and-content"
    elif has("workflow", "planning", "project", "git", "github", "wiki",
             "documentation", "standards", "ddd", "agile", "notes", "jira",
             "linear"):
        macro = "workflows-and-management"

    # 2. Next, match the specific Subcategory within that Macro Category
    sub = "uncategorized-and-misc" # Default subcategory

    if macro == "andruia":
        if has("consultant"): sub = "00-andruia-consultant"
        elif has("smith"): sub = "10-andruia-skill-smith"
        elif has("niche", "intel"): sub = "20-andruia-niche-intelligence"
        else: sub = "uncategorized"

    elif macro == "ai-and-data":
        if has("prompt"): sub = "prompt-engineering-group"
        elif has("hugging"): sub = "hugging-face"
        elif has("mcp", "framework"): sub = "llm-frameworks-and-mcp"
        elif has("rag", "search", "vector", "embed"): sub = "rag-and-search"
        elif has("memory", "context"): sub = "context-and-memory"
        elif has("claude", "gemini", "notebooklm"): sub = "claude-and-assistants"
        elif has("agent", "orchestrat"): sub = "agents-and-orchestration"

    elif macro == "devops-and-security":
        if has("aws"): sub = "aws-cloud"
        elif has("azure"): sub = "azure-cloud"
        elif has("security", "pentest", "hacking", "vulner"): sub = "cybersecurity-and-pentesting"
        elif has("pipeline", "ci-cd", "github-actions"): sub = "ci-cd-and-pipelines"

    elif macro == "marketing-and-seo":
        if has("seo"): sub = "search-engine-optimization"
        elif has("cro", "conversion"): sub = "conversion-rate-optimization"
        elif has("marketing", "strategy", "copy"): sub = "marketing-strategy-and-copy"

    elif macro == "product-and-design":
        if has("3d", "animation"): sub = "3d-motion-and-animation"
        elif has("tailwind", "radix", "system"): sub = "design-systems-and-components"

    elif macro == "business-and-finance":
        if has("odoo"): sub = "odoo-development"
        elif has("finance", "trading"): sub = "finance-and-trading"
        elif has("startup", "business"): sub = "startup-and-business-analysis"

    elif macro == "productivity-and-content":
        if has("health", "wellness", "fitness"): sub = "health-and-wellness-analyzers"

    elif macro == "workflows-and-management":
        if has("git", "github"): sub = "git-and-github-workflows"
        elif has("plan", "execut"): sub = "planning-and-execution"

    elif macro == "engineering":
        if has("refactor", "clean", "quality"): sub = "code-quality-and-refactoring"
        elif has("database", "postgres", "sql"): sub = "databases-and-migrations"
        elif has("debug", "error", "bug"): sub = "debugging-and-error-handling"
        elif has("frontend", "ui"): sub = "frontend-and-ui"
        elif has("game", "unity", "godot"): sub = "game-dev"
        elif has("api", "route"): sub = "backend-and-apis"

    return macro, sub

# ---------------------------------------------------------------------------
# Multi-source + dedup helpers
# ---------------------------------------------------------------------------

def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass
        raise

def refresh_upstream_cache(source, clone_dir):
    index_file = source.get("index_file")
    if not index_file:
        return True
    os.makedirs(data_dir, exist_ok=True)
    cached = os.path.join(data_dir, f"upstream_index_{source['name']}.json")
    upstream = os.path.join(clone_dir, index_file)
    clone_root = os.path.realpath(clone_dir)
    upstream_real = os.path.realpath(upstream)
    inside_clone = (upstream_real == clone_root or
                    upstream_real.startswith(clone_root + os.sep))
    if not os.path.isfile(upstream) or not inside_clone:
        if os.path.exists(cached):
            os.unlink(cached)
        print(f"❌ Source '{source['name']}' has missing or unsafe declared index "
              f"'{index_file}'.")
        return False
    tmp = cached + ".tmp"
    try:
        shutil.copy2(upstream, tmp)
        os.replace(tmp, cached)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return True

def dir_hash(path):
    # Content identity matching what copytree installs: live symlinks are
    # dereferenced, dotfiles are included, and only active ancestors are used
    # for cycle detection so aliases to the same directory hash at each path.
    h = hashlib.sha256()

    def add_file(fp, rel):
        h.update(rel.encode() + b"\0")
        if os.path.islink(fp) and not os.path.exists(fp):
            h.update(b"dangling:" + os.readlink(fp).encode())
        else:
            with open(fp, "rb") as fh:
                h.update(fh.read())
        h.update(b"\0")

    def walk(current, rel_dir, ancestors):
        st = os.stat(current)
        inode = (st.st_dev, st.st_ino)
        active = ancestors | {inode}
        with os.scandir(current) as scan:
            entries = sorted(scan, key=lambda e: e.name)
        for entry in entries:
            if entry.name == ".DS_Store":
                continue
            rel = os.path.join(rel_dir, entry.name) if rel_dir else entry.name
            try:
                is_dir = entry.is_dir(follow_symlinks=True)
            except OSError:
                is_dir = False
            if not is_dir:
                add_file(entry.path, rel)
                continue
            child_st = os.stat(entry.path)
            child_inode = (child_st.st_dev, child_st.st_ino)
            if child_inode in active:
                h.update(rel.encode() + b"\0cycle\0")
                continue
            walk(entry.path, rel, active)

    walk(path, "", set())
    return h.hexdigest()

def frontmatter_description(skill_path):
    sm = os.path.join(skill_path, "SKILL.md")
    if not os.path.isfile(sm):
        return None
    try:
        head = open(sm, encoding="utf-8", errors="replace").read(2000)
    except OSError:
        return None
    m = re.match(r'^---\s*\n(.*?)\n---', head, re.S)
    if not m:
        return None
    d = re.search(r'^description\s*:\s*(.+)', m.group(1), re.M)
    return d.group(1).strip().strip('"\'') if d else None

def desc_tokens(desc):
    return {w for w in re.findall(r"[a-z0-9]+", desc.lower()) if len(w) > 2}

def collect_source_skills(repo_dir, layout):
    # Returns [(skill_name, src_path)] with container units expanded recursively
    # to the same leaf definition find_leaf_skills uses: a dir that directly
    # contains regular files is one skill; a container (subdirs only) recurses.
    if layout == "skills-subdir":
        base = os.path.join(repo_dir, "skills")
        if not os.path.isdir(base):
            print("⚠️  declared layout 'skills-subdir' but no skills/ dir found — skipping source")
            return []
    else:
        base = repo_dir
    clone_root = os.path.realpath(repo_dir)
    skills = []

    def inside_clone(path):
        real = os.path.realpath(path)
        return real == clone_root or real.startswith(clone_root + os.sep)

    def tree_escapes(path):
        # Follow internal directory symlinks because copytree dereferences
        # them, but reject any nested link whose target leaves the clone.
        def walk(current, ancestors):
            st = os.stat(current)
            inode = (st.st_dev, st.st_ino)
            if inode in ancestors:
                return False
            active = ancestors | {inode}
            with os.scandir(current) as scan:
                entries = list(scan)
            for entry in entries:
                if entry.is_symlink() and not inside_clone(entry.path):
                    return True
                try:
                    is_dir = entry.is_dir(follow_symlinks=True)
                except OSError:
                    is_dir = False
                if is_dir and walk(entry.path, active):
                    return True
            return False

        return walk(path, set())

    def has_skill_md(path):
        for _, _, files in os.walk(path):
            if "SKILL.md" in files:
                return True
        return False

    def walk_unit(name, path, allow_markerless=False):
        if os.path.islink(path) or not inside_clone(path):
            print(f"⚠️  unit escapes source clone — rejected: {path}")
            return
        entries = [e for e in os.listdir(path) if not e.startswith('.')]
        direct = [e for e in entries if os.path.isfile(os.path.join(path, e))]
        subdirs = [e for e in entries if os.path.isdir(os.path.join(path, e))]
        # SKILL.md directly marks a canonical skill. Preserve a markerless
        # top-level legacy unit, but do not turn markerless support directories
        # inside a container into standalone skills.
        if "SKILL.md" not in direct and has_skill_md(path):
            for child in subdirs:
                walk_unit(child, os.path.join(path, child), allow_markerless=False)
            return
        if "SKILL.md" not in direct and not allow_markerless:
            return
        if tree_escapes(path):
            print(f"⚠️  skill '{name}' contains symlink escaping the clone — rejected")
            return
        skills.append((name, path))

    for unit in os.listdir(base):
        unit_path = os.path.join(base, unit)
        if os.path.isdir(unit_path) and not unit.startswith('.'):
            walk_unit(unit, unit_path, allow_markerless=True)
    seen, out = set(), []
    for name, path in skills:
        if name in seen:
            print(f"⚠️  duplicate unit name '{name}' within source — skipped {path}")
            continue
        seen.add(name)
        out.append((name, path))
    return out

def install_skill(name, src_path):
    # Returns the placed "macro/sub" path, or None if the destination already
    # exists (duplicate leaf name within one source run — skip, never raise).
    macro, sub = auto_classify_skill(name)
    dest_parent = os.path.join(skills_dir, macro, sub)
    dest = os.path.join(dest_parent, name)
    if os.path.exists(dest):
        return None
    os.makedirs(dest_parent, exist_ok=True)
    shutil.copytree(src_path, dest)
    return f"{macro}/{sub}"

def replace_skill_dir(src_path, dest_path):
    # Stage the new version, retain the old one as a rollback backup, then
    # swap. Dot-prefixed siblings stay invisible to library discovery.
    parent, base = os.path.split(dest_path)
    stage = os.path.join(parent, f".{base}.tmp-upd")
    backup = os.path.join(parent, f".{base}.bak-upd")
    if os.path.exists(stage):
        shutil.rmtree(stage)
    if os.path.exists(backup):
        if os.path.exists(dest_path):
            shutil.rmtree(backup)
        else:
            os.rename(backup, dest_path)
    try:
        shutil.copytree(src_path, stage)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    os.rename(dest_path, backup)
    try:
        os.rename(stage, dest_path)
    except Exception:
        if not os.path.exists(dest_path) and os.path.exists(backup):
            os.rename(backup, dest_path)
        shutil.rmtree(stage, ignore_errors=True)
        raise
    shutil.rmtree(backup)

def sweep_tmp_dirs():
    # Recover interrupted swaps before discovery can observe the taxonomy.
    for macro in MACRO_CATEGORIES:
        root = os.path.join(skills_dir, macro)
        if not os.path.isdir(root):
            continue
        for dirpath, dirs, _ in os.walk(root, topdown=False):
            for name in list(dirs):
                path = os.path.join(dirpath, name)
                if name.startswith('.') and name.endswith(".bak-upd"):
                    base = name[1:-len(".bak-upd")]
                    dest = os.path.join(dirpath, base)
                    if os.path.exists(dest):
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        os.rename(path, dest)
                elif name.startswith('.') and name.endswith(".tmp-upd"):
                    shutil.rmtree(path, ignore_errors=True)

def flat_name_map(entries):
    # Shared flat-naming rule (used by sync_flat_skills and the librarian index):
    # unique basenames keep their name; duplicates get the path slug.
    counts = {}
    for e in entries:
        b = os.path.basename(e)
        counts[b] = counts.get(b, 0) + 1
    return {e: (e.replace("/", "-") if counts[os.path.basename(e)] > 1
                else os.path.basename(e)) for e in entries}

def preflight_sources(sources):
    prepared = []
    clones_ok = True
    metadata_ok = True
    for source in sources:
        print(f"🚀 Fetching source '{source['name']}' from {source['git_url']}...")
        temp_dir = tempfile.mkdtemp()
        success, _ = run_cmd(
            ["git", "clone", "--depth", "1", source["git_url"], temp_dir])
        if not success:
            clones_ok = False
            print(f"❌ Failed to clone {source['name']} — skipping this source.")
            shutil.rmtree(temp_dir)
            continue
        if not refresh_upstream_cache(source, temp_dir):
            metadata_ok = False
            shutil.rmtree(temp_dir)
            continue
        prepared.append((source, temp_dir))
    return prepared, clones_ok, metadata_ok

def main():
    cfg = load_json(sources_path, None)
    if not cfg or not cfg.get("sources"):
        print("❌ sources.json missing or empty.")
        return 1
    sources = sorted(cfg["sources"], key=lambda s: s.get("priority", 99))
    primary = sources[0]["name"]

    prepared_sources, clones_ok, metadata_ok = preflight_sources(sources)
    if not metadata_ok:
        for _, temp_dir in prepared_sources:
            shutil.rmtree(temp_dir)
        print("❌ Source metadata preflight failed — library state was not updated.")
        return 1

    origins = load_json(origins_path, {})
    similars = load_json(similars_path, {})
    sweep_tmp_dirs()

    # Backfill ownership: any pre-existing skill without a record belongs to the primary source.
    for rel in find_leaf_skills():
        origins.setdefault(os.path.basename(rel), {"owner": primary, "also": []})

    report = {"collisions": [], "similars": [], "multi_origin": [], "errors": []}
    new_names = []
    totals = {"updated": 0, "unchanged": 0, "new": 0}
    ok = clones_ok

    for src, temp_dir in prepared_sources:
        current_mapping, ambiguous_names = get_current_skill_mapping()

        for name, src_path in collect_source_skills(temp_dir, src.get("layout", "root")):
            try:
                current_parent = get_update_parent(
                    name, current_mapping, ambiguous_names)
                if current_parent is not None:
                    rec = origins.setdefault(name, {"owner": primary, "also": []})
                    existing = os.path.join(current_parent, name)
                    if rec["owner"] == src["name"]:
                        if dir_hash(src_path) != dir_hash(existing):
                            replace_skill_dir(src_path, existing)
                            totals["updated"] += 1
                        else:
                            totals["unchanged"] += 1
                    else:
                        # Layer 1: identical content from another source -> extra origin, no copy.
                        if dir_hash(src_path) == dir_hash(existing):
                            if src["name"] not in rec["also"]:
                                rec["also"].append(src["name"])
                                report["multi_origin"].append(f"{name} also provided identically by {src['name']}")
                        else:
                            # Layer 2: same name, different content -> namespace, never overwrite.
                            ns = f"{name}__{src['name']}"
                            ns_parent = get_update_parent(
                                ns, current_mapping, ambiguous_names)
                            if ns_parent is not None:
                                ns_existing = os.path.join(ns_parent, ns)
                                if dir_hash(src_path) != dir_hash(ns_existing):
                                    replace_skill_dir(src_path, ns_existing)
                                    totals["updated"] += 1
                            else:
                                placed = install_skill(ns, src_path)
                                if placed is None:
                                    report["errors"].append(f"{ns}: destination already exists — skipped")
                                    continue
                                origins[ns] = {"owner": src["name"], "also": []}
                                current_mapping[ns] = os.path.join(skills_dir, placed)
                                report["collisions"].append(
                                    f"{name}: name owned by {rec['owner']}, differing content from {src['name']} stored as {placed}/{ns}")
                                new_names.append(ns)
                                totals["new"] += 1
                else:
                    placed = install_skill(name, src_path)
                    if placed is None:
                        report["errors"].append(f"{name}: destination already exists — skipped")
                        continue
                    origins[name] = {"owner": src["name"], "also": []}
                    current_mapping[name] = os.path.join(skills_dir, placed)
                    new_names.append(name)
                    totals["new"] += 1
                    print(f"🆕 New skill classified: {name} ➔ {placed}")
            except Exception as ex:
                report["errors"].append(f"{name}: {type(ex).__name__}: {ex}")
                print(f"⚠️  {name}: {type(ex).__name__}: {ex} — skipped, pipeline continues")

        shutil.rmtree(temp_dir)

    # Layer 3: semantic similarity for newly added skills, compared against the
    # POST-update on-disk descriptions (so same-run additions can match each other).
    mapping, _ = get_current_skill_mapping()
    if new_names:
        disk_desc = {}
        for other, parent in mapping.items():
            d = frontmatter_description(os.path.join(parent, other))
            if d:
                disk_desc[other] = desc_tokens(d)
        for name in new_names:
            toks = disk_desc.get(name)
            if not toks:
                continue
            hits = []
            for other, otoks in disk_desc.items():
                if other == name or not otoks:
                    continue
                j = len(toks & otoks) / len(toks | otoks)
                if j >= 0.5 and len(toks & otoks) > 5:
                    hits.append(other)
            if hits:
                similars[name] = sorted(set(similars.get(name, []) + hits))
                report["similars"].append(f"{name} ~ {', '.join(hits)}")

    print(f"✅ Sources done: {totals['updated']} updated, {totals['unchanged']} unchanged, {totals['new']} new.")

    # Prune records for skills that no longer exist on disk.
    leaf_names = set(mapping)
    origins = {k: v for k, v in origins.items() if k in leaf_names}
    similars = {k: [s for s in v if s in leaf_names] for k, v in similars.items() if k in leaf_names}
    similars = {k: v for k, v in similars.items() if v}

    save_json(origins_path, origins)
    save_json(similars_path, similars)

    # Dedup review report (rewritten each run with that run's findings).
    os.makedirs(reports_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    lines = [f"# Dedup review — {stamp}", ""]
    for key, title in (("collisions", "Name collisions (namespaced, needs review)"),
                       ("similars", "Semantic similars (needs review)"),
                       ("multi_origin", "Multi-origin confirmations"),
                       ("errors", "Errors (skill skipped, pipeline continued)")):
        lines.append(f"## {title}")
        lines += [f"- {x}" for x in report[key]] if report[key] else ["- none"]
        lines.append("")
    lines.append("Resolve by adding `alias -> canonical` entries to data/aliases.json.")
    with open(os.path.join(reports_dir, "dedup-review.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("⚙️ Rebuilding .antigravity-install-manifest.json...")
    manifest_entries = find_leaf_skills()
    manifest_data = {
        "schemaVersion": 1,
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "entries": manifest_entries
    }
    save_json(manifest_path, manifest_data)
    print(f"Manifest rebuilt with {len(manifest_entries)} entries.")

    print("📝 Updating README.md tree and categories...")
    ok_tree, tree_out = run_cmd(
        ["python3", os.path.join(skills_dir, "scripts", "generate_full_ascii_tree.py")])
    print(tree_out)

    print("🛡️ Running path verification checks...")
    ok_verify, verify_out = run_cmd(
        ["python3", os.path.join(skills_dir, "scripts", "verify_exact_skills.py")])
    print(verify_out)

    print("🔗 Syncing skills to flat directory...")
    ok_sync, sync_out = run_cmd(
        ["python3", os.path.join(skills_dir, "scripts", "sync_flat_skills.py")])
    print(sync_out)

    print("📇 Building librarian-index.json...")
    ok_idx, idx_out = run_cmd(
        ["python3", os.path.join(skills_dir, "scripts", "build_librarian_index.py")])
    print(idx_out)

    return 0 if ok and ok_tree and ok_verify and ok_sync and ok_idx else 1

if __name__ == "__main__":
    sys.exit(main())
