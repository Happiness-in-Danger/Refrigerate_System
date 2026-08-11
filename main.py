# main.py -- put your code here!
import uasyncio as asyncio
from pyb import Pin
import time
import json
from HAL.PID_Plus import IncrementalController
# from EXV.EXV import step,async_run,get_pwm_status
import EXV.Valve_CRTL as exv_ctrl
from HAL.ADC_sample import SmoothedADC
from Display_Touch.Desplay import desplay
import Board.state as state
from Sensor.read_sensors import reads

# ------------------------
# 全局变量区
# ------------------------
global_state = {
    'motor_speed': 0,
    'valve_pos': 0,
    'sensor_value': 0,
    'sensor_fault': False,
}

# ------------------------
# Model A：Ctrl compressor speed
# ------------------------
async def Compressor():
    while True:
        

        await asyncio.sleep(0.5)  # 每0.5秒执行一次

# ------------------------
# Model B：EXV open
# ------------------------
async def Exv():
    while True:
        exv_ctrl.run()

        await asyncio.sleep_ms(1000)
    

# ------------------------
# Model C：desplay
# ------------------------
async def Display():
    while True:
        # print("[C] Display -> speed:", global_state['motor_speed'],
        #       "valve:", global_state['valve_pos'],
        #       "sensor:", global_state['sensor_value'])
        desplay()
        await asyncio.sleep(1)

# ------------------------
# Model D：read sensor and check alarm
# ------------------------
async def Sensor():
    elapsed = 0
    while True:
        try:
            adc_pc2.sample()
            adc_pc3.sample()
            elapsed += 12   #sample_interval_ms

            if elapsed >= 200:
                elapsed = 0
            
            reads()
        except Exception as e:
            print("[Sensor] 读取异常:", e)
        await asyncio.sleep_ms(200)


# ------------------------
# file log
# ------------------------
async def Log():
    while True:
        try:
            with open("data_log.txt", "a") as f:
                f.write(f"{time.time()},{global_state['sensor_value']},{global_state['motor_speed']},{global_state['valve_pos']}\n")
        except:
            print("[Log] File write error")
        
        exv_ctrl.save_parameters('config.json')
        await asyncio.sleep(5)  # 每5秒写一次文件

# ------------------------
# main
# ------------------------
async def main():

    # ── 读取PID配置 ───────────────────────────────────────────
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        print("[Main] config加载成功:", config)
    except Exception as e:
        print("[Main] config读取失败，使用默认参数:", e)
        config = {}

    # ── 初始化PID控制器 ───────────────────────────────────────
    ctrl_exv = IncrementalController(
        Kp              = config.get('Kp',              0.6),
        Ki              = config.get('Ki',              0.12),
        Kd              = config.get('Kd',              3.0),
        dt              = 1.0,
        setpoint        = config.get('setpiont',        5.0),
        deadband        = config.get('deadband',        0.2),
        max_delta       = config.get('max_delta',       8.0),
        tau             = 3.0,
        error_threshold = config.get('error_threshold', 2.0),
        aggr_mode       = config.get('aggr_mode',       'togoal'),
        T_goal          = 5.0,
    )
    
    # ── 注入PID控制器到EXV调度模块 ───────────────────────────
    exv_ctrl.set_controller(ctrl_exv)


    await asyncio.gather(
        Compressor(),
        Exv(),
        Display(),
        Sensor(),
        Log()
    )

asyncio.run(main())

