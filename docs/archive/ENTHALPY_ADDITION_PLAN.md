# 液管焓值添加改造计划

**创建日期**：2026-01-XX  
**优先级**：P1（中等）  
**预计工作量**：2-3小时  
**改造人员**：待分配

---

## 一、问题描述

### 1.1 当前状况
- ✅ 已实现：吸气焓值计算（`sensor_data[8]`）
  - 计算方式：`read_E(吸气压力, 吸气温度)`
  - 映射到3x位置：需确认
  
- ❌ 缺失：液管焓值计算
  - 应计算：`read_E(排气压力, 液管温度)`
  - 用途：冷凝器出口焓值，用于计算制冷量、COP等性能参数

### 1.2 标准.csv要求
```
3x10: 进口焓（吸气焓）
3x11: 出口焓（液管焓）← 当前缺失
```

### 1.3 影响
- 无法通过Modbus读取液管焓值
- 无法计算制冷量：Q = m * (h_吸气 - h_液管)
- 无法计算COP：COP = Q / P_输入
- 点位表与标准.csv不一致

---

## 二、改造方案

### 2.1 点位插入策略

**在3x9（过冷度）后插入2个焓值点位，其余点位顺延**

#### 变更前：
```
3x1-3x5:   温度（ir[0-4]）
3x6-3x7:   压力（ir[5-6]）
3x8:       过热度（ir[7]）
3x9:       过冷度（ir[8]）
3x10:      EXV当前开度（ir[9]）
3x11:      EXV目标开度（ir[10]）
3x12:      整机电流（ir[11]）
3x13-3x14: 质量码（ir[12-13]）
3x15:      传感器故障位图（ir[14]）
3x100+:    系统状态（ir[99+]）不变
```

#### 变更后：
```
3x1-3x5:   温度（ir[0-4]）          不变
3x6-3x7:   压力（ir[5-6]）          不变
3x8:       过热度（ir[7]）          不变
3x9:       过冷度（ir[8]）          不变
3x10:      进口焓（ir[9]）          ← 新增
3x11:      出口焓（ir[10]）         ← 新增
3x12:      EXV当前开度（ir[11]）    ← 从ir[9]移动
3x13:      EXV目标开度（ir[12]）    ← 从ir[10]移动
3x14:      整机电流（ir[13]）       ← 从ir[11]移动
3x15-3x16: 质量码（ir[14-15]）      ← 从ir[12-13]移动
3x17:      传感器故障位图（ir[16]） ← 从ir[14]移动
3x100+:    系统状态（ir[99+]）      不变
```

---

## 三、具体实施步骤

### Step 1: 添加state变量（Board/state.py）

**文件位置**：约第36行（subcooling定义后）

**修改内容**：
```python
# 过冷度（派生通道，2026-09-19 Modbus 重构新增，映射 3x9）
# 计算：饱和温度(排气压力) - 液管温度；无法计算时 999
subcooling = 999

# 液管焓值（派生通道，2026-XX-XX 新增，映射 3x11）
# 计算：read_E(排气压力, 液管温度)；无法计算时 999
liquid_enthalpy = 999  # ← 新增这一行
```

**改造要点**：
- 变量名：`liquid_enthalpy`
- 初始值：`999`（哨兵值，表示未计算）
- 数据类型：int（焓值 * 100，单位：kJ/kg * 100）

---

### Step 2: 添加液管焓值计算（Sensor/read_sensors.py）

#### 2.1 同步版本修改

**文件位置**：约第72行（吸气焓值计算后）

**当前代码**：
```python
        # 焓值 E：查表失败只影响本通道
        try:
            state.sensor_data[8] = read_E(
                state.sensor_data[5], state.sensor_data[0])
        except Exception as e:
            print("[Sensor] E 查表异常，单通道置 999:", e)
            state.sensor_data[8] = 999

        # 过冷度 SC：查表失败只影响本通道
        try:
            sat_temp_d = read_sat_temp(state.sensor_data[6])
            # ... 后续代码
```

