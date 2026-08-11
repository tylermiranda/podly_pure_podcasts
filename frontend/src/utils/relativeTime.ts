/**
 * Format an ISO timestamp as a short relative English phrase
 * (e.g. "just now", "5 minutes ago", "1 hour ago").
 * Falls back to a short absolute date for timestamps older than ~30 days
 * or when the input cannot be parsed.
 */
export function formatRelativeTime(isoTimestamp: string, now: Date = new Date()): string {
  const date = new Date(isoTimestamp);
  if (Number.isNaN(date.getTime())) {
    return 'unknown';
  }

  const diffMs = date.getTime() - now.getTime();
  const absMs = Math.abs(diffMs);
  const isPast = diffMs <= 0;

  const SECOND = 1000;
  const MINUTE = 60 * SECOND;
  const HOUR = 60 * MINUTE;
  const DAY = 24 * HOUR;
  const MONTH = 30 * DAY;

  // Very recent: avoid "0 seconds ago"
  if (absMs < 45 * SECOND) {
    return 'just now';
  }

  // Beyond ~30 days, prefer a short absolute date
  if (absMs >= MONTH) {
    return date.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  }

  let value: number;
  let unit: Intl.RelativeTimeFormatUnit;

  if (absMs < HOUR) {
    value = Math.round(absMs / MINUTE);
    unit = 'minute';
  } else if (absMs < DAY) {
    value = Math.round(absMs / HOUR);
    unit = 'hour';
  } else {
    value = Math.round(absMs / DAY);
    unit = 'day';
  }

  // Force past tense phrasing for "ago" style matching the reference UI
  const signed = isPast ? -value : value;
  try {
    return new Intl.RelativeTimeFormat('en', { numeric: 'auto' }).format(signed, unit);
  } catch {
    // Extremely old environments without Intl.RelativeTimeFormat
    if (isPast) {
      return `${value} ${unit}${value === 1 ? '' : 's'} ago`;
    }
    return `in ${value} ${unit}${value === 1 ? '' : 's'}`;
  }
}

export function formatLastFetchedLabel(
  lastFetchedAt: string | null | undefined,
  now: Date = new Date()
): string {
  if (!lastFetchedAt) {
    return 'Not fetched yet';
  }
  return `Last fetched ${formatRelativeTime(lastFetchedAt, now)}`;
}
