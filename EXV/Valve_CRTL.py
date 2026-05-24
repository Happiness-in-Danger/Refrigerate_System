#多线程法
import _thread
from HAL.PID_Plus import IncrementalController
# from Extensions.read_r134a import read_record
# from Sensor.read_sensors import reads
# from EXV.EXV import step
import time
class ValveController:
    def __init__(self,cycle=0.5,init_position=0.0):

        self.valve_position = init_position
        self.pending_delta = 0.0
        self.valve_statue = False
        self.cycle_pid = cycle
        self.running = True
        self.lock = _thread.allocate_lock()
        _thread.start_new_thread(self.worker, ())

    def run_valva(self,valve):
        if valve>=0:
            valve*=5
            print(valve)
            # step(round(valve),40,1,0,"PB7",4,2)
        else:
            valve*=5
            print(valve)
            # step(round(abs(valve)),40,0,0,"PB7",4,2)

    def get_delta(self,pid_value):
        with self.lock:
            self.pending_delta += pid_value

    def worker(self):
        """后台线程，不停检查是否需要执行 step()"""
        while self.running:
            with self.lock:
                pid_value = self.pending_delta
                if abs(pid_value) < 0.01:
                    pid_value = 0
                else:
                    self.pending_delta = 0

            if pid_value != 0:
                # 执行真实动作（阻塞）
                self.run_valva(pid_value)
                # 每次动作完成后更新开度
                with self.lock:
                    self.valve_position += pid_value

            else:
                time.sleep(0.01)

    def stop(self):
        self.running = False



# ------------------ test ------------------
