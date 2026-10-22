function kin = calculateTrajectory(t_ms, acc, gyro, baseline, fs, p)
%CALCULATETRAJECTORY Reconstruct world-frame motion for ONE finalized A-to-B window.
%   World frame: right-handed, Z up, origin = Point A (rest sample before motion onset).
%   Steps: gap-safe dt -> orientation (Madgwick) -> low-pass -> rotate -> remove gravity ->
%   integrate with v(A)=v(B)=0 (linear drift removal). Not a raw double integration.
t = (t_ms - t_ms(1)) / 1000;
gaps = [false; diff(t) > p.max_gap_factor / fs];
dt = [1/fs; diff(t)];   dt(gaps) = 1/fs;                 % never integrate across a data gap
accF = filterMotionData(acc, fs, p.lowpass_cutoff_hz, p.filter_order);
[~, worldAcc] = estimateOrientation(acc, accF, gyro - baseline.gyro_bias, dt, baseline.acc_mean, baseline.gravity, p);
linear = worldAcc - [0 0 baseline.gravity];              % linear = measured - gravity component
rawVel = cumtrapz(t, linear);
vel = rawVel - (t / t(end)) * rawVel(end, :);            % enforce v(B) = 0
pos = cumtrapz(t, vel);
kin.t = t;   kin.t_ms = t_ms;   kin.linear = linear;   kin.velocity = vel;   kin.position_m = pos;
kin.closure_error = norm(rawVel(end, :));   kin.gaps = gaps;   kin.acc_norm = sqrt(sum(acc.^2, 2));
end
