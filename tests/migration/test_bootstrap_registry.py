import json

from skill_registry.identity import assign_load_names, stable_skill_id


def test_stable_id_is_deterministic():
    assert stable_skill_id("source", "skills/example") == stable_skill_id("source", "skills/example")
    assert stable_skill_id("source", "skills/example").startswith("asr_")
    assert len(stable_skill_id("source", "skills/example")) == 20


def test_load_name_collision_is_namespaced():
    result = assign_load_names(["alpha/tool", "beta/tool", "beta/other"])
    assert result == {
        "alpha/tool": "alpha--tool",
        "beta/tool": "beta--tool",
        "beta/other": "other",
    }


def test_bootstrap_reconciles_complete_repository(repo_root):
    skills = json.loads((repo_root / "registry/skills.json").read_text())["skills"]
    quarantine = json.loads((repo_root / "registry/quarantine.json").read_text())["records"]
    assert len(skills) == 1952
    assert len(quarantine) == 2
    assert {record["name"] for record in quarantine} == {"SPDD", "linear"}
    assert len({record["skill_id"] for record in skills + quarantine}) == 1954
