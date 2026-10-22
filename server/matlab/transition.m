function s = transition(s, newState)
%TRANSITION Enforce the legal state graph; an illegal change is a programming error, never silent.
allowed = struct('IDLE', {{'ARMED'}}, 'ARMED', {{'START TRIGGERED', 'IDLE'}}, ...
    'START_TRIGGERED', {{'RECORDING'}}, 'RECORDING', {{'STOP TRIGGERED', 'ARMED', 'IDLE'}}, ...
    'STOP_TRIGGERED', {{'COMPLETE'}}, 'COMPLETE', {{'LOCKED'}}, 'LOCKED', {{'IDLE'}});
key = strrep(s.state, ' ', '_');
if ~any(strcmp(newState, allowed.(key)))
    error('Session:illegalTransition', 'Illegal transition %s -> %s', s.state, newState);
end
s.state = newState;
end
