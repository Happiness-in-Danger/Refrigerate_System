# 电脑 HMI 串口通信设计（修订 12）

状态：设计稿。修订 12 已对照固件源码逐项核对；PC 端与固件端 UART HMI 协程已实现（见 `pc_hmi/` 与 `Board/hmi_uart.py`）。本文档仅描述设计，不包含实现代码。

修订记录：
- 修订 6：去掉全部 Modbus 依赖；点位清单来自 `Board/state.py`；加入异步发送的阻塞控制。
- 修订 7：取值范围只保留操作者设定的用户范围，不设硬限；超范围拒绝并回退原值。
- 修订 8：补全点位清单；增加范围读写命令；明确超时停机、原子写入、持久化和快照格式。
- 修订 9：按“只有一台 PC、直连一块板”简化并优化。
  - 快照拆分：动态快照（每秒）与参数快照（按需），带宽降到约 1/3。
  - 报警位图以单个整数传输，不再逐位传。
  - 重新统计点位数（动态 75，参数 166）。
  - 补充：发送队列（行级原子）、范围与当前值的关系、校验规则、哨兵范围、生效时机、重启行为。
  - 统一符号名；`COND_SETPOINT` 改为只读镜像（单一来源 `HP_TARGET`）。
- 修订 10：
  - 停止优先：`W SYSTEM_ENABLE 0` 不受“等待应答”限制，固件优先处理；超时停机同时清除手动模式。
  - 心跳由 PC 界面线程“喂”标志驱动；固件只把合法命令行算作心跳。
  - 增加批量写入；`E` 回复可带出错符号。
  - 写类命令加 XOR 校验，新增 `E CRC`（原 Q7 已确认）。
  - `rxbuf` 改为 512，单行上限 128 字节。
  - 点位清单由 `points.csv` 生成两端代码；握手返回清单哈希。
  - 增加快照生成与内存规则（4.4）和连接状态表（9.1）。
  - 协议版本升为 3。
- 修订 11（修订 10 引入的问题及修正）：
  - 行长上限提到 256，`rxbuf` 提到 1024；批量上限降到 5 对；符号长度不超过 32 且不得为 `END`。
  - 新增 `R *`，批量读取全部已设定范围，以 `R END <n>` 结束；PC 重连时按 `H`、`Q`、`R *` 顺序恢复。
  - 发送队列分为应答槽和数据槽，应答一定有位置；`E BUSY` 只用于数据请求；待发的旧 `S` 可被丢弃让位。
  - 清单哈希覆盖类型、读写、精度、单位、枚举、位图定义；其他影响线上格式的改动必须升协议版本。
  - 规定数值文本表示上限（浮点 12 字符、整数 11 字符），快照缓冲按最坏情形分配（动态 1024、参数 2048），格式化带越界检查。
  - 协议版本升为 4。
- 修订 12（对照固件源码核对后补全；点数 166/75 经脚本核对无误，166 个参数符号均能在 `Board/state.py` 中找到对应键）：
  - 确认 HMI 与 Modbus 完全独立（第 1 节），新增“与组态屏操作的对应”（第 9.2 节）。
  - 关闭 Q9（第 8 节）和 Q17（附录 D）；补充 `SYS_MODE` 等枚举定义（附录 D）。
  - **修正一处设计错误**：`ERROR_REG` 全部正常时为 4294967295，超过原定整数上限 2,147,483,647，按旧规则会被输出成 999。位图点位改按无符号 32 位处理（6.2）。
  - 新增附录 C（符号到 state 的映射与生效时机）。核对发现 18 个参数在现有固件下需重启才生效（Q19，仅作备注），并有一处持久化缺口（Q21）。
  - 新增附录 E（推荐用户范围），由现有 Modbus `_HR_RANGE` 换算，避免从组态屏切换后失去限值。
  - 新增待确认 Q18、Q20、Q21 和备注 Q19；修正 `read_buf_len` 写法、13.1 与第 10 节中的陈旧数值。
  - 协议版本仍为 4（固件尚无实现；点位元数据新增枚举类型，哈希随之变化）。
 - 复核（2026-11-24，逐条对照源码）：13.1 中残留的“Q9 待核对”已改；Q9 增加蒸发风机例外；Q18 补数据来源并同步 A.7、B.3、6.3；Q20 补转速环 `PERIOD_S` 的实际含义；Q21 补保存值与运行值的区分，并给出固件修复前的界面处理；附录 E 重写：改正“唯一限值来源”的说法，拆出不建议应用的 14 项，补默认值与说明，并列出没有现成范围的 49 项。表内数值未变。
 - PC HMI 界面调整（2026-11-25）：`ERROR_REG` 已定义的报警位 0–15（包括 bit 8 `MODBUS_COMM_FAULT`）均逐位显示；保留位 16–31 不作为报警显示。仅 UI 呈现变化，不影响协议版本和清单哈希。
 - 修订 13（2026-11-26）：将 `ESC_TELEMETRY_CFG` 展开为 A.8 组 32 个 HMI 可写参数；参数总数 134 → 166，清单哈希更新为 `14B2`，参数快照缓冲扩至 2304 字节。

## 1. 目标

用电脑上的 HMI 软件替代实体组态屏，通过 USB-TTL 串口与 STM32 主控通信。界面覆盖：运行状态、报警与联锁、参数查看与修改、启停、模式和复位操作。

本文档不涉及 Modbus。控制算法、保护策略、启停状态机和执行器驱动不改动。

**与现有 Modbus 的关系（修订 12 确认）**：UART8 上的 Modbus 从机与 UART1 上的 HMI 链路互相独立，各有一套点位和一套超时。HMI 不读写 Modbus 寄存器表，也不调用其读写函数。没有 Modbus 主站时 `last_modbus_frame_ms` 保持 0，规则 `MODBUS_COMM_FAULT` 不判故障（`Alarm/rules.py:414-426`），所以只用 UART HMI 不会因 Modbus 超时而停机。

**界面目标**：功能上等价于现有组态屏，每一项屏上的操作在 HMI 中都有对应（第 9.2 节）。

**使用前提**：只有一台 PC，点对点连接一块主控板。不考虑多客户端、多会话和权限。

## 2. 设计原则

1. **数据只有一个来源**：运行数据和参数都是 `Board/state.py` 中的工程值。HMI 不维护第二套数据，也不经过寄存器。同一个量只有一个可写点位，其他同值点位只读。
2. **协程不被阻塞**：UART `write()` 在这份固件中是忙等发送，发送必须分块，块间让出调度。
3. **协议最简**：ASCII 文本行，固定顺序数值，不使用 JSON。
4. **先校验，后修改**：写入前完整检查，全部通过才修改 `state`；失败则保持原值。
5. **按需传输**：变化的数据周期发送，几乎不变的参数只在需要时发送。
6. **安全优先**：PC 断开时正常停机，重连不自动恢复。停止命令优先于其他任何命令。

## 3. 固件结构

UART1 通信由三个协程完成：

| 协程 | 周期 | 职责 |
|---|---|---|
| `hmi_rx` | 每 20 ms | 非阻塞读取串口，按 LF 切行，校验，分派命令（停机命令优先），修改 `state`，把应答放入发送队列 |
| `hmi_tx` | 每 1 s 生成动态快照；队列有内容时随时发送 | 从发送队列按行取出，分块发送，块间 `await` |
| `hmi_watchdog` | 每 100 ms | 检查最近一次收到合法命令行的时间，超时请求正常停机并清除手动模式 |

`main.py` 中现有的 `Display()` 协程替换为上面三个协程。

MicroPython 协程是协作式的，三个协程与控制协程共用一个线程。只要写入 `state` 的过程中没有 `await`，就不会与控制协程交错。

### 3.1 发送队列（行级原子，两类槽）

- 所有发往 PC 的内容先成为“一行”。行级原子：一行发完（含 LF）才发下一行，块间 `await` 期间不会插入其他行的字节，所以不会出现两条消息交错。
- **应答槽**：`OK`、`E`、`H`、`R <符号>` 单点应答，容量 4 行，每行缓冲 64 字节，预分配。应答只使用应答槽。PC 协议保证未应答命令不超过 1 条，加上插队的停机命令，最多同时挂起 2 条，所以应答槽不会满。若仍满（说明 PC 违反协议），固件不执行该命令，直接丢弃并递增计数、记日志，不回复。
- **数据槽**：`S`、`Q`、`R *` 的列表，容量 2 项（1 项正在发送，1 项待发）。
- **优先级**：应答槽高于数据槽中的 `Q` 和 `R *` 列表，后者高于 `S`。切换只发生在“行”之间。
- **请求数据时的规则**：
  - `G`：不排队额外快照，只置“需要新 `S`”标志，`hmi_tx` 下一次空闲时发送一份新 `S`。
  - `Q`、`R *`：需要数据槽。若待发位上有尚未开始发送的 `S`，丢弃它让位；若数据槽被正在发送的 `Q` 或 `R *` 占用，回复 `E BUSY`（走应答槽，一定有位置）。
  - 周期 `S`：数据槽没有空位时跳过这一帧，PC 用序号发现丢帧。
- **`R *` 的列表**：作为一个任务占用一个数据槽，`hmi_tx` 每次生成并发送一行，行间先发送应答槽中的内容。列表发送期间不生成新的 `S`。
- **最坏延迟**：应答要等当前正在发送的一行发完。最长的一行是最坏情形的 `Q`，约 210 毫秒（见 4.2），低于命令超时，也包含在停机命令的延迟估计中。
- `hmi_rx` 只放入请求，不直接发送。

## 4. 异步与阻塞

### 4.1 问题

- `ports/stm32/machine_uart.c` 与 `uart.c` 中，`write()` 逐字节忙等发送寄存器，直到整段发完才返回。
- 忙等期间不调用调度器，所有协程停止运行。
- `txdone()` 在这份固件上总是返回 True，没有后台发送机制。
- 115200 baud 下约 11.5 字节/毫秒，即每字节约 87 µs。

### 4.2 规则

- **分块发送**：每次 `write()` 不超过 32 字节，阻塞约 3 毫秒。块间 `await asyncio.sleep_ms(1)`。
- **动态快照**：75 个值。典型约 400 字节，约 13 块，墙钟约 50 毫秒；最坏（每个值 12 字符）约 990 字节，约 31 块，墙钟约 120 毫秒。
- **参数快照**：166 个值。典型约 1000 字节，约 31 块，墙钟约 130 毫秒；最坏约 2161 字节，约 68 块，墙钟约 270 毫秒。仅在握手后、`W` 成功后、`Q` 请求时发送，不周期发送。
- **接收不阻塞**：`timeout=0`，只用 `uart.any()` 判断，再 `uart.read(n)`。不使用 `readline()`。
- **接收缓冲**：`rxbuf=1024`（本项目用 `pyb.UART`，参数名是 `read_buf_len`，`Board/board.py` 中 UART8 已是这种写法；文中的 `rxbuf` 均指它）。单行（含校验）不超过 256 字节，超长则丢弃到下一个 LF，并回复 `E FORMAT`。最坏的批量写入行约 235 字节（5 对，符号 32 字符，值 12 字符）。115200 baud 下 20 ms 约进入 230 字节，1024 能容纳一整行加两个周期的到达量；若实测仍有溢出，把 `hmi_rx` 周期降到 10 ms。
- **单次运行预算**：任何协程的单次运行不超过约 5 毫秒。
- **持久化不在 `hmi_rx` 内执行**：写 flash 可能阻塞十几毫秒。`params_store` 的 30 秒防抖保存由其现有机制触发，不在命令处理路径里同步写 flash。

### 4.3 验收指标

- 实测每个发送块的 `write()` 耗时，记录最大值。
- 实测 `hmi_tx` 运行期间，EXV PID（100 ms）和压缩机容量环的周期抖动。
- 实测 `params_store` 保存一次的阻塞时长，以及一次 `W` 命令（含原子写入）的处理耗时。
- 实测快照格式化每个分片的耗时、快照值复制的耗时、`gc.collect()` 单次耗时，以及 `gc.mem_free()` 的最低余量。
- 用最坏情形数值（每个值 12 字符）构造一次动态快照和参数快照，记录发送总时长和对控制周期的影响。
- 若单次阻塞超过 5 毫秒，减小块大小或降低快照频率。

### 4.4 快照生成与内存

目的：避免反复分配堆内存，减少垃圾回收造成的偶发长停顿，保护 EXV PID 和容量环的周期。

