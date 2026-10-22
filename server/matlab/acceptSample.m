function s = acceptSample(s, t_ms, acc, gyro)
%ACCEPTSAMPLE Offer one IMU sample to the one-shot state machine (each accepted sample is used once).
%   Mirrors session.py. States: IDLE -> ARMED -> START TRIGGERED -> RECORDING -> STOP TRIGGERED ->
%   COMPLETE -> LOCKED. Start/stop are motion based and latched: they can only fire once per session.
if ~isempty(s.lastTs)                                    % duplicate / out-of-order rejection
    if t_ms == s.lastTs, s.duplicates = s.duplicates + 1; return; end
    if t_ms < s.lastTs,  s.outOfOrder = s.outOfOrder + 1;  return; end
end
s.lastTs = t_ms;   s.accepted = s.accepted + 1;
if any(strcmp(s.state, {'IDLE', 'LOCKED', 'COMPLETE'})), s.ignored = s.ignored + 1; return; end
acc = acc(:).';   gyro = gyro(:).';
gap = ~isempty(s.lastT) && (t_ms - s.lastT) > s.p.max_gap_factor * 1000 / s.fs;
s.lastT = t_ms;
if strcmp(s.state, 'ARMED'), s = onArmed(s, t_ms, acc, gyro, gap);
elseif strcmp(s.state, 'RECORDING'), s = onRecording(s, t_ms, acc, gyro, gap); end
end

function s = onArmed(s, t_ms, acc, gyro, gap)
p = s.p;
if gap
    s.ring = struct('t', zeros(0,1), 'acc', zeros(0,3), 'gyro', zeros(0,3));
    s.baseline = [];   s.startCount = 0;
    s.events{end+1} = 'data gap while ARMED: baseline restarted';
end
s.ring.t(end+1, 1) = t_ms;   s.ring.acc(end+1, :) = acc;   s.ring.gyro(end+1, :) = gyro;
maxLen = s.nBase + s.nConfirm + 2;
if numel(s.ring.t) > maxLen
    s.ring.t = s.ring.t(end-maxLen+1:end);   s.ring.acc = s.ring.acc(end-maxLen+1:end, :);
    s.ring.gyro = s.ring.gyro(end-maxLen+1:end, :);
end
if isempty(s.baseline)
    if numel(s.ring.t) >= s.nBase
        s.baseline = calibrateSensor(s.ring.acc, s.ring.gyro, s.nBase, p);
        if ~isempty(s.baseline), s.events{end+1} = sprintf('baseline locked: g=%.3f m/s^2', s.baseline.gravity); end
    end
    return
end
moving = norm(acc - s.baseline.acc_mean) > p.trig_start_acc_mps2 || norm(gyro - s.baseline.gyro_bias) > p.trig_start_gyro_dps;
if moving, s.startCount = s.startCount + 1; else, s.startCount = 0; end
if s.startCount < s.nConfirm, return; end
s = transition(s, 'START TRIGGERED');                       % latched: only reachable from ARMED
L = numel(s.ring.t);   rest = L - s.nConfirm;               % rest sample = last sample before onset
s.buf.t = s.ring.t(rest:L);   s.buf.acc = s.ring.acc(rest:L, :);   s.buf.gyro = s.ring.gyro(rest:L, :);
s.ring = struct('t', zeros(0,1), 'acc', zeros(0,3), 'gyro', zeros(0,3));
s.events{end+1} = sprintf('START trigger confirmed: Point A at t=%.0f ms', s.buf.t(1));
s = transition(s, 'RECORDING');   s.dataGap = false;
end

function s = onRecording(s, t_ms, acc, gyro, gap)
p = s.p;
s.buf.t(end+1, 1) = t_ms;   s.buf.acc(end+1, :) = acc;   s.buf.gyro(end+1, :) = gyro;
if gap && ~s.dataGap, s.dataGap = true; s.events{end+1} = 'data interruption during RECORDING: flagged data_gap'; end
if ~isempty(p.trig_max_session_s) && (t_ms - s.buf.t(1))/1000 > p.trig_max_session_s
    s.events{end+1} = 'session aborted (no stop trigger before timeout); no Point B created';
    s = clearForAbort(s);  return
end
count = numel(s.buf.t);
if count < s.nStop + 1, return; end
trAcc = s.buf.acc(end-s.nStop+1:end, :);   trGyro = s.buf.gyro(end-s.nStop+1:end, :);
still = max(std(trAcc, 0, 1)) < p.trig_stop_acc_std_mps2 && ...
        max(sqrt(sum((trGyro - s.baseline.gyro_bias).^2, 2))) < p.trig_stop_gyro_dps;
if ~still, return; end
b = count - s.nStop + 1;                                    % 1-based index of Point B
tB = s.buf.t(b);
if (tB - s.buf.t(2))/1000 < p.trig_min_movement_s || (tB - s.buf.t(1))/1000 < p.trig_min_session_s
    s.rejectedShort = s.rejectedShort + 1;
    s.events{end+1} = 'movement too short: rejected, re-arming (no trajectory created)';
    seed = struct('t', s.buf.t(end-s.nStop+1:end), 'acc', trAcc, 'gyro', trGyro);
    s.buf = struct('t', zeros(0,1), 'acc', zeros(0,3), 'gyro', zeros(0,3));
    s.baseline = [];   s.startCount = 0;   s.lastT = [];   s.dataGap = false;   s.ring = seed;
    s = transition(s, 'ARMED');  return
end
s = transition(s, 'STOP TRIGGERED');
s.buf.t = s.buf.t(1:b);   s.buf.acc = s.buf.acc(1:b, :);   s.buf.gyro = s.buf.gyro(1:b, :);   % freeze at Point B
s.events{end+1} = sprintf('STOP trigger confirmed: Point B at t=%.0f ms', tB);
s = finalizeTrajectory(s);
end

function s = clearForAbort(s)
s.ring = struct('t', zeros(0,1), 'acc', zeros(0,3), 'gyro', zeros(0,3));
s.buf = s.ring;   s.baseline = [];   s.startCount = 0;   s.lastT = [];   s.dataGap = false;
s.state = 'IDLE';
end
