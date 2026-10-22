function baseline = calibrateSensor(acc, gyro, nBase, p)
%CALIBRATESENSOR Baseline from the last nBase samples; [] unless they form a clean still window.
%   Measured gyro bias, mean accelerometer vector and gravity magnitude (never assumed).
acc = acc(end-nBase+1:end, :);   gyro = gyro(end-nBase+1:end, :);
nrm = sqrt(sum(acc.^2, 2));
gyroStd = max(std(gyro, 0, 1));
if std(nrm) < p.calib_max_acc_std_mps2 && gyroStd < p.calib_max_gyro_std_dps ...
        && max(abs(mean(gyro, 1))) < p.calib_max_gyro_bias_dps ...
        && abs(mean(nrm) - p.standard_gravity) < p.calib_gravity_tol_mps2
    baseline.acc_mean = mean(acc, 1);   baseline.gyro_bias = mean(gyro, 1);
    baseline.gravity = mean(nrm);       baseline.acc_std = std(nrm);   baseline.gyro_std = gyroStd;
else
    baseline = [];
end
end