- **预分配**：动态快照和参数快照各有一个固定大小的发送 `bytearray`，启动时分配一次，反复使用。大小按最坏情形计算（每个值 12 字符加 1 个空格）：
  - 动态快照：`S ` 2 字节 + 序号（最长 10 位）加空格 11 字节 + 75×13 + LF 1 字节 ≈ 989 字节，缓冲 1024 字节。
  - 参数快照：`Q ` 2 字节 + 166×13 + LF 1 字节 ≈ 2161 字节，缓冲 2304 字节。
  - 范围列表每行 `R <符号> <最小> <最大>` 最长约 61 字节，行缓冲 64 字节。

  不用字符串拼接、`%`、`format`、f-string 生成整行。
- **越界检查**：格式化每写一个值之前检查剩余空间。空间不够视为内部错误：丢弃这份快照并记日志，不写到缓冲之外。按上面的最坏情形计算，正常不会触发。
- **一致性**：生成快照时，先在一个不含 `await` 的过程里，把各点位当前值复制到预分配的列表（动态 75 项，参数 166 项）。同一份快照的所有值来自同一时刻。之后分片格式化只读这份列表，不再读 `state`。
- **格式化**：整数直接写入数字。浮点值按“乘 100 取整”手写两位小数，写入 `bytearray`，不调用浮点格式化。每个分片（对应一块发送，不超过 32 字节）的格式化耗时目标不超过 1 毫秒。
- **复制耗时**：需实测。若超过 1 毫秒，记录实测数据后重新评估，不在文档外私自改成分段复制（会破坏同一时刻的一致性）。
- **垃圾回收**：若实测发现偶发回收停顿超过 5 毫秒，则在每个快照发送完成后主动调用一次 `gc.collect()`，让停顿出现在可预测的时刻。不在发送块之间调用。
- **命令解析**：`hmi_rx` 复用接收缓冲，用索引切分字段，不为每条命令创建多个临时字符串。符号查找用启动时建好的字典（符号→点位索引）。

## 5. 硬件与串口

- USB 转 3.3 V TTL 串口。禁止使用 USB-RS485 转换器直接连接 UART1 TTL 引脚。
- 115200 baud，8 data bits，无校验，1 stop bit（8N1）。
- TX 接 RX，RX 接 TX，共地。禁止 5 V 电平接入 STM32 引脚。
- 板型 `MY_H743ZIT6`：UART1 为 PB6(TX)/PB7(RX)，未定义 REPL，可设置 `rxbuf`。
- 接线由开发者自行完成。

## 6. 点位清单

### 6.1 原则

- 点位清单以 `Board/state.py` 为准。附录 A 列出全部点位。
- 符号名与 `state.py` 的键名一致；同名键加组前缀区分。
- 清单分两份，各有固定顺序：
  - **动态清单**（75 个值）：只读量、报警位图、联锁、`SYSTEM_ENABLE`。随 `S` 周期发送。
  - **参数清单**（166 个值）：A.1–A.8 的全部参数。随 `Q` 按需发送。
- 固件与 PC 端使用同一份清单。清单版本号和清单哈希由固件返回（握手时），PC 端对照自己的清单校验。
- 不纳入清单：`MODBUS_TIMEOUT_S`。
- **单一来源**：维护一份 `points.csv`，每行一个点位：符号、所属清单（`S`/`Q`）、类型、默认值、读写、保持、同步目标、说明。用生成脚本产出固件的点位表和 PC 端的点位表，不手工维护两份。脚本位置和名称待定（由用户决定）。
- **清单哈希**：覆盖所有影响线上解析的点位元数据，不只是符号名。`points.csv` 规范化后，按传输顺序（动态清单、参数清单，最后是动作清单）每个点位生成一行：`符号,清单,类型,读写,小数位,单位,取值集合或位定义`，行以 LF 结束，全部拼接后计算 CRC16-CCITT（初值 `0xFFFF`，多项式 `0x1021`），以 4 位大写十六进制表示。
  - 清单：`S`、`Q`，动作为 `-`。
  - 类型：`i` 整数、`f` 浮点、`e` 枚举、`b` 位图、`s` 开关、`a` 动作。
  - 读写：`R` 只读、`W` 可写、`A` 动作。
  - 小数位：整数为 0，浮点为 2。
  - 取值集合：枚举写 `1|2|3`；位图写 `0:名称;1:名称;…`，包含 `ERROR_REG`、`SENSOR_FAULT_REG`、`LIMIT_ACTIONS` 的全部位定义，以及 `SYS_MODE`、`STARTUP_STAGE`、`RUNNING_SUBSTATE`、`SENSOR_QUALITY_0…9` 的取值集合，均见附录 D。其他点位为空。
  - 不进入哈希的字段：默认值、说明、保持、同步目标、界面文字。它们不影响线上解析。
  - **规则**：任何会改变线上格式或数值含义的修改（命令集、行格式、校验方式、数值上限），如果 `points.csv` 元数据里没有对应字段，必须升级协议版本号，不依赖哈希。
  - **生成脚本检查**：符号长度不超过 32 字符，符号不得为 `END`，符号不得重复。

### 6.2 类型

- **整数点位**：`int`，以整数传输。
- **浮点点位**：`float`，快照中保留 2 位小数。
- **开关点位**：`0`/`1`。
- **位图点位**：整数，PC 端按位拆分。
- **只读点位**：不接受 `W`。
- **动作点位**：只能通过 `A` 执行。
- **数值表示上限**（协议规定，不是设备硬限）：
  - 浮点点位：绝对值小于 100,000,000，输出形如 `-1234567.89`，最长 12 字符。
  - 整数点位：绝对值不超过 2,147,483,647，最长 11 字符。
  - **位图点位按无符号 32 位处理**：范围 0–4,294,967,295，最长 10 字符，不适用上一条的有符号上限，也不会被替换成 `999`。`ERROR_REG` 全部正常时就是 4294967295。PC 端按无符号整数解析。
  - `W` 的值和 `R` 设定的最小/最大值都按此检查。超限，或不是有限数（nan、inf），回复 `E FORMAT`，不修改。
  - 只读点位的读数若超出此上限，输出 `999` 并记日志，不输出超长文本。
  - 因此用户范围虽然没有设备硬限，文本长度仍有上限，快照缓冲按最坏情形分配（见 4.4）。

### 6.3 写入规则

- 只允许写入读写点位。
- **取值范围**：每个读写点位有一个用户范围（最小值、最大值），由操作者通过 `R` 命令设定（见第 12 节）。未设定时只检查类型和精度。
- **原子写入**，固件按以下顺序执行：
  1. 检查格式、类型、精度、用户范围、关系约束（见下）和禁止项。
  2. 全部通过后，记录所有受影响字段的原值。
  3. 依次写入（含副作用同步字段）。
  4. 任何一步失败，恢复所有已修改字段的原值，回复错误码。
- **超出范围即拒绝，回退原值**：不做钳位。PC 端收到错误后，界面恢复为上一次参数快照中的值。
- **批量写入**：`W` 可带多个“符号 值”对，最多 5 对（最大的关联组 `HP_TARGET`、`HP_WARN`、`HP_UNLOAD`、`HP_MAX` 共 4 个；行长限制见 4.2），作为一次原子写入。固件先用所有新值构成写入后的完整组合，再统一做关系约束检查，所以整体调整关联阈值时不受逐个写入的顺序限制。同一符号重复出现回复 `E FORMAT`。任何一对失败，整批不修改，回复 `E <码> <出错符号>`。
- **关系约束**（固件检查，写入后的新值组合必须成立）：
  - `HP_TARGET < HP_WARN < HP_UNLOAD < HP_MAX`
  - `LP_MIN < LP_UNLOAD < LP_WARN`
  - `I_WARN < I_LIMIT < I_UNLOAD < I_MAX`
  - `TD_WARN < TD_UNLOAD < TD_TRIP`
  - `SH_CRIT < SH_MIN`
  - `CAP_THROTTLE_MIN < CAP_THROTTLE_MAX`
  - `COND_THROTTLE_MIN < COND_THROTTLE_MAX`
  - 所有 `*_DELAY_SLOT` 点位取值为 0–7
  - `STARTUP_ADVANCE_MODE` 取值为 0 或 1；各 `*_AGGR_MODE` 取值为 1、2、3；各开关点位取值为 0 或 1
- **有副作用的写入（单向同步，目标点位为只读）**：

  | 写入点位 | 同步到 | 说明 |
  |---|---|---|
  | `HP_TARGET` | `cond_control_params['setpoint']`，即只读的 `COND_SETPOINT` | `COND_SETPOINT` 不可直接写 |
  | `CSL_RPM_MAX` | `ST_MOTOR_SPEED_MAX`，即只读的 `MOTOR_SPEED_MAX` | |
  | `FSL_RPM_MAX` | `ST_FAN_SPEED_MAX`，即只读的 `FAN_SPEED_MAX` | |
  | `EVAP_FAN_USER_THROTTLE` | 无（HMI 写入时不再同步 `EVAP_FAN_CMD`） | `EVAP_FAN_CMD` 是蒸发风机转速环的目标转速（rpm），应由蒸发风机模块发布（待固件实现，Q18），不是用户油门；Modbus 的 hr221 把油门值写进该槽位属于混用，HMI 不沿用 |

  同步字段包含在同一次原子写入中。
- **禁止写入**：`FLOW_ENABLE` 固定为 0。
- **动作**：`FAULT_RESET` 为脉冲动作，只能用 `A` 执行。
- **生效时机**：写入发生在 `hmi_rx` 协程内，没有 `await`，控制协程不会看到写了一半的状态。控制环在下一个周期读取新值。PID 参数变化时不重置积分项；实机若发现参数修改引起输出跳变，再在控制环中处理，见第 11 节 Q8。各参数实际生效的时机不同（下一周期，或需重启），见附录 C.2。

### 6.4 持久化

- 参数修改后，调用项目现有的 `params_store`：配置文件、校验和、原子写入、30 秒防抖。
- 用户范围与参数一起持久化，位于同一配置文件的独立段中。
- 需要持久化的点位见附录 A 的“保持”列。
- **版本不匹配**：配置文件中的清单版本或哈希与固件清单不同时，丢弃范围段（参数段按 `params_store` 现有规则处理），并记录日志。

## 7. 协议（版本 4）

### 7.1 通用规则

- 每条消息是一行 ASCII 文本，以 LF 结束。接收端忽略 CR。
- 字段之间用一个空格分隔。
- 数值用工程单位。开关量用 `0`/`1`。
- 枚举值用整数序号传输。`aggr_mode` 取值为 `1`（关闭）、`2`（比例）、`3`（目标时间）。
- 全双工。**命令**（`H`、`G`、`Q`、`W`、`A`、`R`）：PC 每次只发一条，等到应答后再发下一条。**心跳 `P`** 不是命令，不占用等待槽，可在任何时候发送。
- **停止例外**：`W SYSTEM_ENABLE 0` 不受“等待应答”限制，PC 在任何时候都可以立即发送。固件在 `hmi_rx` 每轮处理中，先处理校验通过的停机命令，再处理其余命令。
- **多行应答**：`R *` 的应答是多行，以 `R END <n>` 结束，收到 `END` 之前该命令算未完成。其余命令的应答都是一行。
- 由 MCU 主动发送的 `S` 可以出现在任何时刻，包括 PC 等待命令应答期间。PC 靠首字符区分：`S` 是快照，`OK`/`E`/`R`/`Q`/`H` 是应答。

### 7.2 PC → MCU

| 命令 | 含义 | 示例 |
|---|---|---|
| `H` | 握手，查询版本和点数 | `H` |
| `P` | 心跳，每 0.5 秒一次，无应答 | `P` |
| `G` | 请求一次动态快照 | `G` |
| `Q` | 请求一次参数快照 | `Q` |
| `W <符号> <值>*XX` | 写参数或开关 | `W SH_SETPOINT 8.0*XX` |
| `W <符号1> <值1> <符号2> <值2> …*XX` | 批量写，最多 5 对，原子 | `W HP_WARN 19.5 HP_UNLOAD 21.0*XX` |
| `A <符号>*XX` | 执行动作 | `A FAULT_RESET*XX` |
| `R <符号>` | 查询用户范围 | `R SH_SETPOINT` |
| `R *` | 列出全部已设定的用户范围，PC 重连时使用 | `R *` |
| `R <符号> <最小> <最大>*XX` | 设定用户范围 | `R SH_SETPOINT 3.0 10.0*XX` |
| `R <符号> D*XX` | 清除用户范围 | `R SH_SETPOINT D*XX` |

