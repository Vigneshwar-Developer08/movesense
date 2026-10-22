function [q, worldAcc] = estimateOrientation(acc, accToRotate, gyroDps, dt, accStill, gravity, p)
%ESTIMATEORIENTATION Madgwick IMU filter (acc + gyro), initialised from the still accelerometer vector.
%   q = (n,4) quaternions [w x y z]; worldAcc = accToRotate rotated into the world frame (Z up).
%   Accelerometer correction is applied only when | |a| - g | < acc_gate (impacts / free fall excluded).
n = size(acc, 1);   gyro = gyroDps * pi/180;   q = zeros(n, 4);
a0 = accStill(:).' / norm(accStill);   zUp = [0 0 1];
ax0 = cross(a0, zUp);   s0 = norm(ax0);   c0 = dot(a0, zUp);
if s0 < 1e-9
    if c0 > 0, q0 = [1 0 0 0]; else, q0 = [0 1 0 0]; end
else
    ang = atan2(s0, c0);   q0 = [cos(ang/2), sin(ang/2)*ax0/s0];
end
w = q0(1); x = q0(2); y = q0(3); z = q0(4);
beta = p.madgwick_beta;
for i = 1:n
    gx = gyro(i,1); gy = gyro(i,2); gz = gyro(i,3);
    dw = 0.5*(-x*gx - y*gy - z*gz);   dx = 0.5*(w*gx + y*gz - z*gy);
    dy = 0.5*(w*gy - x*gz + z*gx);    dz = 0.5*(w*gz + x*gy - y*gx);
    ax = acc(i,1); ay = acc(i,2); az = acc(i,3);
    nrm = sqrt(ax*ax + ay*ay + az*az);
    if nrm > 0 && abs(nrm - gravity) < p.acc_gate_mps2
        ax = ax/nrm; ay = ay/nrm; az = az/nrm;
        f1 = 2*(x*z - w*y) - ax;   f2 = 2*(w*x + y*z) - ay;   f3 = 2*(0.5 - x*x - y*y) - az;
        sw = -2*y*f1 + 2*x*f2;
        sx = 2*z*f1 + 2*w*f2 - 4*x*f3;
        sy = -2*w*f1 + 2*z*f2 - 4*y*f3;
        sz = 2*x*f1 + 2*y*f2;
        sn = sqrt(sw*sw + sx*sx + sy*sy + sz*sz);
        if sn > 1e-9
            dw = dw - beta*sw/sn;  dx = dx - beta*sx/sn;  dy = dy - beta*sy/sn;  dz = dz - beta*sz/sn;
        end
    end
    h = dt(i);
    w = w + dw*h;  x = x + dx*h;  y = y + dy*h;  z = z + dz*h;
    qn = sqrt(w*w + x*x + y*y + z*z);
    w = w/qn;  x = x/qn;  y = y/qn;  z = z/qn;
    q(i,:) = [w x y z];
end
w = q(:,1); x = q(:,2); y = q(:,3); z = q(:,4);  u = accToRotate;
worldAcc = [ (1 - 2*(y.^2 + z.^2)).*u(:,1) + 2*(x.*y - w.*z).*u(:,2) + 2*(x.*z + w.*y).*u(:,3), ...
             2*(x.*y + w.*z).*u(:,1) + (1 - 2*(x.^2 + z.^2)).*u(:,2) + 2*(y.*z - w.*x).*u(:,3), ...
             2*(x.*z - w.*y).*u(:,1) + 2*(y.*z + w.*x).*u(:,2) + (1 - 2*(x.^2 + y.^2)).*u(:,3) ];
end
