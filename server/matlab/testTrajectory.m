function testTrajectory()
%TESTTRAJECTORY Self-checks on SYNTHETIC streams (known physics, never real data). Run: testTrajectory
%   Mirrors the Python tests: one-shot lock, duplicate/out-of-order rejection, short-movement rejection, accuracy.
fs = 52;   p = defaultParams();   g = p.standard_gravity;   rand('state', 7);   randn('state', 7);
still = @(sec) [0.02*randn(round(sec*fs),2), g + 0.02*randn(round(sec*fs),1)];
push = @() (still(2) + [pi*sin(2*pi*(0:2*fs-1).'/fs/2), zeros(2*fs,2)]);   % A=pi, T=2 s -> exactly 2.00 m
nTests = 0;
% 1) one-shot full session
[s, t, a, gy] = run_(fs, p, {still(3), push(), still(2)}, true);
check(strcmp(s.state, 'LOCKED'), 'session reaches LOCKED');
r = s.result;   check(abs(r.straight_line_cm - 200) < 16, sprintf('A-B ~200 cm (got %.1f)', r.straight_line_cm));
check(r.path_length_cm >= r.straight_line_cm - 1e-6 && isempty(r.flags), 'path >= straight and no flags');
% 2) nothing appended / no second trajectory after LOCKED; reset + re-arm gives a new trajectory
n0 = numel(r.t_s);   a2 = [still(1); push(); still(2)];   t2 = t(end) + (1:size(a2, 1)).' * 1000/fs;
for i = 1:numel(t2), s = acceptSample(s, t2(i), a2(i,:), [0 0 0]); end
check(numel(s.result.t_s) == n0 && numel(s.completed) == 1, 'LOCKED session ignores further movement');
s = resetSession(s);   check(strcmp(s.state, 'IDLE') && numel(s.completed) == 1, 'reset -> IDLE keeps completed');
% 3) duplicates and out-of-order samples
s = armSession(initializeSession(fs, p));   t0 = 1000 + (0:99).' * 1000/fs;   aa = still(100/fs + 0.01);
for i = 1:100
    s = acceptSample(s, t0(i), aa(i,:), [0 0 0]);   s = acceptSample(s, t0(i), aa(i,:), [0 0 0]);
end
s = acceptSample(s, t0(5), aa(5,:), [0 0 0]);
check(s.duplicates == 100 && s.outOfOrder == 1 && s.accepted == 100, 'duplicate/out-of-order samples rejected');
% 4) very short movement is rejected and re-armed (no trajectory)
bump = still(8/fs) + [[3*ones(4,1); -3*ones(4,1)], zeros(8,2)];
s = run_(fs, p, {still(3), bump, still(3)}, true);
check(strcmp(s.state, 'ARMED') && isempty(s.result) && s.rejectedShort == 1, 'short movement rejected');
% 5) stationary after arming: stop before start is ignored; IDLE ignores everything
s = run_(fs, p, {still(8)}, true);   check(strcmp(s.state, 'ARMED') && isempty(s.result), 'stationary stays ARMED');
s = run_(fs, p, {still(3), push(), still(2)}, false);   check(strcmp(s.state, 'IDLE') && s.ignored == s.accepted, 'IDLE ignores samples');
% 6) filter: DC preserved, 24 Hz tone removed
tt = (0:399).' / fs;   y = filterMotionData(sin(2*pi*2*tt) + sin(2*pi*24*tt), fs, 15, 4);
check(std(y - sin(2*pi*2*tt)) < 0.15, 'low-pass attenuates 24 Hz');
% 7) plotting: ONE path line, A/B markers, equal scaling, cm labels, in-place update (no duplicate lines)
if exist('OCTAVE_VERSION', 'builtin') ~= 0, graphics_toolkit('gnuplot'); end
pos = [0 0 0; 10 5 2; 20 15 6; 30 20 4];
for v3 = [true false]
    f = figure('Visible', 'off');   ax = axes('Parent', f);
    plotTrajectoryAB(ax, pos, v3);   plotTrajectoryAB(ax, pos * 2, v3);          % second call must UPDATE, not add
    check(numel(findobj(ax, 'Tag', 'TrajectoryPath')) == 1, sprintf('single path line after 2 calls (3d=%d)', v3));
    check(numel(findobj(ax, 'Tag', 'PointA')) == 1 && numel(findobj(ax, 'Tag', 'PointB')) == 1, 'one marker each for A and B');
    pl = findobj(ax, 'Tag', 'TrajectoryPath');
    check(abs(get(pl, 'XData')(end) - 60) < 1e-9, 'update replaced the data in place');
    check(numel(findobj(ax, 'Tag', 'AxisTriad')) == 6 * v3, sprintf('XYZ axis triad present only in 3D (3d=%d)', v3));
    d = get(ax, 'DataAspectRatio');   check(all(abs(d - d(1)) < 1e-9), 'equal axis scaling');
    check(~isempty(strfind(get(get(ax, 'XLabel'), 'String'), 'cm')), 'axis labelled with cm');
    close(f);
end
fprintf('%d checks passed.\n', nTests);
    function check(cond, msg)
        nTests = nTests + 1;
        if ~cond, error('testTrajectory:fail', 'FAILED: %s', msg); end
        fprintf('  ok  %s\n', msg);
    end
end

function [s, t, a, gy] = run_(fs, p, parts, arm)
a = vertcat(parts{:});   t = 5000 + (0:size(a,1)-1).' * 1000/fs;   gy = zeros(size(a));
s = initializeSession(fs, p);   if arm, s = armSession(s); end
for i = 1:numel(t), s = acceptSample(s, t(i), a(i,:), gy(i,:)); end
end
