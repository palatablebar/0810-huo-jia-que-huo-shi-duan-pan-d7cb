# 交班缺货结论仲裁

断网恢复后，补录、巡检和系统快照常以乱序到达。本模块在交班时为每个货架组形成
可签收的缺货结论，保证晚到记录不会盖回已确认结果。

## 来源等级

| 来源 | 键 | 等级（越大越高） |
|---|---|---|
| 系统快照 | `snapshot` | 3 |
| 巡检 | `inspection` | 2 |
| 补录 | `supplement` | 1 |

仲裁只使用**来源等级**与**业务时点**两个维度，`arrival_seq`（断网后的到达序号）
仅作追溯展示，不参与任何排序与判定。

## 仲裁规则

1. **低等级或早时点不得改写当前结果。**
   高等级来源压制低等级来源；同等级内业务时点更晚的确认结果生效。落选记录不删除，
   带原因留作未采信依据：
   - `LOWER_SOURCE`：来源等级低于采信记录，即使更晚到达也不覆盖；
   - `EARLIER_BUSINESS_TIME`：同等级但业务时点更早；
   - `REPLAY`：同等级、同时点、同内容的重复回放，保留一份采信。
2. **同等级、同业务时点、内容不一致 → `PENDING_CONFLICT`。**
   该组不产生结论，记录进入 `disputed`，整组退出交接，等待人工核对。
3. **来源全程可追溯。**
   每组 `counts` 满足
   `input = accepted + rejected + disputed + isolated`，
   每条生效结论都能追到采信来源与全部未采信来源；守恒失败则 `fully_traced=false`。
4. **依据不全不能签收。**
   只有同时满足「状态 `CONFIRMED`、无隔离记录、来源守恒、存在采信记录」的组
   `signoff_eligible=true`。存在隔离（`INCOMPLETE_EVIDENCE`）、无有效依据
   （`NO_EVIDENCE`）或争议（`PENDING_CONFLICT`）的组一律不可签收；
   全部组可签收时报告级 `handoff_ready=true`。

## 输入

CSV（UTF-8，允许 BOM），列顺序固定：

```
shelf,source,business_time,outcome,arrival_seq,record_id,note
```

- `source` 接受英文键或中文别名（系统快照/巡检/补录）。
- `business_time` 为 ISO 8601 完整时点（含时分秒），`Z` 或带偏移时区；
  无时区按 UTC 处理。
- `outcome` 接受 `缺货/有货`、`oos/in_stock`、`是/否`。
- 校验失败的行进 `isolated`，原因包括 `UNKNOWN_SOURCE`、`UNKNOWN_OUTCOME`、
  `BAD_TIMESTAMP`、`BAD_ARRIVAL_SEQ`、`COLUMN_COUNT`、`BLANK_ROW` 等。

样例见 `sample_data/handover.csv`，测试见 `tests/test_handover.py`。

## 输出结构

报告 `handover-conclusion/1`：

- `totals`：组数、已确认、待核、无依据、可签收数与全量守恒标志；
- `groups[]`：每组的 `status`、`conclusion`、`blockers`、`accepted`、
  `rejected`（带未采信原因）、`disputed`、`isolated`、`counts` 与签收标志；
- `handoff_ready`：是否所有组都可签收；
- `orphan_isolation`：无法归属到任何货架的隔离行，存在即阻断交接。

## 调用

```python
from app.handover.service import handover

report = handover(Path("sample_data/handover.csv").read_bytes())
```
