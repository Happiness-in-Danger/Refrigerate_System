# Board/io_map.py
"""物理 I/O 通道到软件逻辑信号的映射目录。

映射层的职责：
    底层驱动提供的通道值 × multiplier + correction → 软件逻辑信号

硬件驱动负责芯片初始化、原始码读取和设备协议；本文件只登记物理端点、
底层通道、软件变量以及通道值进入软件变量前的倍率/修正。硬件替换时，
优先新增/替换 hardware profile，并保持 logical_signal/software_target 稳定。

当前文件尚未被运行代码导入。修改本文件不会自动改变硬件行为；现有驱动
和采集代码仍是当前运行事实来源。模块不包含 Modbus 点位、报警位、参数
持久化、状态机、保护阈值或控制算法。
"""

HARDWARE_PROFILE = "STM32H743_current"
INVALID_VALUE = 999

# 字段：(logical_bus, peripheral, physical_pins, direction, bus_settings, note)
BUSES = (
    ("temperature", "SPI1 / AD7124", ("CS=PG15",), "input", {}, "底层 PT100 模块接口；芯片配置由驱动负责"),
    ("suction_pressure", "adc_direct", ("PC2",), "input", {}, "低层压力采集通道"),
    ("discharge_pressure", "adc_direct", ("PC3",), "input", {}, "低层压力采集通道"),
    ("current", "pyb.ADC", ("PA4",), "input", {}, "主电流输入；采样配置由低层采集代码负责"),
    ("current_backup", "pyb.ADC", ("PA5",), "input", {}, "备用输入；当前未采样"),
    ("esc", "UART6", (), "bidirectional", {"baud": 921600}, "H743↔Pico；H743 TX/RX 引脚待板级确认"),
    ("exv_driver", "UART7", ("PE7=TX", "PE8=RX"), "bidirectional", {"baud": 115200}, "TMC2209 PDN_UART 单线半双工；TX/RX 经 1kΩ连接"),
    ("exv_step", "TIM4_CH2", ("PD13",), "output", {}, "TMC2209 STEP"),
    ("exv_direction", "GPIO", ("PE10",), "output", {}, "TMC2209 DIR"),
    ("exv_enable", "GPIO", ("PE12",), "output", {}, "TMC2209 EN；低电平有效"),
    ("hmi", "UART8 / RS485", (), "bidirectional", {"baud": 9600, "bits": 8, "parity": None, "stop": 1}, "物理通信接口；不在此定义 Modbus 点位"),
    ("test_display", "UART1", (), "output", {"baud": 115200}, "本地测试显示接口"),
)

# 输入映射列表按 software_index 排列，运行时可以直接 `INPUT_MAP[index]`，
# 不需要逐项搜索。每项公式：software_value = channel_value*multiplier+correction。
# multiplier/correction 是可变字段，后续 HMI 写入后下一次采样即可使用。
# HMI 地址/权限/范围属于 Modbus 层，不在本 I/O 映射模块定义。
# index 7/8 是软件派生量，不是物理输入，因此对应 None。
INPUT_MAP = [
    {"logical_signal": "suction_temperature", "source": "AD7124_PT100.channels[0]", "software_index": 0, "target": "sensor_data[0]", "unit": "°C", "multiplier": 1.0, "correction": 0.0, "hmi_adjustable": True},
    {"logical_signal": "discharge_temperature", "source": "AD7124_PT100.channels[1]", "software_index": 1, "target": "sensor_data[1]", "unit": "°C", "multiplier": 1.0, "correction": 0.0, "hmi_adjustable": True},
    {"logical_signal": "evaporator_inlet_temperature", "source": "AD7124_PT100.channels[2]", "software_index": 2, "target": "sensor_data[2]", "unit": "°C", "multiplier": 1.0, "correction": 0.0, "hmi_adjustable": True},
    {"logical_signal": "evaporator_outlet_temperature", "source": "AD7124_PT100.channels[3]", "software_index": 3, "target": "sensor_data[3]", "unit": "°C", "multiplier": 1.0, "correction": 0.0, "hmi_adjustable": True},
    {"logical_signal": "liquid_line_temperature", "source": "AD7124_PT100.channels[4]", "software_index": 4, "target": "sensor_data[4]", "unit": "°C", "multiplier": 1.0, "correction": 0.0, "hmi_adjustable": True},
    {"logical_signal": "suction_pressure", "source": "low_level.PC2_pressure_bara", "software_index": 5, "target": "sensor_data[5]", "unit": "bara", "multiplier": 1.0, "correction": 0.0, "hmi_adjustable": True},
    {"logical_signal": "discharge_pressure", "source": "low_level.PC3_pressure_bara", "software_index": 6, "target": "sensor_data[6]", "unit": "bara", "multiplier": 1.0, "correction": 0.0, "hmi_adjustable": True},
    None,
    None,
    {"logical_signal": "system_current", "source": "low_level.PA4_current_A", "software_index": 9, "target": "sensor_data[9]", "unit": "A", "multiplier": 1.0, "correction": 0.0, "hmi_adjustable": True},
]