**新增代码**（在过冷度计算前插入）：
```python
        # 焓值 E：查表失败只影响本通道
        try:
            state.sensor_data[8] = read_E(
                state.sensor_data[5], state.sensor_data[0])
        except Exception as e:
            print("[Sensor] E 查表异常，单通道置 999:", e)
            state.sensor_data[8] = 999

        # 液管焓值：查表失败只影响本通道 ← 新增
        try:
            state.liquid_enthalpy = read_E(
                state.sensor_data[6], state.sensor_data[4])
        except Exception as e:
            print("[Sensor] 液管焓查表异常，置 999:", e)
            state.liquid_enthalpy = 999

        # 过冷度 SC：查表失败只影响本通道
        try:
            sat_temp_d = read_sat_temp(state.sensor_data[6])
            # ... 后续代码
```

**改造要点**：
- 输入参数：
  - `sensor_data[6]`：排气压力（IDX_DISCHARGE_PSI）
  - `sensor_data[4]`：液管温度（IDX_LIQUID_T）
- 输出：`state.liquid_enthalpy`
- 异常处理：查表失败时置999

---

#### 2.2 异步版本修改

**文件位置**：约第145行（reads_async函数中）

**修改内容**：与同步版本完全相同，在吸气焓值计算后插入液管焓值计算

```python
        # 焓值 E：查表失败只影响本通道
        try:
            state.sensor_data[8] = read_E(
                state.sensor_data[5], state.sensor_data[0])
        except Exception as e:
            print("[Sensor] E 查表异步异常，单通道置 999:", e)
            state.sensor_data[8] = 999

        # 液管焓值：查表失败只影响本通道 ← 新增
        try:
            state.liquid_enthalpy = read_E(
                state.sensor_data[6], state.sensor_data[4])
        except Exception as e:
            print("[Sensor] 液管焓查表异步异常，置 999:", e)
            state.liquid_enthalpy = 999

        # 过冷度 SC：查表失败只影响本通道
        # ... 后续代码
```

**改造要点**：
- 与同步版本逻辑完全一致
- 打印信息加上"异步"字样以便区分

---

### Step 3: 调整Input Register映射（Display_Touch/modbus_slave.py）

**文件位置**：`sync_input_regs()` 函数（约582-643行）

**当前代码**（关键部分）：
```python
def sync_input_regs():
    sd = state.sensor_data
    st = state.state_data
    ir = regs.input_regs

    # 3x1-3x5: 温度 sensor_data[0..4] *100
    for i in range(5):
        ir[i] = _fixed100(sd[i])

    # 3x6 排气压力（高压）/ 3x7 吸气压力（低压）*100
    ir[5] = _fixed100(sd[state.IDX_DISCHARGE_PSI])
    ir[6] = _fixed100(sd[state.IDX_SUCTION_PSI])

    # 3x8 过热度 / 3x9 过冷度 *10
    ir[7] = _fixed10(sd[state.IDX_SUPERHEAT])
    ir[8] = _fixed10(state.subcooling)

    # 3x10-3x11 EXV 当前/目标开度 *10
    ir[9] = _fixed10(st[state.ST_VALVE_POS])
    ir[10] = _fixed10(state.exv_target_pct)

    # 3x12 整机电流 *10
    ir[11] = _fixed10(sd[state.IDX_CURRENT])

    # 3x13-3x14 质量码（每通道 2 bit；0=OK 1=SUSPECT 2=BAD）
    q_low = 0
    for i in range(8):
        q_low |= (state.sensor_quality[i] & 0x03) << (2 * i)
    ir[12] = q_low
    q_high = 0
    for i in range(8, 10):
        q_high |= (state.sensor_quality[i] & 0x03) << (2 * (i - 8))
    ir[13] = q_high

    # 3x15 传感器分通道故障位图（完整 16-bit）
    ir[14] = state.sensor_fault_reg
    
    # ... 后续代码（3x100+）不变
```

