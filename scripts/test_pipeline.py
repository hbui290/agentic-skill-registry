"""Self-check for the update pipeline (stdlib only, synthetic fixtures).

Run: python3 scripts/test_pipeline.py  -> prints PASS/FAIL per case, exit 1 on any FAIL.
Never touches the real library: every case works in its own temp dir.
"""
import os
import json
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_skills as U
import build_librarian_index as B
import sync_flat_skills as S

RESULTS = []

def case(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco

def mkskill(base, *rel_files):
    for rf in rel_files:
        p = os.path.join(base, rf)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("content of " + rf)

# --- R2#11: a crash mid-write must not corrupt existing JSON --------------
@case("save_json: crash mid-dump leaves old file parseable")
def _(tmp):
    path = os.path.join(tmp, "data", "x.json")
    U.save_json(path, {"ok": 1})
    real_dump = json.dump
    json.dump = lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    try:
        try:
            U.save_json(path, {"ok": 2})
        except OSError:
            pass
    finally:
        json.dump = real_dump
    with open(path, encoding="utf-8") as f:
        assert json.load(f) == {"ok": 1}, "old content must survive the crash"

# --- F#4: dir_hash must survive dangling symlinks -------------------------
@case("dir_hash tolerates dangling symlink")
def _(tmp):
    d = os.path.join(tmp, "s"); mkskill(d, "s/SKILL.md")
    os.symlink(os.path.join(tmp, "nonexistent"), os.path.join(d, "s", "dead-link"))
    U.dir_hash(os.path.join(d, "s"))  # must not raise

# --- F#11: dir_hash path/content delimiter --------------------------------
@case("dir_hash separates path from content")
def _(tmp):
    a = os.path.join(tmp, "a"); os.makedirs(a)
    with open(os.path.join(a, "ab"), "w") as f: f.write("c")
    b = os.path.join(tmp, "b"); os.makedirs(b)
    with open(os.path.join(b, "a"), "w") as f: f.write("bc")
    assert U.dir_hash(a) != U.dir_hash(b), "hash collision between {ab:c} and {a:bc}"

# --- R2#1: dir_hash follows live symlink-to-DIR like copytree ------------
@case("dir_hash: symlink-to-dir hashes same as copytree result")
def _(tmp):
    src = os.path.join(tmp, "src")
    mkskill(src, "SKILL.md", "sub/real.md")
    os.symlink(os.path.join(src, "sub"), os.path.join(src, "linked"))
    dst = os.path.join(tmp, "dst")
    shutil.copytree(src, dst)
    assert U.dir_hash(src) == U.dir_hash(dst), "hash must see through symlink-dir"

# --- R2#6: dotfile changes must be visible to the hash --------------------
@case("dir_hash: dotfile content change changes the hash")
def _(tmp):
    a, b = os.path.join(tmp, "a"), os.path.join(tmp, "b")
    for d, value in ((a, "one"), (b, "two")):
        os.makedirs(d)
        with open(os.path.join(d, "SKILL.md"), "w") as f:
            f.write("same")
        with open(os.path.join(d, ".env.example"), "w") as f:
            f.write(value)
    assert U.dir_hash(a) != U.dir_hash(b), "dotfile diff must change hash"

# --- R2#1b: symlink loop must not hang ------------------------------------
@case("dir_hash: symlink loop terminates")
def _(tmp):
    src = os.path.join(tmp, "src")
    mkskill(src, "SKILL.md")
    before = U.dir_hash(src)
    os.symlink(src, os.path.join(src, "loop"))
    after = U.dir_hash(src)
    assert after != before, "cycle path must be represented without recursion"

# --- F#5: declared layout dir missing -> no root fallback ------------------
@case("collect_source_skills: missing skills/ dir yields nothing")
def _(tmp):
    repo = os.path.join(tmp, "repo")
    mkskill(repo, "docs/guide.md", "tests/test_x.py")
    got = U.collect_source_skills(repo, "skills-subdir")
    assert got == [], f"fell back to repo root: {[n for n, _ in got]}"

# --- SEC: hostile source symlinks must never escape the clone -------------
@case("collect: symlinked unit escaping clone is rejected")
def _(tmp):
    outside = os.path.join(tmp, "outside")
    os.makedirs(outside)
    with open(os.path.join(outside, "SKILL.md"), "w") as f:
        f.write("secret")
    mkskill(tmp, "repo/skills/normal/SKILL.md")
    os.symlink(outside, os.path.join(tmp, "repo", "skills", "evil"))
    got = [n for n, _ in U.collect_source_skills(
        os.path.join(tmp, "repo"), "skills-subdir")]
    assert got == ["normal"], got

@case("collect: nested file symlink escaping clone is rejected")
def _(tmp):
    secret = os.path.join(tmp, "host-secret.txt")
    with open(secret, "w") as f:
        f.write("x")
    mkskill(tmp, "repo/skills/sneaky/SKILL.md",
            "repo/skills/shared/placeholder.txt")
    os.makedirs(os.path.join(tmp, "repo", "skills", "shared", "nested"))
    os.symlink(secret, os.path.join(
        tmp, "repo", "skills", "shared", "nested", "grab"))
    os.symlink(os.path.join("..", "shared"), os.path.join(
        tmp, "repo", "skills", "sneaky", "internal-link"))
    got = [n for n, _ in U.collect_source_skills(
        os.path.join(tmp, "repo"), "skills-subdir")]
    assert "sneaky" not in got, got

# --- F#6: container-of-containers recurses to real leafs -------------------
@case("collect_source_skills: nested containers reach leaf skills")
def _(tmp):
    repo = os.path.join(tmp, "repo")
    mkskill(repo,
            "skills/plain/SKILL.md",
            "skills/toolkit/alpha/SKILL.md",
            "skills/toolkit/beta/SKILL.md",
            "skills/mega/group/deep/SKILL.md")
    names = sorted(n for n, _ in U.collect_source_skills(repo, "skills-subdir"))
    assert names == ["alpha", "beta", "deep", "plain"], names

# --- R2#3: canonical and file-less source units stay whole ----------------
@case("collect: skill with SKILL.md at root keeps support dirs as one unit")
def _(tmp):
    mkskill(tmp, "repo/skills/myskill/SKILL.md",
            "repo/skills/myskill/references/a.md",
            "repo/skills/myskill/scripts/run.sh")
    got = [n for n, _ in U.collect_source_skills(
        os.path.join(tmp, "repo"), "skills-subdir")]
    assert got == ["myskill"], got

@case("collect: file-less unit without any SKILL.md stays one unit")
def _(tmp):
    mkskill(tmp, "repo/skills/frag/references/a.md",
            "repo/skills/frag/scripts/b.sh")
    got = [n for n, _ in U.collect_source_skills(
        os.path.join(tmp, "repo"), "skills-subdir")]
    assert got == ["frag"], got

@case("collect: container of two skills recurses to each SKILL.md dir")
def _(tmp):
    mkskill(tmp, "repo/skills/cat/alpha/SKILL.md",
            "repo/skills/cat/beta/SKILL.md")
    got = sorted(n for n, _ in U.collect_source_skills(
        os.path.join(tmp, "repo"), "skills-subdir"))
    assert got == ["alpha", "beta"], got

@case("collect: support dir beside a marked skill is not a skill")
def _(tmp):
    mkskill(tmp, "repo/skills/bundle/alpha/SKILL.md",
            "repo/skills/bundle/shared/reference.md")
    got = [n for n, _ in U.collect_source_skills(
        os.path.join(tmp, "repo"), "skills-subdir")]
    assert got == ["alpha"], got

@case("collect: duplicate names within one source keep first only")
def _(tmp):
    mkskill(tmp, "repo/skills/cat1/tool/SKILL.md",
            "repo/skills/cat2/tool/SKILL.md")
    got = [n for n, _ in U.collect_source_skills(
        os.path.join(tmp, "repo"), "skills-subdir")]
    assert got == ["tool"], got

# --- R2#8: duplicate installed basenames are unsafe update targets --------
@case("get_current_skill_mapping: dup basenames returned as ambiguous")
def _(tmp):
    old = U.skills_dir
    U.skills_dir = tmp
    try:
        mkskill(tmp,
                f"{U.MACRO_CATEGORIES[0]}/s1/tool/SKILL.md",
                f"{U.MACRO_CATEGORIES[4]}/s2/tool/SKILL.md")
        mapping, ambiguous = U.get_current_skill_mapping()
        assert "tool" in mapping
        assert "tool" in ambiguous
    finally:
        U.skills_dir = old

@case("mapping lookup refuses an ambiguous derived namespace")
def _(tmp):
    name = "tool__secondary"
    try:
        U.get_update_parent(name, {name: "/last/path"}, {name})
    except ValueError as ex:
        assert name in str(ex)
    else:
        raise AssertionError("ambiguous namespace was accepted")

# --- F#2: install_skill must not raise on existing destination -------------
@case("install_skill skips existing destination")
def _(tmp):
    old = U.skills_dir
    try:
        U.skills_dir = tmp
        src = os.path.join(tmp, "_src"); mkskill(src, "_src/SKILL.md")
        assert U.install_skill("clean-code", src) is not None
        assert U.install_skill("clean-code", src) is None  # second time: skip, no raise
    finally:
        U.skills_dir = old

# --- F#1: replace_skill_dir is atomic-ish ----------------------------------
@case("replace_skill_dir keeps old version when copy fails")
def _(tmp):
    dest = os.path.join(tmp, "skill"); mkskill(dest, "skill/SKILL.md")
    good = os.path.join(tmp, "good"); mkskill(good, "good/SKILL.md")
    with open(os.path.join(good, "good", "SKILL.md"), "w") as f: f.write("v2")
    U.replace_skill_dir(os.path.join(good, "good"), os.path.join(dest, "skill"))
    assert open(os.path.join(dest, "skill", "SKILL.md")).read() == "v2"
    bad = os.path.join(tmp, "bad", "b"); os.makedirs(bad)
    os.symlink("/nonexistent-target-xyz", os.path.join(bad, "dead"))
    try:
        U.replace_skill_dir(bad, os.path.join(dest, "skill"))
    except Exception:
        pass  # failure allowed — but old content must survive
    assert open(os.path.join(dest, "skill", "SKILL.md")).read() == "v2", "old version lost"

# --- R2#2: replacement failures and crash leftovers are recoverable -------
@case("replace_skill_dir: copy failure leaves old version and no visible junk")
def _(tmp):
    dest = os.path.join(tmp, "cat", "sub", "myskill")
    src = os.path.join(tmp, "src")
    mkskill(tmp, "cat/sub/myskill/SKILL.md", "src/SKILL.md")
    real_copytree = shutil.copytree
    shutil.copytree = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
    try:
        try:
            U.replace_skill_dir(src, dest)
        except OSError:
            pass
    finally:
        shutil.copytree = real_copytree
    visible = [e for e in os.listdir(os.path.dirname(dest)) if not e.startswith('.')]
    assert visible == ["myskill"], f"visible junk: {visible}"
    assert os.path.exists(os.path.join(dest, "SKILL.md")), "old version must survive"

@case("replace_skill_dir: stage rename failure rolls old version back")
def _(tmp):
    dest = os.path.join(tmp, "dest")
    src = os.path.join(tmp, "src")
    mkskill(tmp, "dest/SKILL.md", "src/SKILL.md")
    with open(os.path.join(dest, "SKILL.md"), "w") as f:
        f.write("old")
    with open(os.path.join(src, "SKILL.md"), "w") as f:
        f.write("new")
    real_rename = os.rename
    failed = False

    def fail_stage_once(old, new):
        nonlocal failed
        if old.endswith(".tmp-upd") and new == dest and not failed:
            failed = True
            raise OSError("rename failed")
        return real_rename(old, new)

    os.rename = fail_stage_once
    try:
        try:
            U.replace_skill_dir(src, dest)
        except OSError:
            pass
    finally:
        os.rename = real_rename
    assert open(os.path.join(dest, "SKILL.md")).read() == "old"

@case("sweep_tmp_dirs restores backup and removes stale stage")
def _(tmp):
    macro = U.MACRO_CATEGORIES[0]
    parent = os.path.join(tmp, macro, "sub")
    backup = os.path.join(parent, ".dead.bak-upd")
    stage = os.path.join(parent, ".other.tmp-upd")
    mkskill(tmp, f"{macro}/sub/.dead.bak-upd/SKILL.md",
            f"{macro}/sub/.other.tmp-upd/SKILL.md")
    old = U.skills_dir
    U.skills_dir = tmp
    try:
        assert not any(".bak-upd" in rel or ".tmp-upd" in rel
                       for rel in U.find_leaf_skills())
        U.sweep_tmp_dirs()
    finally:
        U.skills_dir = old
    assert os.path.exists(os.path.join(parent, "dead", "SKILL.md"))
    assert not os.path.exists(backup)
    assert not os.path.exists(stage)

# --- F#3: run_cmd takes argv list, no shell interpretation ------------------
@case("run_cmd uses argv list without shell")
def _(tmp):
    ok, out = U.run_cmd(["echo", "hi; touch " + os.path.join(tmp, "pwned")])
    assert ok and "hi; touch" in out
    assert not os.path.exists(os.path.join(tmp, "pwned")), "shell interpreted the argument!"

# --- DRY: shared flat_name rule --------------------------------------------
@case("flat_name_map slugs duplicate basenames")
def _(tmp):
    m = U.flat_name_map(["a/x/skill1", "b/y/skill1", "c/z/skill2"])
    assert m["a/x/skill1"] == "a-x-skill1" and m["b/y/skill1"] == "b-y-skill1"
    assert m["c/z/skill2"] == "skill2"

@case("sync_flat_skills: symlink failure returns exit status 1")
def _(tmp):
    old = (S.skills_dir, S.flat_dir, S.manifest_path, S.os.symlink)
    try:
        S.skills_dir = os.path.join(tmp, "skills")
        S.flat_dir = os.path.join(tmp, "flat")
        S.manifest_path = os.path.join(
            S.skills_dir, ".antigravity-install-manifest.json")
        rel = "ai-and-data/misc/tool"
        mkskill(S.skills_dir, f"{rel}/SKILL.md")
        mkskill(S.flat_dir, "old-skill/SKILL.md")
        with open(S.manifest_path, "w") as f:
            json.dump({"entries": [rel]}, f)
        S.os.symlink = lambda *a: (_ for _ in ()).throw(OSError("forced"))
        assert S.main() == 1
        assert os.path.isfile(os.path.join(S.flat_dir, "old-skill", "SKILL.md"))
    finally:
        S.skills_dir, S.flat_dir, S.manifest_path, S.os.symlink = old

# --- R2#4: anchored classification ignores namespace source ---------------
@case("auto_classify: anchored tokens and __source suffix ignored")
def _(tmp):
    assert U.auto_classify_skill("chrome-devtools")[0] != "business-and-finance"
    assert (U.auto_classify_skill("database-migrations__claude-skills")
            == U.auto_classify_skill("database-migrations"))
    assert U.auto_classify_skill("hr-onboarding")[0] == "business-and-finance"
    assert U.auto_classify_skill("copywriting-basics")[0] == "marketing-and-seo"

# --- F#7: namespaced entry must not inherit another skill's metadata --------
@case("make_entry: no metadata bleed into namespaced skill")
def _(tmp):
    mkskill(tmp, "eng/misc/foo__srcB/SKILL.md")
    with open(os.path.join(tmp, "eng/misc/foo__srcB/SKILL.md"), "w") as f:
        f.write('---\nname: foo__srcB\ndescription: "own desc"\n---\n')
    upstream = {"foo": {"description": "FOO UPSTREAM", "category": "cat-foo",
                        "risk": "dangerous", "source": "community", "date_added": "2020-01-01"}}
    e = B.make_entry("eng/misc/foo__srcB", tmp, upstream,
                     {"foo__srcB": {"owner": "srcB", "also": []}}, {}, {}, {}, {"foo__srcB": 1})
    assert e["description"] == "own desc"
    assert e["risk"] != "dangerous" and e["category_fine"] != "cat-foo", e

@case("make_entry: source_repo prefers exact upstream repository")
def _(tmp):
    mkskill(tmp, "eng/misc/tool/SKILL.md")
    upstream = {"tool": {"source_repo": "owner/tool", "source": "community"}}
    origins = {"tool": {"owner": "aggregator", "also": []}}
    e = B.make_entry("eng/misc/tool", tmp, upstream, origins,
                     {}, {}, {"aggregator": {}}, {"tool": 1})
    assert e["source_repo"] == "owner/tool", e

# --- F#9: dangling canonical is nulled --------------------------------------
@case("make_entry: dangling canonical -> null")
def _(tmp):
    mkskill(tmp, "eng/misc/real/SKILL.md")
    with open(os.path.join(tmp, "eng/misc/real/SKILL.md"), "w") as f:
        f.write('---\nname: real\ndescription: "d"\n---\n')
    e = B.make_entry("eng/misc/real", tmp, {}, {}, {}, {"real": "ghost-skill"}, {}, {"real": 1},
                     valid_names={"real"})
    assert e["canonical"] is None

# --- F#12: missing SKILL.md gets a synthetic searchable description ----------
@case("make_entry: synthetic description when SKILL.md missing")
def _(tmp):
    mkskill(tmp, "eng/misc/SPDD/1-research.md", "eng/misc/SPDD/2-spec.md")
    e = B.make_entry("eng/misc/SPDD", tmp, {}, {}, {}, {}, {}, {"SPDD": 1})
    assert "1-research.md" in e["description"], e["description"]

# --- R2#7: exact verification is bidirectional and returns real status -----
@case("verify_exact_skills: missing and extra disk entries exit 1")
def _(tmp):
    scripts = os.path.join(tmp, "scripts")
    os.makedirs(scripts)
    for name in ("verify_exact_skills.py", "update_skills.py"):
        shutil.copy2(os.path.join(os.path.dirname(__file__), name), scripts)
    mkskill(tmp, f"{U.MACRO_CATEGORIES[0]}/misc/extra/SKILL.md")
    with open(os.path.join(tmp, ".antigravity-install-manifest.json"), "w") as f:
        json.dump({"entries": [f"{U.MACRO_CATEGORIES[0]}/misc/ghost"]}, f)
    result = subprocess.run(
        [sys.executable, os.path.join(scripts, "verify_exact_skills.py")],
        capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "extra" in result.stdout.lower(), result.stdout

@case("verify_exact_skills: matching manifest and disk exit 0")
def _(tmp):
    scripts = os.path.join(tmp, "scripts")
    os.makedirs(scripts)
    for name in ("verify_exact_skills.py", "update_skills.py"):
        shutil.copy2(os.path.join(os.path.dirname(__file__), name), scripts)
    rel = f"{U.MACRO_CATEGORIES[0]}/misc/tool"
    mkskill(tmp, f"{rel}/SKILL.md")
    with open(os.path.join(tmp, ".antigravity-install-manifest.json"), "w") as f:
        json.dump({"entries": [rel]}, f)
    result = subprocess.run(
        [sys.executable, os.path.join(scripts, "verify_exact_skills.py")],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr

# --- R2#5: every declared upstream index cache is required -----------------
@case("build_librarian_index: one missing source cache preserves old index")
def _(tmp):
    scripts = os.path.join(tmp, "scripts")
    data = os.path.join(tmp, "data")
    os.makedirs(scripts)
    os.makedirs(data)
    for name in ("build_librarian_index.py", "update_skills.py"):
        shutil.copy2(os.path.join(os.path.dirname(__file__), name), scripts)
    with open(os.path.join(tmp, "sources.json"), "w") as f:
        json.dump({"sources": [
            {"name": "a", "index_file": "index-a.json", "priority": 1},
            {"name": "b", "index_file": "index-b.json", "priority": 2},
        ]}, f)
    with open(os.path.join(data, "upstream_index_a.json"), "w") as f:
        json.dump([{"id": "present", "risk": "low"}], f)
    sentinel = b'{"sentinel": true}\n'
    index_path = os.path.join(tmp, "librarian-index.json")
    with open(index_path, "wb") as f:
        f.write(sentinel)
    result = subprocess.run(
        [sys.executable, os.path.join(scripts, "build_librarian_index.py")],
        capture_output=True, text=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "b" in (result.stdout + result.stderr).lower()
    with open(index_path, "rb") as f:
        assert f.read() == sentinel, "existing index must remain untouched"

@case("upstream cache: missing declared index removes stale cache")
def _(tmp):
    old = U.data_dir
    U.data_dir = os.path.join(tmp, "data")
    os.makedirs(U.data_dir)
    stale = os.path.join(U.data_dir, "upstream_index_source-b.json")
    with open(stale, "w") as f:
        f.write("stale")
    try:
        ok = U.refresh_upstream_cache(
            {"name": "source-b", "index_file": "missing.json"},
            os.path.join(tmp, "clone"))
        assert ok is False
        assert not os.path.exists(stale)
    finally:
        U.data_dir = old


def main():
    failed = 0
    for name, fn in RESULTS:
        tmp = tempfile.mkdtemp()
        try:
            fn(tmp)
            print(f"PASS  {name}")
        except Exception as ex:
            failed += 1
            print(f"FAIL  {name} — {type(ex).__name__}: {ex}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} passed")
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main()
