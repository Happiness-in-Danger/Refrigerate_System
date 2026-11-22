# 项目文档导航

本目录按“**先了解架构，再查运行流程，最后查接口和硬件点位**”组织。代码行为与设计目标有差异时，优先核对对应实现文件和文档中的“当前代码现状”说明。

## 推荐阅读路径

1. [`STRUCTURE.md`](STRUCTURE.md)：目录、模块职责、数据面和协程清单。
2. [`SYSTEM_FULL_MAP.md`](SYSTEM_FULL_MAP.md)：全系统主链路、状态机、控制环及 Modbus 数据面的总图。
3. [`IO_MAP.md`](IO_MAP.md)：现场传感器/通信接口 → 软件变量 → 控制消费者 → 执行器的映射。
4. [`STARTUP_FLOW.md`](STARTUP_FLOW.md)：上电、启动、运行、停机、复位的时序细节。
5. [`MODBUS_POINT_TABLE.md`](MODBUS_POINT_TABLE.md)：HMI 点位、地址、倍率和读写权限。
6. [`DESIGN.md`](DESIGN.md)：设计原则、状态机、保护和控制策略。
7. [`PLAN.md`](PLAN.md) / [`REPORT.md`](REPORT.md)：进度、未决事项和验收记录。
8. [`AI_DEVELOPMENT_RULES.md`](AI_DEVELOPMENT_RULES.md)：修改代码前必须遵守的约束。

## 文档职责与权威来源

| 主题 | 主文档 | 说明 |
|---|---|---|
| 代码目录、模块职责、共享数据 | [`STRUCTURE.md`](STRUCTURE.md) | 文件和数据面速查 |
| 物理硬件输入/输出映射 | [`IO_MAP.md`](IO_MAP.md) | MCU 引脚、总线、软件索引、控制消费者和反馈链 |
| HMI/Modbus 外部点位 | [`MODBUS_POINT_TABLE.md`](MODBUS_POINT_TABLE.md) / [`MODBUS_POINT_TABLE.csv`](MODBUS_POINT_TABLE.csv) | 地址、倍率、读写权限；不得以 IO_MAP 替代 |
| Modbus 内部地址解释 | [`MODBUS_ADDRESS_MAP.md`](MODBUS_ADDRESS_MAP.md) | 内部变量到寄存器的详细换算 |
| 系统级运行信息流 | [`SYSTEM_FULL_MAP.md`](SYSTEM_FULL_MAP.md) | 传感器、状态机、控制环、设备、HMI 的合并视图 |
| 启停时序 | [`STARTUP_FLOW.md`](STARTUP_FLOW.md) | 阶段和判据细节 |
| 设计规范 | [`DESIGN.md`](DESIGN.md) | 架构和行为规范；当前实现差异须注明 |
| 计划与问题 | [`PLAN.md`](PLAN.md) | 进度及待办的唯一入口 |
| 测试/审计结果 | [`REPORT.md`](REPORT.md) | 阶段验收和历史结论 |
| 开发约束 | [`AI_DEVELOPMENT_RULES.md`](AI_DEVELOPMENT_RULES.md) | 修改代码前必读 |

## 地图边界

- `IO_MAP.md` 描述**物理硬件与固件内部信号**的映射；未接线或未核实的端点会标为待确认。
- `MODBUS_POINT_TABLE.md` 描述**HMI/上位机与固件**之间的外部数据契约。
- `SYSTEM_FULL_MAP.md` 描述**运行时数据流和状态迁移**。

三者互相引用但不互相取代。新增传感器、执行器、通信通道或 Modbus 点位时，应同步更新对应地图和 `STRUCTURE.md`。
