function s = resetSession(s, discard)
%RESETSESSION Explicit new-session command. Completed sessions stay in s.completed.
%   An active (ARMED/RECORDING) session is only dropped when discard = true.
if nargin < 2, discard = false; end
if any(strcmp(s.state, {'COMPLETE', 'LOCKED'}))
    s = clearActive_(s);  s.state = 'IDLE';  s.result = [];
    s.events{end+1} = 'reset: IDLE (completed sessions kept)';
elseif strcmp(s.state, 'IDLE')
    return
elseif discard
    s = clearActive_(s);  s.state = 'IDLE';  s.events{end+1} = 'reset: active session discarded';
else
    s.events{end+1} = 'reset refused: active session cannot be finalized without a Stop Trigger';
end
end

function s = clearActive_(s)
s.ring = struct('t', zeros(0,1), 'acc', zeros(0,3), 'gyro', zeros(0,3));
s.buf  = s.ring;   s.baseline = [];   s.startCount = 0;   s.lastT = [];   s.dataGap = false;
end