# 尚未接入或未校准的物理输入，不参与 INPUT_MAP 实时换算。
UNMAPPED_INPUTS = (
    ("system_current_backup", "pyb.ADC(PA5)", "已创建，当前未采样"),
    ("flow_switch", "未接入实体 GPIO", "软件占位；不可作为实体流量输入"),
)

# software_sensor_value 与 sensor_data 均按 0..9 对齐。
# 7/8 是派生值：调用方应基于已校正的软件父通道计算后传给处理器。
SOFTWARE_SENSOR_COUNT = 10
PHYSICAL_INPUT_INDEXES = (0, 1, 2, 3, 4, 5, 6, 9)
DERIVED_INPUTS = (
    {"index": 7, "logical_signal": "superheat", "parents": (0, 5), "source": "derived_values[7]"},
    {"index": 8, "logical_signal": "suction_enthalpy", "parents": (0, 5), "source": "derived_values[8]"},
)

# 转换后工程量的异常判据；量程/变化率单位均为各通道软件单位。
# 这是 Sensor/validate.py 当前判据的映射层记录，运行代码尚未迁移到本模块。
QUALITY_OK = 0
QUALITY_SUSPECT = 1
QUALITY_BAD = 2
SUSPECT_TO_BAD_COUNT = 2
FRESHNESS_MS = 3400
LOW_SUPERHEAT_THRESHOLD = -5.0
SOFTWARE_RANGES = (
    (-40.0, 150.0), (-40.0, 150.0), (-40.0, 150.0), (-40.0, 150.0),
    (-40.0, 150.0), (0.5, 26.0), (0.5, 26.0), (-10.0, 40.0),
    (180.0, 480.0), (0.0, 50.0),
)
RATE_LIMITS = (20.0, 20.0, 20.0, 20.0, 20.0, 12.0, 12.0, 20.0, 200.0, 80.0)
# raw/software index -> sensor_fault_reg bit；位语义 1=正常、0=故障。
SENSOR_FAULT_BIT_BY_INDEX = (0, 1, 2, 3, 4, 5, 6, None, None, 7)

# 输出映射字段：
# (logical_actuator, software_source, physical_interface, device_id,
#  multiplier, correction, unit/range, note)
OUTPUTS = (
    ("compressor", "supervisor_interlocks.comp_enable/comp_throttle_cmd", "UART6→Pico→ESC", 0, 1.0, 0.0, "throttle", "运行范围 48..2047；0=停机"),
    ("evaporator_fan", "device-layer enable/throttle command", "UART6→Pico→ESC", 1, 1.0, 0.0, "throttle", "RUNNING 油门源为用户设定；其他阶段服从 supervisor"),
    ("condenser_fan", "supervisor_interlocks.cond_fan_enable/cond_fan_throttle_cmd", "UART6→Pico→ESC", 2, 1.0, 0.0, "throttle", "运行范围 48..2047；0=停机"),
    ("spare_esc", "no active consumer", "UART6→Pico→ESC", 3, 1.0, 0.0, "throttle", "备用通道"),
    ("exv_position", "state.exv_target_pct / Valve_CRTL delta", "UART7 + STEP/DIR/EN", None, 5.0, 0.0, "step per percent", "500 步全行程；5 step/%；DIR 1=开、0=关"),
)

