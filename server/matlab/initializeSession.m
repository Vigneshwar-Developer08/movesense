function s = initializeSession(fs, p)
%INITIALIZESESSION New session in state IDLE with empty buffers (one session = one A-to-B trajectory).
s.fs = fs;   s.p = p;   s.state = 'IDLE';   s.sessionId = 'MATLAB';
s.nBase = max(2, floor(p.calib_window_s*fs + 0.5));      % floor(x+0.5) == Python n_samples()
s.nConfirm = max(2, floor(p.trig_start_confirm_s*fs + 0.5));
s.nStop = max(3, floor(p.trig_stop_confirm_s*fs + 0.5));
s.lastTs = [];   s.duplicates = 0;   s.outOfOrder = 0;   s.accepted = 0;   s.ignored = 0;   s.rejectedShort = 0;
s.events = {};   s.result = [];   s.completed = {};   s.baseline = [];
s = clearActive(s);
end

function s = clearActive(s)
s.ring = struct('t', zeros(0,1), 'acc', zeros(0,3), 'gyro', zeros(0,3));
s.buf  = struct('t', zeros(0,1), 'acc', zeros(0,3), 'gyro', zeros(0,3));
s.baseline = [];   s.startCount = 0;   s.lastT = [];   s.dataGap = false;
end
