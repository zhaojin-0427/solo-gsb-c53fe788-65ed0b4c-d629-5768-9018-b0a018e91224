from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, Float, ForeignKey, Integer,
                        String, Text)

from .database import Base


class Zone(Base):
    __tablename__ = "zones"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, default="")


class Chemical(Base):
    __tablename__ = "chemicals"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    cas_number = Column(String(50), default="")
    tags = Column(Text, default="[]")  # JSON 数组，如 ["acid", "corrosive"]
    concentration_pct = Column(Float, nullable=True)   # 质量百分比浓度，可空（未知）
    volume_ml = Column(Float, nullable=True)
    temperature_c = Column(Float, nullable=True)
    container_material = Column(String(50), nullable=True)  # 容器材质，可空（未知）
    zone_id = Column(Integer, ForeignKey("zones.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True)
    event_type = Column(String(20), nullable=False)  # transfer | mix | dispose
    chemical_id = Column(Integer, ForeignKey("chemicals.id"), nullable=False)
    secondary_chemical_id = Column(Integer, ForeignKey("chemicals.id"), nullable=True)  # mix 专用
    target_zone_id = Column(Integer, ForeignKey("zones.id"), nullable=True)             # transfer 专用
    sequence = Column(Integer, default=0)            # 待执行顺序，越小越先执行
    status = Column(String(20), default="pending")   # pending | done | cancelled
    note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class RuleVersion(Base):
    __tablename__ = "rule_versions"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)  # 如 v1、v2-2026Q3
    note = Column(Text, default="")
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Rule(Base):
    __tablename__ = "rules"

    id = Column(Integer, primary_key=True)
    version_id = Column(Integer, ForeignKey("rule_versions.id"), nullable=False)
    rule_type = Column(String(30), nullable=False)  # acid_base | redox | gas | exothermic | corrosion | disposal
    name = Column(String(200), nullable=False)
    severity = Column(String(20), default="forbidden")  # forbidden | review
    conditions = Column(Text, default="{}")  # JSON 对象，见 rules_engine 文档
    message = Column(Text, default="")
    enabled = Column(Boolean, default=True)


class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True)
    rule_version_id = Column(Integer, ForeignKey("rule_versions.id"), nullable=False)
    rule_version_name = Column(String(100), default="")
    status = Column(String(20), default="ok")  # forbidden | review | ok
    result_json = Column(Text, default="{}")   # 审查结果（含发现项、最早冲突、隔离清单）
    rules_snapshot = Column(Text, default="[]")  # 审查时所用规则快照，保证可复现
    created_at = Column(DateTime, default=datetime.utcnow)
