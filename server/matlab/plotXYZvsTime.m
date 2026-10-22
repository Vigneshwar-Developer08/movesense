function fig = plotXYZvsTime(r, pngPath, visible)
%PLOTXYZVSTIME X, Y, Z position (cm) versus time since Point A: shows which axis carries motion/drift.
if nargin < 3, visible = 'on'; end
fig = figure('Visible', visible, 'Color', 'w', 'Position', [100 100 900 700]);
cols = {[0.90 0.49 0.13], [0.56 0.27 0.68], [0.17 0.24 0.31]};   names = {'X', 'Y', 'Z'};
for k = 1:3
    ax = subplot(3, 1, k, 'Parent', fig);   hold(ax, 'on');
    plot(ax, r.t_s, r.position_cm(:, k), '-', 'Color', cols{k}, 'LineWidth', 2);
    plot(ax, r.t_s([1 end]), [0 0], ':', 'Color', [0.5 0.5 0.5]);
    ha = plot(ax, r.t_s(1), r.position_cm(1, k), 'o', 'MarkerSize', 9, 'MarkerFaceColor', [0.10 0.55 0.30], 'MarkerEdgeColor', 'w');
    hb = plot(ax, r.t_s(end), r.position_cm(end, k), 'o', 'MarkerSize', 9, 'MarkerFaceColor', [0.84 0.19 0.19], 'MarkerEdgeColor', 'w');
    ylabel(ax, sprintf('%s position (cm)', names{k}));   grid(ax, 'on');   hold(ax, 'off');
    if k == 1, legend(ax, [ha hb], {'Point A', 'Point B'}, 'Location', 'northwest'); title(ax, [r.session_id ': X, Y, Z position relative to Point A'], 'Interpreter', 'none'); end
end
xlabel(ax, 'Time since Point A (s)');
if ~isempty(pngPath), print(fig, pngPath, '-dpng', '-r110'); end
end
