/** Global toast notifications for command results (start/stop/close/errors). */

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

export interface Toast {
  id: number;
  kind: 'ok' | 'bad' | 'info';
  title: string;
  message?: string;
}

interface ToastApi {
  push: (toast: Omit<Toast, 'id'>) => void;
  ok: (title: string, message?: string) => void;
  bad: (title: string, message?: string) => void;
  info: (title: string, message?: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const remove = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback(
    (toast: Omit<Toast, 'id'>) => {
      const id = Date.now() + Math.random();
      setToasts((current) => [...current, { ...toast, id }].slice(-4));
      window.setTimeout(() => remove(id), toast.kind === 'bad' ? 9000 : 5000);
    },
    [remove],
  );

  const value = useMemo<ToastApi>(
    () => ({
      push,
      ok: (title, message) => push({ kind: 'ok', title, message }),
      bad: (title, message) => push({ kind: 'bad', title, message }),
      info: (title, message) => push({ kind: 'info', title, message }),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast ${toast.kind}`}>
            <div className="toast-body">
              <div className="toast-title">{toast.title}</div>
              {toast.message && <div className="muted">{toast.message}</div>}
            </div>
            <button className="toast-close" onClick={() => remove(toast.id)} aria-label="Dismiss">
              x
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const context = useContext(ToastContext);
  if (!context) throw new Error('useToast must be used inside <ToastProvider>');
  return context;
}
