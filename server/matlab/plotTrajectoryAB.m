function h = plotTrajectoryAB(ax, posCm, view3d)
%PLOTTRAJECTORYAB One continuous A-to-B path (ONE line object) with Point A / Point B markers.
%   posCm = (n,3) positions in cm, row 1 = Point A. view3d = true for the XYZ view (reference style),
%   false for the XY view. Equal axis scaling, grid, labelled axes (cm), a few direction arrows.
%   If ax already holds a trajectory line (Tag 'TrajectoryPath') its data is UPDATED in place, so
%   calling this again can never create a second, overlapping line.
if nargin < 3, view3d = true; end
blue = [0.12 0.47 0.71];   green = [0.10 0.55 0.30];   red = [0.84 0.19 0.19];
existing = findobj(ax, 'Tag', 'TrajectoryPath');
if ~isempty(existing)
    h.path = existing(1);   h.a = findobj(ax, 'Tag', 'PointA');   h.b = findobj(ax, 'Tag', 'PointB');
    delete(findobj(ax, 'Tag', 'ABDecoration'));   delete(findobj(ax, 'Tag', 'AxisTriad'));
    setData(h.path, posCm, view3d);   setData(h.a, posCm(1, :), view3d);   setData(h.b, posCm(end, :), view3d);
else
    hold(ax, 'on');
    h.path = makeLine(ax, posCm, view3d, '-', 'TrajectoryPath', blue, 3);
    set(h.path, 'LineWidth', 3);
    h.a = makeLine(ax, posCm(1, :), view3d, 'o', 'PointA', green, 1);
    h.b = makeLine(ax, posCm(end, :), view3d, 'o', 'PointB', red, 1);
    set([h.a h.b], 'MarkerSize', 12, 'MarkerEdgeColor', 'w');
    set(h.a, 'MarkerFaceColor', green);   set(h.b, 'MarkerFaceColor', red);
    hold(ax, 'off');
end
hold(ax, 'on');
span = max([range_(posCm(:,1)), range_(posCm(:,2)), range_(posCm(:,3)), 1]);
n = size(posCm, 1);   k = max(2, round(0.04 * n));       % local direction over +-k samples (not sample noise)
for f = [0.25 0.5 0.75]                                   % direction indicators, not per-sample markers
    i = min(max(round(f*(n-1)) + 1, 1), n);   d = posCm(min(i+k, n), :) - posCm(max(i-k, 1), :);
    if ~view3d, d(3) = 0; end
    if norm(d) > 1e-9
        u = 0.07 * span * d / norm(d);   p0 = posCm(i, :) - u/2;
        if view3d
            q = quiver3(ax, p0(1), p0(2), p0(3), u(1), u(2), u(3), 0, 'Color', blue, 'MaxHeadSize', 0.5, 'LineWidth', 1.6);
        else
            q = quiver(ax, p0(1), p0(2), u(1), u(2), 0, 'Color', blue, 'MaxHeadSize', 0.5, 'LineWidth', 1.6);
        end
        set(q, 'Tag', 'ABDecoration');
    end
end
% dotted straight A-B reference line + distance annotation (a guide, NOT the measured path)
straight = norm(posCm(end, :) - posCm(1, :));   mid = (posCm(1, :) + posCm(end, :)) / 2;
if straight > 1e-9
    if view3d
        g = plot3(ax, posCm([1 end], 1), posCm([1 end], 2), posCm([1 end], 3), ':', 'Color', [0.5 0.5 0.5], 'LineWidth', 1);
        t = text(ax, mid(1), mid(2), mid(3), sprintf('  A-B %.1f cm', straight), 'Color', [0.35 0.35 0.35], 'FontSize', 10, 'Interpreter', 'none');
    else
        g = plot(ax, posCm([1 end], 1), posCm([1 end], 2), ':', 'Color', [0.5 0.5 0.5], 'LineWidth', 1);
        t = text(ax, mid(1), mid(2), sprintf('  A-B %.1f cm', straight), 'Color', [0.35 0.35 0.35], 'FontSize', 10, 'Interpreter', 'none');
    end
    set([g t], 'Tag', 'ABDecoration');
end
hold(ax, 'off');
% equal axis scaling: identical span on every axis so the geometry is not distorted
c = (max(posCm, [], 1) + min(posCm, [], 1)) / 2;   half = 0.6 * span;
xlim(ax, c(1) + [-half half]);   ylim(ax, c(2) + [-half half]);
if view3d
    zlim(ax, c(3) + [-half half]);   zlabel(ax, 'Vertical Z (cm)');   view(ax, 3);
    pbaspect(ax, [1 1 1]);   daspect(ax, [1 1 1]);
else
    axis(ax, 'equal');
end
grid(ax, 'on');   xlabel(ax, 'Horizontal X (cm)');   ylabel(ax, 'Horizontal Y (cm)');
legend(ax, [h.a h.b], {'Point A', 'Point B'}, 'Location', 'northwest', 'Interpreter', 'none');
end

function hl = makeLine(ax, pts, view3d, style, tag, color, lw)
if view3d, hl = plot3(ax, pts(:,1), pts(:,2), pts(:,3), style, 'Color', color, 'LineWidth', lw);
else,      hl = plot(ax, pts(:,1), pts(:,2), style, 'Color', color, 'LineWidth', lw); end
set(hl, 'Tag', tag);
end

function setData(hl, pts, view3d)
set(hl, 'XData', pts(:,1), 'YData', pts(:,2));
if view3d, set(hl, 'ZData', pts(:,3)); end
end

function r = range_(x), r = max(x) - min(x); end
