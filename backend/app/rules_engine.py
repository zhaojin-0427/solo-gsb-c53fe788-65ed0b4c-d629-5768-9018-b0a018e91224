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

模拟与去重语义
--------------
1. 先报告「当前储存状态」（逐分区、逐组合），再按执行顺序（sequence, id）模拟
   待执行事件，每个事件给出执行前/执行后的模拟库存。
2. 转移：更新化学品所在分区；混合：两项同处目标分区（未指定目标分区时取第一项
   当时所在分区）；废弃：先判定处置等单体规则，再将化学品移出库存。
   后续事件一律基于更新后的状态判定（已废弃对象不再参与后续事件）。
3. 去重只在「当前储存」阶段或「单个事件」内部生效：同一规则与同一组对象在储存
   阶段命中后，仍可在随后的不同事件中分别命中、分别报告。

判定结论：
  forbidden  命中禁止条目
  review     规则要求的数据缺失（如浓度、温度、材质未知），需人工复核
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
    """执行完整审查，返回结果字典（可直接 JSON 序列化）。

    结果中的 timeline 按「当前储存 → 各待执行事件」排列，每个事件携带执行前/
    执行后的模拟库存快照；顶层 findings 为按同一顺序展开的全部发现项。
    """
    zone_names = {z["id"]: z["name"] for z in zones}
    known_zone_ids = set(zone_names)
    chem_by_id = {c["id"]: c for c in chemicals}

    # 模拟库存：化学品 -> 当前分区（None = 未分区）；removed = 已废弃移出库存
    state = {
        c["id"]: (c["zone_id"] if c["zone_id"] in known_zone_ids else None)
        for c in chemicals
    }
    removed = set()

    findings = []        # 全部发现（储存 → 事件顺序展开）
    timeline = []        # 储存阶段 + 每个事件一项，含状态快照与本阶段发现

    pair_rules = [
        r for r in rules if r["enabled"] and r["rule_type"] in PAIR_RULE_TYPES
    ]
    single_rules = [
        r for r in rules if r["enabled"] and r["rule_type"] in SINGLE_RULE_TYPES
    ]

    def chem_brief(cid):
        c = chem_by_id.get(cid)
        return {"id": cid, "name": c["name"] if c else f"#{cid}"}

    def snapshot():
        """当前模拟库存快照：各分区成员、未分区成员、已废弃移出对象。"""
        zones_out = []
        for z in zones:
            ids = sorted(
                cid for cid, zid in state.items()
                if zid == z["id"] and cid not in removed
            )
            zones_out.append({
                "id": z["id"],
                "name": z["name"],
                "chemicals": [chem_brief(cid) for cid in ids],
            })
        unassigned = sorted(
            cid for cid, zid in state.items() if zid is None and cid not in removed
        )
        return {
            "zones": zones_out,
            "unassigned": [chem_brief(cid) for cid in unassigned],
            "removed": [chem_brief(cid) for cid in sorted(removed)],
        }

    def make_finding(rule, severity, objects, zone_id, phase, event, matched, missing):
        return {
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

    def check_pair(bucket, seen, a, b, zone_id, phase, event, event_type):
        for rule in pair_rules:
            if not _applies_to(rule, event_type):
                continue
            result = _match_pair_rule(rule, a, b)
            if result is None:
                continue
            matched, missing = result
            severity = "review" if missing else rule["severity"]
            key = (rule["id"], tuple(sorted([a["id"], b["id"]])), zone_id)
            if key in seen:  # 仅在当前储存阶段 / 单个事件内去重
                continue
            seen.add(key)
            f = make_finding(rule, severity, [a, b], zone_id, phase, event,
                             matched, missing)
            findings.append(f)
            bucket.append(f)

    def check_single(bucket, seen, chem, zone_id, phase, event, event_type):
        for rule in single_rules:
            if not _applies_to(rule, event_type):
                continue
            result = _match_single_rule(rule, chem)
            if result is None:
                continue
            matched, missing = result
            severity = "review" if missing else rule["severity"]
            key = (rule["id"], (chem["id"],), zone_id)
            if key in seen:
                continue
            seen.add(key)
            f = make_finding(rule, severity, [chem], zone_id, phase, event,
                             matched, missing)
            findings.append(f)
            bucket.append(f)

    def zone_members(zone_id):
        return [
            chem_by_id[cid] for cid, zid in state.items()
            if zid == zone_id and cid not in removed
        ]

    # 1) 当前储存状态：逐分区、逐组合判定
    storage_bucket, storage_seen = [], set()
    zone_ids = sorted(known_zone_ids)
    zone_ids.append(None)  # 未分区
    for zone_id in zone_ids:
        chems = zone_members(zone_id)
        for i, c in enumerate(chems):
            check_single(storage_bucket, storage_seen, c, zone_id,
                         "storage", None, "storage")
            for other in chems[i + 1:]:
                check_pair(storage_bucket, storage_seen, c, other, zone_id,
                           "storage", None, "storage")
    timeline.append({
        "phase": "storage",
        "event": None,
        "skipped": False,
        "skip_reason": None,
        "state_before": None,
        "state_after": snapshot(),
        "findings": storage_bucket,
    })

    # 2) 按执行顺序模拟待执行事件：先快照前态 → 应用事件 → 在更新后的状态上判定
    pending = sorted(
        (e for e in events if e["status"] == "pending"),
        key=lambda e: (e["sequence"], e["id"]),
    )
    simulated_count = 0

    def event_descriptor(ev):
        target = ev.get("target_zone_id")
        secondary = ev.get("secondary_chemical_id")
        return {
            "id": ev["id"],
            "sequence": ev["sequence"],
            "event_type": ev["event_type"],
            "event_type_label": EVENT_TYPE_LABELS.get(
                ev["event_type"], ev["event_type"]
            ),
            "note": ev.get("note", ""),
            "chemical": chem_brief(ev["chemical_id"]),
            "secondary_chemical": chem_brief(secondary) if secondary else None,
            "target_zone_id": target,
            "target_zone_name": zone_names.get(target),
        }

    for ev in pending:
        entry = {
            "phase": "event",
            "event": event_descriptor(ev),
            "skipped": False,
            "skip_reason": None,
            "state_before": snapshot(),
            "state_after": None,
            "findings": [],
        }

        def skip(reason):
            entry["skipped"] = True
            entry["skip_reason"] = reason
            entry["state_after"] = snapshot()  # 状态不变
            timeline.append(entry)

        chem = chem_by_id.get(ev["chemical_id"])
        if chem is None:
            skip("事件涉及的化学品不存在，未参与模拟")
            continue
        if chem["id"] in removed:
            skip(f"「{chem['name']}」已在此前事件中废弃并移出库存，事件无法执行")
            continue

        pre = dict(state)  # 事件执行前位置，用于识别「新形成」的同区组合

        def colocated_before(a_id, b_id):
            return (
                a_id in pre and b_id in pre
                and pre[a_id] is not None
                and pre[a_id] == pre[b_id]
            )

        bucket, seen = [], set()

        if ev["event_type"] == "transfer":
            target = ev.get("target_zone_id")
            if target is None or target not in known_zone_ids:
                skip("转移事件缺少有效的目标分区，未参与模拟")
                continue
            state[chem["id"]] = target  # 转移：更新分区
            check_single(bucket, seen, chem, target, "event", ev, "transfer")
            for member in zone_members(target):
                if member["id"] == chem["id"]:
                    continue
                if colocated_before(chem["id"], member["id"]):
                    continue  # 同区挪放：既有组合已在储存阶段判定
                check_pair(bucket, seen, chem, member, target,
                           "event", ev, "transfer")

        elif ev["event_type"] == "mix":
            other_id = ev.get("secondary_chemical_id")
            other = chem_by_id.get(other_id) if other_id else None
            if other is None:
                skip("混合事件缺少有效的第二化学品，未参与模拟")
                continue
            if other["id"] in removed:
                skip(f"第二化学品「{other['name']}」已在此前事件中废弃并移出库存，"
                     "事件无法执行")
                continue
            target = ev.get("target_zone_id")
            if target is not None and target not in known_zone_ids:
                target = None
            # 混合后两项同处目标分区；未指定则取第一项「当时」所在分区
            zone_id = target if target is not None else state[chem["id"]]
            state[chem["id"]] = zone_id
            state[other["id"]] = zone_id
            # 混合的两项无论是否原本同区，都按混合场景判定该组合
            check_pair(bucket, seen, chem, other, zone_id, "event", ev, "mix")
            check_single(bucket, seen, chem, zone_id, "event", ev, "mix")
            check_single(bucket, seen, other, zone_id, "event", ev, "mix")
            participant_ids = {chem["id"], other["id"]}
            for member in zone_members(zone_id):
                if member["id"] in participant_ids:
                    continue
                # 与新加入本分区的混合参与项首次同区 → 新形成的组合需要判定；
                # 同一成员可能与两个参与项分别形成新组合，逐项检查（事件内去重兜底）
                if not colocated_before(chem["id"], member["id"]):
                    check_pair(bucket, seen, member, chem, zone_id, "event", ev, "mix")
                if not colocated_before(other["id"], member["id"]):
                    check_pair(bucket, seen, member, other, zone_id, "event", ev, "mix")

        elif ev["event_type"] == "dispose":
            zone_id = state[chem["id"]]
            # 废弃：先在移出库存前判定处置/腐蚀等单体规则
            check_single(bucket, seen, chem, zone_id, "event", ev, "dispose")
            state[chem["id"]] = None
            removed.add(chem["id"])  # 再移出库存，供后续事件使用

        else:
            skip(f"未知事件类型 {ev['event_type']}，未参与模拟")
            continue

        simulated_count += 1
        entry["findings"] = bucket
        entry["state_after"] = snapshot()
        timeline.append(entry)

    # 3) 汇总：总体结论、计数、最早冲突、需隔离对象
    forbidden = [f for f in findings if f["severity"] == "forbidden"]
    reviews = [f for f in findings if f["severity"] == "review"]
    status = "forbidden" if forbidden else ("review" if reviews else "ok")

    def _when(f):
        if f["event"]:
            return (f"事件 #{f['event']['sequence']}"
                    f"（{f['event']['event_type_label']}）")
        return "当前储存状态"

    earliest = None
    if forbidden:
        f = forbidden[0]  # findings 按 储存→事件顺序 生成，首条即最早
        earliest = {
            "when": _when(f),
            "rule_name": f["rule_name"],
            "objects": [o["name"] for o in f["objects"]],
            "zone_name": f["zone_name"],
        }

    # 按「规则 + 对象组合」去重，保留最早出现的位置；同组对象触发不同规则分别列出
    isolation, iso_seen = [], set()
    for f in forbidden:
        key = (f["rule_id"], tuple(sorted(o["id"] for o in f["objects"])))
        if key in iso_seen:
            continue
        iso_seen.add(key)
        names = [o["name"] for o in f["objects"]]
        where = _when(f)
        if len(names) == 2:
            isolation.append(
                f"「{names[0]}」与「{names[1]}」必须隔离"
                f"（{f['rule_name']}；最早见于{where}）"
            )
        else:
            isolation.append(
                f"「{names[0]}」需单独隔离/更换容器"
                f"（{f['rule_name']}；最早见于{where}）"
            )

    return {
        "status": status,
        "summary": {
            "forbidden_count": len(forbidden),
            "review_count": len(reviews),
            "chemical_count": len(chemicals),
            "pending_event_count": len(pending),
            "simulated_event_count": simulated_count,
            "skipped_event_count": len(pending) - simulated_count,
        },
        "earliest_conflict": earliest,
        "isolation": isolation,
        "timeline": timeline,
        "findings": findings,
    }
