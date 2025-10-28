import math

class IncrementalController:
    def __init__(self,
                 Kp=0.6, Ki=0.12, Kd=3.0,
                 dt=1.0,
                 setpoint=5.0,
                 deadband=0.05,
                 max_delta=5.0,
                 tau=3.0,   #EMA 
                 adapt_alpha=0.05,
                 # --- new params for aggressive behavior ---
                 error_threshold=2,   # if it's None it's max(2*deadband, user set)
                 aggr_mode="togoal", # "off", "proportional", "togoal"
                 K_aggr=None,            # use in proportional（if it's None it's 3*Kp ）
                 T_goal=5.0             # use in "togoal"，
                 ):
        # basic parameters
        self.Kp = Kp; self.Ki = Ki; self.Kd = Kd
        self.dt = dt; self.setpoint = setpoint; self.deadband = deadband
        self.max_delta = max_delta; self.tau = max(1e-6, tau)
        self.adapt_alpha = adapt_alpha

        # statue
        self.integral = 0.0
        self.prev_meas = None
        self.dx_filtered = 0.0
        self.prev_dx_filtered = 0
        self.ddx_filtered = 0.0

        # radical parameters
        self.error_threshold = error_threshold if error_threshold is not None else max(40*deadband, 0.0)
        self.aggr_mode = aggr_mode
        self.K_aggr = K_aggr if K_aggr is not None else (3.0 * self.Kp)  # default 3x Kp
        self.T_goal = T_goal if T_goal is not None else 10.0  # default 10 s if togoal

    def update(self, meas, setpoint=None, measured_aux=None, force_disable_derivative=False):

        if setpoint is None:
            setpoint = self.setpoint
        else:
            self.setpoint = setpoint

        # first not active
        if self.prev_meas is None:
            self.prev_meas = meas
            self.dx_filtered = 0.0
            return 0.0

        # deadband
        error = meas - setpoint   
        eff_error = 0.0 if abs(error) <= self.deadband else error

        use_aggressive = (abs(error) > self.error_threshold) and (self.aggr_mode != "off")

        if use_aggressive:
            
            self.integral *= 0.5

            if self.aggr_mode == "proportional":
                delta_unsat = self.K_aggr * eff_error

            elif self.aggr_mode == "togoal":
                conv_factor = 1.0
                r_per_sec = eff_error / self.T_goal 
                delta_unsat = conv_factor * r_per_sec * self.dt * 1.0
            else:
                delta_unsat = self.K_aggr * eff_error

            if delta_unsat > self.max_delta:
                delta = self.max_delta
            elif delta_unsat < -self.max_delta:
                delta = -self.max_delta
            else:
                delta = delta_unsat
            self.prev_meas = meas
            return delta

        # --- normal ---
        # 1) F_derivative
        raw_dx = (meas - self.prev_meas) / self.dt
        beta = (self.tau / (self.tau + self.dt))
        self.dx_filtered = beta * self.dx_filtered + (1.0 - beta) * raw_dx
        # 2) S_derivative
        raw_ddx = (self.dx_filtered - self.prev_dx_filtered) / self.dt
        beta2 = (1.5*self.tau / (1.5*self.tau + self.dt))
        self.ddx_filtered = beta2 * self.ddx_filtered + (1.0 - beta) * raw_ddx
        self.prev_dx_filtered = self.dx_filtered

        # 2) integral update
        self.integral += eff_error * self.dt

        # 3) Adaptive
        alpha_sign_p = 1 if self.dx_filtered > 0 else -1
        Kp_eff = self.Kp * (1.0 + alpha_sign_p*self.adapt_alpha * abs(error))
        alpha_sign_d = +1 if self.ddx_filtered > 0 else -1
        Kd_eff = self.Kd * (1.0 + alpha_sign_d* self.adapt_alpha * abs(self.dx_filtered))
        if abs(error) > 0.5 * self.deadband:
            alpha_sign_i =1 if self.dx_filtered > 0 else -1
            Ki_eff = self.Ki * (1.0 + alpha_sign_i*self.adapt_alpha)
        else:
            Ki_eff = self.Ki

        # 4) decide use D
        use_derivative = not force_disable_derivative
        if measured_aux is not None:
            if measured_aux < 1e-4:
                use_derivative = False
        deriv_term = self.dx_filtered if use_derivative else 0.0

        delta_unsat =Kp_eff * eff_error + Ki_eff * self.integral + Kd_eff * deriv_term  if eff_error != 0.0 else 0.0

        if delta_unsat > self.max_delta:
            delta = self.max_delta
            self.integral -= eff_error * self.dt
        elif delta_unsat < -self.max_delta:
            delta = -self.max_delta
            self.integral -= eff_error * self.dt
        else:
            delta = delta_unsat

        self.prev_meas = meas
        return delta


# ------------------ test ------------------
def demo():
    # 控制过热度 SH
    ctrl = IncrementalController(Kp=0.6, Ki=0.12, Kd=3.0, dt=1.0, setpoint=5.0, deadband=0.1,
                                 max_delta=8.0, tau=3.0,
                                 error_threshold=2, aggr_mode="togoal", T_goal=5.0)

    valve = 40.0
    SHs = [4.8, 5.2, 6.2, 7.5, 8.0, 7.0, 6.0, 5.4, 5.0,4.99,4.8]
    for sh in SHs:
        d = ctrl.update(sh)
        valve = max(0.0, min(100.0, valve + d))
        print(f"SH={sh:.2f} e={sh-ctrl.setpoint:+.2f} -> delt={d:+.2f}% => valve={valve:.2f}%")

if __name__ == "__main__":
    demo()
