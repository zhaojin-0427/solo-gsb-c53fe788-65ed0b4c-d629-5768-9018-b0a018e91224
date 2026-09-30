"""首次启动时写入演示数据：分区、化学品、事件与 v1 规则包。"""
import json

from . import models
from .database import SessionLocal


def seed_if_empty():
    db = SessionLocal()
    try:
        if db.query(models.RuleVersion).count() > 0:
            return

        # ---- 分区 ----
        z_a = models.Zone(name="A区-酸碱柜", description="无机酸与碱液储存")
        z_b = models.Zone(name="B区-氧化剂柜", description="强氧化剂专用")
        z_c = models.Zone(name="C区-废液暂存", description="待处置废液")
        db.add_all([z_a, z_b, z_c])
        db.flush()

        # ---- 化学品 ----
        chemicals = [
            models.Chemical(
                name="盐酸", cas_number="7647-01-0",
                tags=json.dumps(["acid", "reducer", "corrosive"]),
                concentration_pct=31, volume_ml=500, temperature_c=25,
                container_material="glass", zone_id=z_a.id,
            ),
            models.Chemical(
                name="氢氧化钠溶液", cas_number="1310-73-2",
                tags=json.dumps(["base", "corrosive"]),
                concentration_pct=30, volume_ml=500, temperature_c=25,
                container_material="hdpe", zone_id=z_a.id,
            ),
            models.Chemical(
                name="氢氟酸", cas_number="7664-39-3",
                tags=json.dumps(["acid", "glass_corrosive", "toxic"]),
                concentration_pct=40, volume_ml=250, temperature_c=25,
                container_material="glass", zone_id=z_a.id,  # 玻璃容器：应触发腐蚀规则
            ),
            models.Chemical(
                name="硫化钠溶液", cas_number="1313-82-2",
                tags=json.dumps(["base", "sulfide", "reducer"]),
                concentration_pct=None,  # 浓度未知：应触发人工复核
                volume_ml=1000, temperature_c=25,
                container_material="hdpe", zone_id=z_a.id,
            ),
            models.Chemical(
                name="高锰酸钾", cas_number="7722-64-7",
                tags=json.dumps(["oxidizer"]),
                concentration_pct=99, volume_ml=1000, temperature_c=25,
                container_material="glass", zone_id=z_b.id,
            ),
            models.Chemical(
                name="废丙酮", cas_number="67-64-1",
                tags=json.dumps(["flammable", "organic"]),
                concentration_pct=99, volume_ml=2000, temperature_c=25,
                container_material="hdpe", zone_id=z_c.id,
            ),
        ]
        db.add_all(chemicals)
        db.flush()
        by_name = {c.name: c for c in chemicals}

        # ---- 待执行事件 ----
        events = [
            models.Event(
                event_type="transfer", chemical_id=by_name["硫化钠溶液"].id,
                target_zone_id=z_c.id, sequence=1, note="废液归集",
            ),
            models.Event(
                event_type="transfer", chemical_id=by_name["盐酸"].id,
                target_zone_id=z_b.id, sequence=2, note="临时挪放（演示冲突）",
            ),
            models.Event(
                event_type="mix", chemical_id=by_name["盐酸"].id,
                secondary_chemical_id=by_name["氢氧化钠溶液"].id,
                sequence=3, note="中和处理",
            ),
            models.Event(
                event_type="dispose", chemical_id=by_name["废丙酮"].id,
                sequence=4, note="定期清运",
            ),
        ]
        db.add_all(events)

        # ---- 规则版本 v1 ----
        v1 = models.RuleVersion(name="v1", note="初始规则包", is_active=True)
        db.add(v1)
        db.flush()

        def R(rule_type, name, severity, conditions, message):
            return models.Rule(
                version_id=v1.id, rule_type=rule_type, name=name,
                severity=severity, conditions=json.dumps(conditions, ensure_ascii=False),
                message=message,
            )

        db.add_all([
            R("acid_base", "强酸与强碱共储/混合", "forbidden",
              {"tag_a": "acid", "tag_b": "base",
               "min_concentration_a": 5, "min_concentration_b": 5},
              "酸碱中和剧烈放热，禁止同区存放或直接混合"),
            R("acid_base", "高温环境酸碱反应加速", "forbidden",
              {"tag_a": "acid", "tag_b": "base", "min_temperature": 40},
              "温度 ≥40°C 时酸碱反应显著加速，禁止同区存放"),
            R("redox", "氧化剂与还原剂接触", "forbidden",
              {"tag_a": "oxidizer", "tag_b": "reducer"},
              "氧化还原反应剧烈，可能起火爆炸"),
            R("gas", "酸与硫化物放出剧毒 H2S", "forbidden",
              {"tag_a": "acid", "tag_b": "sulfide", "min_concentration_b": 1},
              "酸遇硫化物释放剧毒硫化氢气体"),
            R("gas", "酸与氰化物放出 HCN", "forbidden",
              {"tag_a": "acid", "tag_b": "cyanide"},
              "酸遇氰化物释放剧毒氰化氢气体"),
            R("exothermic", "氧化剂与易燃物放热反应", "forbidden",
              {"tag_a": "oxidizer", "tag_b": "flammable"},
              "氧化剂助燃，与易燃物接触可能自燃"),
            R("corrosion", "氢氟酸腐蚀玻璃容器", "forbidden",
              {"tag": "glass_corrosive", "material": "glass"},
              "氢氟酸腐蚀玻璃，须使用 PTFE/HDPE 容器"),
            R("corrosion", "强碱腐蚀铝制容器", "forbidden",
              {"tag": "base", "min_concentration": 20, "material": "aluminum"},
              "浓强碱腐蚀铝并放出氢气"),
            R("disposal", "易燃液体废弃处置", "review",
              {"tag": "flammable", "event_types": ["dispose"]},
              "易燃液体废弃需人工复核处置方式与资质"),
        ])
        db.commit()
    finally:
        db.close()
