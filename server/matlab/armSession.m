function s = armSession(s)
%ARMSESSION Explicit IDLE -> ARMED. Ignored in every other state.
if ~strcmp(s.state, 'IDLE')
    s.events{end+1} = sprintf('arm ignored: state is %s', s.state);  return
end
s.state = 'ARMED';
s.events{end+1} = 'ARMED: waiting for a still baseline, then Start Trigger (Point A)';
end
