function fig = createTrajectoryDashboard(r, summary, pngPath, view, visible, statusMsg)
%CREATETRAJECTORYDASHBOARD Reference-style dashboard: trajectory (left) + live-style readout (right).
%   view = '3d' (default, like the reference screenshot) or 'xy'. All lengths in cm.
%   r = result struct (calculateTrajectoryMetrics), summary = struct with validation_status.
if nargin < 4 || isempty(view), view = '3d'; end
if nargin < 5 || isempty(visible), visible = 'on'; end
if nargin < 6 || isempty(statusMsg), statusMsg = 'Stop trigger confirmed at Point B'; end
fig = figure('Visible', visible, 'Color', 'w', 'Position', [80 80 1150 640], 'Name', 'Movesense trajectory');
ax = axes('Parent', fig, 'Position', [0.05 0.09 0.55 0.80]);
plotTrajectoryAB(ax, r.position_cm, strcmpi(view, '3d'));
title(ax, 'Reconstructed A to B trajectory', 'FontWeight', 'normal', 'Interpreter', 'none');

pn = axes('Parent', fig, 'Position', [0.64 0.04 0.34 0.92], 'XLim', [0 1], 'YLim', [0 1]);   axis(pn, 'off');
gray = [0.38 0.40 0.47];   hasFlags = ~isempty(r.flags);
if hasFlags, statusColor = [0.80 0.45 0.05]; else, statusColor = [0.10 0.55 0.30]; end
put = @(y, str, sz, wt, col) text(pn, 0, y, str, 'FontSize', sz, 'FontWeight', wt, 'Color', col, 'Interpreter', 'none', 'VerticalAlignment', 'top');
put(0.99, 'MOVESENSE TRAJECTORY ANALYSIS', 15, 'bold', [0 0 0]);
put(0.90, 'Session COMPLETE  |  LOCKED', 15, 'normal', statusColor);
put(0.85, r.session_id, 10, 'normal', gray);
put(0.74, 'Straight-line A to B', 10.5, 'normal', gray);
put(0.70, sprintf('%.1f cm', r.straight_line_cm), 34, 'bold', [0 0 0]);
put(0.55, 'Trajectory length', 10.5, 'normal', gray);
put(0.51, sprintf('%.1f cm', r.path_length_cm), 20, 'normal', [0 0 0]);
put(0.41, 'Peak height', 10.5, 'normal', gray);
put(0.37, sprintf('%.1f cm', r.peak_height_cm), 20, 'normal', [0 0 0]);
put(0.27, sprintf('Displacement  X %.1f   Y %.1f   Z %.1f cm', r.displacement_cm), 10, 'normal', [0 0 0]);
put(0.23, sprintf('Duration %.2f s   |   Max speed %.2f m/s', r.duration_s, r.max_speed_mps), 10, 'normal', [0 0 0]);
put(0.19, sprintf('Point B (%.1f, %.1f, %.1f) cm   |   Closure |v(B)| %.2f m/s', r.point_b_cm, r.closure_error_mps), 9, 'normal', gray);
put(0.12, statusMsg, 16, 'normal', gray);
if hasFlags, flagCol = [0.76 0.07 0.12]; flagStr = strjoin(r.flags, ', '); else, flagCol = [0.10 0.55 0.30]; flagStr = 'none'; end
put(0.055, ['Quality flags: ' flagStr], 10, 'normal', flagCol);
if strcmp(summary.validation_status, 'UNVALIDATED'), vCol = [0.76 0.07 0.12]; else, vCol = [0.10 0.55 0.30]; end
put(0.02, ['Status: ' summary.validation_status], 10, 'bold', vCol);
if ~isempty(pngPath), print(fig, pngPath, '-dpng', '-r110'); end
end
