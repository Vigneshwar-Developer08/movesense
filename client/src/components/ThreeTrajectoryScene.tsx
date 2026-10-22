"use client";

import React, { useEffect, useRef, useState, useCallback } from "react";
import * as THREE from "three";
import { UnitType } from "./Header";

interface ThreeTrajectorySceneProps {
  unit: UnitType;
  unitMultiplier: number;
  onPositionUpdate?: (pos: { x: number; y: number; z: number }) => void;
  isStreaming?: boolean;
  /** Live path from GET /api/tracking/{id}/trajectory (metres, backend Z-up). */
  livePoints?: Array<[number, number, number]>;
}

/** Backend is Z-up (X/Y horizontal, Z vertical); Three.js is Y-up. */
function toScenePoint(p: [number, number, number]): THREE.Vector3 {
  return new THREE.Vector3(p[0], p[2], -p[1]);
}

/** Camera framing limits in metres (the camera far plane is 500 m). */
const MIN_CAMERA_RADIUS = 1.5;
const MAX_CAMERA_RADIUS = 400;
const DEFAULT_CAMERA_RADIUS = 4.8;
/** Re-frame when the live path grows this much beyond the currently framed extent. */
const REFIT_GROWTH_FACTOR = 1.4;

function clampCameraRadius(radius: number): number {
  return Math.min(MAX_CAMERA_RADIUS, Math.max(MIN_CAMERA_RADIUS, radius));
}

/** Drop non-finite values and consecutive duplicates before building meshes. */
function sanitizePath(
  points?: Array<[number, number, number]>,
): Array<[number, number, number]> {
  return (points ?? [])
    .filter(
      (point) =>
        Array.isArray(point) &&
        point.length >= 3 &&
        point.every((value) => Number.isFinite(value)),
    )
    .filter(
      (point, index, all) =>
        index === 0 ||
        point[0] !== all[index - 1][0] ||
        point[1] !== all[index - 1][1] ||
        point[2] !== all[index - 1][2],
    );
}

