import { createContext, useCallback, useContext, useState } from 'react';

const ToastContext = createContext(null);

const TYPE_STYLES = {
  success: { bg: '#1b5e20', fg: 'white', icon: '✓' },
  error:   { bg: '#b71c1c', fg: 'white', icon: '✕' },
  info:    { bg: '#1e40af', fg: 'white', icon: 'ℹ' },
};

export function ToastProvider({ children, defaultDuration = 4000 }) {
  const [toasts, setToasts] = useState([]);

  const remove = useCallback((id) => {
    setToasts((current) => current.filter((t) => t.id !== id));
  }, []);

  const push = useCallback((message, type = 'info', duration = defaultDuration) => {
    const id =
      typeof crypto !== 'undefined' && crypto.randomUUID
        ? crypto.randomUUID()
        : `t-${Math.random().toString(36).slice(2)}-${Date.now()}`;
    setToasts((current) => [...current, { id, message, type }]);
    if (duration > 0) {
      setTimeout(() => remove(id), duration);
    }
    return id;
  }, [defaultDuration, remove]);

  const api = {
    success: (m, d) => push(m, 'success', d),
    error: (m, d) => push(m, 'error', d),
    info: (m, d) => push(m, 'info', d),
    dismiss: remove,
  };

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div data-testid="toast-container" style={containerStyle}>
        {toasts.map((t) => {
          const style = TYPE_STYLES[t.type] || TYPE_STYLES.info;
          return (
            <div
              key={t.id}
              data-testid="toast"
              data-toast-type={t.type}
              style={{ ...toastStyle, background: style.bg, color: style.fg }}
              onClick={() => remove(t.id)}
            >
              <span style={{ marginRight: 8 }}>{style.icon}</span>
              {t.message}
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  // Tolerant: missing provider returns no-op API so components don't crash
  // in isolated test renders.
  return (
    useContext(ToastContext) || {
      success: () => {},
      error: () => {},
      info: () => {},
      dismiss: () => {},
    }
  );
}

const containerStyle = {
  position: 'fixed',
  bottom: 20,
  right: 20,
  display: 'flex',
  flexDirection: 'column',
  gap: 8,
  zIndex: 2000,
  maxWidth: 360,
};

const toastStyle = {
  padding: '10px 14px',
  borderRadius: 6,
  fontSize: 14,
  cursor: 'pointer',
  boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
};
