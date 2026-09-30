from typing import Optional

from pydantic import BaseModel, Field


# ---------- 分区 ----------
class ZoneIn(BaseModel):
    name: str
    description: str = ""


class ZoneOut(ZoneIn):
    id: int

    class Config:
        from_attributes = True


# ---------- 化学品 ----------
class ChemicalIn(BaseModel):
    name: str
    cas_number: str = ""
    tags: list[str] = Field(default_factory=list)
    concentration_pct: Optional[float] = None
    volume_ml: Optional[float] = None
    temperature_c: Optional[float] = None
    container_material: Optional[str] = None
    zone_id: Optional[int] = None


class ChemicalOut(ChemicalIn):
    id: int


# ---------- 事件 ----------
class EventIn(BaseModel):
    event_type: str  # transfer | mix | dispose
    chemical_id: int
    secondary_chemical_id: Optional[int] = None
    target_zone_id: Optional[int] = None
    sequence: int = 0
    note: str = ""


class EventOut(EventIn):
    id: int
    status: str


class EventStatusIn(BaseModel):
    status: str  # pending | done | cancelled


# ---------- 规则 ----------
class RuleVersionIn(BaseModel):
    name: str
    note: str = ""
    copy_from_id: Optional[int] = None  # 从既有版本复制规则，便于迭代


class RuleVersionOut(BaseModel):
    id: int
    name: str
    note: str
    is_active: bool

    class Config:
        from_attributes = True


class RuleIn(BaseModel):
    rule_type: str
    name: str
    severity: str = "forbidden"
    conditions: dict = Field(default_factory=dict)
    message: str = ""
    enabled: bool = True


class RuleOut(RuleIn):
    id: int
    version_id: int


# ---------- 审查 ----------
class ReviewRunIn(BaseModel):
    rule_version_id: Optional[int] = None  # 缺省使用当前启用版本


class ReviewOut(BaseModel):
    id: int
    rule_version_id: int
    rule_version_name: str
    status: str
    created_at: str
