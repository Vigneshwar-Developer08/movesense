function s = finalizeTrajectory(s)
%FINALIZETRAJECTORY Process the frozen A-to-B window exactly once, then COMPLETE -> LOCKED.
kin = calculateTrajectory(s.buf.t, s.buf.acc, s.buf.gyro, s.baseline, s.fs, s.p);
r = calculateTrajectoryMetrics(kin, s.fs, s.p, s.sessionId);
if s.dataGap && ~any(strcmp(r.flags, 'data_gap')), r.flags{end+1} = 'data_gap'; end
s.result = r;   s.completed{end+1} = r;
s = transition(s, 'COMPLETE');
s.events{end+1} = sprintf('COMPLETE: %s, A->B = %.1f cm, path = %.1f cm', r.session_id, r.straight_line_cm, r.path_length_cm);
s = transition(s, 'LOCKED');
s.events{end+1} = 'LOCKED: session frozen; an explicit resetSession is required for a new session';
end
