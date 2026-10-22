function [s, result] = runTrajectorySession(csvPath, varargin)
%RUNTRAJECTORYSESSION Replay a raw Movesense CSV through the one-shot A-to-B state machine and export.
%   [s, result] = runTrajectorySession('data/raw/x.csv', 'SampleRate', 52, 'ArmAtS', 0, 'OutDir', dir, ...
%       'SessionId', 'MOVE_20261003_001', 'ReferenceDistanceCm', 250, 'View', '3d', 'Visible', 'on')
%   Writes <id>_matlab_dashboard_3d.png and _xy.png; 'View' selects which one is shown on screen.
o = struct('SampleRate', [], 'ArmAtS', 0, 'OutDir', fullfile(fileparts(mfilename('fullpath')), '..', 'data', 'processed'), ...
    'SessionId', 'MATLAB', 'ReferenceDistanceCm', [], 'ReferencePathCm', [], 'AlignXToB', false, 'View', '3d', 'Visible', 'on');
for k = 1:2:numel(varargin), o.(varargin{k}) = varargin{k+1}; end
data = loadMovesenseData(csvPath, o.SampleRate);
p = defaultParams();   p.align_x_to_b = o.AlignXToB;
s = initializeSession(data.fs, p);   s.sessionId = o.SessionId;
armAtMs = data.t_ms(1) + o.ArmAtS * 1000;
fprintf('Replaying %d samples @ %g Hz; arming at t=%.1f s (explicit ARM).\n', numel(data.t_ms), data.fs, o.ArmAtS);
for i = 1:numel(data.t_ms)
    if strcmp(s.state, 'IDLE') && data.t_ms(i) >= armAtMs && isempty(s.completed), s = armSession(s); end
    s = acceptSample(s, data.t_ms(i), data.acc(i,:), data.gyro(i,:));
    if strcmp(s.state, 'LOCKED'), break; end
end
result = s.result;
if isempty(result)
    fprintf('No complete A->B session (final state: %s).\n', s.state);  return
end
sm = buildSummary(result, o);
outDir = fullfile(o.OutDir, o.SessionId);   if exist(outDir, 'dir') ~= 7, mkdir(outDir); end
base = fullfile(outDir, [o.SessionId '_matlab']);
writeTrajectoryCsv([base '_trajectory.csv'], result);
fid = fopen([base '_summary.json'], 'w');   fwrite(fid, jsonencode(sm)); fclose(fid);
save([base '.mat'], 'result', 'sm');
views = {'3d', 'xy'};   views(strcmpi(views, o.View)) = [];   views = [{lower(o.View)}, views];   % chosen view first
for k = 1:2
    show = k == 1 && strcmp(o.Visible, 'on');
    fig = createTrajectoryDashboard(result, sm, [base '_dashboard_' views{k} '.png'], views{k}, ternary(show, 'on', 'off'));
    if ~show, close(fig); end
end
fx = plotXYZvsTime(result, [base '_xyz_time.png'], 'off');   close(fx);
fprintf('A->B straight-line = %.1f cm | Trajectory length = %.1f cm | dX = %.1f cm, dY = %.1f cm\n', ...
    result.straight_line_cm, result.path_length_cm, result.displacement_cm(1), result.displacement_cm(2));
fprintf('Vertical displacement = %.1f cm | Peak height = %.1f cm | Duration = %.2f s | Status: %s\n', ...
    result.vertical_displacement_cm, result.peak_height_cm, result.duration_s, sm.validation_status);
end

function sm = buildSummary(r, o)
sm.session_id = r.session_id;   sm.state = 'LOCKED';
sm.point_a_cm = r.point_a_cm;   sm.point_b_cm = r.point_b_cm;
sm.straight_line_distance_cm = r.straight_line_cm;   sm.horizontal_distance_cm = r.horizontal_distance_cm;
sm.trajectory_length_cm = r.path_length_cm;   sm.horizontal_path_length_cm = r.horizontal_path_length_cm;
sm.displacement_x_cm = r.displacement_cm(1);   sm.displacement_y_cm = r.displacement_cm(2);
sm.vertical_displacement_cm = r.vertical_displacement_cm;   sm.peak_height_cm = r.peak_height_cm;
sm.movement_duration_s = r.duration_s;   sm.mean_speed_mps = r.mean_speed_mps;   sm.max_speed_mps = r.max_speed_mps;
sm.max_acceleration_mps2 = r.max_acceleration_mps2;   sm.efficiency = r.efficiency;
sm.closure_error_mps = r.closure_error_mps;   sm.flight_time_s = r.flight_time_s;   sm.flight_height_cm = r.flight_height_cm;
sm.quality_flags = r.flags;
sm.reference_distance_cm = o.ReferenceDistanceCm;   sm.reference_path_cm = o.ReferencePathCm;
sm.distance_error_cm = [];   sm.distance_error_percent = [];   sm.path_error_cm = [];   sm.path_error_percent = [];
if ~isempty(o.ReferenceDistanceCm)
    sm.distance_error_cm = r.straight_line_cm - o.ReferenceDistanceCm;
    sm.distance_error_percent = 100 * sm.distance_error_cm / o.ReferenceDistanceCm;
end
if ~isempty(o.ReferencePathCm)
    sm.path_error_cm = r.path_length_cm - o.ReferencePathCm;
    sm.path_error_percent = 100 * sm.path_error_cm / o.ReferencePathCm;
end
if isempty(o.ReferenceDistanceCm) && isempty(o.ReferencePathCm), sm.validation_status = 'UNVALIDATED';
else, sm.validation_status = 'compared with reference (single trial)'; end
end

function writeTrajectoryCsv(path, r)
cum = [0; cumsum(sqrt(sum(diff(r.position_cm, 1, 1).^2, 2)))];
M = [(0:numel(r.t_s)-1).', r.t_ms, r.t_s, r.position_cm, cum, r.velocity_mps];
fid = fopen(path, 'w');
fprintf(fid, 'sample_index,timestamp_ms,time_s,x_cm,y_cm,z_cm,cumulative_path_cm,vx_mps,vy_mps,vz_mps\n');
fprintf(fid, '%d,%.6f,%.9f,%.9f,%.9f,%.9f,%.9f,%.9f,%.9f,%.9f\n', M.');
fclose(fid);
end

function v = ternary(c, a, b)
if c, v = a; else, v = b; end
end
