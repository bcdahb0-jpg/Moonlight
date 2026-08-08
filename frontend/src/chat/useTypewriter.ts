import { useEffect, useRef, useState } from 'react';

interface TypewriterOptions {
  speedMs?: number;
}

/**
 * Incrementally reveals a string, character by character. Returns the visible
 * prefix and whether the reveal is still in progress.
 */
export function useTypewriter(fullText: string, { speedMs = 18 }: TypewriterOptions = {}): {
  visible: string;
  done: boolean;
} {
  const [visibleLength, setVisibleLength] = useState(0);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    setVisibleLength(0);
    if (!fullText) return;
    let i = 0;
    const step = (): void => {
      i += 1;
      setVisibleLength(i);
      if (i < fullText.length) {
        timerRef.current = window.setTimeout(step, speedMs);
      }
    };
    timerRef.current = window.setTimeout(step, speedMs);
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, [fullText, speedMs]);

  return {
    visible: fullText.slice(0, visibleLength),
    done: visibleLength >= fullText.length,
  };
}
