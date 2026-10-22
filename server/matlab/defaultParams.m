function p = defaultParams()
%DEFAULTPARAMS Processing parameters. Mirrors config.Params in the Python project (keep in sync).
p.standard_gravity = 9.80665;
% calibration (stationary baseline)
p.calib_window_s = 1.5;  p.calib_max_acc_std_mps2 = 0.15;  p.calib_max_gyro_std_dps = 1.0;
p.calib_max_gyro_bias_dps = 10.0;  p.calib_gravity_tol_mps2 = 0.5;
% filtering / orientation
p.lowpass_cutoff_hz = 15.0;  p.filter_order = 4;  p.madgwick_beta = 0.02;  p.acc_gate_mps2 = 1.5;
% flight detection and sanity limits
p.flight_acc_max_mps2 = 4.0;  p.flight_min_s = 0.08;  p.flight_merge_gap_s = 0.04;
p.max_motion_s = 8.0;  p.max_plausible_speed_mps = 25.0;  p.max_closure_error_mps = 0.5;
p.height_mismatch_tol_m = 1.0;  p.max_gap_factor = 1.5;
% motion-based one-shot triggers (still -> move -> still)
p.trig_start_acc_mps2 = 1.0;  p.trig_start_gyro_dps = 30.0;  p.trig_start_confirm_s = 0.06;
p.trig_stop_confirm_s = 0.5;  p.trig_stop_acc_std_mps2 = 0.25;  p.trig_stop_gyro_dps = 15.0;
p.trig_min_movement_s = 0.3;  p.trig_min_session_s = 0.6;
p.trig_max_session_s = [];     % [] = never time out
p.align_x_to_b = false;        % rotate XY so Point B lies on +X
end
