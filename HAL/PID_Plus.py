import json


class IncrementalController:
    """Incremental PID controller.

    ``update`` returns an actuator *increment* (delta-u), not an absolute
    actuator command. The project uses ``error = measurement - setpoint``;
    ``output_sign`` selects the actuator direction for a particular loop.

    The normal incremental equation is:
        du = Kp * (e[k] - e[k-1]) + Ki * e[k] * dt
             + Kd * (de/dt[k] - de/dt[k-1])

    ``aggr_mode`` is retained as large-error gain scheduling. It changes the
    incremental P/I gains without replacing the incremental PID equation.
    Automatic gain learning is not performed by ``update``; the legacy
    constructor arguments remain accepted, and learning can only be requested
    explicitly through ``adapt_parameters``.
    """

    def __init__(self,
                 Kp=0.6, Ki=0.12, Kd=3.0,
                 dt=1.0,
                 setpoint=5.0,
                 deadband=0.2,
                 max_delta=5.0,
                 tau=3.0,
                 adapt_alpha=0.05,
                 gain_learn_rate=0.0,
                 gain_bounds_ratio=(0.2, 5.0),
                 error_threshold=2,
                 aggr_mode="togoal",
                 K_aggr=None,
                 T_goal=5.0,
                 output_sign=1.0):
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd
        self.dt = dt if dt > 0 else 1e-6
        self.setpoint = setpoint
        self.deadband = max(0.0, deadband)
        self.max_delta = abs(max_delta)
        self.tau = max(0.0, tau)
        self.adapt_alpha = adapt_alpha
        self.gain_learn_rate = max(0.0, gain_learn_rate)
        self._ratio_min, self._ratio_max = gain_bounds_ratio
        self._Kp0, self._Ki0, self._Kd0 = Kp, Ki, Kd
        self.output_sign = -1.0 if output_sign < 0 else 1.0

        self.error_threshold = (max(0.0, 2.0 * self.deadband)
                                if error_threshold is None
                                else max(0.0, error_threshold))
        self.aggr_mode = aggr_mode
        self.K_aggr = 3.0 * self.Kp if K_aggr is None else K_aggr
        self.T_goal = 10.0 if T_goal is None or T_goal <= 0 else T_goal

        self.reset()

    def reset(self):
        """Clear measurement history after a stop or an invalid-data gap."""
        self.integral = 0.0
        self.prev_meas = None
        self.prev_error = 0.0
        self.dx_filtered = 0.0
        self.prev_dx_filtered = 0.0
        self.ddx_filtered = 0.0

    def adapt_parameters(self, Kp_eff, Ki_eff, Kd_eff):
        """Explicit legacy hook; automatic adaptation is disabled by default."""
        rate = self.gain_learn_rate
        if rate <= 0:
            return
        self.Kp += rate * (Kp_eff - self.Kp)
        self.Ki += rate * (Ki_eff - self.Ki)
        self.Kd += rate * (Kd_eff - self.Kd)
        self.Kp = self._clamp_gain(self.Kp, self._Kp0)
        self.Ki = self._clamp_gain(self.Ki, self._Ki0)
        self.Kd = self._clamp_gain(self.Kd, self._Kd0)

    def _clamp_gain(self, value, base):
        lo = base * self._ratio_min
        hi = base * self._ratio_max
        if lo > hi:
            lo, hi = hi, lo
        return min(max(value, lo), hi)

    def _limit_delta(self, delta):
        limit = self.max_delta
        if delta > limit:
            return limit
        if delta < -limit:
            return -limit
        return delta

    def save_parameters(self, path):
        data = {
            "Kp": self.Kp, "Ki": self.Ki, "Kd": self.Kd,
            "dt": self.dt, "setpoint": self.setpoint,
            "deadband": self.deadband, "max_delta": self.max_delta,
            "tau": self.tau, "error_threshold": self.error_threshold,
            "aggr_mode": self.aggr_mode, "K_aggr": self.K_aggr,
            "T_goal": self.T_goal, "output_sign": self.output_sign,
        }
        with open(path, "w") as f:
            json.dump(data, f)

    def load_parameters(self, path):
        with open(path, "r") as f:
            data = json.load(f)
        self.Kp = data.get("Kp", self.Kp)
        self.Ki = data.get("Ki", self.Ki)
        self.Kd = data.get("Kd", self.Kd)
        self.dt = max(1e-6, data.get("dt", self.dt))
        self.setpoint = data.get("setpoint", self.setpoint)
        self.deadband = max(0.0, data.get("deadband", self.deadband))
        self.max_delta = abs(data.get("max_delta", self.max_delta))
        self.tau = max(0.0, data.get("tau", self.tau))
        self.error_threshold = max(
            0.0, data.get("error_threshold", self.error_threshold))
        self.aggr_mode = data.get("aggr_mode", self.aggr_mode)
        self.K_aggr = data.get("K_aggr", 3.0 * self.Kp)
        self.T_goal = max(1e-6, data.get("T_goal", self.T_goal))
        self.output_sign = (-1.0 if data.get("output_sign", self.output_sign) < 0
                            else 1.0)
        self._Kp0, self._Ki0, self._Kd0 = self.Kp, self.Ki, self.Kd
        self.reset()

    def update(self, meas, setpoint=None, measured_aux=None,
               force_disable_derivative=False):
        if setpoint is None:
            setpoint = self.setpoint
        else:
            self.setpoint = setpoint

        error = meas - setpoint
        eff_error = 0.0 if abs(error) <= self.deadband else error

        # Filter the measured-value derivative. Derivative-on-measurement avoids
        # a derivative kick when setpoint changes; in incremental form its
        # contribution is the change in filtered derivative since last sample.
        if self.prev_meas is None:
            self.dx_filtered = 0.0
            self.ddx_filtered = 0.0
        else:
            raw_dx = (meas - self.prev_meas) / self.dt
            beta = self.tau / (self.tau + self.dt) if self.tau > 0 else 0.0
            previous_dx = self.dx_filtered
            self.dx_filtered = beta * previous_dx + (1.0 - beta) * raw_dx
            self.ddx_filtered = (self.dx_filtered - previous_dx) / self.dt

        use_derivative = not force_disable_derivative
        if measured_aux is not None and measured_aux < 1e-4:
            use_derivative = False

        # Large-error modes adjust gains but still use the incremental PID
        # equation. "togoal" contributes Kp/T_goal as an integral-rate gain;
        # "proportional" uses K_aggr for the incremental P term.
        use_aggressive = (abs(error) > self.error_threshold and
                          self.aggr_mode != "off")
        kp_eff = self.Kp
        ki_eff = self.Ki
        if use_aggressive:
            if self.aggr_mode == "proportional":
                kp_eff = self.K_aggr
            elif self.aggr_mode == "togoal":
                ki_eff = max(self.Ki, self.Kp / self.T_goal)

        delta_error = eff_error - self.prev_error
        p_delta = kp_eff * delta_error
        integral_increment = eff_error * self.dt
        i_delta = ki_eff * integral_increment
        d_delta = (self.Kd * (self.dx_filtered - self.prev_dx_filtered)
                   if use_derivative else 0.0)
        delta_unsat = p_delta + i_delta + d_delta

        # Anti-windup for the controller's own per-sample output limit.
        self.integral += integral_increment
        if ((delta_unsat > self.max_delta and integral_increment * ki_eff > 0) or
                (delta_unsat < -self.max_delta and integral_increment * ki_eff < 0)):
            self.integral -= integral_increment

        delta = self.output_sign * self._limit_delta(delta_unsat)

        self.prev_meas = meas
        self.prev_error = eff_error
        self.prev_dx_filtered = self.dx_filtered
        return delta


# Backwards-compatible smoke demo (not run by the firmware).
def demo():
    ctrl = IncrementalController(Kp=0.6, Ki=0.12, Kd=3.0, dt=1.0,
                                 setpoint=5.0, deadband=0.1,
                                 max_delta=8.0, aggr_mode="off")
    valve = 40.0
    for sh in (4.8, 5.2, 6.2, 7.5, 8.0, 7.0, 6.0, 5.4, 5.0, 4.99, 4.8):
        delta = ctrl.update(sh)
        valve = max(0.0, min(100.0, valve + delta))
        print("SH=%.2f delta=%+.2f valve=%.2f" % (sh, delta, valve))


if __name__ == "__main__":
    demo()
