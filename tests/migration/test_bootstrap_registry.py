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
    legacy_sources = {"legacy-local", "sickn33-agentic-awesome-skills"}
    legacy_skills = [record for record in skills if record["source_id"] in legacy_sources]
    legacy_quarantine = [
        record for record in quarantine if record["source_id"] in legacy_sources
    ]
    assert len(legacy_skills) == 1981
    assert len(quarantine) == 12
    assert len(legacy_quarantine) == 12
    assert {"SPDD", "linear"} <= {record["name"] for record in quarantine}
    assert sum(
        record["source_id"] == "sickn33-agentic-awesome-skills"
        for record in quarantine
    ) == 10
    assert len({record["skill_id"] for record in skills + quarantine}) == len(
        skills + quarantine
    )
