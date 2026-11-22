# Modbus 点位表

**版本**：v4.1（2026-09-22 新增 3x10 进口焓 / 3x11 出口焓）
**基准**：`docs/参考.csv`（最新地址以此为准）
**完整机器可读点表**：`docs/MODBUS_POINT_TABLE.csv`
**详细地址说明**：`docs/MODBUS_ADDRESS_MAP.md`
**物理硬件与软件信号映射**：`docs/IO_MAP.md`（引脚/总线 → 软件变量 → 控制与执行器；不替代本 Modbus 点表）

---

## 1. 连接参数

| 参数 | 值 |
|---|---|
| 波特率 | 9600 |
| 数据位 | 8 |
| 停止位 | 1 |
| 校验 | None |
| 从机地址 | 1（可配置） |
| 接口 | UART8, RS485 |

**地址约定**：报文地址 = 代码索引（0 基，标准 Modbus）；屏上显示地址 = 代码索引 + 1。
例如 4x1 对应报文地址 0、`hr[0]`。

> **消费者说明**：本表中部分点位**仅用于人机交互显示**（如 ESC 遥测、系统状态/联锁镜像、
> 过冷度、进出口焓等），由固件单向发布供触摸屏/上位机读取，代码内无消费者属正常设计，
> 并非缺陷或死点位（DESIGN.md §4.1 显示专用点位原则）。

> **样机范围**：当前样机为风冷式直膨制冷，蒸发侧没有水/载冷剂回路。4x6 `FLOW_ENABLE`、1x16 `FLOW_FAULT` 为后续带水回路版本预留；当前固件的 `di_flow` 是软件占位，不是实体输入。当前样机须保持 `FLOW_ENABLE=0`，不得将该点位描述为已具备实物流量保护。

---

## 2. Coils（0x，FC01/05/0F）

| 显示地址 | 代码索引 | 名称 | 符号 | 读写 | 单位 | 倍率 | 说明 |
|---:|---|---|---|---|---|---|---|
| 1 | `coils[0]` | 系统使能开关 | `SYSTEM_ENABLE` | R/W | - | 1 | 0=停机 1=运行 |
| 2 | `coils[1]` | 手动模式使能 | `MANUAL_MODE_ENABLE` | R/W | - | 1 | 0=自动 1=手动 |
| 3 | `coils[2]` | 压缩机转速环使能 | `COMP_SPEED_LOOP_ENABLE` | R/W | - | 1 | 0=禁用 1=启用 |
| 4 | `coils[3]` | 冷凝风机转速环使能 | `COND_FAN_SPEED_LOOP_ENABLE` | R/W | - | 1 | 0=禁用 1=启用 |
| 5 | `coils[4]` | 蒸发风机转速环使能 | `EVAP_FAN_SPEED_LOOP_ENABLE` | R/W | - | 1 | 0=禁用 1=启用 |
| 6 | `coils[5]` | 流量检查使能（后续带水回路版本） | `FLOW_ENABLE` | R/W | - | 1 | 当前样机保持 0；实体 DI 接入后方可启用 |
| 7 | `coils[6]` | 故障复位 | `FAULT_RESET` | W | - | 1 | 写1脉冲复位，回读恒0 |
| 8 | `coils[7]` | 晋级模式切换 | `STARTUP_ADVANCE_MODE` | R/W | - | 1 | 0=定速保持 1=转速稳定性 |

---

## 3. Discrete Inputs（1x，FC02，只读，1=正常 0=故障）