**校验**：
- 所有有副作用的命令（`W`、`A`、`R` 设定、`R` 清除）行尾必须带 `*XX`。`XX` 是行内 `*` 之前全部字节（含空格，不含 CR、LF）的逐字节异或，用两位大写十六进制表示。
- `H`、`P`、`G`、`Q`、`R <符号>`（查询）和 `R *` 无副作用，不带校验。
- 缺少校验或校验不符，固件不执行，回复 `E CRC`，也不刷新心跳时间。

### 7.3 MCU → PC

| 回复 | 含义 | 示例 |
|---|---|---|
| `H <版本> <动态点数> <参数点数> <哈希>` | 握手应答 | `H 4 75 166 14B2` |
| `S <序号> <值1> … <值75>` | 动态快照，按动态清单顺序 | `S 17 12.34 0 …` |
| `Q <值1> … <值166>` | 参数快照，按参数清单顺序 | `Q 0.60 0.12 …` |
| `R <符号> <最小> <最大>` | 范围应答，未设定时为 `-` | `R SH_SETPOINT - -` |
| `R END <n>` | `R *` 列表结束，`n` 为前面范围行数 | `R END 12` |
| `OK` | 命令成功 | `OK` |
| `E <错误码> [符号]` | 命令失败，批量写入时可带出错符号 | `E RANGE HP_UNLOAD` |

- `S` 每秒一次，由 `hmi_tx` 发送。`G` 的应答也是一行 `S`。`Q` 的应答是一行 `Q`。
- `S` 序号递增，PC 用于判断丢帧。MCU 重启后序号回到 0，PC 据此判断重启并重新握手。
- 整数点位输出整数；浮点点位输出 2 位小数。数值文本长度受 6.2 的表示上限约束。
- **`R *`**：应答是若干行 `R <符号> <最小> <最大>`，只包含已设定范围的点位（未出现的视为未设定），最后一行 `R END <n>`，PC 以 `END` 为完成标志并核对 `n`。全部点位都设了范围时约 4 KB，墙钟约 0.4 秒，期间不生成新的 `S`，PC 可能少收到一帧，属正常。列表期间 PC 仍可能收到插队停机的 `OK`/`E`，PC 按首字符和内容区分。
- **哨兵 `999`**：只可能出现在传感器模拟量（A.9 的温度、压力、过热度、焓值、电流、过冷度）和 ESC 遥测中，表示传感器故障或不使用。状态、计数、位图、开关类点位不会出现哨兵，其数值按原意解读。PC 端只对上述两组点位把 `999` 显示为“无效”。
- `P` 不回复，固件只记录收到时间。

### 7.4 错误码

| 码 | 含义 |
|---|---|
| `FORMAT` | 格式错误，或数值无法解析，或超出数值表示上限（见 6.2），或整数点位收到小数，或行超长，或批量超过 5 对或符号重复 |
| `CRC` | 缺少校验或校验不符 |
| `NAME` | 符号不存在 |
| `READONLY` | 只读或禁止写入的点位 |
| `RANGE` | 超出用户范围，或违反关系约束，或范围设定的最小值大于最大值 |
| `PRECISION` | 数值超过点位的分辨率 |
| `ACTION` | 未知动作 |
| `BUSY` | 数据槽被占用，`Q` 或 `R *` 未执行，可稍后重发（`W`、`A`、`R` 设定不会返回此码） |
| `INTERNAL` | 内部错误，记录日志；原子写入已回滚 |

### 7.5 超时与重试

- PC 对每条命令设置超时（建议 2 秒；`R *` 为 5 秒）。超时后界面显示“结果未知”，并发送 `Q`（参数）、`R *`（范围）或 `G`（状态）核对。
- 不自动重发 `W`、`A`、`R`。
- **停机例外**：`W SYSTEM_ENABLE 0` 幂等且安全。发出后 PC 不依赖应答判断结果，而是请求 `G`，以快照中 `SYSTEM_ENABLE=0` 为成功。1 秒内没有看到则重发，最多 3 次，仍失败则界面报警。
- 插队发出停机命令时，之前挂起命令的应答可能晚到。PC 把这些命令标为“结果未知”，之后用 `Q`/`G` 核对，不按应答顺序硬匹配。
- 收到 `OK` 前，界面不显示写入已生效。`W` 收到 `OK` 后，PC 立即发 `Q`，用返回的参数快照更新界面，这样副作用字段也一并刷新。

### 7.6 数据规模

- 动态快照约 400 字节，参数快照约 800 字节。
- PC 端接收缓冲不小于 8192 字节，处理分段到达和粘连。

## 8. 安全与运行

- **链路超时**：只有 `ST_SYSTEM_ENABLE` 为 1 时才判断。系统运行期间，若超过 3 秒没有收到合法命令行（含 `P`），`hmi_watchdog` 把 `ST_SYSTEM_ENABLE` 置为 0，由现有 supervisor 执行正常停机，与停机命令的路径一致。不设置故障位，不锁存。系统处于停机状态时不判断超时。
- **超时时清除手动模式**：触发超时停机时，同时把 `MANUAL_MODE_ENABLE` 置为 0（只清开关，不改手动油门和开度的数值）。这样重连后不会遇到手动输出仍然有效。手动模式下 `SYSTEM_ENABLE=0` 的实际行为已核对（Q9 关闭）：supervisor 在 STARTUP、RUNNING、ALARM 任一状态见到 `SYSTEM_ENABLE=0` 都进入 STOPPING 正常停机（`Alarm/supervisor.py:719,735,755`）；压缩机、冷凝风机、EXV 的手动覆盖要求 `MANUAL_MODE_ENABLE=1` 且处于 `MODE_RUNNING`（`Compressor/control.py:151-153`、`Condenser/control.py:140-142`、`EXV/Valve_CRTL.py:125-127`），进入 STOPPING 后不再套用，所以停机序列不会被手动值干扰。**蒸发风机例外**：RUNNING 且使能时始终读取 `EVAP_FAN_USER_THROTTLE`，不受 `MANUAL_MODE_ENABLE` 控制（`Evaporator/control.py:58-61`）；它同样只在 RUNNING 内生效，停机时由联锁油门接管。超时时清除 `MANUAL_MODE_ENABLE` 仍然保留，目的是防止下次启动进入 RUNNING 后立刻套用旧的手动值。
- **合法命令行**：能被识别、格式正确、需要校验的命令校验通过。噪声字符、校验错误的行、超长行不刷新心跳时间。这比“任何字符”更严格，是修订 10 对原决定的收紧。
- **心跳频率**：PC 每 0.5 秒发一次 `P`，3 秒内有 6 次机会，单次延迟不会触发停机。
- **心跳反映界面存活**：PC 界面主线程每 0.5 秒更新一个“存活”时间戳；串口线程只在该时间戳不超过 1.5 秒时才发 `P`。串口线程不自行周期性发送其他命令，所以界面卡死时固件会收不到合法命令行而停机。
- **独立计时**：使用 `last_hmi_rx_ms`，不复用其他通信的时间戳。只在收到合法命令行时更新。
- **重连**：不自动恢复运行。操作者重新下发 `W SYSTEM_ENABLE 1` 才能启动。
- **停止优先**：停止按钮在所有已打开串口的状态下都可用，发送时不排队、不等待其他命令的应答（见 7.1、7.5）。
- **MCU 重启**：`SYSTEM_ENABLE` 默认为 0。PC 看到序号回零，重新握手并读取参数快照，不自动下发启动。
- **手动和执行器项**：手动模式、手动油门、EXV 手动开度等直接驱动执行器。界面允许直接写入，不需要二次确认。固件保留现有的模式和联锁检查。
- **启动**：界面允许直接下发 `SYSTEM_ENABLE=1`，不需要二次确认。
- **无设备硬限**：设备保护依赖用户范围和现有保护逻辑。界面在保存超出常规范围的值时提示，但不阻止保存。
- **流量预留位**：`FLOW_ENABLE` 显示为禁用，并说明原因。

## 9. 电脑端 HMI

- 框架：Python，PySide6，PySerial。串口收发放在后台线程，界面线程不做阻塞 I/O。
- 开发环境：复用 `pc_hmi/` 下现有的 Python 3 虚拟环境；PySide6 和 pyserial 已安装，不要重复创建环境或安装依赖。
- 单实例：软件只打开一个串口，不需要会话或权限管理。
- 页面：
  1. **运行总览**：温度、压力、过热度、过冷度、焓值、电流、EXV 开度、压缩机和风机转速。
  2. **全部状态**：附录 A 中的全部只读点位。
  3. **报警与联锁**：由 `ERROR_REG` 位图拆出的故障位（1=正常，0=故障）和联锁状态。
  4. **参数**：读写点位，显示当前值与用户范围，提交前检查格式与精度。提交后等待 `OK`，再请求 `Q` 刷新。范围在同一页面设定，通过 `R` 命令发送。当前值在范围之外时标红。
  5. **运行控制**：开关和动作按钮。连接时不自动下发启用。复位为单次动作。**常驻停止按钮**：所有页面可见，位置和颜色固定醒目。
- 手动油门、EXV 开度等滑块控件：松手后才发送，不在拖动过程中发送。
- 连接后发送 `H`，校验版本、点数和哈希，一致后依次发 `Q` 和 `R *` 取得参数和范围。每 0.5 秒发送 `P`（见第 8 节的存活规则）。超过 3 秒未收到 `S`，显示“数据过期”，不把旧值当实时值。

### 9.1 连接状态

| 状态 | 进入条件 | 数据显示 | 可用控件 |
|---|---|---|---|
| 未连接 | 串口未打开、打开失败、串口断开（任何状态均可进入） | 无 | 仅“连接” |
| 握手中 | 串口已打开，等待 `H` 应答（超时 2 秒，重试 3 次，失败回到未连接） | 无 | 仅“断开” |
| 已连接 | 版本、点数、哈希一致，已收到 `Q`、`R END` 和 `S` | 实时 | 全部 |
| 数据过期 | 已连接，超过 3 秒未收到 `S` | 最后的值，灰化并标“数据过期” | 停止按钮、重新握手；写类控件禁用 |
| 不兼容 | 版本、点数或哈希不一致 | 不解析 `S`/`Q`，不显示数值（顺序可能错位），提示更新清单 | 仅停止按钮和重新握手 |
| MCU 重启 | `S` 序号回零 | 清空，重新握手 | 同“握手中”；不自动下发启动 |

- 数据过期时收到 `S` 自动回到已连接。
- 停止按钮使用符号名 `SYSTEM_ENABLE`，不依赖清单顺序，所以不兼容状态下仍然可用。
- 这是对原“版本不一致时只读”的收紧：不一致时连数值都不显示，避免按错位的清单把数值当成别的量。

### 9.2 与组态屏操作的对应

组态屏是 Modbus RTU 主站（点表见 `docs/MODBUS_ADDRESS_MAP.md`）。屏上能做的每一项操作，HMI 都有对应的符号和命令。

| 组态屏（Modbus 区） | HMI | 说明 |
|---|---|---|
| 线圈 0x1 系统使能 | `W SYSTEM_ENABLE 0/1` | 常驻停止按钮即写 0，可插队 |
| 线圈 0x2 手动模式 | `W MANUAL_MODE_ENABLE 0/1` | 超时停机时固件清零 |
| 线圈 0x3 / 0x4 / 0x5 转速环使能 | `W COMP_SPEED_LOOP_ENABLE`、`W COND_FAN_SPEED_LOOP_ENABLE`、`W EVAP_FAN_SPEED_LOOP_ENABLE` | |
| 线圈 0x6 流量检查使能 | 只读，固定 0 | 当前样机无实体流量开关 |
| 线圈 0x7 故障复位 | `A FAULT_RESET*XX` | 脉冲动作 |
| 线圈 0x8 晋级模式 | `W STARTUP_ADVANCE_MODE 0/1` | |
| 离散量 1x1–1x16 | `ERROR_REG` 位拆分 | 1=正常，0=故障；位 0–15 均逐位显示 |
| 离散量 1x100–1x105 | `DRIVER_LINK_OK`、联锁中的 4 个使能位、`RETRY_LOCKOUT` | |
| 输入寄存器 3x1–3x17 | 动态快照 B.1 | 3x10 进口焓 = `SUCTION_ENTHALPY`，3x11 出口焓 = `LIQUID_ENTHALPY`，3x13 `EXV_TARGET` = `EXV_TARGET_PCT`；3x15/3x16 两位打包的质量码改为 `SENSOR_QUALITY_0…9`，每通道一个值 |
| 3x100–3x102、3x110–3x111 | `SYS_MODE`、`STARTUP_STAGE`、`RUNNING_SUBSTATE`、`ERROR_REG` | `ERROR_REG` 是一个 32 位值，不拆低高 16 位 |
| 3x200–3x205、3x207–3x223 | B.3 转速与油门点、B.4 ESC 遥测、`LIMIT_ACTIONS` | Modbus 的 `*_THROTTLE_CMD`（3x200/202/204）取自实际下发油门，对应 HMI 的 `*_THROTTLE_OUT` |
| 保持寄存器 4x1–4x223 | 同名参数，改名见下表 | |
| 4x205 Modbus 无帧超时 | 不纳入 HMI | HMI 有自己的超时（第 8 节） |
| 4x220–4x223 手动油门与 EXV 开度 | `MANUAL_COMP_THROTTLE`、`MANUAL_COND_FAN_THROTTLE`、`EVAP_FAN_USER_THROTTLE`、`MANUAL_EXV_PCT` | |

