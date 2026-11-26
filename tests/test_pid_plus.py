from HAL.PID_Plus import IncrementalController


def make_controller(**kwargs):
    defaults = {
        "Kp": 0.0,
        "Ki": 0.0,
        "Kd": 0.0,
        "dt": 1.0,
        "setpoint": 0.0,
        "deadband": 0.0,
        "max_delta": 1000.0,
        "tau": 0.0,
        "aggr_mode": "off",
    }
    defaults.update(kwargs)
    return IncrementalController(**defaults)


def test_proportional_term_is_error_difference():
    ctrl = make_controller(Kp=2.0)

    assert ctrl.update(5.0) == 10.0
    # Constant error does not repeatedly add the positional P output.
    assert ctrl.update(5.0) == 0.0


def test_integral_term_is_per_sample_increment():
    ctrl = make_controller(Ki=2.0, dt=0.5)

    assert ctrl.update(3.0) == 3.0
    assert ctrl.update(3.0) == 3.0


def test_derivative_term_is_change_in_measurement_rate():
    ctrl = make_controller(Kd=2.0, dt=1.0)

    assert ctrl.update(0.0) == 0.0
    assert ctrl.update(1.0) == 2.0
    assert ctrl.update(3.0) == 2.0


def test_output_sign_reverses_final_actuator_increment():
    ctrl = make_controller(Kp=0.5, max_delta=100.0, output_sign=-1.0)

    # Measured speed below the target gives a negative error; reversed output
    # increases throttle, with the configured per-sample limit applied.
    assert ctrl.update(0.0, setpoint=1000.0) == 100.0


def test_aggressive_togoal_keeps_incremental_equation_and_output_sign():
    ctrl = make_controller(
        Kp=2.0,
        dt=0.5,
        error_threshold=2.0,
        aggr_mode="togoal",
        T_goal=5.0,
        output_sign=-1.0,
    )

    # Incremental P step is 20; the togoal integral-rate increment is 2.
    assert ctrl.update(10.0) == -22.0


def test_aggressive_proportional_mode_changes_incremental_p_gain():
    ctrl = make_controller(
        Kp=1.0,
        K_aggr=3.0,
        error_threshold=2.0,
        aggr_mode="proportional",
    )

    assert ctrl.update(10.0) == 30.0
    # It does not repeatedly emit a position-style proportional output.
    assert ctrl.update(10.0) == 0.0


def test_integral_is_rolled_back_when_delta_saturates():
    ctrl = make_controller(Ki=1.0, max_delta=0.5)

    assert ctrl.update(1.0) == 0.5
    assert ctrl.integral == 0.0


def test_reset_clears_incremental_history():
    ctrl = make_controller(Kp=2.0)
    assert ctrl.update(5.0) == 10.0

    ctrl.reset()
    assert ctrl.update(5.0) == 10.0
