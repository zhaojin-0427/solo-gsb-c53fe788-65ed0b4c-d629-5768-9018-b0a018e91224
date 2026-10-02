"""相容性规则引擎（纯函数，不依赖数据库）。

输入均为已解析的字典结构：
- chemicals: [{id, name, cas_number, tags[], concentration_pct, volume_ml,
               temperature_c, container_material, zone_id}]
- zones:     [{id, name}]
- events:    [{id, event_type, chemical_id, secondary_chemical_id,
               target_zone_id, sequence, status, note}]
- rules:     [{id, rule_type, name, severity, conditions{}, message, enabled}]

规则条件（conditions）支持的字段
--------------------------------
成对规则（acid_base / redox / gas / exothermic），对同区两两组合或混合事件判定：
  tag_a / tag_b                 该侧化学品必须携带的标签
  min_concentration_a / _b      该侧浓度(%)必须 >= 阈值；缺数据 → 人工复核
  max_concentration_a / _b      该侧浓度(%)必须 <= 阈值；缺数据 → 人工复核
  min_temperature               任一化学品温度(°C) >= 阈值才命中；缺数据 → 人工复核
  max_temperature               所有化学品温度(°C) <= 阈值才命中；缺数据 → 人工复核
  event_types                   限定触发场景列表，如 ["mix"]；缺省表示储存与所有事件均适用，
                                可含 "storage" 表示仅/也适用于同区储存状态

单体规则（corrosion / disposal），对单个化学品（及其容器）判定：
  tag                           必须携带的标签
  min_concentration             浓度(%) >= 阈值；缺数据 → 人工复核
  material                      容器材质等于该值才命中；材质未知 → 人工复核
  event_types                   同上；disposal 规则通常写作 ["dispose"]

判定结论：
  forbidden  命中禁止条目
  review     规则要求的数据缺失（如浓度、温度、材质未知），需人工复核

审查流程
--------
1. 先判定当前储存状态（逐分区、逐组合）；
2. 再按执行顺序模拟待执行事件，逐步更新模拟库存并判定：
   - transfer：化学品移入目标分区，随后判定目标分区全部组合；
   - mix：两项同处目标分区（未指定则取第一项当时分区），判定该组合及各自容器；
   - dispose：先判定处置规则，再将化学品移出库存；
   后续事件基于更新后的库存状态判定。
3. 去重范围：同一规则与对象只在当前储存阶段或单个事件内去重，
   可在不同事件（及储存阶段）分别命中。

返回结果含 event_steps：每个待执行事件的前/后分区库存快照，供报告展示。
"""

PAIR_RULE_TYPES = {"acid_base", "redox", "gas", "exothermic"}
SINGLE_RULE_TYPES = {"corrosion", "disposal"}

EVENT_TYPE_LABELS = {"transfer": "转移", "mix": "混合", "dispose": "废弃"}
RULE_TYPE_LABELS = {
    "acid_base": "酸碱",
    "redox": "氧化还原",
    "gas": "放气",
    "exothermic": "放热",
    "corrosion": "容器腐蚀",
    "disposal": "废弃处置",
}


def _cmp(value, op, threshold):
    if op == ">=":
        return value >= threshold
    if op == "<=":
        return value <= threshold
    raise ValueError(f"未知比较符: {op}")


def _match_side(chem, side, cond):
    """检查化学品是否满足成对规则某一侧的条件。

    返回 None 表示不匹配；否则返回 (matched[], missing[])。
    """
    matched, missing = [], []
    tag = cond.get(f"tag_{side}")
    if tag:
        if tag not in chem["tags"]:
            return None
        matched.append(f"「{chem['name']}」携带标签 {tag}")
    for field, op, op_label in (
        ("min_concentration", ">=", "≥"),
        ("max_concentration", "<=", "≤"),
    ):
        key = f"{field}_{side}"
        if key in cond:
            threshold = cond[key]
            value = chem["concentration_pct"]
            if value is None:
                missing.append(
                    f"「{chem['name']}」缺少浓度数据（规则要求浓度 {op_label} {threshold}%）"
                )
            elif not _cmp(value, op, threshold):
                return None
            else:
                matched.append(f"「{chem['name']}」浓度 {value}% {op_label} {threshold}%")
    return matched, missing


