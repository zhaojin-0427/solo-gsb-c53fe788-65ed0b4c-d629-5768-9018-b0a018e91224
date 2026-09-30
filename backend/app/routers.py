import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from . import models, schemas
from .database import get_db
from .rules_engine import run_review

router = APIRouter(prefix="/api")


# ---------- 工具 ----------
def _chemical_dict(c: models.Chemical) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "cas_number": c.cas_number or "",
        "tags": json.loads(c.tags or "[]"),
        "concentration_pct": c.concentration_pct,
        "volume_ml": c.volume_ml,
        "temperature_c": c.temperature_c,
        "container_material": c.container_material,
        "zone_id": c.zone_id,
    }


def _event_dict(e: models.Event) -> dict:
    return {
        "id": e.id,
        "event_type": e.event_type,
        "chemical_id": e.chemical_id,
        "secondary_chemical_id": e.secondary_chemical_id,
        "target_zone_id": e.target_zone_id,
        "sequence": e.sequence,
        "status": e.status,
        "note": e.note or "",
    }


def _rule_dict(r: models.Rule) -> dict:
    return {
        "id": r.id,
        "version_id": r.version_id,
        "rule_type": r.rule_type,
        "name": r.name,
        "severity": r.severity,
        "conditions": json.loads(r.conditions or "{}"),
        "message": r.message or "",
        "enabled": r.enabled,
    }


def _get_or_404(db: Session, model, obj_id: int, label: str):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, f"{label}不存在")
    return obj


# ---------- 分区 ----------
@router.get("/zones", response_model=list[schemas.ZoneOut])
def list_zones(db: Session = Depends(get_db)):
    return db.query(models.Zone).order_by(models.Zone.id).all()


@router.post("/zones", response_model=schemas.ZoneOut, status_code=201)
def create_zone(payload: schemas.ZoneIn, db: Session = Depends(get_db)):
    if db.query(models.Zone).filter_by(name=payload.name).first():
        raise HTTPException(400, "分区名称已存在")
    z = models.Zone(**payload.model_dump())
    db.add(z)
    db.commit()
    db.refresh(z)
    return z


@router.put("/zones/{zone_id}", response_model=schemas.ZoneOut)
def update_zone(zone_id: int, payload: schemas.ZoneIn, db: Session = Depends(get_db)):
    z = _get_or_404(db, models.Zone, zone_id, "分区")
    z.name, z.description = payload.name, payload.description
    db.commit()
    db.refresh(z)
    return z


@router.delete("/zones/{zone_id}", status_code=204)
def delete_zone(zone_id: int, db: Session = Depends(get_db)):
    z = _get_or_404(db, models.Zone, zone_id, "分区")
    db.query(models.Chemical).filter_by(zone_id=zone_id).update({"zone_id": None})
    db.delete(z)
    db.commit()


# ---------- 化学品 ----------
@router.get("/chemicals", response_model=list[schemas.ChemicalOut])
def list_chemicals(db: Session = Depends(get_db)):
    rows = db.query(models.Chemical).order_by(models.Chemical.id).all()
    return [_chemical_dict(c) for c in rows]


