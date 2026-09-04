/** Tiny data-loading helper: run an async function, expose {data, error, loading}. */

import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '../lib/api';

export function useAsync<T>(
  loader: () => Promise<T>,
  deps: unknown[] = [],
  options: { intervalMs?: number; immediate?: boolean } = {},
) {
  const { intervalMs, immediate = true } = options;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(immediate);

  const run = useCallback(async () => {
    setLoading(true);
    try {
      setData(await loader());
      setError(null);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    if (immediate) void run();
    if (!intervalMs) return undefined;
    const timer = window.setInterval(run, intervalMs);
    return () => window.clearInterval(timer);
  }, [run, intervalMs, immediate]);

  return { data, error, loading, reload: run, setData };
}