export function ThreeTrajectoryScene({
  unit,
  unitMultiplier,
  onPositionUpdate,
  isStreaming = true,
  livePoints,
}: ThreeTrajectorySceneProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [activeView, setActiveView] = useState<"persp" | "xy" | "xz" | "yz">("persp");
  const [currentCoords, setCurrentCoords] = useState({ x: 0, y: 0, z: 0 });
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const sphericalRef = useRef({ radius: DEFAULT_CAMERA_RADIUS, theta: 0.85, phi: 1.15 });
  const centerTargetRef = useRef(new THREE.Vector3(0, 0, 0));
  const sceneRef = useRef<THREE.Scene | null>(null);
  const tubeRef = useRef<THREE.Mesh | null>(null);
  const lineRef = useRef<THREE.Line | null>(null);
  const projRef = useRef<THREE.Mesh | null>(null);
  const headRef = useRef<THREE.Group | null>(null);
  const livePointsRef = useRef<Array<[number, number, number]>>([]);
  const fittedExtentRef = useRef(0);
  const isStreamingRef = useRef(isStreaming);
  const onPositionUpdateRef = useRef(onPositionUpdate);
  const lastHudUpdateRef = useRef(0);

  useEffect(() => {
    const clean = sanitizePath(livePoints);
    livePointsRef.current = clean.length >= 2 ? clean : [];
  }, [livePoints]);

  useEffect(() => {
    isStreamingRef.current = isStreaming;
  }, [isStreaming]);

  useEffect(() => {
    onPositionUpdateRef.current = onPositionUpdate;
  }, [onPositionUpdate]);

  const updateCameraPosition = useCallback(() => {
    if (!cameraRef.current) return;
    const { radius, theta, phi } = sphericalRef.current;
    const center = centerTargetRef.current;

    cameraRef.current.position.x =
      center.x + radius * Math.sin(phi) * Math.sin(theta);
    cameraRef.current.position.y = center.y + radius * Math.cos(phi);
    cameraRef.current.position.z =
      center.z + radius * Math.sin(phi) * Math.cos(theta);
    cameraRef.current.lookAt(center);
  }, []);

  // View Presets
  const setCameraPreset = useCallback(
    (view: "persp" | "xy" | "xz" | "yz") => {
      setActiveView(view);
      if (view === "persp") {
        sphericalRef.current = { radius: DEFAULT_CAMERA_RADIUS, theta: 0.85, phi: 1.15 };
      } else if (view === "xy") {
        // Top view looking down
        sphericalRef.current = { radius: 4.5, theta: 0.001, phi: 0.001 };
      } else if (view === "xz") {
        // Front view
        sphericalRef.current = { radius: 4.5, theta: 0.0, phi: Math.PI / 2 };
      } else if (view === "yz") {
        // Side view
        sphericalRef.current = { radius: 4.5, theta: Math.PI / 2, phi: Math.PI / 2 };
      }
      updateCameraPosition();
    },
    [updateCameraPosition]
  );

  const resetCamera = useCallback(() => {
    setCameraPreset("persp");
  }, [setCameraPreset]);

  const fitTrajectory = useCallback(() => {
    const points = livePointsRef.current.map(toScenePoint);
    if (points.length === 0) {
      centerTargetRef.current.set(0, 0, 0);
      sphericalRef.current.radius = DEFAULT_CAMERA_RADIUS;
      fittedExtentRef.current = 0;
    } else {
      const box = new THREE.Box3().setFromPoints(points);
      const size = box.getSize(new THREE.Vector3());
      centerTargetRef.current.copy(box.getCenter(new THREE.Vector3()));
      const extent = Math.max(size.x, size.y, size.z, 0.2);
      sphericalRef.current.radius = clampCameraRadius(extent * 2.2);
      fittedExtentRef.current = extent;
    }
    updateCameraPosition();
  }, [updateCameraPosition]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const width = container.clientWidth || 800;
    const height = container.clientHeight || 520;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xf8fafc);

    const camera = new THREE.PerspectiveCamera(45, width / height, 0.05, 500);
    cameraRef.current = camera;
    updateCameraPosition();

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;

    // Clear any previous canvas
    while (container.firstChild) {
      container.removeChild(container.firstChild);
    }
    container.appendChild(renderer.domElement);

    // Lighting
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.95);
    scene.add(ambientLight);

    const sunLight = new THREE.DirectionalLight(0xffffff, 0.85);
    sunLight.position.set(6, 12, 8);
    sunLight.castShadow = true;
    scene.add(sunLight);

    const fillLight = new THREE.DirectionalLight(0x2563eb, 0.3);
    fillLight.position.set(-6, -2, -6);
    scene.add(fillLight);

    // Ground Grid (XZ Plane)
    const gridHelper = new THREE.GridHelper(4.0, 20, 0xcbd5e1, 0xe2e8f0);
    gridHelper.position.y = -0.5;
    scene.add(gridHelper);

    // Cartesian Triad / XYZ Reference Axes
    const axesGroup = new THREE.Group();
    axesGroup.position.set(-1.8, -0.5, -1.8);

    function createAxisVector(dir: THREE.Vector3, colorHex: number, length = 0.45) {
      const group = new THREE.Group();
      const shaftGeo = new THREE.CylinderGeometry(0.008, 0.008, length, 16);
      const headGeo = new THREE.ConeGeometry(0.026, 0.07, 16);
      const mat = new THREE.MeshStandardMaterial({
        color: colorHex,
        roughness: 0.3,
      });

      const shaft = new THREE.Mesh(shaftGeo, mat);
      shaft.position.y = length / 2;
      const head = new THREE.Mesh(headGeo, mat);
      head.position.y = length + 0.035;

      group.add(shaft);
      group.add(head);

      if (dir.x !== 0) {
        group.rotation.z = (-Math.PI / 2) * Math.sign(dir.x);
      } else if (dir.z !== 0) {
        group.rotation.x = (Math.PI / 2) * Math.sign(dir.z);
      }
      return group;
    }

    const xAxis = createAxisVector(new THREE.Vector3(1, 0, 0), 0xd97706, 0.4); // Amber X
    const yAxis = createAxisVector(new THREE.Vector3(0, 1, 0), 0x16a34a, 0.4); // Green Y
    const zAxis = createAxisVector(new THREE.Vector3(0, 0, 1), 0x2563eb, 0.4); // Blue Z
    axesGroup.add(xAxis);
    axesGroup.add(yAxis);
    axesGroup.add(zAxis);
    scene.add(axesGroup);

    // Origin Point A Reticle & Sphere
    const originGroup = new THREE.Group();
    originGroup.position.set(0, 0, 0);

    const originCoreGeo = new THREE.SphereGeometry(0.035, 24, 24);
    const originCoreMat = new THREE.MeshStandardMaterial({
      color: 0x16a34a,
      roughness: 0.2,
      emissive: 0x16a34a,
      emissiveIntensity: 0.2,
    });
    const originCore = new THREE.Mesh(originCoreGeo, originCoreMat);
    originGroup.add(originCore);

    const ringGeo = new THREE.RingGeometry(0.055, 0.075, 32);
    const ringMat = new THREE.MeshBasicMaterial({
      color: 0x16a34a,
      side: THREE.DoubleSide,
    });
    const ringMesh = new THREE.Mesh(ringGeo, ringMat);
    ringMesh.rotation.x = Math.PI / 2;
    originGroup.add(ringMesh);
    scene.add(originGroup);

    // Trajectory Tube (empty until the backend delivers a live path)
    const tubeMaterial = new THREE.MeshStandardMaterial({
      color: 0x2563eb,
      roughness: 0.25,
      metalness: 0.2,
    });
    const trajectoryTube = new THREE.Mesh(new THREE.BufferGeometry(), tubeMaterial);
    trajectoryTube.name = "trajectoryTube";
    trajectoryTube.visible = false;
    scene.add(trajectoryTube);
    sceneRef.current = scene;
    tubeRef.current = trajectoryTube;

    // Thin overlay line: keeps the path visible even for a 2-point preview or
    // if a tube segment degenerates. Depth test is off so it is never hidden.
    const trajectoryLine = new THREE.Line(
      new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ color: 0x2563eb, depthTest: false }),
    );
    trajectoryLine.name = "trajectoryLine";
    trajectoryLine.visible = false;
    trajectoryLine.frustumCulled = false;
    trajectoryLine.renderOrder = 3;
    scene.add(trajectoryLine);
    lineRef.current = trajectoryLine;

    // Trajectory Ground Shadow Projection
    const projMat = new THREE.MeshBasicMaterial({
      color: 0x94a3b8,
      transparent: true,
      opacity: 0.45,
    });
    const projMesh = new THREE.Mesh(new THREE.BufferGeometry(), projMat);
    projMesh.name = "trajectoryProjection";
    projMesh.visible = false;
    scene.add(projMesh);
    projRef.current = projMesh;

    // Movesense Sensor Puck Node
    const sensorHeadGroup = new THREE.Group();
    sensorHeadGroup.visible = false;

    // Cylindrical Puck Body
    const puckGeo = new THREE.CylinderGeometry(0.068, 0.068, 0.024, 32);
    const puckMat = new THREE.MeshStandardMaterial({
      color: 0x1e293b,
      roughness: 0.35,
      metalness: 0.4,
    });
    const puckMesh = new THREE.Mesh(puckGeo, puckMat);
    sensorHeadGroup.add(puckMesh);

    // Blue Active Ring LED
    const ledGeo = new THREE.TorusGeometry(0.052, 0.006, 16, 32);
    const ledMat = new THREE.MeshBasicMaterial({ color: 0x3b82f6 });
    const ledMesh = new THREE.Mesh(ledGeo, ledMat);
    ledMesh.rotation.x = Math.PI / 2;
    ledMesh.position.y = 0.013;
    sensorHeadGroup.add(ledMesh);

    // Velocity Vector Pointer
    const velDir = new THREE.Vector3(0.4, 0.08, -0.15).normalize();
    const velArrow = new THREE.ArrowHelper(
      velDir,
      new THREE.Vector3(0, 0, 0),
      0.28,
      0x2563eb,
      0.07,
      0.035
    );
    sensorHeadGroup.add(velArrow);

    // Pulsing Halo
    const haloGeo = new THREE.SphereGeometry(0.12, 16, 16);
    const haloMat = new THREE.MeshBasicMaterial({
      color: 0x2563eb,
      wireframe: true,
      transparent: true,
      opacity: 0.35,
    });
    const haloMesh = new THREE.Mesh(haloGeo, haloMat);
    sensorHeadGroup.add(haloMesh);

    scene.add(sensorHeadGroup);
    headRef.current = sensorHeadGroup;

    // Drag Orbit Controls
    let isDragging = false;
    let previousMousePosition = { x: 0, y: 0 };

    const onMouseDown = (e: MouseEvent) => {
      isDragging = true;
      previousMousePosition = { x: e.clientX, y: e.clientY };
    };

    const onMouseUp = () => {
      isDragging = false;
    };

    const onMouseMove = (e: MouseEvent) => {
      if (!isDragging) return;
      const deltaX = e.clientX - previousMousePosition.x;
      const deltaY = e.clientY - previousMousePosition.y;

      sphericalRef.current.theta -= deltaX * 0.008;
      sphericalRef.current.phi = Math.max(
        0.05,
        Math.min(Math.PI / 2 - 0.02, sphericalRef.current.phi - deltaY * 0.008)
      );

      updateCameraPosition();
      previousMousePosition = { x: e.clientX, y: e.clientY };
    };

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      sphericalRef.current.radius = clampCameraRadius(
        sphericalRef.current.radius + e.deltaY * 0.004
      );
      updateCameraPosition();
    };

    // Touch controls for mobile / tablet
    let initialTouchDist = 0;
    const onTouchStart = (e: TouchEvent) => {
      if (e.touches.length === 1) {
        isDragging = true;
        previousMousePosition = {
          x: e.touches[0].clientX,
          y: e.touches[0].clientY,
        };
      } else if (e.touches.length === 2) {
        isDragging = false;
        initialTouchDist = Math.hypot(
          e.touches[0].clientX - e.touches[1].clientX,
          e.touches[0].clientY - e.touches[1].clientY
        );
      }
    };

    const onTouchMove = (e: TouchEvent) => {
      if (e.touches.length === 1 && isDragging) {
        const deltaX = e.touches[0].clientX - previousMousePosition.x;
        const deltaY = e.touches[0].clientY - previousMousePosition.y;

        sphericalRef.current.theta -= deltaX * 0.008;
        sphericalRef.current.phi = Math.max(
          0.05,
          Math.min(Math.PI / 2 - 0.02, sphericalRef.current.phi - deltaY * 0.008)
        );

        updateCameraPosition();
        previousMousePosition = {
          x: e.touches[0].clientX,
          y: e.touches[0].clientY,
        };
      } else if (e.touches.length === 2 && initialTouchDist > 0) {
        const dist = Math.hypot(
          e.touches[0].clientX - e.touches[1].clientX,
          e.touches[0].clientY - e.touches[1].clientY
        );
        const deltaDist = initialTouchDist - dist;
        sphericalRef.current.radius = clampCameraRadius(
          sphericalRef.current.radius + deltaDist * 0.01
        );
        initialTouchDist = dist;
        updateCameraPosition();
      }
    };

    const onTouchEnd = () => {
      isDragging = false;
      initialTouchDist = 0;
    };

    container.addEventListener("mousedown", onMouseDown);
    window.addEventListener("mouseup", onMouseUp);
    window.addEventListener("mousemove", onMouseMove);
    container.addEventListener("wheel", onWheel, { passive: false });

    container.addEventListener("touchstart", onTouchStart, { passive: true });
    container.addEventListener("touchmove", onTouchMove, { passive: true });
    container.addEventListener("touchend", onTouchEnd);

    // Resize Observer
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width: newWidth, height: newHeight } = entry.contentRect;
        if (newWidth > 0 && newHeight > 0) {
          camera.aspect = newWidth / newHeight;
          camera.updateProjectionMatrix();
          renderer.setSize(newWidth, newHeight);
        }
      }
    });
    resizeObserver.observe(container);

    // Animation Loop
    let animationFrameId: number;
    const clock = new THREE.Clock();

    const animate = () => {
      animationFrameId = requestAnimationFrame(animate);
      const elapsedTime = clock.getElapsedTime();

      // Halo breathing pulse only while the backend stream is running.
      if (isStreamingRef.current) {
        haloMesh.scale.setScalar(1.0 + 0.12 * Math.sin(elapsedTime * 4.0));
        haloMat.opacity = 0.25 + 0.15 * Math.sin(elapsedTime * 4.0);
      } else {
        haloMesh.scale.setScalar(1.0);
        haloMat.opacity = 0.15;
      }

      const live = livePointsRef.current;
      if (live.length >= 1) {
        const raw = live[live.length - 1];
        const scenePoint = toScenePoint(raw);
        sensorHeadGroup.visible = true;
        sensorHeadGroup.position.copy(scenePoint);

        // Throttle React state updates instead of re-rendering every frame.
        // Report scene coordinates so the HUD numbers match the drawn axes.
        if (elapsedTime - lastHudUpdateRef.current > 0.1) {
          lastHudUpdateRef.current = elapsedTime;
          const next = { x: scenePoint.x, y: scenePoint.y, z: scenePoint.z };
          setCurrentCoords(next);
          onPositionUpdateRef.current?.(next);
        }
      } else {
        sensorHeadGroup.visible = false;
      }

      renderer.render(scene, camera);
    };

    animate();

    return () => {
      cancelAnimationFrame(animationFrameId);
      container.removeEventListener("mousedown", onMouseDown);
      window.removeEventListener("mouseup", onMouseUp);
      window.removeEventListener("mousemove", onMouseMove);
      container.removeEventListener("wheel", onWheel);
      container.removeEventListener("touchstart", onTouchStart);
      container.removeEventListener("touchmove", onTouchMove);
      container.removeEventListener("touchend", onTouchEnd);
      resizeObserver.disconnect();

      if (container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement);
      }
      renderer.dispose();
    };
  }, [updateCameraPosition]);

  // Rebuild the path meshes when the backend delivers a live path.
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    const raw = sanitizePath(livePoints);
    if (raw.length < 2) {
      if (tubeRef.current) tubeRef.current.visible = false;
      if (lineRef.current) lineRef.current.visible = false;
      if (projRef.current) projRef.current.visible = false;
      if (headRef.current) headRef.current.visible = false;
      return;
    }
    const pts = raw.map(toScenePoint);
    if (lineRef.current) {
      lineRef.current.geometry.dispose();
      lineRef.current.geometry = new THREE.BufferGeometry().setFromPoints(pts);
      lineRef.current.visible = true;
    }
    if (pts.length >= 3) {
      const curve = new THREE.CatmullRomCurve3(pts);
      const tubeGeo = new THREE.TubeGeometry(curve, 120, 0.013, 16, false);
      const projPts = pts.map((p) => new THREE.Vector3(p.x, -0.495, p.z));
      const projCurve = new THREE.CatmullRomCurve3(projPts);
      const projGeo = new THREE.TubeGeometry(projCurve, 120, 0.005, 8, false);
      if (tubeRef.current) {
        tubeRef.current.geometry.dispose();
        tubeRef.current.geometry = tubeGeo;
        tubeRef.current.visible = true;
      }
      if (projRef.current) {
        projRef.current.geometry.dispose();
        projRef.current.geometry = projGeo;
        projRef.current.visible = true;
      }
    } else {
      if (tubeRef.current) tubeRef.current.visible = false;
      if (projRef.current) projRef.current.visible = false;
    }
    const tip = raw[raw.length - 1];
    if (headRef.current) {
      headRef.current.position.copy(toScenePoint(tip));
      headRef.current.visible = true;
    }
  }, [livePoints]);

  // Keep the path framed as it grows. The old code fitted once, on the first
  // two points, so a real path quickly left the viewport and looked static.
  useEffect(() => {
    const raw = sanitizePath(livePoints);
    if (raw.length < 2) {
      fittedExtentRef.current = 0; // a new run is framed from scratch
      return;
    }
    const pts = raw.map(toScenePoint);
    const box = new THREE.Box3().setFromPoints(pts);
    const size = box.getSize(new THREE.Vector3());
    const extent = Math.max(size.x, size.y, size.z, 0.2);
    const neededRadius = clampCameraRadius(extent * 2.2);
    const firstFrame = fittedExtentRef.current === 0;
    const grewPastFrame = extent > fittedExtentRef.current * REFIT_GROWTH_FACTOR;
    if (firstFrame || (grewPastFrame && neededRadius > sphericalRef.current.radius)) {
      centerTargetRef.current.copy(box.getCenter(new THREE.Vector3()));
      // Auto-framing only zooms out, so a deliberate zoom-in is not undone.
      sphericalRef.current.radius = firstFrame
        ? Math.max(DEFAULT_CAMERA_RADIUS, neededRadius)
        : Math.max(sphericalRef.current.radius, neededRadius);
      fittedExtentRef.current = extent;
      updateCameraPosition();
    }
  }, [livePoints, updateCameraPosition]);

  // Formatted display values according to unit
  const formatCoord = (val: number) => {
    const converted = val * unitMultiplier;
    return (converted >= 0 ? "+" : "") + converted.toFixed(3);
  };

  const hasPath = sanitizePath(livePoints).length >= 2;

  return (
    <div className="flex flex-col gap-space-md bg-surface-container-lowest p-space-md sm:p-space-lg rounded-xl shadow-xs border border-outline-variant/30">
      {/* 3D Header Toolbar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-space-sm pb-space-xs border-b border-outline-variant/20">
        <div className="flex items-center gap-space-sm flex-wrap">
          <div className="flex items-center gap-1.5">
            <span className="material-symbols-outlined text-primary text-[20px]">
              view_in_ar
            </span>
            <span className="font-headline-sm text-headline-sm text-on-surface">
              Estimated 3D Trajectory
            </span>
          </div>

          <div className="group relative flex items-center cursor-help">
            <span className="material-symbols-outlined text-secondary text-[16px]">
              info
            </span>
            <div className="absolute left-0 bottom-full mb-1 hidden group-hover:block w-72 p-space-sm rounded-lg bg-inverse-surface text-inverse-on-surface shadow-xl z-30 font-body-sm text-body-sm">
              Position and distance are estimated from inertial sensor
              measurements via double integration. Errors may accumulate over
              time. Zero-velocity updates (ZUPT) apply during stance. World
              axes: X/Y horizontal, Z up; the 3D view maps Z to its vertical
              (Y) axis.
            </div>
          </div>
        </div>

        {/* 3D Camera Controls */}
        <div className="flex items-center gap-space-xs flex-wrap">
          <div className="flex items-center bg-surface-container-low rounded-lg p-0.5 border border-outline-variant/30">
            {(
              [
                { id: "persp", label: "Persp" },
                { id: "xy", label: "XY Top" },
                { id: "xz", label: "XZ Front" },
                { id: "yz", label: "YZ Side" },
              ] as const
            ).map((view) => (
              <button
                key={view.id}
                onClick={() => setCameraPreset(view.id)}
                className={`px-2 py-1 rounded font-label-sm text-label-sm transition-all cursor-pointer ${
                  activeView === view.id
                    ? "font-semibold text-primary bg-surface-container-lowest shadow-xs"
                    : "text-secondary hover:text-on-surface hover:bg-surface-container-high"
                }`}
                type="button"
              >
                {view.label}
              </button>
            ))}
          </div>

          <button
            onClick={fitTrajectory}
            className="p-1.5 rounded-lg bg-surface-container-low text-on-surface hover:bg-surface-container hover:text-primary transition-colors cursor-pointer border border-outline-variant/20"
            title="Fit Trajectory"
            type="button"
          >
            <span className="material-symbols-outlined text-[16px] block">
              crop_free
            </span>
          </button>

          <button
            onClick={resetCamera}
            className="p-1.5 rounded-lg bg-surface-container-low text-on-surface hover:bg-surface-container hover:text-primary transition-colors cursor-pointer border border-outline-variant/20"
            title="Reset Camera"
            type="button"
          >
            <span className="material-symbols-outlined text-[16px] block">
              restart_alt
            </span>
          </button>
        </div>
      </div>

      {/* 3D Canvas Viewport */}
      <div className="relative w-full h-[460px] sm:h-[520px] rounded-lg overflow-hidden bg-surface-container-low shadow-inner border border-outline-variant/30 select-none">
        <div
          ref={containerRef}
          className="absolute inset-0 w-full h-full cursor-grab active:cursor-grabbing"
        />

        {/* Dynamic Overlay Gizmo / Spatial Compass */}
        {/* Scene axes (Three.js Y-up): X and Z are horizontal, Y is vertical. */}
        <div className="absolute top-space-md left-space-md p-space-sm rounded-lg bg-surface-container-lowest/90 backdrop-blur-sm shadow-xs border border-outline-variant/20 flex flex-col gap-1 pointer-events-none">
          <div className="flex items-center gap-2 font-label-sm text-label-sm">
            <span className="w-2.5 h-1 rounded-full bg-error"></span>
            <span className="font-mono text-on-surface">X: Horizontal</span>
          </div>
          <div className="flex items-center gap-2 font-label-sm text-label-sm">
            <span className="w-2.5 h-1 rounded-full bg-tertiary"></span>
            <span className="font-mono text-on-surface">Y: Vertical (up)</span>
          </div>
          <div className="flex items-center gap-2 font-label-sm text-label-sm">
            <span className="w-2.5 h-1 rounded-full bg-primary"></span>
            <span className="font-mono text-on-surface">
              Z: Horizontal (depth)
            </span>
          </div>
        </div>

        {/* Floating Live Coords HUD (scene coordinates: Y is up) */}
        <div className="absolute top-space-md right-space-md px-space-md py-1.5 rounded-lg bg-surface-container-lowest/95 backdrop-blur-sm shadow-xs border border-outline-variant/20 flex items-center gap-space-sm sm:gap-space-md pointer-events-none">
          <span className="font-label-sm text-label-sm uppercase tracking-wider text-secondary font-semibold hidden sm:inline">
            Scene · Y up
          </span>
          <div className="flex items-center gap-space-xs sm:gap-space-sm font-mono text-xs sm:text-body-sm font-medium text-on-surface">
            <span>
              X:{" "}
              <span className="text-error font-semibold">
                {formatCoord(currentCoords.x)}
              </span>{" "}
              {unit}
            </span>
            <span className="text-secondary">•</span>
            <span>
              Y:{" "}
              <span className="text-tertiary font-semibold">
                {formatCoord(currentCoords.y)}
              </span>{" "}
              {unit}
            </span>
            <span className="text-secondary">•</span>
            <span>
              Z:{" "}
              <span className="text-primary font-semibold">
                {formatCoord(currentCoords.z)}
              </span>{" "}
              {unit}
            </span>
          </div>
        </div>

        {!hasPath && (
          <div className="absolute inset-0 flex items-center justify-center px-space-md pointer-events-none">
            <div className="max-w-sm text-center px-space-md py-space-sm rounded-xl bg-surface-container-lowest/90 backdrop-blur-sm border border-outline-variant/30 shadow-xs">
              <span className="material-symbols-outlined text-primary text-[28px]">
                route
              </span>
              <p className="font-label-md text-label-md text-on-surface font-semibold mt-1">
                {isStreaming
                  ? "Waiting for an A→B movement"
                  : "No trajectory yet"}
              </p>
              <p className="font-body-sm text-body-sm text-secondary mt-1">
                {isStreaming
                  ? "Hold the sensor still for the baseline, then move. The path appears when recording starts."
                  : "Start tracking, then perform a still → move → still sequence."}
              </p>
            </div>
          </div>
        )}

        {/* Scale Calibration Cue */}
        <div className="absolute bottom-space-md right-space-md px-space-sm py-1 rounded bg-surface-container-lowest/85 backdrop-blur-sm text-[10px] font-mono text-secondary pointer-events-none flex items-center gap-2 border border-outline-variant/20">
          <span>Grid unit: {(0.2 * unitMultiplier).toFixed(2)} {unit}</span>
          <div className="w-12 h-1 bg-secondary/50 rounded-full"></div>
        </div>
      </div>

      {/* Trajectory Legend & Spatial Details */}
      <div className="flex flex-wrap items-center justify-between gap-space-md pt-space-xs text-body-sm">
        <div className="flex items-center gap-space-md flex-wrap">
          <div className="flex items-center gap-1.5 font-label-sm text-label-sm text-on-surface">
            <span className="w-3 h-1 rounded-full bg-primary"></span>
            <span>Estimated Path (Movesense 9-DoF)</span>
          </div>
          <div className="flex items-center gap-1.5 font-label-sm text-label-sm text-on-surface">
            <span className="w-2.5 h-2.5 rounded-full bg-tertiary"></span>
            <span>Point A Origin [0, 0, 0]</span>
          </div>
          <div className="flex items-center gap-1.5 font-label-sm text-label-sm text-on-surface">
            <span className="w-3 h-3 rounded-full bg-on-surface flex items-center justify-center text-[8px] text-surface-container-lowest font-bold">
              ▲
            </span>
            <span>Current Pose &amp; Velocity Vector</span>
          </div>
        </div>
        <div className="flex items-center gap-space-xs font-label-sm text-label-sm text-secondary">
          <span className="material-symbols-outlined text-[15px]">
            rotate_90_degrees_ccw
          </span>
          <span>Click &amp; drag to orbit • Scroll to zoom</span>
        </div>
      </div>
    </div>
  );
}
