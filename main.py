# main.py -- put your code here!
import uasyncio as asyncio
from pyb import Pin
import time
import json
from HAL.PID_Plus import IncrementalController
# from EXV.EXV import step,async_run,get_pwm_status
import EXV.Valve_CRTL as exv_ctrl
from HAL.ADC_sample import SmoothedADC
import Display_Touch.modbus_slave as modbus_slave
from Board import board
import Board.state as state
import Board.params_store as params_store
import Board.hmi_uart as hmi_uart
from Sensor.read_sensors import reads_async
import Alarm.supervisor as supervisor
from HAL.Driver import UartLink, get_driver_link
from Compressor import Compressor
from Evaporator import Evaporator
from Condenser import Condenser


# ------------------------
# 全局变量区
# ------------------------

import Board.state as state

# ------------------------
# Model B：EXV open
# ------------------------
async def Exv():
    while True:
        await exv_ctrl.run()

        await asyncio.sleep_ms(1000)
    

# ------------------------
# Model C/D：read sensor and check alarm
# ------------------------
async def Sensor():
    while True:
        try:
            board.adc_pc2c.sample()
            board.adc_pc3c.sample()
            
            # Step4: 使用异步版本，不再阻塞事件循环
            await reads_async()
        except Exception as e:
            print("[Sensor] 读取异常:", e)
        await asyncio.sleep_ms(200)


# ------------------------
# Model E: Driver link (Step4)
# ------------------------
async def DriverLink():
    """
    Driver link 协程（Step4：完成 Pico/ESC 通讯链路）
    
    职责：
    1. 轮询接收 Pico 状态帧（poll_status）
    2. 定期发送命令帧（send_all_commands）
    3. 更新 link_ok 状态到 state
    4. 将来供 Compressor/Evaporator/Condenser 消费
    
    当前为空载模式：ESC 全部禁用，仅维持心跳
    """
    link = get_driver_link()
    if link is None:
        print("[Driver] 链路初始化失败，协程退出")
        return

    # 确保所有 ESC 初始禁用（空载）；设备模块随后按需 enable
    for i in range(4):
        link.disable(i)
    
    last_cmd_ms = time.ticks_ms()
    CMD_PERIOD_MS = 10  # 每 20ms 发送一次命令（50Hz，满足 Pico 100ms 超时要求）
    
    while True:
        try:
            # 1. 接收状态帧（非阻塞）
            link_ok = link.poll_status()
            
            # 2. 更新到 state（P1-1 修复：供 supervisor 消费）
            state.driver_link_ok = link_ok
            
            # 3. Step5a: ESC 遥测数据采集（2026-09-03 新增）
            #    职责分离：Driver.py 负责数据换算，main.py 只负责调用
            link.update_telemetry_to_state(state)
            
            # 4. 定期发送命令（空载：全部 disabled）
            now = time.ticks_ms()
            if time.ticks_diff(now, last_cmd_ms) >= CMD_PERIOD_MS:
                link.send_all_commands()
                last_cmd_ms = now
            
            # 5. 让出控制权（高频协程）
            await asyncio.sleep_ms(5)
            
        except Exception as e:
            print("[Driver] 链路异常:", e)
            await asyncio.sleep_ms(100)


# ------------------------
# file log
# ------------------------
# async def Log():
#     while True:
#         try:
#             with open("data_log.txt", "a") as f:
#                 f.write(f"{time.time()},{global_state['sensor_value']},{global_state['motor_speed']},{global_state['valve_pos']}\n")
#         except:
#             print("[Log] File write error")

#         #state.save_control_params('config.json')   # 换掉不存在的 exv_ctrl.save_parameters
#         await asyncio.sleep(5000)

# ------------------------
# main
# ------------------------
async def main():

    # ── Step6: 加载持久化参数（损坏/缺失则使用 state.py 默认值）──
    params_store.load_config()

    # ── 初始化PID控制器 ───────────────────────────────────────
    ctrl_exv = IncrementalController(
        Kp              = state.control_params['Kp'],
        Ki              = state.control_params['Ki'],
        Kd              = state.control_params['Kd'],
        dt              = 1.0,
        setpoint        = state.control_params['setpoint'],
        deadband        = state.control_params['deadband'],
        max_delta       = state.control_params['max_delta'],
        tau             = state.control_params['tau'],
        error_threshold = state.control_params['error_threshold'],
        aggr_mode       = state.control_params['aggr_mode'],
        T_goal          = state.control_params['T_goal'],
    )
    
    # ── 注入PID控制器到EXV调度模块 ───────────────────────────
    exv_ctrl.set_controller(ctrl_exv)


    await asyncio.gather(
        Compressor(),                    # Step5a: 压缩机执行层
        Evaporator(),                    # Step5a: 蒸发风机执行层
        Condenser(),                     # Step5a: 冷凝风机执行层
        Exv(),
        hmi_uart.hmi_rx(),               # PC HMI 接收
        hmi_uart.hmi_tx(),               # PC HMI 发送
        hmi_uart.hmi_watchdog(),         # PC HMI 超时停机
        Sensor(),
        modbus_slave.slave_loop(),
        modbus_slave.sync_loop(),
        supervisor.supervisor_loop(),   # Step3: Alarm 延时表 + sys_mode 状态机 + 手动复位
        DriverLink(),                    # Step4/5a: Pico/ESC 通讯链路 + 遥测
        params_store.loop(),             # Step6: 参数掉电保持（30s 防抖落盘）
        # Log()
    )

asyncio.run(main())

