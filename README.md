# 电台播出与版权窗口排程

一个不依赖第三方包、使用 SQLite 和标准库 HTTP 服务的电台排程项目。系统把“计划排期”和“实际播出”分开保存，支持地区授权、日期窗口、禁播时段、节目冷却、赞助商间隔、直播临时替换、实播对账与版权越界检查。

## 运行

需要 Python 3.11+。

```bash
python app.py
```

默认端口为 `8111`，页面地址是 <http://127.0.0.1:8111>。第一次启动会创建 `radio.db` 并写入三条演示排期。也可以设置端口和数据库位置：

```bash
PORT=9000 RADIO_DB=/tmp/radio.db python app.py
```

## 测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖完整流程：排期、临时替换、播放日志、按日期对账；同时覆盖时间重叠、未授权地区和实播错节目等失败场景，以及节目单锁定、解锁原因与重新锁定流程。

## 节目单锁定

播出前审核完当天节目单后，可按“日期 + 地区”锁定已审核的计划，锁定时记录锁定人和时间：

- 锁定后新建排期、替换该日期地区的节目会被直接拒绝：“节目单已锁定，原安排不能动”。
- 实播登记（playout）和对账不受锁定影响，照常进行。
- 需要改时先解锁并**必填原因**，解锁记录解锁人和时间；原锁定记录保留在历史中。
- 解锁后可调整安排，再次审核后可以重新锁定，锁定/解锁历史完整保留。
- 页面显示当前选择日期地区的锁定状态、全部锁定状态一览，以及锁定/解锁历史（最近操作）；操作后当前计划即时刷新。

## 主要 API

- `GET /api/state`：节目、排期、锁定状态、锁定历史和最近对账异常
- `POST /api/programs`：创建节目并授权地区
- `POST /api/programs/{id}/regions`：追加地区授权
- `POST /api/schedule`：创建排期（该日期地区已锁定时拒绝）
- `POST /api/slots/{id}/replace`：替换计划节目并重新校验（已锁定时拒绝）
- `POST /api/playout`：登记实播记录（锁定时照常）
- `POST /api/reconcile`：按日期生成漏播、错播、时长偏差和超授权异常
- `POST /api/locks/lock`：锁定节目单，参数 `air_date`、`region`、`operator`
- `POST /api/locks/unlock`：解锁并填写原因，参数 `air_date`、`region`、`operator`、`reason`
- `GET /api/locks?date=YYYY-MM-DD&region=...`：查询指定日期地区的锁定状态与完整历史

准备排期时填写 `air_date`、`start_time`、`program_id`、`region`。页面会直接显示校验错误，不会保存失败的排期。