# 反馈映射字段：
# (logical_feedback, low_level_channel, software_target, unit, multiplier, correction, note)
# 公式统一表示为：software_value = channel_value * multiplier + correction。
FEEDBACKS = (
    ("compressor_rpm", "ESC0.eRPM", "state_data[16] / state_data[0]", "rpm", 1.0 / 3.0, 0.0, "3 极对；取整规则由低层驱动处理"),
    ("compressor_esc_temperature", "ESC0.temperature", "state_data[17]", "°C", 1.0, 0.0, ""),
    ("compressor_esc_voltage", "ESC0.voltage_V", "state_data[18]", "V×10", 10.0, 0.0, ""),
    ("compressor_esc_current", "ESC0.current_A", "state_data[19]", "A×10", 10.0, 0.0, ""),
    ("evaporator_fan_rpm", "ESC1.eRPM", "state_data[20] / state_data[5]", "rpm", 1.0 / 7.0, 0.0, "7 极对；取整规则由低层驱动处理"),
    ("evaporator_fan_esc_temperature", "ESC1.temperature", "state_data[21]", "°C", 1.0, 0.0, ""),
    ("evaporator_fan_esc_voltage", "ESC1.voltage_V", "state_data[22]", "V×10", 21.0, 0.0, "硬件倍率 2.1，再转 V×10"),
    ("evaporator_fan_esc_current", "ESC1.current_A", "state_data[23]", "A×10", None, None, "当前无效，软件写 999"),
    ("condenser_fan_rpm", "ESC2.eRPM", "state_data[24] / state_data[8]", "rpm", 1.0 / 7.0, 0.0, "7 极对；取整规则由低层驱动处理"),
    ("condenser_fan_esc_temperature", "ESC2.temperature", "state_data[25]", "°C", 1.0, 0.0, ""),
    ("condenser_fan_esc_voltage", "ESC2.voltage_V", "state_data[26]", "V×10", 21.0, 0.0, "硬件倍率 2.1，再转 V×10"),
    ("condenser_fan_esc_current", "ESC2.current_A", "state_data[27]", "A×10", None, None, "当前无效，软件写 999"),
    ("spare_esc_rpm", "ESC3.eRPM", "state_data[28]", "rpm", 1.0 / 7.0, 0.0, "7 极对"),
    ("spare_esc_temperature", "ESC3.temperature", "state_data[29]", "°C", 1.0, 0.0, ""),
    ("spare_esc_voltage", "ESC3.voltage_V", "state_data[30]", "V×10", 10.0, 0.0, ""),
    ("spare_esc_current", "ESC3.current_A", "state_data[31]", "A×10", 10.0, 0.0, ""),
    ("exv_position_estimate", "EXV commanded step count", "state_data[1] / state_data[2]", "%/step", 0.2, 0.0, "5 step/%；软件估算，不是位置传感器反馈"),
)

ESC_LINK = {
    "channels": 4,
    "command_frame": {"sync": 0xA5, "length": 5, "direction": "H743_to_Pico"},
    "status_frame": {"sync": 0x5A, "length": 13, "direction": "Pico_to_H743"},
    "invalid_value": INVALID_VALUE,
    "throttle_min": 48,
    "throttle_max": 2047,
    "stop_value": 0,
}

EXV_ENDPOINTS = {
    "driver": "TMC2209",
    "enable_active_level": 0,
    "full_stroke_steps": 500,
    "direction_open": 1,
    "direction_close": 0,
    "diag_connected": False,
}


