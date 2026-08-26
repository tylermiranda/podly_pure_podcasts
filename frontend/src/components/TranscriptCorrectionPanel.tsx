import { useCallback, useMemo, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'react-hot-toast';
import { feedsApi } from '../services/api';
import { useAudioPlayer } from '../contexts/AudioPlayerContext';
import { getHttpErrorInfo } from '../utils/httpError';
import type { SuggestedPromptStatus } from '../types';

type CorrectionKind = 'missed_ad' | 'false_positive' | 'retime';

interface TranscriptWord {
  word: string;
  start: number;
  end: number;
}

interface TranscriptSegmentRow {
  id: number;
  sequence_num: number;
  start_time: number;
  end_time: number;
  text: string;
  words?: TranscriptWord[] | null;
  primary_label: 'ad' | 'content';
  mixed: boolean;
}

interface AdBlock {
  start_time: number;
  end_time: number;
}

interface CorrectionRow {
  id: number;
  kind: CorrectionKind;
  label: 'ad' | 'content';
  start_time: number;
  end_time: number;
  reason: string | null;
  example_text: string | null;
}

interface TranscriptCorrectionPanelProps {
  episodeGuid: string;
  feedId: number;
  canEdit: boolean;
  hasUnprocessedAudio: boolean;
  segments: TranscriptSegmentRow[];
  adBlocks: AdBlock[];
  corrections: CorrectionRow[];
  suggestedPrompt: SuggestedPromptStatus | null;
  existingPrompt: string | null;
}

const ADJACENT_GAP_SECONDS = 1;

function contiguousIndexGroups(
  indexes: Set<number>,
  segments: TranscriptSegmentRow[] = []
): number[][] {
  if (indexes.size === 0) return [];
  const sorted = [...indexes].sort((a, b) => a - b);
  const groups: number[][] = [];
  let current: number[] = [sorted[0]];
  for (let i = 1; i < sorted.length; i++) {
    const prev = sorted[i - 1];
    const next = sorted[i];
    const prevRow = segments[prev];
    const nextRow = segments[next];
    const adjacentIndex = next === prev + 1;
    const adjacentTime =
      prevRow && nextRow
        ? nextRow.start_time - prevRow.end_time <= ADJACENT_GAP_SECONDS
        : true;
    if (adjacentIndex && adjacentTime) {
      current.push(next);
    } else {
      groups.push(current);
      current = [next];
    }
  }
  groups.push(current);
  return groups;
}

function rangeIndexes(from: number, to: number): Set<number> {
  const start = Math.min(from, to);
  const end = Math.max(from, to);
  return new Set(Array.from({ length: end - start + 1 }, (_, i) => start + i));
}

function overlaps(start: number, end: number, block: { start_time: number; end_time: number }): boolean {
  return start < block.end_time && end > block.start_time;
}

function latestCorrectionLabel(
  start: number,
  end: number,
  corrections: CorrectionRow[]
): 'ad' | 'content' | null {
  let label: 'ad' | 'content' | null = null;
  for (const correction of corrections) {
    if (overlaps(start, end, correction)) {
      label = correction.label;
    }
  }
  return label;
}

function snapToWords(
  start: number,
  end: number,
  segments: TranscriptSegmentRow[],
  tolerance = 0.75
): { start: number; end: number } {
  const words = segments.flatMap((segment) => segment.words || []);
  if (!words.length) {
    return { start, end };
  }
  const startCandidates = words.filter((word) => Math.abs(word.start - start) <= tolerance);
  const endCandidates = words.filter((word) => Math.abs(word.end - end) <= tolerance);
  const snappedStart = startCandidates.length
    ? startCandidates.reduce((best, word) =>
        Math.abs(word.start - start) < Math.abs(best.start - start) ? word : best
      ).start
    : start;
  const snappedEnd = endCandidates.length
    ? endCandidates.reduce((best, word) =>
        Math.abs(word.end - end) < Math.abs(best.end - end) ? word : best
      ).end
    : end;
  if (snappedEnd <= snappedStart) {
    return { start, end };
  }
  return { start: snappedStart, end: snappedEnd };
}

export default function TranscriptCorrectionPanel({
  episodeGuid,
  feedId,
  canEdit,
  hasUnprocessedAudio,
  segments,
  adBlocks,
  corrections,
  suggestedPrompt,
  existingPrompt,
}: TranscriptCorrectionPanelProps) {
  const queryClient = useQueryClient();
  const { audioRef: globalAudioRef, reloadProcessedAudio } = useAudioPlayer();
  const originalAudioRef = useRef<HTMLAudioElement>(null);
  const [selectedIndexes, setSelectedIndexes] = useState<Set<number>>(() => new Set());
  const [rangeAnchor, setRangeAnchor] = useState<number | null>(null);
  const [startTime, setStartTime] = useState('');
  const [endTime, setEndTime] = useState('');
  const [kind, setKind] = useState<CorrectionKind>('missed_ad');
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [learnedDraft, setLearnedDraft] = useState<string | null>(null);
  const [learnedExistingPrompt, setLearnedExistingPrompt] = useState<string | null>(
    null
  );

  const selectionGroups = useMemo(
    () => contiguousIndexGroups(selectedIndexes, segments),
    [selectedIndexes, segments]
  );

  const selectionSummary =
    selectedIndexes.size > 0
      ? `${selectionGroups.length} span${selectionGroups.length === 1 ? '' : 's'} · ${selectedIndexes.size} segment${selectedIndexes.size === 1 ? '' : 's'} selected`
      : null;

  const applySelectionBounds = useCallback((rows: TranscriptSegmentRow[]) => {
    if (!rows.length) return;
    const snapped = snapToWords(rows[0].start_time, rows[rows.length - 1].end_time, rows);
    setStartTime(snapped.start.toFixed(1));
    setEndTime(snapped.end.toFixed(1));
  }, []);

  const updateBoundsFromSelection = useCallback(
    (indexes: Set<number>) => {
      const groups = contiguousIndexGroups(indexes, segments);
      if (groups.length !== 1) return;
      const rows = groups[0].map((index) => segments[index]);
      applySelectionBounds(rows);
    },
    [applySelectionBounds, segments]
  );

  const pauseGlobalPlayer = useCallback(() => {
    const globalAudio = globalAudioRef.current;
    if (globalAudio && !globalAudio.paused) {
      globalAudio.pause();
    }
  }, [globalAudioRef]);

  const playFrom = useCallback(
    (time: number) => {
      const audio = originalAudioRef.current;
      if (!audio) return;
      pauseGlobalPlayer();
      audio.currentTime = time;
      void audio.play();
    },
    [pauseGlobalPlayer]
  );

  const toggleSegmentSelection = useCallback(
    (index: number, shiftKey: boolean) => {
      setSelectedIndexes((prev) => {
        let next: Set<number>;
        if (shiftKey && rangeAnchor !== null) {
          next = new Set([...prev, ...rangeIndexes(rangeAnchor, index)]);
        } else {
          next = new Set(prev);
          if (next.has(index)) next.delete(index);
          else next.add(index);
        }
        updateBoundsFromSelection(next);
        return next;
      });
      setRangeAnchor(index);
    },
    [rangeAnchor, updateBoundsFromSelection]
  );

  const clearSelection = useCallback(() => {
    setSelectedIndexes(new Set());
    setRangeAnchor(null);
  }, []);

  const refreshStatsOnly = async () => {
    await queryClient.invalidateQueries({ queryKey: ['episode-stats', episodeGuid] });
    await queryClient.refetchQueries({ queryKey: ['episode-stats', episodeGuid] });
  };

  const refreshAfterRecut = async () => {
    feedsApi.bumpProcessedAudio(episodeGuid);
    reloadProcessedAudio(episodeGuid);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['episode-stats', episodeGuid] }),
      queryClient.invalidateQueries({ queryKey: ['episode-status', episodeGuid] }),
      queryClient.invalidateQueries({ queryKey: ['episodes'] }),
    ]);
    await queryClient.refetchQueries({ queryKey: ['episode-stats', episodeGuid] });
  };

  const saveMutation = useMutation({
    mutationFn: async ({
      label,
      correctionKind,
    }: {
      label: 'ad' | 'content';
      correctionKind: CorrectionKind;
    }) => {
      const groups = contiguousIndexGroups(selectedIndexes, segments);
      if (groups.length > 0) {
        await Promise.all(
          groups.map((group) => {
            const rows = group.map((index) => segments[index]);
            const snapped = snapToWords(
              rows[0].start_time,
              rows[rows.length - 1].end_time,
              rows
            );
            return feedsApi.createAdCorrection(episodeGuid, {
              label,
              kind: correctionKind,
              start_time: snapped.start,
              end_time: snapped.end,
              segment_ids: rows.map((segment) => segment.id),
              reason: reason.trim() || undefined,
              apply: false,
            });
          })
        );
        return { count: groups.length };
      }

      const start = Number(startTime);
      const end = Number(endTime);
      if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
        throw new Error('Select segment rows or enter a start and end time.');
      }
      await feedsApi.createAdCorrection(episodeGuid, {
        label,
        kind: correctionKind,
        start_time: start,
        end_time: end,
        reason: reason.trim() || undefined,
        apply: false,
      });
      return { count: 1 };
    },
    onSuccess: async (result) => {
      clearSelection();
      const count = result.count;
      setError(null);
      setStatus(
        `${count} correction${count === 1 ? '' : 's'} saved — recut when you are done marking spans.`
      );
      toast.success(`Saved ${count} correction${count === 1 ? '' : 's'}`);
      await refreshStatsOnly();
    },
    onError: async (err: unknown) => {
      setStatus(null);
      setError(getHttpErrorInfo(err).message);
      await refreshStatsOnly();
    },
  });

  const recutMutation = useMutation({
    mutationFn: () => feedsApi.applyAdCorrections(episodeGuid),
    onSuccess: async () => {
      setError(null);
      setStatus('Processed audio recut from current corrections.');
      toast.success('Processed audio updated');
      await refreshAfterRecut();
    },
    onError: (err: unknown) => {
      setStatus(null);
      setError(getHttpErrorInfo(err).message);
    },
  });

  const acceptSnippetMutation = useMutation({
    mutationFn: async (existingPrompt: string | null) => {
      const snippet = suggestedPrompt?.snippet;
      if (!snippet) return;
      const existing = existingPrompt?.trim() || '';
      if (existing && existing.includes(snippet.trim())) {
        return feedsApi.updateFeedSettings(feedId, {
          custom_llm_ad_prompt: existing,
        });
      }
      const next = existing
        ? `${existing}\n\n${snippet}`
        : snippet;
      return feedsApi.updateFeedSettings(feedId, { custom_llm_ad_prompt: next });
    },
    onSuccess: async () => {
      toast.success('Feed prompt updated');
      await queryClient.invalidateQueries({ queryKey: ['episode-stats', episodeGuid] });
      await queryClient.refetchQueries({ queryKey: ['episode-stats', episodeGuid] });
    },
    onError: (err: unknown) => {
      setError(getHttpErrorInfo(err).message);
    },
  });

  const analyzePromptMutation = useMutation({
    mutationFn: () => feedsApi.analyzeAdCorrectionsPrompt(episodeGuid),
    onSuccess: (data) => {
      setError(null);
      const draft = (data.draft || '').trim();
      if (!draft) {
        setLearnedDraft(null);
        setError('Model returned an empty prompt draft');
        return;
      }
      setLearnedDraft(data.draft);
      setLearnedExistingPrompt(data.existing_prompt);
      setStatus(null);
      toast.success('Draft show prompt ready — review before appending');
    },
    onError: (err: unknown) => {
      setStatus(null);
      setError(getHttpErrorInfo(err).message);
    },
  });

  const acceptLearnedDraftMutation = useMutation({
    mutationFn: async () => {
      const snippet = learnedDraft?.trim();
      if (!snippet) return;
      const existing =
        (learnedExistingPrompt ?? existingPrompt)?.trim() || '';
      if (existing && existing.includes(snippet)) {
        toast.success('Draft already present in show prompt');
        return feedsApi.updateFeedSettings(feedId, {
          custom_llm_ad_prompt: existing,
        });
      }
      const next = existing ? `${existing}\n\n${snippet}` : snippet;
      return feedsApi.updateFeedSettings(feedId, { custom_llm_ad_prompt: next });
    },
    onSuccess: async () => {
      toast.success('Feed prompt updated — Recut if cuts should change');
      setLearnedDraft(null);
      setLearnedExistingPrompt(null);
      await queryClient.invalidateQueries({ queryKey: ['episode-stats', episodeGuid] });
      await queryClient.refetchQueries({ queryKey: ['episode-stats', episodeGuid] });
    },
    onError: (err: unknown) => {
      setError(getHttpErrorInfo(err).message);
    },
  });

  const suggestedSnippet = suggestedPrompt?.snippet ?? null;
  const promptProgress =
    suggestedPrompt &&
    suggestedPrompt.repeat_count > 0 &&
    suggestedPrompt.repeat_count < suggestedPrompt.min_repeats
      ? suggestedPrompt
      : null;

  const originalAudioUrl = hasUnprocessedAudio
    ? feedsApi.getPostOriginalAudioUrl(episodeGuid)
    : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-shrink-0 space-y-4 border-b bg-white px-6 pt-6 pb-4">
        <h3 className="font-semibold text-gray-900 text-left">
          Transcript Segments ({segments.length})
        </h3>
        {originalAudioUrl ? (
          <div className="text-left">
            <p className="mb-2 text-sm text-gray-600">
              Original audio (with ads). Click a row to play. Check rows to mark them; Shift+click a
              checkbox to select a range.
            </p>
            <audio
              ref={originalAudioRef}
              controls
              className="w-full"
              src={originalAudioUrl}
              preload="metadata"
              onPlay={pauseGlobalPlayer}
              onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
            />
          </div>
        ) : (
          <p className="text-sm text-amber-800 text-left">
            Original audio is not on disk; recut/reprocess keeps it for later episodes.
          </p>
        )}
        {canEdit && (
          <div className="rounded-lg border border-indigo-100 bg-indigo-50 p-3 text-left">
            <p className="text-sm text-indigo-900 mb-3">
              Check rows, or Shift+click a checkbox for a range, or edit start/end seconds
              manually, then mark as ad or content while listening to the original audio. Mark
              ad/content saves each span; click Recut audio once when finished to update the
              processed MP3. You do not need Reprocess (that re-runs Whisper/LLM). Effective cuts
              are highlighted in red.
            </p>
            {selectionSummary && (
              <p className="mb-3 flex flex-wrap items-center gap-3 text-sm font-medium text-indigo-800">
                <span>{selectionSummary}</span>
                <button
                  type="button"
                  onClick={clearSelection}
                  className="rounded border border-indigo-300 bg-white px-2 py-0.5 text-xs font-medium text-indigo-700 hover:bg-indigo-50"
                >
                  Clear selection
                </button>
              </p>
            )}
            {corrections.length > 0 && (
              <p className="text-sm text-indigo-800 mb-3">
                {corrections.length} correction{corrections.length === 1 ? '' : 's'} saved — click
                Recut audio to update the episode. The main player still plays the old MP3 until
                you recut.
              </p>
            )}
            {promptProgress && (
              <p className="text-sm text-indigo-800 mb-3">
                {promptProgress.repeat_count} similar correction
                {promptProgress.repeat_count === 1 ? '' : 's'} on this feed —{' '}
                {promptProgress.min_repeats - promptProgress.repeat_count} more unlocks a feed
                prompt suggestion for future episodes.
              </p>
            )}
            <div className="flex flex-wrap items-end gap-3">
              <label className="text-xs text-gray-700">
                Start (s)
                <input
                  type="number"
                  step="0.1"
                  value={startTime}
                  onChange={(event) => setStartTime(event.target.value)}
                  className="mt-1 block w-28 rounded border border-gray-300 px-2 py-1 text-sm"
                />
              </label>
              <label className="text-xs text-gray-700">
                End (s)
                <input
                  type="number"
                  step="0.1"
                  value={endTime}
                  onChange={(event) => setEndTime(event.target.value)}
                  className="mt-1 block w-28 rounded border border-gray-300 px-2 py-1 text-sm"
                />
              </label>
              <label className="text-xs text-gray-700">
                Reason
                <select
                  value={kind}
                  onChange={(event) => setKind(event.target.value as CorrectionKind)}
                  className="mt-1 block rounded border border-gray-300 px-2 py-1 text-sm"
                >
                  <option value="missed_ad">missed_ad</option>
                  <option value="false_positive">false_positive</option>
                  <option value="retime">retime</option>
                </select>
              </label>
              <label className="text-xs text-gray-700">
                Note
                <input
                  type="text"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  className="mt-1 block w-48 rounded border border-gray-300 px-2 py-1 text-sm"
                  placeholder="optional"
                />
              </label>
              <button
                type="button"
                onClick={() => {
                  setKind('missed_ad');
                  setStatus('Saving correction…');
                  saveMutation.mutate({ label: 'ad', correctionKind: 'missed_ad' });
                }}
                disabled={saveMutation.isPending || recutMutation.isPending}
                className="rounded bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                {saveMutation.isPending ? 'Saving…' : 'Mark ad'}
              </button>
              <button
                type="button"
                onClick={() => {
                  const correctionKind = kind === 'missed_ad' ? 'false_positive' : kind;
                  setKind(correctionKind);
                  setStatus('Saving correction…');
                  saveMutation.mutate({ label: 'content', correctionKind });
                }}
                disabled={saveMutation.isPending || recutMutation.isPending}
                className="rounded bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50"
              >
                {saveMutation.isPending ? 'Saving…' : 'Mark content'}
              </button>
              <button
                type="button"
                onClick={() => {
                  setStatus('Recutting processed audio…');
                  recutMutation.mutate();
                }}
                disabled={saveMutation.isPending || recutMutation.isPending}
                className="rounded bg-indigo-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-800 disabled:opacity-50"
              >
                {recutMutation.isPending ? 'Recutting…' : 'Recut audio'}
              </button>
            </div>
            {status && <p className="mt-2 text-sm text-indigo-800">{status}</p>}
            {error && (
              <div className="mt-2 flex flex-wrap items-center gap-3">
                <p className="text-sm text-red-700">{error}</p>
                <button
                  type="button"
                  onClick={() => recutMutation.mutate()}
                  disabled={recutMutation.isPending}
                  className="rounded border border-red-300 bg-white px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
                >
                  Retry recut
                </button>
              </div>
            )}
          </div>
        )}

        {suggestedSnippet && canEdit && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-left">
            <p className="text-sm font-medium text-amber-900 mb-1">Suggested feed prompt</p>
            <p className="text-sm text-amber-800 mb-2">
              Append to teach the LLM permanently for future episodes of this feed.
            </p>
            <p className="text-sm text-amber-800 mb-2">{suggestedSnippet}</p>
            <button
              type="button"
              onClick={() => acceptSnippetMutation.mutate(existingPrompt)}
              disabled={acceptSnippetMutation.isPending}
              className="rounded bg-amber-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-800 disabled:opacity-50"
            >
              {acceptSnippetMutation.isPending ? 'Appending…' : 'Append to feed prompt'}
            </button>
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
        <div className="bg-white border rounded-lg overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 select-none">
              <thead className="bg-gray-50">
                <tr>
                  {canEdit && (
                    <th className="w-10 px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      <span className="sr-only">Select</span>
                    </th>
                  )}
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Seq #</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Time Range</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Label</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Text</th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {segments.map((segment, index) => {
                  const correctionLabel = latestCorrectionLabel(
                    segment.start_time,
                    segment.end_time,
                    corrections
                  );
                  const inEffectiveCut =
                    correctionLabel === 'ad' ||
                    (correctionLabel !== 'content' &&
                      adBlocks.some((block) =>
                        overlaps(segment.start_time, segment.end_time, block)
                      ));
                  const isAdRow =
                    inEffectiveCut ||
                    (correctionLabel !== 'content' && segment.primary_label === 'ad');
                  const selected = selectedIndexes.has(index);
                  const isLast = index === segments.length - 1;
                  const isPlayingRow =
                    currentTime >= segment.start_time &&
                    (isLast
                      ? currentTime <= segment.end_time
                      : currentTime < segment.end_time);
                  const pillText = inEffectiveCut
                    ? (segment.mixed && correctionLabel !== 'ad' ? 'Cut (mixed)' : 'Cut')
                    : isAdRow
                      ? (segment.mixed ? 'Ad (mixed)' : 'Ad')
                      : 'Content';
                  return (
                    <tr
                      key={segment.id}
                      className={`${
                        selected
                          ? 'bg-indigo-100 hover:bg-indigo-200'
                          : isPlayingRow
                            ? 'bg-sky-50 hover:bg-sky-100'
                            : inEffectiveCut
                              ? 'bg-red-50 hover:bg-gray-50'
                              : isAdRow
                                ? 'bg-red-50/60 hover:bg-gray-50'
                                : 'hover:bg-gray-50'
                      } cursor-pointer`}
                      onClick={() => playFrom(segment.start_time)}
                    >
                      {canEdit && (
                        <td
                          className="w-10 px-4 py-3"
                          onClick={(event) => event.stopPropagation()}
                        >
                          <button
                            type="button"
                            role="checkbox"
                            aria-checked={selected}
                            aria-label={`Select segment ${segment.sequence_num}`}
                            className={`flex h-4 w-4 items-center justify-center rounded border ${
                              selected
                                ? 'border-blue-500 bg-blue-600 text-white'
                                : 'border-gray-400 bg-white'
                            }`}
                            onClick={(event) => {
                              event.stopPropagation();
                              toggleSegmentSelection(index, event.shiftKey);
                            }}
                          >
                            {selected && (
                              <svg className="h-3 w-3" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                                <path
                                  fillRule="evenodd"
                                  d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                                  clipRule="evenodd"
                                />
                              </svg>
                            )}
                          </button>
                        </td>
                      )}
                      <td className="px-4 py-3 text-sm text-gray-900">{segment.sequence_num}</td>
                      <td className="px-4 py-3 text-sm text-gray-600">
                        {segment.start_time}s - {segment.end_time}s
                      </td>
                      <td className="px-4 py-3">
                        <span className={`inline-flex px-2 py-1 text-xs font-medium rounded-full ${
                          isAdRow
                            ? 'bg-red-100 text-red-800'
                            : 'bg-green-100 text-green-800'
                        }`}>
                          {pillText}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-900 max-w-md">
                        <div className="truncate text-left" title={segment.text}>
                          {segment.text}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {corrections.length > 0 && (
          <div className="mt-4 text-left">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <h4 className="font-medium text-gray-900">
                Saved corrections ({corrections.length})
              </h4>
              {canEdit && (
                <button
                  type="button"
                  onClick={() => {
                    setStatus('Analyzing corrections for show prompt…');
                    analyzePromptMutation.mutate();
                  }}
                  disabled={
                    analyzePromptMutation.isPending ||
                    acceptLearnedDraftMutation.isPending ||
                    saveMutation.isPending
                  }
                  className="rounded bg-amber-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-800 disabled:opacity-50"
                >
                  {analyzePromptMutation.isPending
                    ? 'Analyzing…'
                    : 'Learn for show prompt'}
                </button>
              )}
            </div>
            {learnedDraft && canEdit && (
              <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-left">
                <p className="text-sm font-medium text-amber-900 mb-1">
                  Draft show prompt from corrections
                </p>
                <p className="text-sm text-amber-800 mb-2 whitespace-pre-wrap">
                  {learnedDraft}
                </p>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => acceptLearnedDraftMutation.mutate()}
                    disabled={acceptLearnedDraftMutation.isPending}
                    className="rounded bg-amber-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-800 disabled:opacity-50"
                  >
                    {acceptLearnedDraftMutation.isPending
                      ? 'Appending…'
                      : 'Append to feed prompt'}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setLearnedDraft(null);
                      setLearnedExistingPrompt(null);
                    }}
                    disabled={acceptLearnedDraftMutation.isPending}
                    className="rounded border border-amber-300 bg-white px-3 py-1.5 text-sm font-medium text-amber-900 hover:bg-amber-100 disabled:opacity-50"
                  >
                    Dismiss
                  </button>
                </div>
              </div>
            )}
            <ul className="space-y-1 text-sm text-gray-700">
              {corrections.map((correction) => (
                <li key={correction.id}>
                  [{correction.start_time}s-{correction.end_time}s] {correction.label.toUpperCase()} ({correction.kind})
                  {correction.reason ? ` — ${correction.reason}` : ''}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
