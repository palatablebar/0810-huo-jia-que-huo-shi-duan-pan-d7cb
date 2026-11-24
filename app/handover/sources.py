"""来源等级登记。

数值越大等级越高，仲裁时高等级记录压制低等级记录，
低等级记录一律保留为未采信依据，不得改写结论。
断网后补传只改变到达顺序，不改变来源等级与业务时点。
"""

# 等级：系统快照 > 巡检 > 补录
SOURCE_LEVELS = {
    "snapshot": 3,
    "inspection": 2,
    "supplement": 1,
}

# 中文来源别名，读取 CSV 时归一化为英文键
SOURCE_ALIASES = {
    "系统快照": "snapshot",
    "快照": "snapshot",
    "巡检": "inspection",
    "补录": "supplement",
    "补录记录": "supplement",
}

# 缺货结论归一化：判定值与中文表述的互转
OUTCOME_CANONICAL = {
    "oos": "oos",
    "out_of_stock": "oos",
    "缺货": "oos",
    "是": "oos",
    "in_stock": "in_stock",
    "有货": "in_stock",
    "否": "in_stock",
}

OUTCOME_LABEL = {"oos": "缺货", "in_stock": "有货"}


def canonical_source(value):
    text = value.strip()
    if text in SOURCE_LEVELS:
        return text
    alias = SOURCE_ALIASES.get(text)
    if alias is None:
        raise KeyError(text)
    return alias


def level(source):
    return SOURCE_LEVELS[source]


def canonical_outcome(value):
    text = value.strip()
    outcome = OUTCOME_CANONICAL.get(text)
    if outcome is None:
        raise KeyError(text)
    return outcome
