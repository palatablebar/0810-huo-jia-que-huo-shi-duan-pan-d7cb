# 货架缺货时段盘

零售数据质量分析员可在工作台上传来源 CSV，对照规则判断差异，签发合格批次或记录暂缓意见。

合并停售区间并从门店营业窗口扣除后计算缺货率。

## 交班仲裁

断网恢复后补录、巡检、快照乱序到达时，按来源等级与业务时点为每个货架组形成
可交接的缺货结论：低等级或早时点记录只留作未采信依据，同等级同时点内容不一致的
组待核退出，依据不全的组不能签收。规则与输出见 [交班仲裁说明](docs/HANDOVER.md)，
样例见 `sample_data/handover.csv`。

## 启动

```sh
docker compose up --build -d --wait
```

打开 <http://localhost:18304>。页面预览带异常样例。选中阶段查看计算证据，选择可签发样例并执行批次，然后提交复核意见。

## 核验

```sh
docker compose exec -T app pytest -q
```

[输入字段](docs/DATA-DICTIONARY.md) · [阶段接口](docs/HTTP.md) · [操作步骤](docs/OPERATIONS.md) · [规则与测试](docs/DEPTH.md)