**改名对照**（左为 Modbus 点表符号，右为 HMI 符号，后缀 KP/KI/KD 同理）：

| Modbus | HMI |
|---|---|
| `SH_PID_*`、`CAP_PID_*`、`COND_PID_*` | `SH_*`、`CAP_*`、`COND_*` |
| `CAP_LOOP_PERIOD_S` | `CAP_PERIOD_S` |
| `COMP_RPM_MAX`、`COMP_SPEED_LOOP_KP/KI/DEADBAND`、`COMP_THROTTLE_MAX_ADJUST` | `CSL_RPM_MAX`、`CSL_KP/KI/DEADBAND`、`CSL_THROTTLE_MAX_ADJUST` |
| `FAN_RPM_MAX`、`FAN_SPEED_LOOP_KP/KI/DEADBAND`、`FAN_THROTTLE_MAX_ADJUST` | `FSL_RPM_MAX`、`FSL_KP/KI/DEADBAND`、`FSL_THROTTLE_MAX_ADJUST` |

**HMI 比组态屏多出的 49 个参数**：`SH_TAU`、`SH_T_GOAL`、`SH_AGGR_MODE`、`CAP_ERROR_THRESHOLD`、`CAP_TAU`、`CAP_T_GOAL`、`CAP_AGGR_MODE`、`COND_ERROR_THRESHOLD`、`COND_DEADBAND`、`COND_TAU`、`COND_T_GOAL`、`COND_PERIOD_S`、`COND_AGGR_MODE`、`CSL_KD`、`CSL_PERIOD_S`、`FSL_KD`、`FSL_PERIOD_S`、`AGE_LIMIT_MS`、`LINK_CHECK_ENABLE`、`CURRENT_BAD_ACTION`、`HP_DELAY_S`、`DISCHARGE_PSI_BAD_DELAY_S`、`LP_DELAY_S`、`SUCTION_PSI_BAD_DELAY_S`、`LSH_DELAY_S`、`OC_DELAY_S`、`CURRENT_BAD_DELAY_S`、`LINK_LOSS_DELAY_S`、`SENSOR_STALE_DELAY_S`、`EXV_FAULT_DELAY_S`、`COMPRESSOR_FAULT_DELAY_SLOT`、`EVAP_FAN_FAULT_DELAY_SLOT`、`COND_FAN_FAULT_DELAY_SLOT`、`FLOW_FAULT_DELAY_SLOT`、`L1_LIMIT_DELAY_SLOT`、`TD_TRIP_DELAY_SLOT`、`COMP_HOLD_S`、`COMP_RPM_STABILITY_SAMPLE_S`、`RPM_VALID_MIN`、`FAN_RPM_TOL`、`FAULT_FAN_HOLD_MS`、`S1_TIMEOUT_MS`、`S2_TIMEOUT_MS`、`S3_TIMEOUT_MS`、`S4_TIMEOUT_MS`、`S5_TIMEOUT_MS`、`THERMO_HYST`、`MODULATING_THRESHOLD`、`EVAP_FAN_THERMO_OFF_KEEP`。

参数页对附录 C.2 标为“重启生效”的点位，写入成功后提示“已保存，重启后生效”。组态屏原有的取值范围表（`_HR_RANGE`）在 HMI 中的去向见附录 E。

## 10. 实施与验收

1. **点位清单**：固件与 PC 端按附录 A 的顺序实现。
2. **固件**：设置 `read_buf_len=1024`；实现 `hmi_rx`、`hmi_tx`（含发送队列、分块）、`hmi_watchdog`；替换 `main.py` 中的 `Display()`。
3. **PC 端**：串口收发、快照解析、命令超时、界面。
4. **协议测试**：分段到达、粘连、CRLF、超长行、未知符号、只读、整数点位收到小数、越界、精度、关系约束、队列满、校验缺失和校验错误、批量写入（成功、中途失败、符号重复、超过 5 对、整体调整关联阈值、最长符号加最长数值的行长）、数值表示上限（刚好等于、刚好超过、nan、inf、过长数字串）。
5. **范围测试**：设定、查询、恢复默认；最小大于最大时拒绝；当前值在新范围外时仍可设定；重启后范围保留；清单版本变化后范围被丢弃；`R *` 列出全部已设定范围，PC 重连后恢复一致，核对 `END` 的计数。
6. **原子性测试**：`HP_TARGET` 等有副作用的写入，中途失败时所有字段回滚。
7. **队列测试**：`G`、`Q`、`W` 连续快速发送，确认没有行交错，`OK` 优先于 `S`；数据槽被 `Q` 占用时发 `G`，确认回 `E BUSY` 且应答不丢；`S` 待发时发 `Q`，确认旧 `S` 被丢弃让位。
8. **阻塞测试**：按第 4.3 节测量。
9. **写入测试**：先测无动作参数并读回；再在安全条件下测试启停、复位和手动项。
10. **异常测试**：拔插 USB、断开串口、超过 3 秒无数据、命令超时、重连、MCU 重启、界面线程卡死（人为阻塞界面主线程）。确认正常停机，且不自动重发或恢复。
11. **长时间测试**：连续运行 24 小时，统计 `S` 丢帧率、命令超时次数、控制周期抖动。
12. **停止与手动测试**：在等待应答期间插入停机命令；停机命令的应答丢失，确认 PC 通过 `G` 核对并重发；手动模式下超时，确认 `MANUAL_MODE_ENABLE` 被清除。
13. **清单测试**：故意改乱一端的顺序或符号，确认握手后进入“不兼容”状态；确认哈希随清单变化；只改类型、读写、精度、单位、枚举或位定义，哈希也要变；只改默认值或说明，哈希不变。
14. **内存与抖动测试**：按第 4.3、4.4 节测量快照格式化、值复制、垃圾回收，核对对 EXV PID 和容量环周期的影响。
15. **实机**：核对 `999` 哨兵、报警显示、数值精度和串口稳定性。

验收必须与实际主控联调。涉及压缩机、风机和 EXV 的测试，按项目现有安全流程执行。

## 11. 待确认

| 编号 | 问题 | 状态 |
|---|---|---|
| Q4 | 分块大小 32 字节是否满足抖动要求 | 待实测，由用户在实机使用时反馈，再调整 |
| Q5 | 动态快照 1 秒一次是否满足带宽和阻塞要求 | 待实测，由用户在实机使用时反馈，再调整 |
| Q6 | `aggr_mode` 的表示方式 | 已确认：界面用下拉框，1 关闭（`off`）、2 比例（`proportional`）、3 目标时间（`togoal`），默认 3，协议传 1/2/3 |
| Q7 | PC→MCU 的写类命令加行尾校验 | 已确认：`W`、`A`、`R` 设定/清除加 `*XX`（XOR），见 7.2 |
| Q8 | PID 参数在运行中修改是否需要重置积分项或在周期边界生效 | 待实机观察 |
| Q9 | 手动模式下 `SYSTEM_ENABLE=0` 时，supervisor 是否会停掉手动油门和手动 EXV | 已核对关闭（2026-11-24 复核）：`SYSTEM_ENABLE=0` 在 STARTUP、RUNNING、ALARM 都进入 STOPPING 正常停机（`Alarm/supervisor.py:719,735,755`），SAFE_STOP 下不启动（:697）。压缩机、冷凝风机、EXV 的手动覆盖要求 `MANUAL_MODE_ENABLE=1` 且 RUNNING，停机中不再套用。蒸发风机例外：RUNNING 且使能时始终读 `EVAP_FAN_USER_THROTTLE`，不受手动开关控制。见第 8 节 |
| Q10 | 固件故障事件记录：约 16 条环形缓冲，含运行毫秒、故障位、关键值，用 `L` 命令读取；`S` 增加 `UPTIME_MS` | 待确认，暂不实现 |
| Q11 | 噪声容错：无法识别首字符的行静默丢弃；错误回复限速（每秒最多 2 条） | 待确认，暂不实现 |
| Q12 | `W` 成功后的 `Q` 防抖（PC 端最后一次 `OK` 后 300 ms 再发），以及支持 `Q <符号>` 单点读取 | 待确认，暂不实现 |
| Q13 | 参数方案导入导出（带清单哈希和范围校验）；`S` 记录为带时间戳的 CSV 并显示趋势曲线 | 待确认，PC 端后续功能 |
| Q14 | PC 端虚拟固件模拟器：按协议应答，可注入分段、粘包、丢帧、超时、`E BUSY` | 待确认，建议与 PC 端同时开发 |
| Q15 | 趋势记录是否需要比 1 秒更快的高频子集 | 待实机用过后再决定 |
| Q16 | `W` 后用全量 `Q` 刷新是否过慢，是否改为只返回被改动及其同步字段 | 待实机观察 |
| Q17 | `LIMIT_ACTIONS` 和 `SENSOR_FAULT_REG` 的位定义 | 已补全，见附录 D |
| Q18 | `EVAP_FAN_CMD`、`COND_FAN_CMD` 的数据来源是各自转速环的目标转速 `rpm_cmd`，由 `_throttle_to_rpm_cmd` 算出：`rpm_cmd = int(throttle_cmd / ESC_THROTTLE_MAX × ST_FAN_SPEED_MAX)`，油门为 0 时为 0。`throttle_cmd`：蒸发风机，RUNNING 且使能时取 `EVAP_FAN_USER_THROTTLE`，否则取 `supervisor_interlocks['evap_fan_throttle_cmd']`（`Evaporator/control.py:58-61`）；冷凝风机，取 `supervisor_interlocks['cond_fan_throttle_cmd']`，RUNNING 且 `MANUAL_MODE_ENABLE=1` 时改取 `MANUAL_COND_FAN_THROTTLE`，高压限载 `ACT_COND_FAN_MAX` 时强制为 `ESC_THROTTLE_MAX`（`Condenser/control.py:134-148`）。现状：`rpm_cmd` 是每 0.5 s 循环内的局部变量，没有写入 `state_data`；槽位 `ST_EVAP_FAN_SPEED_CMD`、`ST_COND_FAN_SPEED_CMD` 注释为“转速指令”，只有 Modbus 的 hr221 写过（写入的是用户油门，单位不符）。压缩机同理有 `rpm_cmd`，但 `state.py` 没有对应槽位 | 待确认：固件在两个风机模块的 `rpm_cmd` 算出后各加一行写入对应槽位，HMI 保留这两点，不再沿用 hr221 的混用；若还要显示压缩机目标转速，需新增 `COMP_RPM_CMD`（建议用模块变量 `comp_rpm_cmd`，与 `comp_throttle_out` 同类，动态清单 75 变 76）。写入前这两点显示 0，界面标注“固件未发布” |
| Q19 | 原注明有 18 个 PID 参数在运行中修改需重启；现已增加控制器参数热同步：容量环/冷凝压力环同步 Kp/Ki/Kd、设定值、死区、限幅、aggressive 参数和采样周期；三个转速环同步 Kp/Ki/Kd、死区、限幅和周期 | 已修复。转速环周期按 500ms 控制任务的实际采样间隔运行，最小执行周期为 500ms |
| Q20 | PID 的 `dt` 必须与实际两次采样间隔一致；外环周期修改、传感器质量中断以及转速环周期调整都可能造成误差 | 已修复：容量环/冷凝压力环在同步参数时更新 `dt`；转速环按配置周期调度，并用实际采样间隔更新 `dt`；传感器失效或控制暂停时清除历史状态 |
| Q21 | `CSL_RPM_MAX`、`FSL_RPM_MAX` 各有保存值（参数字典）和运行值（`ST_MOTOR_SPEED_MAX`、`ST_FAN_SPEED_MAX`）两份，映射目标转速时读取运行值 | 已修复：`params_store.apply_params()` 在加载参数后同步两组运行值 |