| 显示地址 | 代码索引 | 名称 | 符号 | 读写 | 单位 | 倍率 | 说明 |
|---:|---|---|---|---|---|---|---|
| 1 | `di[0]` | EXV故障 | `EXV_FAULT` | R | - | 1 | 1=正常 0=故障 |
| 2 | `di[1]` | 压缩机故障 | `COMPRESSOR_FAULT` | R | - | 1 | 1=正常 0=故障 |
| 3 | `di[2]` | 蒸发风机故障 | `EVAP_FAN_FAULT` | R | - | 1 | 1=正常 0=故障 |
| 4 | `di[3]` | 冷凝风机故障 | `COND_FAN_FAULT` | R | - | 1 | 1=正常 0=故障 |
| 5 | `di[4]` | 高压保护 | `HIGH_PRESSURE_FAULT` | R | - | 1 | 1=正常 0=故障 |
| 6 | `di[5]` | 低压保护 | `LOW_PRESSURE_FAULT` | R | - | 1 | 1=正常 0=故障 |
| 7 | `di[6]` | 排气温度保护 | `HIGH_DISCHARGE_TEMP` | R | - | 1 | 1=正常 0=故障 |
| 8 | `di[7]` | 过流保护 | `OVERCURRENT_FAULT` | R | - | 1 | 1=正常 0=故障 |
| 9 | `di[8]` | Modbus通讯故障 | `MODBUS_COMM_FAULT` | R | - | 1 | 1=正常 0=故障 |
| 10 | `di[9]` | 低过热度保护 | `LOW_SUPERHEAT_FAULT` | R | - | 1 | 1=正常 0=故障 |
| 11 | `di[10]` | 配置IO故障 | `CONFIG_IO_FAULT` | R | - | 1 | 1=正常 0=故障 |
| 12 | `di[11]` | 吸气压力传感器故障 | `SUCTION_PSI_FAULT` | R | - | 1 | 1=正常 0=故障 |
| 13 | `di[12]` | 排气压力传感器故障 | `DISCHARGE_PSI_FAULT` | R | - | 1 | 1=正常 0=故障 |
| 14 | `di[13]` | 电流传感器故障 | `CURRENT_SENSOR_FAULT` | R | - | 1 | 1=正常 0=故障 |
| 15 | `di[14]` | 传感器超龄故障 | `SENSOR_STALE_FAULT` | R | - | 1 | 1=正常 0=故障 |
| 16 | `di[15]` | 流量保护（后续带水回路版本） | `FLOW_FAULT` | R | - | 1 | 当前为预留状态；软件占位值不代表实体流量 |
| 100 | `di[99]` | Driver链路状态 | `DRIVER_LINK_OK` | R | - | 1 | 0=断开 1=正常 |
| 101 | `di[100]` | 压缩机使能联锁 | `COMP_ENABLE` | R | - | 1 |  |
| 102 | `di[101]` | 蒸发风机使能联锁 | `EVAP_FAN_ENABLE` | R | - | 1 |  |
| 103 | `di[102]` | 冷凝风机使能联锁 | `COND_FAN_ENABLE` | R | - | 1 |  |
| 104 | `di[103]` | EXV使能联锁 | `EXV_ENABLE` | R | - | 1 |  |
| 105 | `di[104]` | 重试锁定标志 | `RETRY_LOCKOUT` | R | - | 1 |  |
| 106 | `di[105]` | 保留_0 | `-` | R | - | 1 |  |
| 107 | `di[106]` | 保留_1 | `-` | R | - | 1 |  |
| 108 | `di[107]` | 保留_2 | `-` | R | - | 1 |  |
| 109 | `di[108]` | 保留_3 | `-` | R | - | 1 |  |
| 110 | `di[109]` | 保留_4 | `-` | R | - | 1 |  |
| 111 | `di[110]` | 保留_5 | `-` | R | - | 1 |  |
| 112 | `di[111]` | 保留_6 | `-` | R | - | 1 |  |
| 113 | `di[112]` | 保留_7 | `-` | R | - | 1 |  |
| 114 | `di[113]` | 保留_8 | `-` | R | - | 1 |  |
| 115 | `di[114]` | 保留_9 | `-` | R | - | 1 |  |

---

## 4. Holding Registers（4x，FC03/06/10）

