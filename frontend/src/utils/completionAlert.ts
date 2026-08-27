export type CompletionAlertOptions = {
  title: string;
  body: string;
  /** Alternating tab title while waiting for acknowledge/focus. */
  blinkTitle?: string;
  tag?: string;
  /** Play a short Web Audio chime (default true). */
  playSound?: boolean;
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

/** Resume AudioContext on a user gesture so the completion chime can play later. */
export async function unlockCompletionAudio(): Promise<void> {
  if (typeof window === 'undefined') return;
  const AC =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AC) return;
  try {
    const ctx = new AC();
    if (ctx.state === 'suspended') {
      await ctx.resume();
    }
    await ctx.close();
  } catch {
    // ignore
  }
}

function playCompletionChime(): void {
  if (typeof window === 'undefined') return;
  const AC =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AC) return;

  try {
    const ctx = new AC();
    const playTone = (freq: number, start: number, duration: number) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(0.18, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(start);
      osc.stop(start + duration + 0.02);
    };

    void ctx.resume().then(() => {
      const t0 = ctx.currentTime;
      playTone(880, t0, 0.12);
      playTone(1174.7, t0 + 0.14, 0.18);
      window.setTimeout(() => {
        void ctx.close().catch(() => undefined);
      }, 500);
    });
  } catch {
    // ignore autoplay / unsupported
  }
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

  if (options.playSound !== false) {
    playCompletionChime();
  }

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