## 12. 取值范围

- 每个读写点位有一个**用户范围**（最小值、最大值），由操作者通过 `R` 命令设定。
- **初始状态**：未设定范围，只检查类型与精度。
- **设定**：`R <符号> <最小> <最大>`，要求最小值不大于最大值。范围值同样受 6.2 的数值表示上限约束，超限回复 `E FORMAT`。这是文本表示上限，不是设备硬限。
- **恢复默认**：`R <符号> D`，清除用户范围，回到未设定状态。
- **与当前值的关系**：设定范围时不检查、也不修改当前值，即使当前值在新范围之外也回复 `OK`。界面把该点位标红。之后对该点位的 `W` 必须落在新范围内。
- 默认值只表示 `state.py` 中的初始值，不作为限制依据。
- 范围变更不需要二次确认。固件记录日志。
- 固件不保留 Modbus 范围表（`_HR_RANGE`）。UART 路线只使用用户范围。
- 用户范围与参数一起持久化（见 6.4）。

**风险**：没有设备硬限时，设备保护依赖用户设定的范围和现有保护逻辑。操作者可以把阈值设在设备额定值之上，界面需要给出提示。
**可选建议**（不改变当前决定）：在固件里保留一组编译期的绝对上限，只用于保护阈值（`HP_MAX`、`I_MAX`、`TD_TRIP`、`LP_MIN`），不对用户开放。是否采用由用户决定。

## 附录 A：点位清单（来自 `Board/state.py`）

说明：
- 默认值为 `state.py` 中的初始值。
- “保持”：是否持久化。除 A.7 和 `SYSTEM_ENABLE` 外，所有可写参数都持久化。
- “写”：可通过 `W` 修改并可设定用户范围。
- 清单顺序即传输顺序。动态清单顺序为 B.1 至 B.7，参数清单顺序为 A.1 至 A.7。

### 参数清单（`Q`，共 166 个值）

#### A.1 过热度环（`control_params`，10 个）

| 符号 | 默认值 | 类型 |
|---|---|---|
| SH_KP | 0.6 | float |
| SH_KI | 0.12 | float |
| SH_KD | 3.0 | float |
| SH_SETPOINT | 5.0 | float |
| SH_ERROR_THRESHOLD | 2.0 | float |
| SH_DEADBAND | 0.2 | float |
| SH_MAX_DELTA | 8.0 | float |
| SH_TAU | 3.0 | float |
| SH_T_GOAL | 5.0 | float |
| SH_AGGR_MODE | 3 | 选择 1/2/3 |

#### A.2 压缩机容量环（`compressor_control_params`，13 个）

| 符号 | 默认值 | 类型 |
|---|---|---|
| CAP_KP | 50.0 | float |
| CAP_KI | 5.0 | float |
| CAP_KD | 10.0 | float |
| CAP_SETPOINT | 22.0 | float |
| CAP_ERROR_THRESHOLD | 5.0 | float |
| CAP_DEADBAND | 0.5 | float |
| CAP_MAX_DELTA | 200.0 | float |
| CAP_TAU | 3.0 | float |
| CAP_T_GOAL | 10.0 | float |
| CAP_THROTTLE_MIN | 48 | int |
| CAP_THROTTLE_MAX | 2047 | int |
| CAP_PERIOD_S | 10.0 | float |
| CAP_AGGR_MODE | 3 | 选择 1/2/3 |

#### A.3 冷凝压力环（`cond_control_params`，13 个）

| 符号 | 默认值 | 类型 | 说明 |
|---|---|---|---|
| COND_KP | 100.0 | float | |
| COND_KI | 10.0 | float | |
| COND_KD | 20.0 | float | |
| COND_SETPOINT | 12.0 | float | **只读**，镜像 `HP_TARGET` |
| COND_ERROR_THRESHOLD | 5.0 | float | |
| COND_DEADBAND | 0.2 | float | |
| COND_MAX_DELTA | 150.0 | float | |
| COND_TAU | 3.0 | float | |
| COND_T_GOAL | 5.0 | float | |
| COND_THROTTLE_MIN | 200 | int | |
| COND_THROTTLE_MAX | 2047 | int | |
| COND_PERIOD_S | 2.0 | float | 只改执行间隔，PID 的 `dt` 固定 2 s，见 Q20 |
| COND_AGGR_MODE | 3 | 选择 1/2/3 | |

#### A.4 压缩机转速环（`comp_speed_loop_params`，7 个）

| 符号 | 默认值 | 类型 | 说明 |
|---|---|---|---|
| CSL_KP | 0.5 | float | |
| CSL_KI | 0.1 | float | |
| CSL_KD | 0.0 | float | |
| CSL_PERIOD_S | 0.5 | float | 只决定 PID 的 `dt`，执行间隔固定 0.5 s，见 Q20 |
| CSL_RPM_MAX | 6000 | int | 同步 `ST_MOTOR_SPEED_MAX`；开机加载不同步，见 Q21 |
| CSL_DEADBAND | 200 | int | |
| CSL_THROTTLE_MAX_ADJUST | 100 | int | |

#### A.5 风机转速环（`fan_speed_loop_params`，7 个）

| 符号 | 默认值 | 类型 | 说明 |
|---|---|---|---|
| FSL_KP | 0.3 | float | |
| FSL_KI | 0.05 | float | |
| FSL_KD | 0.0 | float | |
| FSL_PERIOD_S | 0.5 | float | 只决定 PID 的 `dt`，执行间隔固定 0.5 s，见 Q20 |
| FSL_RPM_MAX | 3000 | int | 同步 `ST_FAN_SPEED_MAX`；开机加载不同步，见 Q21 |
| FSL_DEADBAND | 200 | int | |
| FSL_THROTTLE_MAX_ADJUST | 100 | int | |

#### A.6 保护与时序（`protect_params`，79 个）

| 符号 | 默认值 | 类型 | 说明 |
|---|---|---|---|
| HP_MAX | 22.0 | float | 高压 L2 停机，bara |
| HP_UNLOAD | 20.5 | float | 高压 L1 降容，bara |
| HP_WARN | 19.0 | float | 高压 L0 告警，bara |
| HP_TARGET | 12.0 | float | 冷凝压力目标，同步 `COND_SETPOINT` |
| LP_MIN | 2.5 | float | 低压 L2 停机，bara |
| LP_UNLOAD | 2.8 | float | 低压 L1 降容，bara |
| LP_WARN | 3.0 | float | 低压 L0 告警，bara |
| LP_HYST | 0.3 | float | 低压恢复迟滞，bara |
| I_MAX | 45.0 | float | 电流 L2 停机，A |
| I_UNLOAD | 42.0 | float | 电流 L1 降容，A |
| I_LIMIT | 40.0 | float | 电流 L1 禁升速，A |
| I_WARN | 38.0 | float | 电流 L0 告警，A |
| AGE_LIMIT_MS | 2000 | int | 传感器超龄，ms |
| LINK_CHECK_ENABLE | 0 | int | 驱动链路检查开关 |
| CURRENT_BAD_ACTION | 0 | int | 电流传感器故障动作 |
| TD_TRIP | 115.0 | float | 排气温度 L2 停机，°C |
| TD_UNLOAD | 105.0 | float | 排气温度 L1 降容，°C |
| TD_WARN | 95.0 | float | 排气温度 L0 告警，°C |
| SH_MIN | 2.0 | float | 最小过热度，L1 关小 EXV，K |
| SH_CRIT | 1.0 | float | 临界过热度，L1 降容，K |
| HP_DELAY_S | 3.0 | float | 高压 L2 延时，s |
| DISCHARGE_PSI_BAD_DELAY_S | 5.0 | float | 排气压力传感器故障延时，s |
| LP_DELAY_S | 10.0 | float | 低压 L2 延时，s |
| SUCTION_PSI_BAD_DELAY_S | 10.0 | float | 吸气压力传感器故障延时，s |
| LSH_DELAY_S | 5.0 | float | 低过热度延时，s |
| OC_DELAY_S | 3.0 | float | 过流延时，s |
| CURRENT_BAD_DELAY_S | 5.0 | float | 电流传感器故障延时，s |
| LINK_LOSS_DELAY_S | 2.0 | float | 驱动链路断延时，s |
| SENSOR_STALE_DELAY_S | 3.0 | float | 传感器超龄延时，s |
| EXV_FAULT_DELAY_S | 3.0 | float | EXV 故障延时，s |
| DELAY_SLOT_0 … DELAY_SLOT_7 | 0, 5, 10, 30, 45, 60, 300, 900 | int | 8 个点位，延时槽，s |
| EXV_TO_COMP_DELAY_SLOT | 1 | int | 槽号 0–7 |
| COMP_STOP_DELAY_SLOT | 1 | int | 槽号 0–7 |
| FAN_STOP_DELAY_SLOT | 1 | int | 槽号 0–7 |
| COMPRESSOR_FAULT_DELAY_SLOT | 1 | int | 槽号 0–7 |
| EVAP_FAN_FAULT_DELAY_SLOT | 1 | int | 槽号 0–7 |
| COND_FAN_FAULT_DELAY_SLOT | 1 | int | 槽号 0–7 |
| FLOW_FAULT_DELAY_SLOT | 2 | int | 槽号 0–7 |
| L1_LIMIT_DELAY_SLOT | 1 | int | 槽号 0–7 |
| TD_TRIP_DELAY_SLOT | 6 | int | 槽号 0–7 |
| EXV_START_POS | 30.0 | float | 启动开度，% |
| COND_FAN_START_THROTTLE | 1000 | int | 启动油门 |
| EVAP_FAN_START_THROTTLE | 800 | int | 启动油门 |
| COMP_START_THROTTLE | 1200 | int | 启动油门 |
| STARTUP_ADVANCE_MODE | 1 | int | 0=定速保持，1=转速稳定性 |
| COMP_HOLD_S | 10.0 | float | 模式 0 定速保持秒数 |
| COMP_START_DELAY_S | 15.0 | float | 模式 1 开机延时秒数 |
| COMP_RPM_STABILITY_WINDOW_S | 10.0 | float | 稳定性窗口，s |
| COMP_RPM_STABILITY_THRESHOLD | 100 | int | 窗口内转速变化阈值，rpm |
| COMP_RPM_STABILITY_SAMPLE_S | 1.0 | float | 稳定性采样间隔，s |
| RPM_VALID_MIN | 100 | int | 转速有效下限，rpm |
| FAN_RPM_TOL | 200 | int | 风机转速容差，rpm |
| FAULT_FAN_HOLD_MS | 15000 | int | 故障停机后风机保持时间，ms |
| S1_TIMEOUT_MS | 10000 | int | 状态 S1 超时，ms（冷凝风机） |
| S2_TIMEOUT_MS | 10000 | int | 状态 S2 超时，ms（蒸发风机） |
| S3_TIMEOUT_MS | 10000 | int | 状态 S3 超时，ms（EXV 到位） |
| S4_TIMEOUT_MS | 20000 | int | 状态 S4 超时，ms（压缩机启动） |
| S5_TIMEOUT_MS | 30000 | int | 状态 S5 超时，ms（晋级判据） |
| COMP_OIL_RETURN_THROTTLE | 200 | int | 回油最低油门 |
| UNLOAD_RATE_THROTTLE_S | 50 | int | 降容速率，油门/s |
| COMP_SPEED_LOOP_ENABLE | 0 | int | 压缩机转速环开关 |
| COND_FAN_SPEED_LOOP_ENABLE | 0 | int | 冷凝风机转速环开关 |
| EVAP_FAN_SPEED_LOOP_ENABLE | 0 | int | 蒸发风机转速环开关 |
| MIN_ON_S | 180.0 | float | 压缩机最短运行时间，s |
| MIN_OFF_S | 300.0 | float | 压缩机最短停机间隔，s |
| MAX_STARTS_PER_HOUR | 6 | int | 每小时最大启动次数 |
| THERMO_HYST | 2.0 | float | 温控回差，K |
| MODULATING_THRESHOLD | 2.0 | float | PULLDOWN→MODULATING 偏差阈值，K |
| EVAP_FAN_THERMO_OFF_KEEP | 1 | int | 温控停机期间蒸发风机保持 |
| RETRY_MAX | 3 | int | 窗口内最大重试次数 |
| RETRY_WINDOW_S | 3600 | int | 重试窗口，s |
| FLOW_ENABLE | 0 | int | **只读**，固定为 0 |