**修改后代码**：
```python
def sync_input_regs():
    sd = state.sensor_data
    st = state.state_data
    ir = regs.input_regs

    # 3x1-3x5: 温度 sensor_data[0..4] *100
    for i in range(5):
        ir[i] = _fixed100(sd[i])

    # 3x6 排气压力（高压）/ 3x7 吸气压力（低压）*100
    ir[5] = _fixed100(sd[state.IDX_DISCHARGE_PSI])
    ir[6] = _fixed100(sd[state.IDX_SUCTION_PSI])

    # 3x8 过热度 / 3x9 过冷度 *10
    ir[7] = _fixed10(sd[state.IDX_SUPERHEAT])
    ir[8] = _fixed10(state.subcooling)

    # 3x10 进口焓（吸气焓）/ 3x11 出口焓（液管焓）*100 ← 新增
    ir[9] = _fixed100(sd[state.IDX_E_VALUE])
    ir[10] = _fixed100(state.liquid_enthalpy)

    # 3x12-3x13 EXV 当前/目标开度 *10 ← 索引从9-10改为11-12
    ir[11] = _fixed10(st[state.ST_VALVE_POS])
    ir[12] = _fixed10(state.exv_target_pct)

    # 3x14 整机电流 *10 ← 索引从11改为13
    ir[13] = _fixed10(sd[state.IDX_CURRENT])

    # 3x15-3x16 质量码 ← 索引从12-13改为14-15
    q_low = 0
    for i in range(8):
        q_low |= (state.sensor_quality[i] & 0x03) << (2 * i)
    ir[14] = q_low
    q_high = 0
    for i in range(8, 10):
        q_high |= (state.sensor_quality[i] & 0x03) << (2 * (i - 8))
    ir[15] = q_high

    # 3x17 传感器分通道故障位图 ← 索引从14改为16
    ir[16] = state.sensor_fault_reg
    
    # ... 后续代码（3x100+）不变
```

**改造要点**：
- ir[9-10]：新增焓值映射（*100倍率）
- ir[11-16]：原ir[9-14]整体后移2位
- ir[99+]：高位地址不受影响

---

### Step 4: 更新参考.csv（docs/参考.csv）

**文件位置**：当前第8-15行

**当前内容**（推测）：
```csv
过热度,MODBUS RTU,3x,8
过冷度,MODBUS RTU,3x,9
EXV当前开度,MODBUS RTU,3x,10
EXV目标开度,MODBUS RTU,3x,11
整机电流,MODBUS RTU,3x,12
质量码低,MODBUS RTU,3x,13
质量码高,MODBUS RTU,3x,14
传感器故障位图,MODBUS RTU,3x,15
```

**修改后内容**：
```csv
过热度,MODBUS RTU,3x,8
过冷度,MODBUS RTU,3x,9
进口焓,MODBUS RTU,3x,10               ← 新增（吸气焓，*100，单位：kJ/kg*100）
出口焓,MODBUS RTU,3x,11               ← 新增（液管焓，*100，单位：kJ/kg*100）
EXV当前开度,MODBUS RTU,3x,12          ← 从3x10改为3x12
EXV目标开度,MODBUS RTU,3x,13          ← 从3x11改为3x13
整机电流,MODBUS RTU,3x,14             ← 从3x12改为3x14
质量码低,MODBUS RTU,3x,15             ← 从3x13改为3x15
质量码高,MODBUS RTU,3x,16             ← 从3x14改为3x16
传感器故障位图,MODBUS RTU,3x,17      ← 从3x15改为3x17
```

**改造要点**：
- 在3x9后插入2行
- 后续点位地址全部+2
- 3x100+点位不受影响（稀疏地址）

---

## 四、测试计划

### 4.1 单元测试（Python脚本）

