function y = filterMotionData(x, fs, cutoffHz, order)
%FILTERMOTIONDATA Zero-phase Butterworth low-pass (offline). Base MATLAB only (no toolbox);
%   same design, odd padding and initial conditions as scipy.signal.filtfilt for numerical parity.
if ~(cutoffHz > 0 && cutoffHz < fs/2), error('filterMotionData:cutoff', 'Cutoff must be below Nyquist (%g Hz)', fs/2); end
[b, a] = butterLowpass(order, cutoffHz, fs);
padlen = min(3*(order+1), size(x, 1) - 1);
y = zeros(size(x));
for c = 1:size(x, 2), y(:, c) = zeroPhase(b, a, x(:, c), padlen); end
end

function [b, a] = butterLowpass(order, fc, fs)
k = 1:order;
poles = exp(1i*pi*(2*k + order - 1)/(2*order));        % unit-circle prototype, left half-plane
s = 2*fs*tan(pi*fc/fs) * poles;                         % pre-warped cutoff
z = (2*fs + s) ./ (2*fs - s);                           % bilinear transform
a = real(poly(z));   b = real(poly(-ones(1, order)));  % zeros at z = -1
b = b * (sum(a) / sum(b));                              % unity DC gain
end

function y = zeroPhase(b, a, x, padlen)
n = max(numel(a), numel(b));
zi = (eye(n-1) - compan(a).') \ (b(2:end) - a(2:end)*b(1)).';   % steady-state initial conditions
ext = [2*x(1) - x(padlen+1:-1:2); x; 2*x(end) - x(end-1:-1:end-padlen)];
y = filter(b, a, ext, zi*ext(1));
y = filter(b, a, y(end:-1:1), zi*y(end));
y = y(end:-1:1);
y = y(padlen+1:end-padlen);
end
