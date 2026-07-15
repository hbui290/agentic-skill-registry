from dataclasses import asdict, dataclass


RISK_VALUES = frozenset({"safe", "review", "dangerous", "unknown"})
STATE_VALUES = frozenset({"active", "deprecated", "quarantined"})


@dataclass(frozen=True)
class SkillRecord:
    skill_id: str
    name: str
    load_name: str
    catalog_path: str
    source_id: str
    source_commit: str
    source_path: str
    content_sha256: str
    license: str
    risk: str
    risk_reasons: tuple[str, ...]
    state: str
    canonical_skill_id: str | None
    first_seen_version: str

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["risk_reasons"] = sorted(self.risk_reasons)
        return value


@dataclass(frozen=True)
class QuarantineRecord:
    skill_id: str
    name: str
    catalog_path: str
    source_id: str
    source_commit: str
    content_sha256: str
    rule_ids: tuple[str, ...]
    first_seen_date: str
    disposition: str

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["rule_ids"] = sorted(self.rule_ids)
        return value


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    url: str
    commit: str
    layout: str
    skills_root: str
    metadata_index: str
    license_note: str
