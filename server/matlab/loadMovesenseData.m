function data = loadMovesenseData(csvPath, sampleRateHz)
%LOADMOVESENSEDATA Load a raw CSV written by the Python acquisition (units: m/s^2, deg/s, uT, ms).
%   data = loadMovesenseData(csvPath [, sampleRateHz]); the rate defaults to the JSON sidecar.
if exist(csvPath, 'file') ~= 2, error('loadMovesenseData:missing', 'File not found: %s', csvPath); end
cols = {'Timestamp_ms','Accel_X','Accel_Y','Accel_Z','Gyro_X','Gyro_Y','Gyro_Z','Mag_X','Mag_Y','Mag_Z'};
if exist('OCTAVE_VERSION', 'builtin') ~= 0
    m = dlmread(csvPath, ',', 1, 0);          % Octave has no readtable; same columns, fixed order
    m = m(:, 1:10);
else
    T = readtable(csvPath);
    missing = setdiff(cols, T.Properties.VariableNames);
    if ~isempty(missing), error('loadMovesenseData:columns', 'Missing columns: %s', strjoin(missing, ', ')); end
    m = T{:, cols};
end
if nargin < 2 || isempty(sampleRateHz)
    sidecar = [csvPath(1:end-4) '.json'];
    if exist(sidecar, 'file') ~= 2, error('loadMovesenseData:rate', 'Sample rate unknown: pass it or provide %s', sidecar); end
    meta = jsondecode(fileread(sidecar));
    sampleRateHz = meta.sample_rate_hz;
end
data.t_ms = m(:, 1);   data.acc = m(:, 2:4);   data.gyro = m(:, 5:7);   data.mag = m(:, 8:10);
data.fs = double(sampleRateHz);   data.source = csvPath;
if isempty(data.t_ms), error('loadMovesenseData:empty', 'No samples in %s', csvPath); end
end
