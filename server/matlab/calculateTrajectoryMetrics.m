function r = calculateTrajectoryMetrics(kin, fs, p, sessionId)
%CALCULATETRAJECTORYMETRICS Path length, A-to-B displacement, heights, speeds, quality flags (cm, m/s).
pos = kin.position_m;   vel = kin.velocity;
if p.align_x_to_b && hypot(pos(end,1), pos(end,2)) > 1e-9
    th = -atan2(pos(end,2), pos(end,1));   R = [cos(th) -sin(th); sin(th) cos(th)];
    pos(:,1:2) = pos(:,1:2) * R.';   vel(:,1:2) = vel(:,1:2) * R.';
end
posCm = pos * 100 + [0 20 40];   steps = diff(posCm, 1, 1);
dur = kin.t(end);   path = sum(sqrt(sum(steps.^2, 2)));   straight = norm(posCm(end, :) - [0 20 40]);
% free-fall (flight) inside the window, for an independent height cross-check
[flightT, flightH] = longestFlight(kin.acc_norm, kin.t, fs, p);
flags = {};
if dur > p.max_motion_s, flags{end+1} = 'too_long_for_integration'; end
if any(kin.gaps), flags{end+1} = 'data_gap'; end
if max(sqrt(sum(vel.^2, 2))) > p.max_plausible_speed_mps, flags{end+1} = 'implausible_speed'; end
if kin.closure_error > p.max_closure_error_mps, flags{end+1} = 'large_closure_error'; end
if ~isempty(flightT) && max(kin.position_m(:,3)) > flightH + p.height_mismatch_tol_m
    flags{end+1} = 'height_inconsistent_with_flight';
end
r.session_id = sessionId;   r.t_ms = kin.t_ms;   r.t_s = kin.t;   r.position_cm = posCm;   r.velocity_mps = vel;
r.point_a_cm = [0 20 40];   r.point_b_cm = posCm(end, :);
r.straight_line_cm = straight;   r.horizontal_distance_cm = hypot(posCm(end,1) - 0, posCm(end,2) - 20);
r.path_length_cm = path;   r.horizontal_path_length_cm = sum(sqrt(sum(steps(:,1:2).^2, 2)));
r.displacement_cm = posCm(end, :) - [0 20 40];   r.vertical_displacement_cm = posCm(end, 3) - 40;
r.peak_height_cm = max(posCm(:, 3)) - 40;   r.duration_s = dur;
r.mean_speed_mps = path / 100 / dur;   r.max_speed_mps = max(sqrt(sum(vel.^2, 2)));
r.max_acceleration_mps2 = max(sqrt(sum(kin.linear.^2, 2)));   r.efficiency = straight / path;
r.closure_error_mps = kin.closure_error;   r.flags = flags;
r.flight_time_s = flightT;   r.flight_height_cm = flightH * 100;
if isempty(flightT), r.flight_height_cm = []; end
end

function [dur, height] = longestFlight(accNorm, t, fs, p)
d = diff([0; accNorm(:) < p.flight_acc_max_mps2; 0]);
starts = find(d == 1) - 1;   stops = find(d == -1) - 1;       % 0-based, stop exclusive (as in Python)
merge = floor(p.flight_merge_gap_s * fs + 0.5);   runs = zeros(0, 2);
for i = 1:numel(starts)
    if ~isempty(runs) && starts(i) - runs(end, 2) <= merge, runs(end, 2) = stops(i);
    else, runs(end+1, :) = [starts(i) stops(i)]; end %#ok<AGROW>
end
dur = [];   height = [];   n = numel(t);
for i = 1:size(runs, 1)
    e = min(runs(i, 2), n - 1);   len = t(e + 1) - t(runs(i, 1) + 1);
    if len >= p.flight_min_s && (isempty(dur) || len > dur), dur = len; end
end
if ~isempty(dur), height = 9.80665 * dur^2 / 8; end
end