def _match_temperature(chems, cond):
    """成对规则的温度条件。返回 (ok, matched[], missing[])。"""
    matched, missing = [], []
    if "min_temperature" in cond:
        threshold = cond["min_temperature"]
        hit, unknown = False, []
        for c in chems:
            t = c["temperature_c"]
            if t is None:
                unknown.append(c["name"])
            elif t >= threshold:
                hit = True
                matched.append(f"「{c['name']}」温度 {t}°C ≥ {threshold}°C")
        if not hit:
            if unknown:
                missing.append(
                    f"{'、'.join(unknown)} 缺少温度数据（规则要求任一温度 ≥ {threshold}°C）"
                )
            else:
                return False, matched, missing
    if "max_temperature" in cond:
        threshold = cond["max_temperature"]
        for c in chems:
            t = c["temperature_c"]
            if t is None:
                missing.append(
                    f"「{c['name']}」缺少温度数据（规则要求温度 ≤ {threshold}°C）"
                )
            elif t > threshold:
                return False, matched, missing
            else:
                matched.append(f"「{c['name']}」温度 {t}°C ≤ {threshold}°C")
    return True, matched, missing


def _applies_to(rule, event_type):
    """规则是否适用于当前场景（storage / transfer / mix / dispose）。"""
    event_types = rule["conditions"].get("event_types")
    if not event_types:
        return True
    return event_type in event_types


def _match_pair_rule(rule, a, b):
    """两种朝向都尝试；返回 (matched[], missing[]) 或 None。"""
    cond = rule["conditions"]
    best = None
    for x, y in ((a, b), (b, a)):
        mx = _match_side(x, "a", cond)
        my = _match_side(y, "b", cond)
        if mx is None or my is None:
            continue
        ok, mt, miss_t = _match_temperature([x, y], cond)
        if not ok:
            continue
        matched = mx[0] + my[0] + mt
        missing = mx[1] + my[1] + miss_t
        if not missing:  # 完全命中优先
            return matched, missing
        best = best or (matched, missing)
    return best


def _match_single_rule(rule, chem):
    cond = rule["conditions"]
    matched, missing = [], []
    tag = cond.get("tag")
    if tag:
        if tag not in chem["tags"]:
            return None
        matched.append(f"「{chem['name']}」携带标签 {tag}")
    if "min_concentration" in cond:
        threshold = cond["min_concentration"]
        value = chem["concentration_pct"]
        if value is None:
            missing.append(
                f"「{chem['name']}」缺少浓度数据（规则要求浓度 ≥ {threshold}%）"
            )
        elif value < threshold:
            return None
        else:
            matched.append(f"「{chem['name']}」浓度 {value}% ≥ {threshold}%")
    if "material" in cond:
        material = chem["container_material"]
        if material is None:
            missing.append(
                f"「{chem['name']}」容器材质未知（规则针对材质 {cond['material']}）"
            )
        elif material != cond["material"]:
            return None
        else:
            matched.append(f"「{chem['name']}」容器材质为 {material}")
    return matched, missing