| 显示地址 | 代码索引 | 名称 | 符号 | 读写 | 单位 | 倍率 | 说明 |
|---:|---|---|---|---|---|---|---|
| 1 | `hr[0]` | 过热度PID_Kp | `SH_PID_KP` | R/W | - | 100 | Kp=寄存器/100 |
| 2 | `hr[1]` | 过热度PID_Ki | `SH_PID_KI` | R/W | - | 100 | Ki=寄存器/100 |
| 3 | `hr[2]` | 过热度PID_Kd | `SH_PID_KD` | R/W | - | 100 | Kd=寄存器/100 |
| 4 | `hr[3]` | 过热度设定值 | `SH_SETPOINT` | R/W | K | 10 |  |
| 5 | `hr[4]` | 过热度误差阈值 | `SH_ERROR_THRESHOLD` | R/W | K | 10 |  |
| 6 | `hr[5]` | 过热度死区 | `SH_DEADBAND` | R/W | K | 10 |  |
| 7 | `hr[6]` | 过热度最大变化量 | `SH_MAX_DELTA` | R/W | - | 1 | EXV单次最大变化 |
| 8 | `hr[7]` | 保留 | `-` | - | - | - |  |
| 16 | `hr[15]` | 容量环PID_Kp | `CAP_PID_KP` | R/W | - | 100 |  |
| 17 | `hr[16]` | 容量环PID_Ki | `CAP_PID_KI` | R/W | - | 100 |  |
| 18 | `hr[17]` | 容量环PID_Kd | `CAP_PID_KD` | R/W | - | 100 |  |
| 19 | `hr[18]` | 容量环设定温度 | `CAP_SETPOINT` | R/W | °C | 100 |  |
| 20 | `hr[19]` | 容量环死区 | `CAP_DEADBAND` | R/W | K | 10 |  |
| 21 | `hr[20]` | 容量环最小油门 | `CAP_THROTTLE_MIN` | R/W | - | 1 | 48-2047 |
| 22 | `hr[21]` | 容量环最大油门 | `CAP_THROTTLE_MAX` | R/W | - | 1 | 48-2047 |
| 23 | `hr[22]` | 容量环最大变化量 | `CAP_MAX_DELTA` | R/W | - | 1 |  |
| 24 | `hr[23]` | 容量环周期 | `CAP_LOOP_PERIOD_S` | R/W | s | 1 |  |
| 25 | `hr[24]` | 冷凝压力环PID_Kp | `COND_PID_KP` | R/W | - | 100 |  |
| 26 | `hr[25]` | 冷凝压力环PID_Ki | `COND_PID_KI` | R/W | - | 100 |  |
| 27 | `hr[26]` | 冷凝压力环PID_Kd | `COND_PID_KD` | R/W | - | 100 |  |
| 28 | `hr[27]` | 冷凝压力环最小油门 | `COND_THROTTLE_MIN` | R/W | - | 1 | 48-2047 |
| 29 | `hr[28]` | 冷凝压力环最大油门 | `COND_THROTTLE_MAX` | R/W | - | 1 | 48-2047 |
| 30 | `hr[29]` | 冷凝压力环最大变化量 | `COND_MAX_DELTA` | R/W | - | 1 |  |
| 31 | `hr[30]` | 高压停机阈值 | `HP_MAX` | R/W | bara | 100 | L2 停机 |
| 32 | `hr[31]` | 高压降容阈值 | `HP_UNLOAD` | R/W | bara | 100 | L1 降容 |
| 33 | `hr[32]` | 高压告警阈值 | `HP_WARN` | R/W | bara | 100 | L0 告警 |
| 34 | `hr[33]` | 冷凝压力目标 | `HP_TARGET` | R/W | bara | 100 |  |
| 50 | `hr[49]` | 低压停机阈值 | `LP_MIN` | R/W | bara | 100 | L2 停机 |
| 51 | `hr[50]` | 低压降容阈值 | `LP_UNLOAD` | R/W | bara | 100 | L1 降容 |
| 52 | `hr[51]` | 低压告警阈值 | `LP_WARN` | R/W | bara | 100 | L0 告警 |
| 53 | `hr[52]` | 低压回升迟滞 | `LP_HYST` | R/W | bara | 100 |  |
| 54 | `hr[53]` | 电流停机阈值 | `I_MAX` | R/W | A | 10 | L2 停机 |
| 55 | `hr[54]` | 电流降容阈值 | `I_UNLOAD` | R/W | A | 10 | L1 降容 |
| 56 | `hr[55]` | 电流禁升阈值 | `I_LIMIT` | R/W | A | 10 | L1 禁升速 |
| 57 | `hr[56]` | 电流告警阈值 | `I_WARN` | R/W | A | 10 | L0 告警 |
| 58 | `hr[57]` | 排气温度停机阈值 | `TD_TRIP` | R/W | °C | 100 | L2 停机 |
| 59 | `hr[58]` | 排气温度降容阈值 | `TD_UNLOAD` | R/W | °C | 100 | L1 降容 |
| 60 | `hr[59]` | 排气温度告警阈值 | `TD_WARN` | R/W | °C | 100 | L0 告警 |
| 61 | `hr[60]` | 最小过热度 | `SH_MIN` | R/W | K | 10 |  |
| 62 | `hr[61]` | 临界过热度 | `SH_CRIT` | R/W | K | 10 |  |
| 63 | `hr[62]` | 保留_2 | `-` | - | - | - |  |
| 100 | `hr[99]` | 延时槽0 | `DELAY_SLOT_0` | R/W | s | 1 | 0-3600 |
| 101 | `hr[100]` | 延时槽1 | `DELAY_SLOT_1` | R/W | s | 1 | 0-3600 |
| 102 | `hr[101]` | 延时槽2 | `DELAY_SLOT_2` | R/W | s | 1 | 0-3600 |
| 103 | `hr[102]` | 延时槽3 | `DELAY_SLOT_3` | R/W | s | 1 | 0-3600 |
| 104 | `hr[103]` | 延时槽4 | `DELAY_SLOT_4` | R/W | s | 1 | 0-3600 |
| 105 | `hr[104]` | 延时槽5 | `DELAY_SLOT_5` | R/W | s | 1 | 0-3600 |
| 106 | `hr[105]` | 延时槽6 | `DELAY_SLOT_6` | R/W | s | 1 | 0-3600 |
| 107 | `hr[106]` | 延时槽7 | `DELAY_SLOT_7` | R/W | s | 1 | 0-3600 |
| 120 | `hr[119]` | EXV启动开度 | `EXV_START_POS` | R/W | % | 10 |  |
| 121 | `hr[120]` | 压缩机启动油门 | `COMP_START_THROTTLE` | R/W | - | 1 | 48-2047 |
| 122 | `hr[121]` | 冷凝风机启动油门 | `COND_FAN_START_THROTTLE` | R/W | - | 1 | 48-2047 |
| 123 | `hr[122]` | 蒸发风机启动油门 | `EVAP_FAN_START_THROTTLE` | R/W | - | 1 | 48-2047 |
| 130 | `hr[129]` | EXV到压缩机延时槽号 | `EXV_TO_COMP_DELAY_SLOT` | R/W | - | 1 | 0-7 |
| 131 | `hr[130]` | 停机压缩机延时槽号 | `COMP_STOP_DELAY_SLOT` | R/W | - | 1 | 0-7 |
| 132 | `hr[131]` | 停机风机延时槽号 | `FAN_STOP_DELAY_SLOT` | R/W | - | 1 | 0-7 |
| 150 | `hr[149]` | 压缩机启动延时 | `COMP_START_DELAY_S` | R/W | s | 1 |  |
| 151 | `hr[150]` | 压缩机转速稳定窗口 | `COMP_RPM_STABILITY_WINDOW_S` | R/W | s | 1 |  |
| 152 | `hr[151]` | 压缩机转速稳定阈值 | `COMP_RPM_STABILITY_THRESHOLD` | R/W | rpm | 1 |  |
| 153 | `hr[152]` | 压缩机最大转速 | `COMP_RPM_MAX` | R/W | rpm | 1 |  |
| 154 | `hr[153]` | 压缩机转速环Kp | `COMP_SPEED_LOOP_KP` | R/W | - | 100 |  |
| 155 | `hr[154]` | 压缩机转速环Ki | `COMP_SPEED_LOOP_KI` | R/W | - | 100 |  |
| 156 | `hr[155]` | 压缩机转速环死区 | `COMP_SPEED_LOOP_DEADBAND` | R/W | rpm | 1 |  |
| 157 | `hr[156]` | 压缩机最大调节量 | `COMP_THROTTLE_MAX_ADJUST` | R/W | - | 1 |  |
| 180 | `hr[179]` | 风机最大转速 | `FAN_RPM_MAX` | R/W | rpm | 1 |  |
| 181 | `hr[180]` | 风机转速环Kp | `FAN_SPEED_LOOP_KP` | R/W | - | 100 |  |
| 182 | `hr[181]` | 风机转速环Ki | `FAN_SPEED_LOOP_KI` | R/W | - | 100 |  |
| 183 | `hr[182]` | 风机转速环死区 | `FAN_SPEED_LOOP_DEADBAND` | R/W | rpm | 1 |  |
| 184 | `hr[183]` | 风机最大调节量 | `FAN_THROTTLE_MAX_ADJUST` | R/W | - | 1 |  |
| 185 | `hr[184]` | 压缩机回油最低油门 | `COMP_OIL_RETURN_THROTTLE` | R/W | - | 1 | 48-500 |
| 186 | `hr[185]` | 降容速率 | `UNLOAD_RATE_THROTTLE_S` | R/W | - | 1 | throttle/s |
| 187 | `hr[186]` | 保留_3 | `-` | - | - | - |  |
| 188 | `hr[187]` | 保留_4 | `-` | - | - | - |  |
| 189 | `hr[188]` | 保留_5 | `-` | - | - | - |  |
| 200 | `hr[199]` | 最短运行时间 | `MIN_ON_S` | R/W | s | 1 |  |
| 201 | `hr[200]` | 最短停机间隔 | `MIN_OFF_S` | R/W | s | 1 |  |
| 202 | `hr[201]` | 每小时最大启动次数 | `MAX_STARTS_PER_HOUR` | R/W | - | 1 | 1-10 |
| 203 | `hr[202]` | 最大重试次数 | `RETRY_MAX` | R/W | - | 1 | 1-10 |
| 204 | `hr[203]` | 重试窗口 | `RETRY_WINDOW_S` | R/W | s | 1 |  |
| 205 | `hr[204]` | Modbus无帧超时 | `MODBUS_TIMEOUT_S` | R/W | s | 1 |  |
| 220 | `hr[219]` | 压缩机手动油门 | `MANUAL_COMP_THROTTLE` | R/W | - | 1 | 0-2047 |
| 221 | `hr[220]` | 冷凝风机手动油门 | `MANUAL_COND_FAN_THROTTLE` | R/W | - | 1 | 0-2047 |
| 222 | `hr[221]` | 蒸发风机用户油门 | `EVAP_FAN_USER_THROTTLE` | R/W | - | 1 | 0-2047 |
| 223 | `hr[222]` | EXV手动开度 | `MANUAL_EXV_PCT` | R/W | % | 10 | 0-1000 |