#### A.7 手动与用户油门（5 个，不持久化）

| 符号 | 来源 | 默认值 | 类型 | 说明 |
|---|---|---|---|---|
| MANUAL_MODE_ENABLE | manual_params | 0 | int | |
| MANUAL_COMP_THROTTLE | manual_params | 1000 | int | |
| MANUAL_COND_FAN_THROTTLE | manual_params | 1000 | int | |
| MANUAL_EXV_PCT | manual_params | 30.0 | float | |
| EVAP_FAN_USER_THROTTLE | evap_fan_user_params | 1500 | int | RUNNING 且使能时直接作为蒸发风机油门，不受 `MANUAL_MODE_ENABLE` 控制；不再同步 `EVAP_FAN_CMD`（Q18） |

### 动态清单（`S`，共 75 个值）

#### B.1 传感器与派生量（24 个，只读）

| 符号 | 来源 | 类型 | 说明 |
|---|---|---|---|
| SUCTION_TEMP | `sensor_data[IDX_SUCTION_T]` | float | 吸气温度，°C |
| DISCHARGE_TEMP | `sensor_data[IDX_DISCHARGE_T]` | float | 排气温度，°C |
| INLET_TEMP | `sensor_data[IDX_INLET_T]` | float | 蒸发器入口空气温度，°C |
| OUTLET_TEMP | `sensor_data[IDX_OUTLET_T]` | float | 蒸发器出口空气温度，°C |
| LIQUID_TEMP | `sensor_data[IDX_LIQUID_T]` | float | 液管温度，°C |
| SUCTION_PRESSURE | `sensor_data[IDX_SUCTION_PSI]` | float | 吸气压力，bara |
| DISCHARGE_PRESSURE | `sensor_data[IDX_DISCHARGE_PSI]` | float | 排气压力，bara |
| SUPERHEAT | `sensor_data[IDX_SUPERHEAT]` | float | 过热度，K |
| SUCTION_ENTHALPY | `sensor_data[IDX_E_VALUE]` | float | 吸气焓，kJ/kg |
| CURRENT | `sensor_data[IDX_CURRENT]` | float | 整机电流，A |
| SUBCOOLING | `subcooling` | float | 过冷度，K |
| LIQUID_ENTHALPY | `liquid_enthalpy` | float | 液管焓，kJ/kg |
| SENSOR_QUALITY_0 … SENSOR_QUALITY_9 | `sensor_quality[0..9]` | 枚举 | 10 个点位，0=OK，1=SUSPECT，2=BAD；通道顺序见附录 D |
| SENSOR_FAULT_REG | `sensor_fault_reg` | 位图 | 传感器故障位图，1=正常 |
| SENSOR_STALE_FLAG | `ST_SENSOR_FAULT` | int | 任一传感器命中哨兵 |

#### B.2 运行状态（6 个，只读）

| 符号 | 来源 | 类型 | 说明 |
|---|---|---|---|
| SYS_MODE | `sys_mode` | 枚举 | 系统模式，见附录 D |
| STARTUP_STAGE | `startup_stage` | 枚举 | 启动阶段，见附录 D |
| RUNNING_SUBSTATE | `running_substate` | 枚举 | 运行子状态，见附录 D |
| LIMIT_ACTIONS | `limit_actions` | 位图 | 限载动作位图 |
| RETRY_LOCKOUT | `retry_lockout` | int | 重试锁定 |
| DRIVER_LINK_OK | `driver_link_ok` | int | 驱动链路状态 |

#### B.3 执行器反馈（20 个，只读）

| 符号 | 来源 | 类型 | 说明 |
|---|---|---|---|
| COMP_RPM | `state_data[ST_MOTOR_SPEED]` | int | 压缩机转速，rpm |
| COMP_THROTTLE_OUT | `comp_throttle_out` | int | 压缩机实际油门 |
| EVAP_FAN_RPM | `state_data[ST_EVAP_FAN_RPM]` | int | 蒸发风机转速，rpm |
| EVAP_FAN_CMD | `Evaporator/control.py` 的 `rpm_cmd`，待写入 `state_data[ST_EVAP_FAN_SPEED_CMD]` | int | 蒸发风机转速环目标转速，rpm；固件发布前恒为 0，见 Q18 |
| EVAP_FAN_THROTTLE_OUT | `evap_fan_throttle_out` | int | 蒸发风机实际油门 |
| COND_FAN_RPM | `state_data[ST_COND_FAN_RPM]` | int | 冷凝风机转速，rpm |
| COND_FAN_CMD | `Condenser/control.py` 的 `rpm_cmd`，待写入 `state_data[ST_COND_FAN_SPEED_CMD]` | int | 冷凝风机转速环目标转速，rpm；固件发布前恒为 0，见 Q18 |
| COND_FAN_THROTTLE_OUT | `cond_fan_throttle_out` | int | 冷凝风机实际油门 |
| EXV_POS | `state_data[ST_VALVE_POS]` | float | EXV 当前开度，% |
| EXV_STEPS | `state_data[ST_VALVE_POS_STEPS]` | int | EXV 当前步数 |
| EXV_TARGET_PCT | `exv_target_pct` | float | EXV 目标开度，% |
| EXV_DRIVE_FAULT | `state_data[ST_VALVE_FAULT]` | int | 驱动器故障事件，1=故障 |
| EVAP_FAN_FAULT_FLAG | `state_data[ST_EVAP_FAN_FAULT]` | int | 蒸发风机转速异常，1=异常 |
| COND_FAN_FAULT_FLAG | `state_data[ST_COND_FAN_FAULT]` | int | 冷凝风机转速异常，1=异常 |
| COMPRESSOR_FAULT_FLAG | `state_data[ST_COMPRESSOR_FAULT]` | int | 压缩机故障事件 |
| MOTOR_SPEED_MIN | `state_data[ST_MOTOR_SPEED_MIN]` | int | 压缩机转速下限，rpm |
| MOTOR_SPEED_MAX | `state_data[ST_MOTOR_SPEED_MAX]` | int | 压缩机转速上限的运行值，rpm；写入 `CSL_RPM_MAX` 时同步，开机不同步，见 Q21 |
| FAN_SPEED_MIN | `state_data[ST_FAN_SPEED_MIN]` | int | 风机转速下限，rpm |
| FAN_SPEED_MAX | `state_data[ST_FAN_SPEED_MAX]` | int | 风机转速上限的运行值，rpm；写入 `FSL_RPM_MAX` 时同步，开机不同步，见 Q21 |
| FLOW_SWITCH | `state_data[ST_FLOW_SWITCH]` | int | 流量开关（软件占位） |

#### B.4 变频器遥测（16 个，只读）

| 符号 | 类型 | 说明 |
|---|---|---|
| ESC0_RPM / ESC0_TEMP / ESC0_VOLTAGE / ESC0_CURRENT | int / int / float / float | 压缩机 ESC：转速 rpm、温度 °C、母线电压 V（源值 ÷10）、相电流 A（源值 ÷10） |
| ESC1_RPM / ESC1_TEMP / ESC1_VOLTAGE / ESC1_CURRENT | int / int / float / 固定 999 | 蒸发风机 ESC，电流不使用 |
| ESC2_RPM / ESC2_TEMP / ESC2_VOLTAGE / ESC2_CURRENT | int / int / float / 固定 999 | 冷凝风机 ESC，电流不使用 |
| ESC3_RPM / ESC3_TEMP / ESC3_VOLTAGE / ESC3_CURRENT | int / int / float / float | 备用 ESC |

来源均为 `state_data[ST_ESCn_*]`。

#### B.5 报警位图（1 个，只读）

| 符号 | 来源 | 类型 | 说明 |
|---|---|---|---|
| ERROR_REG | `error_reg` | 位图 | 1=正常，0=故障。PC 端按下表拆位 |

| 位 | 名称 | 位 | 名称 |
|---|---|---|---|
| 0 | EXV_FAULT | 8 | （不纳入界面） |
| 1 | COMPRESSOR_FAULT | 9 | LOW_SUPERHEAT_FAULT |
| 2 | EVAP_FAN_FAULT | 10 | CONFIG_IO_FAULT |
| 3 | COND_FAN_FAULT | 11 | SUCTION_PSI_FAULT |
| 4 | HIGH_PRESSURE_FAULT | 12 | DISCHARGE_PSI_FAULT |
| 5 | LOW_PRESSURE_FAULT | 13 | CURRENT_SENSOR_FAULT |
| 6 | HIGH_DISCHARGE_TEMP | 14 | SENSOR_STALE_FAULT |
| 7 | OVERCURRENT_FAULT | 15 | FLOW_FAULT（预留） |

位 0–15 均在界面中逐位显示，包括位 8 `MODBUS_COMM_FAULT`；位 16–31 为保留位，不作为报警显示。

#### B.6 联锁（7 个，只读，`supervisor_interlocks`）

COMP_ENABLE、COMP_THROTTLE_CMD、EVAP_FAN_ENABLE、EVAP_FAN_THROTTLE_CMD、COND_FAN_ENABLE、COND_FAN_THROTTLE_CMD、EXV_ENABLE。均为 int，来源为同名小写键。

#### B.7 开关与动作（1 个值 + 1 个动作）

| 符号 | 来源 | 默认值 | 类型 | 写 | 说明 |
|---|---|---|---|---|---|
| SYSTEM_ENABLE | `state_data[ST_SYSTEM_ENABLE]` | 0 | int | 是 | 启停开关，在动态快照中传输，便于看到超时停机的结果 |
| FAULT_RESET | `fault_reset_request` | — | 动作 | 仅 `A` | 脉冲复位，不在快照中 |

### 点数汇总

| 清单 | 分组 | 值的个数 |
|---|---|---|
| 参数 `Q` | A.1 10 + A.2 13 + A.3 13 + A.4 7 + A.5 7 + A.6 79 + A.7 5 + A.8 32 | 166 |
| 动态 `S` | B.1 24 + B.2 6 + B.3 20 + B.4 16 + B.5 1 + B.6 7 + `SYSTEM_ENABLE` 1 | 75 |

## 13. 决策记录与修改指引

### 13.1 已决定

| 项目 | 决定 |
|---|---|
| 使用场景 | 单台 PC、点对点，不做多客户端 |
| 固件默认范围表（Modbus `_HR_RANGE`） | 不用于 UART 路线，只使用用户范围 |
| 超时停机 | 只有 `ST_SYSTEM_ENABLE` 为 1 时判断。3 秒无数据则置 0，正常停机；停机状态不判断 |
| 枚举值传输 | 整数序号，`aggr_mode` 为 1/2/3 |
| 启动 | 允许直接下发，不需要二次确认 |
| 手动项 | 允许直接写入，不需要二次确认 |
| 报警位 `MODBUS_COMM_FAULT`（位 8） | 作为独立报警位逐位显示 |
| 用户范围保存 | 沿用 `params_store`，与参数同一配置文件的独立段中 |
| 范围变更确认 | 不需要二次确认 |
| PC 端代码目录 | 新建 `pc_hmi/`（名称如需修改，由用户决定） |
| PC 开发环境 | `pc_hmi/` 下已有 Python 3 虚拟环境，PySide6 和 pyserial 已安装，后续复用 |
| 接线 | 由用户自行完成 |
| 实机反馈（Q4、Q5） | 由用户在使用时反馈，再调整分块大小和快照频率 |
| 停止优先 | `W SYSTEM_ENABLE 0` 可插队；PC 以快照核对，必要时重发最多 3 次 |
| 超时时手动模式 | 同时清 `MANUAL_MODE_ENABLE`（Q9 已核对，见第 8 节）；`EVAP_FAN_USER_THROTTLE` 不受该开关控制，但停机后不再生效 |
| 心跳判据 | 固件只认合法命令行；PC 由界面线程“喂”标志驱动 `P` |
| 批量写入 | 采用，最多 5 对（修订 11 起），原子，关系约束按整体新值检查 |
| 校验 | `W`、`A`、`R` 设定/清除加 XOR 校验 |
| 接收缓冲 | `read_buf_len=1024`（`pyb.UART`），单行上限 256 字节（修订 11 起） |
| 点位清单来源 | `points.csv` 生成两端代码，握手返回版本和哈希 |
| 快照生成 | 预分配缓冲，一次复制保证一致性，手写数字格式化 |
| 不兼容状态 | 版本或哈希不一致时不显示数值，仅保留停止按钮 |
| 行长与批量 | 行上限 256 字节，批量最多 5 对，`rxbuf=1024`，符号不超过 32 字符 |
| 范围恢复 | `R *` 列出已设定范围，以 `R END <n>` 结束；重连按 `H`、`Q`、`R *` |
| 发送队列 | 应答槽（4 行，专用）与数据槽（2 项）分开；`E BUSY` 只用于 `Q`、`R *` |
| 哈希范围 | 符号、清单、类型、读写、精度、单位、枚举、位定义；其他线上格式变化升协议版本 |
| 数值表示上限 | 浮点绝对值小于 1e8（12 字符），整数 int32（11 字符），位图按无符号 32 位（10 字符）；不是设备硬限 |
| 与 Modbus 的关系 | 完全独立：不共用寄存器表和超时；无 Modbus 主站时不会触发 `MODBUS_COMM_FAULT`（修订 12） |
| 界面目标 | 功能上等价于组态屏，对应关系见 9.2（修订 12） |
| 枚举点位 | `SYS_MODE`、`STARTUP_STAGE`、`RUNNING_SUBSTATE`、`SENSOR_QUALITY_*` 按枚举（类型 `e`）处理，取值集合见附录 D（修订 12） |
| 取值范围的来源 | 固件不设范围表；PC 端提供“应用推荐范围”，数据来自附录 E，由用户点击后才发送（修订 12） |