@router.post("/chemicals", response_model=schemas.ChemicalOut, status_code=201)
def create_chemical(payload: schemas.ChemicalIn, db: Session = Depends(get_db)):
    c = models.Chemical(
        **payload.model_dump(exclude={"tags"}),
        tags=json.dumps(payload.tags, ensure_ascii=False),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _chemical_dict(c)


@router.put("/chemicals/{chem_id}", response_model=schemas.ChemicalOut)
def update_chemical(chem_id: int, payload: schemas.ChemicalIn, db: Session = Depends(get_db)):
    c = _get_or_404(db, models.Chemical, chem_id, "化学品")
    for k, v in payload.model_dump(exclude={"tags"}).items():
        setattr(c, k, v)
    c.tags = json.dumps(payload.tags, ensure_ascii=False)
    db.commit()
    db.refresh(c)
    return _chemical_dict(c)


@router.delete("/chemicals/{chem_id}", status_code=204)
def delete_chemical(chem_id: int, db: Session = Depends(get_db)):
    c = _get_or_404(db, models.Chemical, chem_id, "化学品")
    db.query(models.Event).filter(
        (models.Event.chemical_id == chem_id)
        | (models.Event.secondary_chemical_id == chem_id)
    ).delete(synchronize_session=False)
    db.delete(c)
    db.commit()


# ---------- 事件 ----------
@router.get("/events", response_model=list[schemas.EventOut])
def list_events(db: Session = Depends(get_db)):
    rows = db.query(models.Event).order_by(models.Event.sequence, models.Event.id).all()
    return [_event_dict(e) for e in rows]


@router.post("/events", response_model=schemas.EventOut, status_code=201)
def create_event(payload: schemas.EventIn, db: Session = Depends(get_db)):
    if payload.event_type not in ("transfer", "mix", "dispose"):
        raise HTTPException(400, "event_type 须为 transfer / mix / dispose")
    _get_or_404(db, models.Chemical, payload.chemical_id, "化学品")
    if payload.event_type == "mix":
        if not payload.secondary_chemical_id:
            raise HTTPException(400, "混合事件须指定第二化学品")
        _get_or_404(db, models.Chemical, payload.secondary_chemical_id, "化学品")
    if payload.event_type == "transfer" and not payload.target_zone_id:
        raise HTTPException(400, "转移事件须指定目标分区")
    e = models.Event(**payload.model_dump())
    db.add(e)
    db.commit()
    db.refresh(e)
    return _event_dict(e)


@router.put("/events/{event_id}", response_model=schemas.EventOut)
def update_event(event_id: int, payload: schemas.EventIn, db: Session = Depends(get_db)):
    e = _get_or_404(db, models.Event, event_id, "事件")
    for k, v in payload.model_dump().items():
        setattr(e, k, v)
    db.commit()
    db.refresh(e)
    return _event_dict(e)


@router.patch("/events/{event_id}/status", response_model=schemas.EventOut)
def set_event_status(event_id: int, payload: schemas.EventStatusIn, db: Session = Depends(get_db)):
    if payload.status not in ("pending", "done", "cancelled"):
        raise HTTPException(400, "status 须为 pending / done / cancelled")
    e = _get_or_404(db, models.Event, event_id, "事件")
    e.status = payload.status
    db.commit()
    db.refresh(e)
    return _event_dict(e)


@router.delete("/events/{event_id}", status_code=204)
def delete_event(event_id: int, db: Session = Depends(get_db)):
    e = _get_or_404(db, models.Event, event_id, "事件")
    db.delete(e)
    db.commit()


# ---------- 规则版本与规则 ----------
@router.get("/rule-versions", response_model=list[schemas.RuleVersionOut])
def list_rule_versions(db: Session = Depends(get_db)):
    return db.query(models.RuleVersion).order_by(models.RuleVersion.id).all()


@router.post("/rule-versions", response_model=schemas.RuleVersionOut, status_code=201)
def create_rule_version(payload: schemas.RuleVersionIn, db: Session = Depends(get_db)):
    if db.query(models.RuleVersion).filter_by(name=payload.name).first():
        raise HTTPException(400, "版本名称已存在")
    v = models.RuleVersion(name=payload.name, note=payload.note)
    db.add(v)
    db.flush()
    if payload.copy_from_id:
        src = _get_or_404(db, models.RuleVersion, payload.copy_from_id, "规则版本")
        for r in db.query(models.Rule).filter_by(version_id=src.id).all():
            db.add(models.Rule(
                version_id=v.id, rule_type=r.rule_type, name=r.name,
                severity=r.severity, conditions=r.conditions,
                message=r.message, enabled=r.enabled,
            ))
    db.commit()
    db.refresh(v)
    return v


@router.post("/rule-versions/{version_id}/activate", response_model=schemas.RuleVersionOut)
def activate_rule_version(version_id: int, db: Session = Depends(get_db)):
    v = _get_or_404(db, models.RuleVersion, version_id, "规则版本")
    db.query(models.RuleVersion).update({"is_active": False})
    v.is_active = True
    db.commit()
    db.refresh(v)
    return v


@router.get("/rule-versions/{version_id}/rules", response_model=list[schemas.RuleOut])
def list_rules(version_id: int, db: Session = Depends(get_db)):
    _get_or_404(db, models.RuleVersion, version_id, "规则版本")
    rows = db.query(models.Rule).filter_by(version_id=version_id).order_by(models.Rule.id).all()
    return [_rule_dict(r) for r in rows]


@router.post("/rule-versions/{version_id}/rules", response_model=schemas.RuleOut, status_code=201)
def create_rule(version_id: int, payload: schemas.RuleIn, db: Session = Depends(get_db)):
    _get_or_404(db, models.RuleVersion, version_id, "规则版本")
    r = models.Rule(
        version_id=version_id,
        **payload.model_dump(exclude={"conditions"}),
        conditions=json.dumps(payload.conditions, ensure_ascii=False),
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return _rule_dict(r)


@router.put("/rules/{rule_id}", response_model=schemas.RuleOut)
def update_rule(rule_id: int, payload: schemas.RuleIn, db: Session = Depends(get_db)):
    r = _get_or_404(db, models.Rule, rule_id, "规则")
    for k, v in payload.model_dump(exclude={"conditions"}).items():
        setattr(r, k, v)
    r.conditions = json.dumps(payload.conditions, ensure_ascii=False)
    db.commit()
    db.refresh(r)
    return _rule_dict(r)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    r = _get_or_404(db, models.Rule, rule_id, "规则")
    db.delete(r)
    db.commit()


# ---------- 审查 ----------
def _review_out(rv: models.Review) -> dict:
    return {
        "id": rv.id,
        "rule_version_id": rv.rule_version_id,
        "rule_version_name": rv.rule_version_name,
        "status": rv.status,
        "created_at": rv.created_at.isoformat(timespec="seconds"),
    }


@router.post("/reviews", status_code=201)
def run_new_review(payload: schemas.ReviewRunIn, db: Session = Depends(get_db)):
    if payload.rule_version_id:
        version = _get_or_404(db, models.RuleVersion, payload.rule_version_id, "规则版本")
    else:
        version = db.query(models.RuleVersion).filter_by(is_active=True).first()
        if version is None:
            raise HTTPException(400, "没有可用的规则版本，请先创建并启用")

    chemicals = [_chemical_dict(c) for c in db.query(models.Chemical).all()]
    zones = [{"id": z.id, "name": z.name} for z in db.query(models.Zone).all()]
    events = [_event_dict(e) for e in db.query(models.Event).all()]
    rules = [_rule_dict(r) for r in
             db.query(models.Rule).filter_by(version_id=version.id).all()]

    result = run_review(chemicals, zones, events, rules)
    rv = models.Review(
        rule_version_id=version.id,
        rule_version_name=version.name,
        status=result["status"],
        result_json=json.dumps(result, ensure_ascii=False),
        rules_snapshot=json.dumps(rules, ensure_ascii=False),
    )
    db.add(rv)
    db.commit()
    db.refresh(rv)
    return {**_review_out(rv), "result": result}


@router.get("/reviews", response_model=list[schemas.ReviewOut])
def list_reviews(db: Session = Depends(get_db)):
    rows = db.query(models.Review).order_by(models.Review.id.desc()).all()
    return [_review_out(r) for r in rows]


@router.get("/reviews/{review_id}")
def get_review(review_id: int, db: Session = Depends(get_db)):
    rv = _get_or_404(db, models.Review, review_id, "审查记录")
    return {
        **_review_out(rv),
        "result": json.loads(rv.result_json),
        "rules_snapshot": json.loads(rv.rules_snapshot),
    }
