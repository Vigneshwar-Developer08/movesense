"use client";

import React, { useEffect, useState } from "react";

import {
  apiErrorMessage,
  apiFetch,
  downloadArtifact,
  type ArtifactFormat,
} from "@/lib/api";

interface ExportSession {
  session_id: string;
  motion: string;
  movement_duration_s: number | null;
}

interface SessionListResponse {
  sessions: ExportSession[];
  count: number;
}

interface ExportModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** Preselect a session (e.g. the one open on Live Tracking). */
  sessionId?: string | null;
}

export function ExportModal({
  isOpen,
  onClose,
  sessionId,
}: ExportModalProps) {
  const [format, setFormat] = useState<ArtifactFormat>("csv");
  const [sessions, setSessions] = useState<ExportSession[] | null>(null);
  const [selected, setSelected] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    apiFetch<SessionListResponse>("/api/sessions?limit=100")
      .then((data) => {
        if (cancelled) return;
        setSessions(data.sessions);
        setSelected((current) => sessionId || current || data.sessions[0]?.session_id || "");
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setSessions([]);
          setError(apiErrorMessage(err, "Could not load processed sessions."));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [isOpen, sessionId]);

  const handleClose = () => {
    setSessions(null);
    setSelected("");
    setError(null);
    onClose();
  };

  const handleExport = async () => {
    if (!selected) return;
    setExporting(true);
    setError(null);
    try {
      await downloadArtifact(selected, format);
      handleClose();
    } catch (err) {
      setError(apiErrorMessage(err, "Could not download this export."));
    } finally {
      setExporting(false);
    }
  };

  if (!isOpen) return null;

  const loading = sessions === null;
  const available = sessions ?? [];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-xs">
      <div className="bg-surface-container-lowest rounded-xl shadow-2xl border border-outline-variant/30 max-w-md w-full p-space-lg flex flex-col gap-space-md animate-in fade-in zoom-in-95 duration-150">
        <div className="flex items-center justify-between pb-space-xs border-b border-outline-variant/20">
          <div className="flex items-center gap-space-sm">
            <span className="material-symbols-outlined text-primary text-[22px]">
              file_download
            </span>
            <h2 className="font-headline-sm text-headline-sm text-on-surface">
              Export Kinematics Data
            </h2>
          </div>
          <button
            onClick={handleClose}
            className="p-1 rounded-md text-secondary hover:text-on-surface hover:bg-surface-container-low cursor-pointer"
            type="button"
          >
            <span className="material-symbols-outlined text-[18px]">close</span>
          </button>
        </div>

        <div className="flex flex-col gap-space-sm">
          <label
            htmlFor="export-session"
            className="font-label-md text-label-md text-on-surface-variant font-semibold"
          >
            Processed Session:
          </label>
          <select
            id="export-session"
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
            disabled={loading || available.length === 0}
            className="w-full rounded-lg border border-outline-variant/40 bg-surface-container-low px-3 py-2 text-body-sm text-on-surface disabled:opacity-60"
          >
            {loading && <option value="">Loading sessions…</option>}
            {!loading && available.length === 0 && (
              <option value="">No processed sessions available</option>
            )}
            {available.map((session) => (
              <option key={session.session_id} value={session.session_id}>
                {session.session_id} · {session.motion}
              </option>
            ))}
          </select>
          <p className="text-[11px] text-secondary">
            Files are served from the backend&apos;s processed output directory.
            MATLAB (.mat) is only produced by the offline analysis pipeline.
          </p>
        </div>

        <div className="flex flex-col gap-space-sm">
          <span className="font-label-md text-label-md text-on-surface-variant font-semibold">
            Export Format:
          </span>
          <div className="grid grid-cols-3 gap-space-xs">
            {(
              [
                { id: "csv", label: "Trajectory CSV", sub: ".csv" },
                { id: "json", label: "Summary JSON", sub: ".json" },
                { id: "mat", label: "MATLAB", sub: ".mat" },
              ] as const
            ).map((opt) => (
              <button
                key={opt.id}
                onClick={() => setFormat(opt.id)}
                className={`p-2.5 rounded-lg border text-center transition-all cursor-pointer flex flex-col items-center gap-0.5 ${
                  format === opt.id
                    ? "bg-primary/10 border-primary text-primary font-semibold"
                    : "border-outline-variant/30 text-secondary hover:bg-surface-container-low"
                }`}
                type="button"
              >
                <span className="font-label-md text-label-md">{opt.label}</span>
                <span className="text-[10px] opacity-75 font-mono">{opt.sub}</span>
              </button>
            ))}
          </div>
        </div>

        {error && (
          <div className="rounded-lg border border-error/30 bg-error-container/40 px-space-sm py-2 text-body-sm text-on-error-container">
            {error}
          </div>
        )}

        <div className="flex items-center justify-end gap-space-sm pt-space-xs border-t border-outline-variant/20">
          <button
            onClick={handleClose}
            className="px-space-md py-1.5 rounded-lg font-label-md text-label-md text-secondary hover:bg-surface-container-low cursor-pointer"
            type="button"
          >
            Cancel
          </button>
          <button
            onClick={handleExport}
            disabled={!selected || exporting}
            className="px-space-lg py-1.5 rounded-lg font-label-md text-label-md bg-primary text-on-primary font-semibold hover:bg-primary-container shadow-sm cursor-pointer flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
            type="button"
          >
            <span className="material-symbols-outlined text-[16px]">download</span>
            {exporting ? "Downloading…" : "Download File"}
          </button>
        </div>
      </div>
    </div>
  );
}