### 13.2 修改指引

- **增加或修改点位**：改 `points.csv` 和附录 A，用生成脚本更新固件和 PC 端的点位表，清单版本号同时递增（递增后旧配置中的范围段会被丢弃，见 6.4）。哈希随清单元数据自动变化（覆盖范围见 6.1）。默认值、说明、保持、同步目标不改变哈希。
- **修改取值范围**：用户在界面中用 `R` 命令修改，不需要改代码。
- **修改快照频率或分块大小**：只改固件中 `hmi_tx` 的两个常量，并在代码注释中写明原因和实测数据。
- **修改超时时间**：只改 `hmi_watchdog` 中的常量（默认 3 秒）；PC 心跳间隔（默认 0.5 秒）保持在超时的 1/6 以内。

### 13.3 代码注释规则

- 每个通信协程、命令分派和点位读写函数，在代码中注明对应的设计章节（如“见 PC_HMI_UART_DESIGN.md 第 7.2 节”）。
- 涉及阻塞、分块或定时的常量，注释写明实测值和修改原因。
- 涉及有副作用的写入（如 `HP_TARGET` 同步），注释写明同步的字段。


## 附录 C：符号到固件 state 的映射与生效时机

本附录由固件源码核对得出（`Board/state.py`、`Display_Touch/modbus_slave.py`、`Board/params_store.py`、各控制模块）。

### C.1 映射规则

| 符号前缀或类别 | 写入的字典（`Board/state.py`） | 键名规则 |
|---|---|---|
| `SH_`（过热度环，A.1） | `control_params` | `KP`→`Kp`，`KI`→`Ki`，`KD`→`Kd`，`SETPOINT`→`setpoint`，`ERROR_THRESHOLD`→`error_threshold`，`DEADBAND`→`deadband`，`MAX_DELTA`→`max_delta`，`TAU`→`tau`，`T_GOAL`→`T_goal`，`AGGR_MODE`→`aggr_mode` |
| `CAP_`（A.2） | `compressor_control_params` | 同上，另有 `THROTTLE_MIN`→`throttle_min`，`THROTTLE_MAX`→`throttle_max`，`PERIOD_S`→`period_s` |
| `COND_`（A.3，`COND_SETPOINT` 只读） | `cond_control_params` | 同 `CAP_` |
| `CSL_`（A.4） | `comp_speed_loop_params` | `KP/KI/KD`，`PERIOD_S`→`period_s`，`RPM_MAX`→`rpm_max`，`DEADBAND`→`deadband`，`THROTTLE_MAX_ADJUST`→`throttle_max_adjust` |
| `FSL_`（A.5） | `fan_speed_loop_params` | 同 `CSL_` |
| A.6 其余符号（含 `SH_MIN`、`SH_CRIT`、`COND_FAN_*`） | `protect_params` | 键名与符号相同 |
| `DELAY_SLOT_0…7` | `protect_params['delay_slots'][i]` | 下标 i |
| A.7 手动项 | `manual_params`（`EVAP_FAN_USER_THROTTLE` 在 `evap_fan_user_params`） | 键名与符号相同，不持久化 |
| `SYSTEM_ENABLE` | `state_data[ST_SYSTEM_ENABLE]` | 不持久化 |
| `FAULT_RESET` | `fault_reset_request = True`，由 supervisor 处理后清零 | 动作 |

先判断是否是 `protect_params` 的键，再按前缀处理；否则 `SH_MIN` 会被误当成过热度环的参数。

**`*_AGGR_MODE` 的取值转换**：协议传 1、2、3，`state` 中保存字符串，`HAL/PID_Plus.py` 也只认字符串。固件在读写边界转换：1↔`"off"`，2↔`"proportional"`，3↔`"togoal"`。配置文件中保存的仍是字符串。

### C.2 生效时机

| 参数 | 生效时机 | 依据 |
|---|---|---|
| `SH_*`（10 个，含 `TAU`、`T_GOAL`、`AGGR_MODE`、`ERROR_THRESHOLD`） | 下一个 1 s PID 周期 | `EXV/Valve_CRTL.py` 的 `_sync_controller_params` 每周期比较并同步，不重置积分 |
| `CAP_`、`COND_` 的 `KP/KI/KD`、`SETPOINT`、`DEADBAND`、`MAX_DELTA`、`ERROR_THRESHOLD` | 下一个环周期 | `_sync_cap_params`、`_sync_pres_params` |
| `CAP_`、`COND_` 的 `THROTTLE_MIN/MAX`、`PERIOD_S` | 下一个环周期（每次直接读字典） | `Compressor/control.py`、`Condenser/control.py`；`PERIOD_S` 的限制见 Q20 |
| **`CAP_`、`COND_` 的 `TAU`、`T_GOAL`、`AGGR_MODE`（6 个）** | **重启后** | 只在控制器构造时读取，没有同步函数 |
| **`CSL_`、`FSL_` 的 `KP/KI/KD`、`PERIOD_S`、`DEADBAND`、`THROTTLE_MAX_ADJUST`（12 个）** | **重启后** | 转速环控制器在模块加载时构造，没有同步函数。`PERIOD_S` 只决定构造时的 PID `dt`，转速环的执行间隔固定为 0.5 s（`asyncio.sleep_ms(500)`），见 Q20 |
| `CSL_RPM_MAX`、`FSL_RPM_MAX` | 写入命令发生时立即生效；**重启后回到默认值** | 写入同步 `ST_MOTOR_SPEED_MAX`、`ST_FAN_SPEED_MAX`，转速环每次读取；开机加载不做这一步（Q21） |
| `protect_params` 的其余参数 | 下一次判断 | 抽查 `Alarm/rules.py`、`Alarm/supervisor.py`，均在判断时读字典 |
| `MANUAL_COMP_THROTTLE`、`MANUAL_COND_FAN_THROTTLE`、`MANUAL_EXV_PCT` | 下一个设备周期；要求 `MANUAL_MODE_ENABLE=1` 且 RUNNING | 见第 8 节 |
| `EVAP_FAN_USER_THROTTLE` | 下一个 0.5 s 周期；RUNNING 且使能时直接生效，不受 `MANUAL_MODE_ENABLE` 控制 | `Evaporator/control.py:58-61` |

“重启后”共 18 个：`CAP_`、`COND_` 各 3 个，`CSL_`、`FSL_` 各 6 个。HMI 对这些点位在写入成功后提示“已保存，重启后生效”。
`points.csv` 增加一列“生效”：`H`（热）、`B`（重启）。该列不影响线上解析，不进入清单哈希。固件补上热同步后，把对应点位改为 `H` 即可。

## 附录 D：枚举与位图定义（关闭 Q17）

位图点位的极性不一致，界面必须按各自规则处理：`ERROR_REG` 和 `SENSOR_FAULT_REG` 是 1=正常、0=故障；`LIMIT_ACTIONS` 是 1=动作生效。

**ERROR_REG**（32 位，全部正常为 4294967295；位 16–31 保留，恒为 1）：

`0:EXV_FAULT;1:COMPRESSOR_FAULT;2:EVAP_FAN_FAULT;3:COND_FAN_FAULT;4:HIGH_PRESSURE_FAULT;5:LOW_PRESSURE_FAULT;6:HIGH_DISCHARGE_TEMP;7:OVERCURRENT_FAULT;8:MODBUS_COMM_FAULT;9:LOW_SUPERHEAT_FAULT;10:CONFIG_IO_FAULT;11:SUCTION_PSI_FAULT;12:DISCHARGE_PSI_FAULT;13:CURRENT_SENSOR_FAULT;14:SENSOR_STALE_FAULT;15:FLOW_FAULT`

位 8 在清单哈希中保留原名并作为独立报警位显示；位 16–31 为保留位，不作为报警显示。

**SENSOR_FAULT_REG**（16 位，1=正常，全部正常为 65535；位 8–15 保留）：

`0:SUCTION_T_FAULT;1:DISCHARGE_T_FAULT;2:INLET_T_FAULT;3:OUTLET_T_FAULT;4:LIQUID_T_FAULT;5:SUCTION_PSI_FAULT;6:DISCHARGE_PSI_FAULT;7:CURRENT_FAULT`

**LIMIT_ACTIONS**（1=生效，来自 `state.ACT_*`）：

`0:INHIBIT_UP;1:UNLOAD;2:COND_FAN_MAX;3:EXV_REDUCE;4:NORMAL_STOP;5:SAFE_STOP`

| 位 | 含义 |
|---|---|
| 0 | 禁升速：容量环只许降不许升 |
| 1 | 降容：强制下降到回油油门 |
| 2 | 冷凝风机强制全速 |
| 3 | 关小 EXV |
| 4 | 正常停机（不锁存） |
| 5 | 故障停机 |

**枚举**（取值集合写入清单哈希）：

| 点位 | 取值集合 | 含义 |
|---|---|---|
| `SYS_MODE` | `0|1|2|3|4|5|6|7` | 0 BOOT，1 SELF_TEST，2 SAFE_STOP，3 STARTUP，4 RUNNING，5 ALARM，6 STOPPING，7 DEGRADED |
| `STARTUP_STAGE` | `0|1|2|3|4|5|6` | 0 未在启动（S0 锁存启动请求只占一个调度周期，该周期内即置为 1），1 S1 冷凝风机，2 S2 蒸发风机，3 S3 EXV 到位，4 S4 压缩机软启（EXV 动作后延时），5 S5 晋级判据，6 S6 进入运行 |
| `RUNNING_SUBSTATE` | `0|1|2|3` | 0 R1 降温中，1 R2 调节，2 R3 L1 限载，3 R4 达设定压缩机停 |
| `SENSOR_QUALITY_0…9` | `0|1|2` | 0 OK，1 SUSPECT，2 BAD |

`SENSOR_QUALITY_n` 的通道顺序与 `state.IDX_*` 一致：0 吸气温度，1 排气温度，2 蒸发器入口空气温度，3 蒸发器出口空气温度，4 液管温度，5 吸气压力，6 排气压力，7 过热度，8 吸气焓，9 整机电流。

## 附录 E：推荐用户范围

固件不设范围表（第 12 节）。组态屏时期，Modbus 写入代码强制了两类限制，切换到 HMI 后要有地方承接：

1. **取值范围**：Modbus 对每个保持寄存器按 `_HR_RANGE` 检查，越界返回 0x03 拒绝（`Display_Touch/modbus_slave.py` 的 `_write_and_apply`）。HMI 默认不拒绝，由用户用 `R` 命令设定范围。本附录给出由 `_HR_RANGE` 换算的推荐值。PC 端提供“应用推荐范围”按钮，**用户点击后**逐条发送 `R <符号> <最小> <最大>*XX`，不自动发送。
2. **分级阈值的先后关系**（`HP`、`LP`、`I`、`TD` 四组）：Modbus 在 `_prospective_consistency_ok` 中检查。HMI 在 6.3 的关系约束中检查，约束包含这四组，并多出 `HP_TARGET`、`SH_CRIT < SH_MIN`、油门上下限等，所以这一类不靠用户范围承接。

