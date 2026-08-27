export type CompletionAlertOptions = {
  title: string;
  body: string;
  /** Alternating tab title while waiting for acknowledge/focus. */
  blinkTitle?: string;
  tag?: string;
};

export type CompletionAlertController = {
  stopBlink: () => void;
  stop: () => void;
};

const BLINK_MS = 1000;

export async function ensureNotificationPermission(): Promise<NotificationPermission | 'unsupported'> {
  if (typeof window === 'undefined' || typeof Notification === 'undefined') {
    return 'unsupported';
  }
  if (Notification.permission === 'default') {
    try {
      return await Notification.requestPermission();
    } catch {
      return Notification.permission;
    }
  }
  return Notification.permission;
}

export function startCompletionAlert(
  options: CompletionAlertOptions
): CompletionAlertController {
  const blinkTitle = options.blinkTitle ?? 'Done: prompt + recut';
  const tag = options.tag ?? 'podly-improve-recut';
  const originalTitle =
    typeof document !== 'undefined' ? document.title : '';
  let intervalId: ReturnType<typeof setInterval> | null = null;
  let showingAlertTitle = false;
  let notification: Notification | null = null;
  let stopped = false;

  const stopBlink = () => {
    if (intervalId !== null) {
      clearInterval(intervalId);
      intervalId = null;
    }
    if (typeof document !== 'undefined' && originalTitle) {
      document.title = originalTitle;
    }
    showingAlertTitle = false;
  };

  const stop = () => {
    if (stopped) return;
    stopped = true;
    stopBlink();
    if (notification) {
      try {
        notification.close();
      } catch {
        // ignore
      }
      notification = null;
    }
  };

  if (typeof window !== 'undefined' && typeof Notification !== 'undefined') {
    if (Notification.permission === 'granted') {
      try {
        notification = new Notification(options.title, {
          body: options.body,
          tag,
        });
      } catch {
        notification = null;
      }
    }
  }

  if (typeof document !== 'undefined') {
    intervalId = setInterval(() => {
      showingAlertTitle = !showingAlertTitle;
      document.title = showingAlertTitle ? blinkTitle : originalTitle;
    }, BLINK_MS);
  }

  return { stopBlink, stop };
}
