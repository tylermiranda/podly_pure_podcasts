/**
 * Parse an API ISO timestamp, treating values without a timezone as UTC.
 * Podly stores several datetimes as UTC-naive; older payloads omit `Z`.
 */
export function parseApiTimestamp(isoTimestamp: string): Date {
  const trimmed = isoTimestamp.trim();
  const hasTimezone = /([zZ]|[+-]\d{2}:?\d{2})$/.test(trimmed);
  return new Date(hasTimezone ? trimmed : `${trimmed}Z`);
}

/**
 * Format an ISO timestamp as a short relative English phrase
 * (e.g. "just now", "5 minutes ago", "1 hour ago").
 * Falls back to a short absolute date for timestamps older than ~30 days
 * or when the input cannot be parsed.
 */
export function formatRelativeTime(isoTimestamp: string, now: Date = new Date()): string {
  const date = parseApiTimestamp(isoTimestamp);
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

/** Label for when a podcast app last requested the Podly RSS URL. */
export function formatClientFetchedLabel(
  lastClientPolledAt: string | null | undefined,
  clientName: string | null | undefined,
  now: Date = new Date()
): string {
  if (!lastClientPolledAt) {
    return 'Not fetched yet';
  }
  const relative = formatRelativeTime(lastClientPolledAt, now);
  const trimmedName = clientName?.trim();
  if (trimmedName) {
    return `Last fetched ${relative} via ${trimmedName}`;
  }
  return `Last fetched ${relative}`;
}

/** Label for when Podly last refreshed the upstream publisher RSS. */
export function formatUpstreamRefreshedLabel(
  lastFetchedAt: string | null | undefined,
  now: Date = new Date()
): string {
  if (!lastFetchedAt) {
    return 'Upstream RSS not refreshed yet';
  }
  return `Upstream RSS refreshed ${formatRelativeTime(lastFetchedAt, now)}`;
}