---

## 5. Input Registers（3x，FC04，只读）

| 显示地址 | 代码索引 | 名称 | 符号 | 读写 | 单位 | 倍率 | 说明 |
|---:|---|---|---|---|---|---|---|
| 1 | `ir[0]` | 吸气温度 | `SUCTION_TEMP` | R | °C | 100 |  |
| 2 | `ir[1]` | 排气温度 | `DISCHARGE_TEMP` | R | °C | 100 |  |
| 3 | `ir[2]` | 蒸发器入口空气温度 | `INLET_TEMP` | R | °C | 100 |  |
| 4 | `ir[3]` | 蒸发器出口空气温度 | `OUTLET_TEMP` | R | °C | 100 | 容量环被控温度 |
| 5 | `ir[4]` | 液管温度 | `LIQUID_TEMP` | R | °C | 100 |  |
| 6 | `ir[5]` | 排气压力 | `DISCHARGE_PRESSURE` | R | bara | 100 |  |
| 7 | `ir[6]` | 吸气压力 | `SUCTION_PRESSURE` | R | bara | 100 |  |
| 8 | `ir[7]` | 过热度 | `SUPERHEAT` | R | K | 10 |  |
| 9 | `ir[8]` | 过冷度 | `SUBCOOLING` | R | K | 10 |  |
| 10 | `ir[9]` | 进口焓 | `INLET_ENTHALPY` | R | kJ/kg | 100 |  |
| 11 | `ir[10]` | 出口焓 | `OUTLET_ENTHALPY` | R | kJ/kg | 100 |  |
| 12 | `ir[11]` | EXV当前开度 | `EXV_POS` | R | % | 10 |  |
| 13 | `ir[12]` | EXV目标开度 | `EXV_TARGET` | R | % | 10 |  |
| 14 | `ir[13]` | 整机电流 | `CURRENT` | R | A | 10 |  |
| 15 | `ir[14]` | 传感器质量码低8位 | `SENSOR_QUALITY_LOW` | R | - | 1 |  |
| 16 | `ir[15]` | 传感器质量码高2位 | `SENSOR_QUALITY_HIGH` | R | - | 1 |  |
| 17 | `ir[16]` | 传感器故障寄存器 | `SENSOR_FAULT_REG` | R | - | 1 |  |
| 100 | `ir[99]` | 系统模式 | `SYS_MODE` | R | - | 1 |  |
| 101 | `ir[100]` | 启动阶段 | `STARTUP_STAGE` | R | - | 1 |  |
| 102 | `ir[101]` | 运行子状态 | `RUNNING_SUBSTATE` | R | - | 1 |  |
| 110 | `ir[109]` | 故障寄存器低16位 | `ERROR_REG_LOW` | R | - | 1 |  |
| 111 | `ir[110]` | 故障寄存器高16位 | `ERROR_REG_HIGH` | R | - | 1 |  |
| 200 | `ir[199]` | 压缩机当前油门 | `COMP_THROTTLE_CMD` | R | - | 1 |  |
| 201 | `ir[200]` | 压缩机当前转速 | `COMP_RPM` | R | rpm | 1 |  |
| 202 | `ir[201]` | 蒸发风机当前油门 | `EVAP_FAN_THROTTLE_CMD` | R | - | 1 |  |
| 203 | `ir[202]` | 蒸发风机当前转速 | `EVAP_FAN_RPM` | R | rpm | 1 |  |
| 204 | `ir[203]` | 冷凝风机当前油门 | `COND_FAN_THROTTLE_CMD` | R | - | 1 |  |
| 205 | `ir[204]` | 冷凝风机当前转速 | `COND_FAN_RPM` | R | rpm | 1 |  |
| 206 | `ir[205]` | 保留_6 | `-` | R | - | - |  |
| 207 | `ir[206]` | ESC0压缩机转速 | `ESC0_RPM` | R | rpm | 1 |  |
| 208 | `ir[207]` | ESC0压缩机温度 | `ESC0_TEMP` | R | °C | 1 |  |
| 209 | `ir[208]` | ESC0压缩机电压 | `ESC0_VOLTAGE` | R | V | 10 |  |
| 210 | `ir[209]` | ESC0压缩机电流 | `ESC0_CURRENT` | R | A | 10 |  |
| 211 | `ir[210]` | ESC1蒸发风机转速 | `ESC1_RPM` | R | rpm | 1 |  |
| 212 | `ir[211]` | ESC1蒸发风机温度 | `ESC1_TEMP` | R | °C | 1 |  |
| 213 | `ir[212]` | ESC1蒸发风机电压 | `ESC1_VOLTAGE` | R | V | 10 |  |
| 214 | `ir[213]` | ESC1蒸发风机电流 | `ESC1_CURRENT` | R | A | 10 |  |
| 215 | `ir[214]` | ESC2冷凝风机转速 | `ESC2_RPM` | R | rpm | 1 |  |
| 216 | `ir[215]` | ESC2冷凝风机温度 | `ESC2_TEMP` | R | °C | 1 |  |
| 217 | `ir[216]` | ESC2冷凝风机电压 | `ESC2_VOLTAGE` | R | V | 10 |  |
| 218 | `ir[217]` | ESC2冷凝风机电流 | `ESC2_CURRENT` | R | A | 10 |  |
| 219 | `ir[218]` | ESC3备用转速 | `ESC3_RPM` | R | rpm | 1 |  |
| 220 | `ir[219]` | ESC3备用温度 | `ESC3_TEMP` | R | °C | 1 |  |
| 221 | `ir[220]` | ESC3备用电压 | `ESC3_VOLTAGE` | R | V | 10 |  |
| 222 | `ir[221]` | ESC3备用电流 | `ESC3_CURRENT` | R | A | 10 |  |
| 223 | `ir[222]` | L1限载动作码 | `LIMIT_ACTIONS` | R | - | 1 |  |

---

## 6. 缩放与无效值

- 温度/压力/焓值 ×100；过热度/过冷度/电流 ×10；PID 增益 ×100（见各表"倍率"列）。
- 传感器故障哨兵 `999` 原样透传。

---

**文档结束**