#### 测试用例1：液管焓值计算正确性
```python
import Board.state as state
from Extensions.read_r134a import read_E

# 模拟传感器数据
state.sensor_data[6] = 18.5  # 排气压力 18.5 bar
state.sensor_data[4] = 35.0  # 液管温度 35°C

# 手动计算期望值
expected = read_E(18.5, 35.0)

# 执行传感器采集
from Sensor.read_sensors import reads_async
await reads_async()

# 验证结果
assert state.liquid_enthalpy == int(expected * 100) / 100
print(f"液管焓值测试通过: {state.liquid_enthalpy} kJ/kg")
```

#### 测试用例2：异常处理
```python
# 模拟压力超范围
state.sensor_data[6] = 0.01  # 极低压力，触发查表异常

await reads_async()

# 验证异常处理
assert state.liquid_enthalpy == 999
print("异常处理测试通过")
```

---

### 4.2 Modbus集成测试

#### 测试用例3：点位映射正确性
使用Modbus Poll或pymodbus读取：

```python
from pymodbus.client import ModbusSerialClient

client = ModbusSerialClient(
    method='rtu',
    port='/dev/ttyUSB0',
    baudrate=115200,
    timeout=1
)

# 读取3x10-3x17（焓值 + 原有点位）
result = client.read_input_registers(address=9, count=8, slave=1)

print(f"3x10 进口焓: {result.registers[0] / 100} kJ/kg")
print(f"3x11 出口焓: {result.registers[1] / 100} kJ/kg")
print(f"3x12 EXV当前开度: {result.registers[2] / 10} %")
print(f"3x13 EXV目标开度: {result.registers[3] / 10} %")
print(f"3x14 整机电流: {result.registers[4] / 10} A")
print(f"3x15 质量码低: {bin(result.registers[5])}")
print(f"3x16 质量码高: {bin(result.registers[6])}")
print(f"3x17 传感器故障: {bin(result.registers[7])}")
```

**预期结果**：
- 3x10: 吸气焓值（例如420.50 kJ/kg → 读取42050）
- 3x11: 液管焓值（例如258.30 kJ/kg → 读取25830）
- 3x12-3x17: 与原3x10-3x15值一致

---

#### 测试用例4：高位地址不受影响
```python
# 读取3x100-3x102（系统状态）
result = client.read_input_registers(address=99, count=3, slave=1)

print(f"3x100 系统模式: {result.registers[0]}")
print(f"3x101 启动阶段: {result.registers[1]}")
print(f"3x102 运行子状态: {result.registers[2]}")
```

**预期结果**：与改造前完全一致

---

### 4.3 性能测试

#### 测试用例5：查表性能
```python
import time

# 测试1000次查表耗时
start = time.ticks_ms()
for _ in range(1000):
    read_E(18.5, 35.0)
elapsed = time.ticks_diff(time.ticks_ms(), start)

print(f"1000次查表耗时: {elapsed} ms")
print(f"单次平均: {elapsed / 1000} ms")
```

**性能要求**：单次查表 < 5ms（与吸气焓值查表一致）

---

### 4.4 边界测试

#### 测试用例6：边界条件
```python
# 测试极端压力/温度
test_cases = [
    (0.05, -40),   # 最低压力，极低温
    (30.0, 80),    # 最高压力，极高温
    (10.0, 0),     # 饱和状态（dT=0）
    (10.0, -5),    # 过冷状态（dT<0）
    (10.0, 10),    # 过热状态（dT>0）
]

for psi, temp in test_cases:
    try:
        h = read_E(psi, temp)
        print(f"P={psi} bar, T={temp}°C → H={h} kJ/kg")
    except Exception as e:
        print(f"P={psi} bar, T={temp}°C → 异常: {e}")
```

---

## 五、风险评估