def run_review(chemicals, zones, events, rules):
    """执行完整审查，返回结果字典（可直接 JSON 序列化）。"""
    zone_names = {z["id"]: z["name"] for z in zones}
    chem_by_id = {c["id"]: c for c in chemicals}
    state = {c["id"]: c["zone_id"] for c in chemicals}  # 模拟库存：化学品 -> 分区
    disposed = set()  # 已废弃、移出库存的化学品
    findings = []
    seen = set()

    def add_finding(rule, severity, objects, zone_id, phase, event, matched, missing):
        # 去重范围：当前储存阶段 或 单个事件内部；
        # 同一规则与对象可在不同事件（及储存阶段）分别命中
        scope = ("storage",) if event is None else ("event", event["id"])
        key = (scope, rule["id"], tuple(sorted(o["id"] for o in objects)), zone_id)
        if key in seen:
            return
        seen.add(key)
        findings.append(
            {
                "severity": severity,
                "rule_id": rule["id"],
                "rule_name": rule["name"],
                "rule_type": rule["rule_type"],
                "rule_type_label": RULE_TYPE_LABELS.get(rule["rule_type"], rule["rule_type"]),
                "message": rule["message"],
                "zone_id": zone_id,
                "zone_name": zone_names.get(zone_id, "未分区"),
                "phase": phase,  # storage | event
                "event": (
                    {
                        "id": event["id"],
                        "sequence": event["sequence"],
                        "event_type": event["event_type"],
                        "event_type_label": EVENT_TYPE_LABELS.get(
                            event["event_type"], event["event_type"]
                        ),
                    }
                    if event
                    else None
                ),
                "objects": [
                    {"id": o["id"], "name": o["name"], "cas_number": o["cas_number"]}
                    for o in objects
                ],
                "conditions": matched + missing,
                "missing_data": list(missing),
            }
        )

    pair_rules = [
        r for r in rules if r["enabled"] and r["rule_type"] in PAIR_RULE_TYPES
    ]
    single_rules = [
        r for r in rules if r["enabled"] and r["rule_type"] in SINGLE_RULE_TYPES
    ]

    def check_pair(a, b, zone_id, phase, event, event_type):
        for rule in pair_rules:
            if not _applies_to(rule, event_type):
                continue
            result = _match_pair_rule(rule, a, b)
            if result is None:
                continue
            matched, missing = result
            severity = "review" if missing else rule["severity"]
            add_finding(rule, severity, [a, b], zone_id, phase, event, matched, missing)

    def check_single(chem, phase, event, event_type):
        zone_id = state[chem["id"]]
        for rule in single_rules:
            if not _applies_to(rule, event_type):
                continue
            result = _match_single_rule(rule, chem)
            if result is None:
                continue
            matched, missing = result
            severity = "review" if missing else rule["severity"]
            add_finding(rule, severity, [chem], zone_id, phase, event, matched, missing)

    def check_zone(zone_id, phase, event, event_type):
        chems = [
            c for c in chemicals
            if c["id"] not in disposed and state[c["id"]] == zone_id
        ]
        for i in range(len(chems)):
            check_single(chems[i], phase, event, event_type)
            for j in range(i + 1, len(chems)):
                check_pair(chems[i], chems[j], zone_id, phase, event, event_type)

    def snapshot():
        """当前模拟库存快照：各分区（含未分区）在库的化学品名单。"""
        snap = []
        for zid in [z["id"] for z in zones] + [None]:
            members = [
                {"id": c["id"], "name": c["name"]}
                for c in chemicals
                if c["id"] not in disposed and state[c["id"]] == zid
            ]
            snap.append(
                {
                    "zone_id": zid,
                    "zone_name": zone_names.get(zid, "未分区"),
                    "chemicals": members,
                }
            )
        return snap

    # 1) 当前储存状态：逐分区、逐组合判定
    for zone_id in {z["id"] for z in zones} | {None}:
        check_zone(zone_id, "storage", None, "storage")

    # 2) 按登记顺序模拟待执行事件：逐步更新模拟库存，判定事件后状态，
    #    并记录每个事件的前/后库存快照供报告展示
    pending = sorted(
        (e for e in events if e["status"] == "pending"),
        key=lambda e: (e["sequence"], e["id"]),
    )
    event_steps = []
    for ev in pending:
        chem = chem_by_id.get(ev["chemical_id"])
        if chem is None:
            continue
        before = snapshot()
        findings_before = len(findings)
        skipped = None
        if chem["id"] in disposed:
            skipped = f"「{chem['name']}」已废弃移出库存，事件不再执行"
        elif ev["event_type"] == "transfer":
            if ev["target_zone_id"] is not None:
                # 转移：更新分区，随后判定目标分区全部组合
                state[chem["id"]] = ev["target_zone_id"]
                check_zone(ev["target_zone_id"], "event", ev, "transfer")
            else:
                skipped = "转移事件缺少目标分区，已跳过"
        elif ev["event_type"] == "mix":
            other = chem_by_id.get(ev["secondary_chemical_id"])
            if other is None or other["id"] in disposed:
                skipped = "混合对象不存在或已废弃，事件已跳过"
            else:
                # 混合后两项同处目标分区（未指定则取第一项当时分区）
                zone_id = (
                    ev["target_zone_id"]
                    if ev["target_zone_id"] is not None
                    else state[chem["id"]]
                )
                state[chem["id"]] = zone_id
                state[other["id"]] = zone_id
                # 判定该组合及各自容器腐蚀
                check_pair(chem, other, zone_id, "event", ev, "mix")
                check_single(chem, "event", ev, "mix")
                check_single(other, "event", ev, "mix")
        elif ev["event_type"] == "dispose":
            # 先判定处置规则，再移出库存
            check_single(chem, "event", ev, "dispose")
            disposed.add(chem["id"])
            state[chem["id"]] = None
        event_steps.append(
            {
                "event": {
                    "id": ev["id"],
                    "sequence": ev["sequence"],
                    "event_type": ev["event_type"],
                    "event_type_label": EVENT_TYPE_LABELS.get(
                        ev["event_type"], ev["event_type"]
                    ),
                    "chemical_id": chem["id"],
                    "chemical_name": chem["name"],
                    "secondary_chemical_name": (
                        chem_by_id[ev["secondary_chemical_id"]]["name"]
                        if ev["event_type"] == "mix"
                        and ev["secondary_chemical_id"] in chem_by_id
                        else None
                    ),
                    "target_zone_id": ev["target_zone_id"],
                    "target_zone_name": (
                        zone_names.get(ev["target_zone_id"], "未分区")
                        if ev["target_zone_id"] is not None
                        else None
                    ),
                    "note": ev.get("note", ""),
                },
                "skipped": skipped,
                "before": before,
                "after": snapshot(),
                "finding_count": len(findings) - findings_before,
            }
        )

    # 3) 汇总：总体结论、最早冲突、需隔离对象
    forbidden = [f for f in findings if f["severity"] == "forbidden"]
    reviews = [f for f in findings if f["severity"] == "review"]
    status = "forbidden" if forbidden else ("review" if reviews else "ok")

    earliest = None
    if forbidden:
        f = forbidden[0]  # findings 按 储存→事件顺序 生成，首条即最早
        if f["event"]:
            when = f"事件 #{f['event']['sequence']}（{f['event']['event_type_label']}）"
        else:
            when = "当前储存状态"
        earliest = {
            "when": when,
            "rule_name": f["rule_name"],
            "objects": [o["name"] for o in f["objects"]],
            "zone_name": f["zone_name"],
        }

    isolation, iso_seen = [], set()
    for f in forbidden:
        names = tuple(o["name"] for o in f["objects"])
        key = tuple(sorted(o["id"] for o in f["objects"]))
        if key in iso_seen:
            continue
        iso_seen.add(key)
        if len(names) == 2:
            isolation.append(f"「{names[0]}」与「{names[1]}」必须隔离（{f['rule_name']}）")
        else:
            isolation.append(f"「{names[0]}」需单独隔离/更换容器（{f['rule_name']}）")

    return {
        "status": status,
        "summary": {
            "forbidden_count": len(forbidden),
            "review_count": len(reviews),
            "chemical_count": len(chemicals),
            "pending_event_count": len(pending),
        },
        "earliest_conflict": earliest,
        "isolation": isolation,
        "event_steps": event_steps,
        "findings": findings,
    }