class SensorIOPipeline:
    """sensor_data → 校验/校正 → software_sensor_value 的映射处理器。

    保留输入 raw 数组不变。Q_BAD/Q_SUSPECT 时软件值维持最后一个有效值，
    质量与时间戳单独输出；控制端必须同时检查 quality，不能只读软件值。
    multiplier/correction 每次处理时按索引读取，因此运行中被 HMI 更新后，
    下一帧立即采用新值。Modbus 写入和参数保存由外层适配器负责。
    """

    def __init__(self):
        self.software_sensor_value = [float(INVALID_VALUE)] * SOFTWARE_SENSOR_COUNT
        self.software_sensor_quality = [QUALITY_BAD] * SOFTWARE_SENSOR_COUNT
        self.software_sensor_checked_ms = [0] * SOFTWARE_SENSOR_COUNT
        self._last_good = [None] * SOFTWARE_SENSOR_COUNT
        self._suspect_count = [0] * SOFTWARE_SENSOR_COUNT
        self.sensor_fault_reg = 0xFFFF

    def _candidate(self, index, channel_value):
        if channel_value == INVALID_VALUE:
            return None
        spec = INPUT_MAP[index]
        if spec is None:
            return channel_value
        # O(1) 索引字段访问；不按 logical_signal 扫描/查询。
        return channel_value * spec["multiplier"] + spec["correction"]

    def _evaluate(self, index, channel_value, now_ms, ticks_diff,
                  parent_quality=QUALITY_OK, count_rate_suspect=True):
        value = self._candidate(index, channel_value)
        quality = QUALITY_OK

        if value is None:
            quality = QUALITY_BAD
        else:
            lo, hi = SOFTWARE_RANGES[index]
            if value < lo or value > hi:
                quality = QUALITY_BAD
            elif self._last_good[index] is not None:
                if abs(value - self._last_good[index]) > RATE_LIMITS[index]:
                    quality = QUALITY_SUSPECT

        if quality == QUALITY_SUSPECT and count_rate_suspect:
            self._suspect_count[index] += 1
            if self._suspect_count[index] >= SUSPECT_TO_BAD_COUNT:
                quality = QUALITY_BAD
        elif quality == QUALITY_OK:
            self._suspect_count[index] = 0

        if parent_quality > quality:
            quality = parent_quality

        # 低过热度是信号质量告警，不是 ADC/传感器量程错误。
        if (index == 7 and quality == QUALITY_OK and value is not None and
                value < LOW_SUPERHEAT_THRESHOLD):
            quality = QUALITY_SUSPECT

        if quality == QUALITY_OK:
            self.software_sensor_value[index] = float(value)
            self._last_good[index] = float(value)
            self.software_sensor_quality[index] = QUALITY_OK
            self.software_sensor_checked_ms[index] = now_ms
        else:
            # 错误/可疑原始值不污染控制值；保留最后一次有效软件值。
            self.software_sensor_quality[index] = quality
            if self._last_good[index] is None:
                self.software_sensor_value[index] = float(INVALID_VALUE)

    def process(self, sensor_data, now_ms, ticks_diff, derived_provider=None):
        """处理一帧 sensor_data，原地更新软件值/质量并返回 fault register。

        Args:
            sensor_data: 低层采集值；本方法只读，不改写。
            now_ms: 调用方提供的 ticks_ms() 值。
            ticks_diff: MicroPython time.ticks_diff 函数，处理 tick 回绕。
            derived_provider: 可选回调，接收当前 software_sensor_value 并返回
                (superheat, suction_enthalpy)。派生量应基于校正后的父通道计算；
                未提供时将派生通道标记为无效，不回退使用 sensor_data[7]/[8]。
        """
        if len(sensor_data) != SOFTWARE_SENSOR_COUNT:
            raise ValueError("sensor_data length must be 10")

        # 物理输入通道：先哨兵/倍率修正，再按软件单位检查量程与变化率。
        for index in PHYSICAL_INPUT_INDEXES:
            self._evaluate(index, sensor_data[index], now_ms, ticks_diff)

        # 先将当前软件父通道交给上层物性计算，再校验派生量及其父通道质量。
        # 不复用 sensor_data[7/8]，避免派生值基于未校正的父通道。
        if derived_provider is None:
            derived = (INVALID_VALUE, INVALID_VALUE)
        else:
            derived = derived_provider(self.software_sensor_value)
        for derived_pos, item in enumerate(DERIVED_INPUTS):
            index = item["index"]
            parent_quality = max(self.software_sensor_quality[p] for p in item["parents"])
            self._evaluate(index, derived[derived_pos], now_ms, ticks_diff,
                           parent_quality=parent_quality,
                           count_rate_suspect=(index != 7))

        # 数据新鲜度在本帧转换后检查；超龄只标 BAD，不覆盖最后有效值。
        for index in range(SOFTWARE_SENSOR_COUNT):
            checked_ms = self.software_sensor_checked_ms[index]
            if checked_ms and ticks_diff(now_ms, checked_ms) > FRESHNESS_MS:
                self.software_sensor_quality[index] = QUALITY_BAD

        # 直接测量通道故障位：1=正常，0=BAD。
        fault_reg = 0xFFFF
        for index in PHYSICAL_INPUT_INDEXES:
            bit = SENSOR_FAULT_BIT_BY_INDEX[index]
            if bit is not None and self.software_sensor_quality[index] == QUALITY_BAD:
                fault_reg &= ~(1 << bit)
        self.sensor_fault_reg = fault_reg
        return fault_reg


# 供后续接入时按硬件 profile 选择。保持软件逻辑信号名/目标稳定，
# 切换硬件只需要新增 profile 中的物理通道和 scale/correction。
HARDWARE_PROFILES = {
    HARDWARE_PROFILE: {
        "buses": BUSES,
        "inputs": INPUT_MAP,
        "outputs": OUTPUTS,
        "feedbacks": FEEDBACKS,
        "esc_link": ESC_LINK,
        "exv": EXV_ENDPOINTS,
    },
}
