import { useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { feedsApi } from '../services/api';

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
  segments: TranscriptSegmentRow[];
  adBlocks: AdBlock[];
  corrections: CorrectionRow[];
  suggestedPromptSnippet: string | null;
  existingPrompt: string | null;
}

function overlaps(start: number, end: number, block: AdBlock): boolean {
  return start < block.end_time && end > block.start_time;
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
  segments,
  adBlocks,
  corrections,
  suggestedPromptSnippet,
  existingPrompt,
}: TranscriptCorrectionPanelProps) {
  const queryClient = useQueryClient();
  const [anchorIndex, setAnchorIndex] = useState<number | null>(null);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const [dragging, setDragging] = useState(false);
  const [startTime, setStartTime] = useState('');
  const [endTime, setEndTime] = useState('');
  const [kind, setKind] = useState<CorrectionKind>('missed_ad');
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);

  const selectedIndexes = useMemo(() => {
    if (anchorIndex === null) return new Set<number>();
    const other = hoverIndex ?? anchorIndex;
    const from = Math.min(anchorIndex, other);
    const to = Math.max(anchorIndex, other);
    return new Set(Array.from({ length: to - from + 1 }, (_, i) => from + i));
  }, [anchorIndex, hoverIndex]);

  const selectedSegments = segments.filter((_, index) => selectedIndexes.has(index));

  const applySelectionBounds = (rows: TranscriptSegmentRow[]) => {
    if (!rows.length) return;
    const snapped = snapToWords(rows[0].start_time, rows[rows.length - 1].end_time, rows);
    setStartTime(snapped.start.toFixed(1));
    setEndTime(snapped.end.toFixed(1));
  };

  const saveMutation = useMutation({
    mutationFn: async (label: 'ad' | 'content') => {
      const start = Number(startTime);
      const end = Number(endTime);
      if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
        throw new Error('Select a start and end time.');
      }
      return feedsApi.createAdCorrection(episodeGuid, {
        label,
        kind,
        start_time: start,
        end_time: end,
        segment_ids: selectedSegments.map((segment) => segment.id),
        reason: reason.trim() || undefined,
        apply: true,
      });
    },
    onSuccess: async () => {
      setError(null);
      await queryClient.invalidateQueries({ queryKey: ['episode-stats', episodeGuid] });
    },
    onError: (err: unknown) => {
      const message = err instanceof Error ? err.message : 'Failed to save correction';
      setError(message);
    },
  });

  const acceptSnippetMutation = useMutation({
    mutationFn: async (existingPrompt: string | null) => {
      if (!suggestedPromptSnippet) return;
      const existing = existingPrompt?.trim() || '';
      const next = existing
        ? `${existing}\n\n${suggestedPromptSnippet}`
        : suggestedPromptSnippet;
      return feedsApi.updateFeedSettings(feedId, { custom_llm_ad_prompt: next });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['episode-stats', episodeGuid] });
    },
  });

  return (
    <div>
      <h3 className="font-semibold text-gray-900 mb-4 text-left">
        Transcript Segments ({segments.length})
      </h3>
      {canEdit && (
        <div className="mb-4 rounded-lg border border-indigo-100 bg-indigo-50 p-3 text-left">
          <p className="text-sm text-indigo-900 mb-3">
            Drag across rows or edit start/end seconds, then mark the span as ad or content.
            Effective cuts are highlighted in red.
          </p>
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
                saveMutation.mutate('ad');
              }}
              disabled={saveMutation.isPending}
              className="rounded bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
            >
              Mark ad
            </button>
            <button
              type="button"
              onClick={() => {
                setKind(kind === 'missed_ad' ? 'false_positive' : kind);
                saveMutation.mutate('content');
              }}
              disabled={saveMutation.isPending}
              className="rounded bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50"
            >
              Mark content
            </button>
          </div>
          {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
        </div>
      )}

      {suggestedPromptSnippet && canEdit && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-left">
          <p className="text-sm font-medium text-amber-900 mb-1">Suggested feed prompt</p>
          <p className="text-sm text-amber-800 mb-2">{suggestedPromptSnippet}</p>
          <button
            type="button"
            onClick={() => acceptSnippetMutation.mutate(existingPrompt)}
            disabled={acceptSnippetMutation.isPending}
            className="rounded bg-amber-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-800 disabled:opacity-50"
          >
            Append to feed prompt
          </button>
        </div>
      )}

      <div className="bg-white border rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Seq #</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Time Range</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Label</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Text</th>
              </tr>
            </thead>
            <tbody
              className="bg-white divide-y divide-gray-200"
              onMouseLeave={() => {
                if (dragging) setDragging(false);
              }}
            >
              {segments.map((segment, index) => {
                const inEffectiveCut = adBlocks.some((block) =>
                  overlaps(segment.start_time, segment.end_time, block)
                );
                const selected = selectedIndexes.has(index);
                return (
                  <tr
                    key={segment.id}
                    className={`${
                      selected
                        ? 'bg-indigo-100'
                        : inEffectiveCut
                          ? 'bg-red-50'
                          : segment.primary_label === 'ad'
                            ? 'bg-red-50/60'
                            : ''
                    } ${canEdit ? 'cursor-pointer' : ''} hover:bg-gray-50`}
                    onMouseDown={() => {
                      if (!canEdit) return;
                      setDragging(true);
                      setAnchorIndex(index);
                      setHoverIndex(index);
                      applySelectionBounds([segment]);
                    }}
                    onMouseEnter={() => {
                      if (!canEdit || !dragging || anchorIndex === null) return;
                      setHoverIndex(index);
                      const from = Math.min(anchorIndex, index);
                      const to = Math.max(anchorIndex, index);
                      applySelectionBounds(segments.slice(from, to + 1));
                    }}
                    onMouseUp={() => setDragging(false)}
                  >
                    <td className="px-4 py-3 text-sm text-gray-900">{segment.sequence_num}</td>
                    <td className="px-4 py-3 text-sm text-gray-600">
                      {segment.start_time}s - {segment.end_time}s
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex px-2 py-1 text-xs font-medium rounded-full ${
                        inEffectiveCut || segment.primary_label === 'ad'
                          ? 'bg-red-100 text-red-800'
                          : 'bg-green-100 text-green-800'
                      }`}>
                        {inEffectiveCut
                          ? (segment.mixed ? 'Cut (mixed)' : 'Cut')
                          : segment.primary_label === 'ad'
                            ? (segment.mixed ? 'Ad (mixed)' : 'Ad')
                            : 'Content'}
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
          <h4 className="font-medium text-gray-900 mb-2">Saved corrections ({corrections.length})</h4>
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
  );
}
