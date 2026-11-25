# 阶段接口

`POST /api/stages/<stage>` 接收 `multipart/form-data`。字段 `sample=dirty`、`sample=clean` 或 `file=<CSV>` 三选一。阶段为 `intake`、`prepare`、`calculate`、`policy`、`balance`。预览不入库。可选 `pilot_partitions=7,8` 指定启用同序列等值剔除新规则的分区。

`POST /api/runs` 接收同样的输入，执行全批并返回运行报告和 `signability`。`GET /api/runs` 返回最近 50 批。`GET /api/runs/<id>` 查询一批、当前复核状态和动态 `signability`。

`PATCH /api/runs/<id>` 接收 `revision`、`status` 和 `note`。状态使用 `APPROVED` 或 `HELD`。冲突返回 409，客户端读取新版本后重试。若批次使用的规则版本已停用或规则版本已换版，签发返回 409 `STALE_RULE_VERSION`；重新按当前规则执行批次后再签发。

`GET /api/rules` 查看持久化的规则停用状态；`POST /api/rules/<version>/disable` 与 `/enable` 可切换规则状态。规则检查和复核提交在同一写事务内完成。

`GET /api/runs/<id>/evidence` 下载内含运行清单、行号隔离、业务证据和复核信息的 JSON。
