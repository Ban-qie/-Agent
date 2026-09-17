# 来源与许可

## 代码

本项目基于 [Microsoft Data Formulator](https://github.com/microsoft/data-formulator)，固定标签 `0.8b1`、提交 `5477f0e236426dc8f74a498ec400414fba7fbc0f`，上游包版本 `0.8.0b1`。保留完整上游历史、[MIT LICENSE](LICENSE)、原版权声明、上游 README 和相关贡献说明。本项目的源码增量沿用 MIT；项目版本标签不修改上游包名或暗示微软背书。

主要增量：受限电商快照/指标/执行器、单 AnalystAgent 窄适配、模型预算、本机身份兼容、工作区持久化、React 电商页面及测试和运行工具。React、MUI、Vega、Python/Flask 和其他第三方包仍按各自许可证使用；依赖版本以原 `uv.lock` / `yarn.lock` 为准。本次源码发布不打包第三方二进制、node_modules 或虚拟环境。

## 数据

[Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)，作者 Olist，Kaggle 数据集 `olistbr/brazilian-ecommerce`，版本 2，许可证 [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)。这不是 MIT 数据；使用须遵守署名、非商业和相同方式共享等原许可条件。

本仓库只提供取得、校验和转换脚本、固定指纹与计算口径，不分发 Olist 原始 CSV、压缩包、Parquet 派生快照、逐笔记录、详细计算结果或数据截图。使用者自行从官方来源合法取得数据。订单粒度快照转换包括订单/明细/客户关联、金额转换为整数分、状态与质量校验；转换不改变源数据许可证。不要将原始或派生数据纳入源码提交或发布附件。

## 模型服务与本地记录

Qwen Flash 经阿里云百炼北京地域兼容接口调用，需用户自己的服务账号、密钥和服务使用授权。仓库不分发模型权重、密钥、调用凭据、工作区记录或逐次费用账目；价格估计不是供应商账单。