### 5.1 高风险项

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|---------|
| 点位后移导致触摸屏配置失效 | 高 | 高 | 改造前备份触摸屏配置，改造后逐点核对 |
| 液管焓值计算错误 | 中 | 中 | 与手工计算对比验证 |
| 查表性能影响采集周期 | 低 | 中 | 性能测试，必要时优化查表算法 |

### 5.2 中风险项

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|---------|
| 异常处理不完善 | 中 | 低 | 增加边界测试用例 |
| 文档更新遗漏 | 中 | 低 | 改造后全面核对文档 |

---

## 六、回滚方案

### 6.1 回滚步骤

1. **恢复state.py**：删除`liquid_enthalpy`变量
2. **恢复read_sensors.py**：删除液管焓值计算代码（2处）
3. **恢复modbus_slave.py**：将ir[11-16]映射改回ir[9-14]
4. **恢复参考.csv**：删除进口焓/出口焓两行，后续点位地址-2

### 6.2 回滚触发条件

- 液管焓值计算错误率 > 5%
- 查表性能 > 10ms/次
- Modbus通讯异常率 > 1%
- 触摸屏点位读取错误

---

## 七、时间计划

| 阶段 | 任务 | 预计耗时 | 负责人 |
|------|------|---------|--------|
| Day 1 | Step1-2：添加液管焓值计算 | 1小时 | TBD |
| Day 1 | Step3：调整Modbus映射 | 0.5小时 | TBD |
| Day 1 | Step4：更新文档 | 0.5小时 | TBD |
| Day 2 | 单元测试 | 1小时 | TBD |
| Day 2 | Modbus集成测试 | 1小时 | TBD |
| Day 2 | 性能+边界测试 | 1小时 | TBD |
| **总计** | | **5小时** | |

---

## 八、验收标准

### 8.1 功能验收

- [ ] 液管焓值计算正确（与手工计算误差 < 1%）
- [ ] 3x10读取进口焓（吸气焓）正确
- [ ] 3x11读取出口焓（液管焓）正确
- [ ] 3x12-3x17点位值与原3x10-3x15一致
- [ ] 3x100+点位不受影响
- [ ] 异常处理正确（压力超范围时焓值=999）

### 8.2 性能验收

- [ ] 单次液管焓值查表 < 5ms
- [ ] 传感器采集周期 < 100ms（不受影响）
- [ ] Modbus响应时间 < 50ms（不受影响）

### 8.3 稳定性验收

- [ ] 连续运行24小时无异常
- [ ] 液管焓值计算异常率 < 0.1%
- [ ] Modbus通讯成功率 > 99.9%

### 8.4 文档验收

- [ ] 参考.csv更新完成
- [ ] 改造计划文档完整
- [ ] 测试报告归档

---

## 九、注意事项

### 9.1 代码规范
- 变量命名：使用`liquid_enthalpy`（与`subcooling`命名风格一致）
- 异常处理：查表失败时置999，打印日志
- 注释：说明计算公式和单位

### 9.2 数据单位
- state存储：`int(焓值 * 100) / 100`（保留2位小数）
- Modbus传输：`int(焓值 * 100)`（*100倍率）
- 触摸屏显示：`读取值 / 100`（恢复kJ/kg）

### 9.3 查表依赖
- 依赖文件：`Extensions/pt_table.bin`
- 依赖函数：`read_E(psi, temp)`
- 异常类型：`ValueError(-999)`或`ValueError(9999)`

### 9.4 触摸屏配置
改造后需更新威纶通触摸屏配置：
- 3x10 → 进口焓（新增）
- 3x11 → 出口焓（新增）
- 3x12-3x17 → 地址全部+2

---

## 十、相关文档

- [Modbus重构计划](MODBUS_REFACTOR_PLAN.md)
- [Modbus审核报告](MODBUS_REFACTOR_AUDIT_REPORT.md)
- [参考点表](参考.csv)
- [标准点表](标准.csv)

---

**文档版本**：v1.0  
**最后更新**：2026-01-XX  
**状态**：待执行