下表数值按各点位倍率由 `_HR_RANGE` 换算为工程单位（倍率已与写入代码逐项核对），默认值取自 `Board/state.py`，每一项默认值都落在范围内。共 78 项有现成范围，分三类；另有 49 项没有范围可沿用（E.3）。`MODBUS_TIMEOUT_S`（4x205）不在 HMI 点位中，未列入。

### E.1 建议应用（64 项）

| 符号 | 默认值 | 推荐最小 | 推荐最大 | Modbus 地址 | 说明 |
|---|---|---|---|---|---|
| SH_SETPOINT | 5 | 1 | 10 | 4x4 |  |
| SH_ERROR_THRESHOLD | 2 | 0.5 | 5 | 4x5 |  |
| SH_DEADBAND | 0.2 | 0 | 2 | 4x6 |  |
| SH_MAX_DELTA | 8 | 1 | 100 | 4x7 |  |
| CAP_SETPOINT | 22 | 5 | 30 | 4x19 |  |
| CAP_DEADBAND | 0.5 | 0 | 5 | 4x20 |  |
| CAP_THROTTLE_MIN | 48 | 48 | 2047 | 4x21 |  |
| CAP_THROTTLE_MAX | 2047 | 48 | 2047 | 4x22 |  |
| CAP_MAX_DELTA | 200 | 10 | 500 | 4x23 |  |
| COND_THROTTLE_MIN | 200 | 48 | 2047 | 4x28 |  |
| COND_THROTTLE_MAX | 2047 | 48 | 2047 | 4x29 |  |
| COND_MAX_DELTA | 150 | 10 | 500 | 4x30 |  |
| HP_MAX | 22 | 10 | 26 | 4x31 | 高压 L2 停机，bara；关系约束见 6.3 |
| HP_UNLOAD | 20.5 | 10 | 26 | 4x32 | 高压 L1 降容，bara；关系约束见 6.3 |
| HP_WARN | 19 | 10 | 26 | 4x33 | 高压 L0 告警，bara；关系约束见 6.3 |
| HP_TARGET | 12 | 8 | 20 | 4x34 | 冷凝压力目标，同步 `COND_SETPOINT`；关系约束见 6.3 |
| LP_MIN | 2.5 | 1 | 5 | 4x50 | 低压 L2 停机，bara；关系约束见 6.3 |
| LP_UNLOAD | 2.8 | 1 | 5 | 4x51 | 低压 L1 降容，bara；关系约束见 6.3 |
| LP_WARN | 3 | 1 | 5 | 4x52 | 低压 L0 告警，bara；关系约束见 6.3 |
| LP_HYST | 0.3 | 0.1 | 1 | 4x53 | 低压恢复迟滞，bara；关系约束见 6.3 |
| I_MAX | 45 | 10 | 50 | 4x54 | 电流 L2 停机，A；关系约束见 6.3 |
| I_UNLOAD | 42 | 10 | 50 | 4x55 | 电流 L1 降容，A；关系约束见 6.3 |
| I_LIMIT | 40 | 10 | 50 | 4x56 | 电流 L1 禁升速，A；关系约束见 6.3 |
| I_WARN | 38 | 10 | 50 | 4x57 | 电流 L0 告警，A；关系约束见 6.3 |
| TD_TRIP | 115 | 80 | 150 | 4x58 | 排气温度 L2 停机，°C；关系约束见 6.3 |
| TD_UNLOAD | 105 | 80 | 150 | 4x59 | 排气温度 L1 降容，°C；关系约束见 6.3 |
| TD_WARN | 95 | 80 | 150 | 4x60 | 排气温度 L0 告警，°C；关系约束见 6.3 |
| SH_MIN | 2 | 0.5 | 5 | 4x61 | 最小过热度，L1 关小 EXV，K |
| SH_CRIT | 1 | 0.5 | 5 | 4x62 | 临界过热度，L1 降容，K |
| DELAY_SLOT_0 | 0 | 0 | 3600 | 4x100 | 延时秒数；槽位序号点位 `*_DELAY_SLOT` 另取 0–7 |
| DELAY_SLOT_1 | 5 | 0 | 3600 | 4x101 | 延时秒数；槽位序号点位 `*_DELAY_SLOT` 另取 0–7 |
| DELAY_SLOT_2 | 10 | 0 | 3600 | 4x102 | 延时秒数；槽位序号点位 `*_DELAY_SLOT` 另取 0–7 |
| DELAY_SLOT_3 | 30 | 0 | 3600 | 4x103 | 延时秒数；槽位序号点位 `*_DELAY_SLOT` 另取 0–7 |
| DELAY_SLOT_4 | 45 | 0 | 3600 | 4x104 | 延时秒数；槽位序号点位 `*_DELAY_SLOT` 另取 0–7 |
| DELAY_SLOT_5 | 60 | 0 | 3600 | 4x105 | 延时秒数；槽位序号点位 `*_DELAY_SLOT` 另取 0–7 |
| DELAY_SLOT_6 | 300 | 0 | 3600 | 4x106 | 延时秒数；槽位序号点位 `*_DELAY_SLOT` 另取 0–7 |
| DELAY_SLOT_7 | 900 | 0 | 3600 | 4x107 | 延时秒数；槽位序号点位 `*_DELAY_SLOT` 另取 0–7 |
| EXV_START_POS | 30 | 10 | 50 | 4x120 | 启动开度，% |
| COMP_START_THROTTLE | 1200 | 48 | 2047 | 4x121 | 启动油门 |
| COND_FAN_START_THROTTLE | 1000 | 48 | 2047 | 4x122 | 启动油门 |
| EVAP_FAN_START_THROTTLE | 800 | 48 | 2047 | 4x123 | 启动油门 |
| EXV_TO_COMP_DELAY_SLOT | 1 | 0 | 7 | 4x130 | 槽号 0–7 |
| COMP_STOP_DELAY_SLOT | 1 | 0 | 7 | 4x131 | 槽号 0–7 |
| FAN_STOP_DELAY_SLOT | 1 | 0 | 7 | 4x132 | 槽号 0–7 |
| COMP_START_DELAY_S | 15 | 5 | 60 | 4x150 | 模式 1 开机延时秒数 |
| COMP_RPM_STABILITY_WINDOW_S | 10 | 5 | 30 | 4x151 | 稳定性窗口，s |
| COMP_RPM_STABILITY_THRESHOLD | 100 | 10 | 500 | 4x152 | 窗口内转速变化阈值，rpm |
| CSL_RPM_MAX | 6000 | 1000 | 6000 | 4x153 | 同步 `ST_MOTOR_SPEED_MAX`；开机加载不同步，见 Q21 |
| CSL_DEADBAND | 200 | 10 | 500 | 4x156 |  |
| CSL_THROTTLE_MAX_ADJUST | 100 | 10 | 500 | 4x157 |  |
| FSL_RPM_MAX | 3000 | 500 | 3000 | 4x180 | 同步 `ST_FAN_SPEED_MAX`；开机加载不同步，见 Q21 |
| FSL_DEADBAND | 200 | 10 | 500 | 4x183 |  |
| FSL_THROTTLE_MAX_ADJUST | 100 | 10 | 500 | 4x184 |  |
| COMP_OIL_RETURN_THROTTLE | 200 | 48 | 500 | 4x185 | 回油最低油门 |
| UNLOAD_RATE_THROTTLE_S | 50 | 10 | 200 | 4x186 | 降容速率，油门/s |
| MIN_ON_S | 180 | 60 | 600 | 4x200 | 压缩机最短运行时间，s |
| MIN_OFF_S | 300 | 60 | 600 | 4x201 | 压缩机最短停机间隔，s |
| MAX_STARTS_PER_HOUR | 6 | 1 | 10 | 4x202 | 每小时最大启动次数 |
| RETRY_MAX | 3 | 1 | 10 | 4x203 | 窗口内最大重试次数 |
| RETRY_WINDOW_S | 3600 | 600 | 7200 | 4x204 | 重试窗口，s |
| MANUAL_COMP_THROTTLE | 1000 | 0 | 2047 | 4x220 |  |
| MANUAL_COND_FAN_THROTTLE | 1000 | 0 | 2047 | 4x221 |  |
| EVAP_FAN_USER_THROTTLE | 1500 | 0 | 2047 | 4x222 | RUNNING 且使能时直接作为蒸发风机油门，不受 `MANUAL_MODE_ENABLE` 控制；不再同步 `EVAP_FAN_CMD`（Q18） |
| MANUAL_EXV_PCT | 30 | 0 | 100 | 4x223 |  |

### E.2 沿用数值但不建议应用（14 项）

PID 增益的原范围是 0–327.67，是为了不拒绝默认值而放宽的，没有保护意义。`CAP_PERIOD_S` 的范围有意义，但改动周期会使 PID 的 `dt` 与实际间隔不符（Q20），Q20 解决前不建议应用。

| 符号 | 默认值 | 推荐最小 | 推荐最大 | Modbus 地址 | 说明 |
|---|---|---|---|---|---|
| SH_KP | 0.6 | 0 | 327.67 | 4x1 |  |
| SH_KI | 0.12 | 0 | 327.67 | 4x2 |  |
| SH_KD | 3 | 0 | 327.67 | 4x3 |  |
| CAP_KP | 50 | 0 | 327.67 | 4x16 |  |
| CAP_KI | 5 | 0 | 327.67 | 4x17 |  |
| CAP_KD | 10 | 0 | 327.67 | 4x18 |  |
| CAP_PERIOD_S | 10 | 5 | 60 | 4x24 | 改动会使 PID 的 `dt` 与实际间隔不符，见 Q20 |
| COND_KP | 100 | 0 | 327.67 | 4x25 |  |
| COND_KI | 10 | 0 | 327.67 | 4x26 |  |
| COND_KD | 20 | 0 | 327.67 | 4x27 |  |
| CSL_KP | 0.5 | 0 | 327.67 | 4x154 |  |
| CSL_KI | 0.1 | 0 | 327.67 | 4x155 |  |
| FSL_KP | 0.3 | 0 | 327.67 | 4x181 |  |
| FSL_KI | 0.05 | 0 | 327.67 | 4x182 |  |

### E.3 没有现成范围的点位（49 项）

这些点位组态屏上没有对应寄存器，因此没有可沿用的范围，本附录不推荐数值，由操作者按需用 `R` 命令设定：

`SH_TAU`、`SH_T_GOAL`、`SH_AGGR_MODE`、`CAP_ERROR_THRESHOLD`、`CAP_TAU`、`CAP_T_GOAL`、`CAP_AGGR_MODE`、`COND_ERROR_THRESHOLD`、`COND_DEADBAND`、`COND_TAU`、`COND_T_GOAL`、`COND_PERIOD_S`、`COND_AGGR_MODE`、`CSL_KD`、`CSL_PERIOD_S`、`FSL_KD`、`FSL_PERIOD_S`、`AGE_LIMIT_MS`、`LINK_CHECK_ENABLE`、`CURRENT_BAD_ACTION`、`HP_DELAY_S`、`DISCHARGE_PSI_BAD_DELAY_S`、`LP_DELAY_S`、`SUCTION_PSI_BAD_DELAY_S`、`LSH_DELAY_S`、`OC_DELAY_S`、`CURRENT_BAD_DELAY_S`、`LINK_LOSS_DELAY_S`、`SENSOR_STALE_DELAY_S`、`EXV_FAULT_DELAY_S`、`COMPRESSOR_FAULT_DELAY_SLOT`、`EVAP_FAN_FAULT_DELAY_SLOT`、`COND_FAN_FAULT_DELAY_SLOT`、`FLOW_FAULT_DELAY_SLOT`、`L1_LIMIT_DELAY_SLOT`、`TD_TRIP_DELAY_SLOT`、`COMP_HOLD_S`、`COMP_RPM_STABILITY_SAMPLE_S`、`RPM_VALID_MIN`、`FAN_RPM_TOL`、`FAULT_FAN_HOLD_MS`、`S1_TIMEOUT_MS`、`S2_TIMEOUT_MS`、`S3_TIMEOUT_MS`、`S4_TIMEOUT_MS`、`S5_TIMEOUT_MS`、`THERMO_HYST`、`MODULATING_THRESHOLD`、`EVAP_FAN_THERMO_OFF_KEEP`

开关和枚举类点位（各 `*_ENABLE`、`*_AGGR_MODE`、`STARTUP_ADVANCE_MODE`、`*_DELAY_SLOT` 等）的取值由 6.3 的取值规则固定，不依赖用户范围。`COND_SETPOINT` 为只读镜像，不设范围。
