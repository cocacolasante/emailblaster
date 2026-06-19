/** @type {import('tailwindcss').Config} */
// Design tokens (Phase 5).  ADDITIVE — formalizes the de-facto conventions
// (slate/blue/emerald palette, rounded-xl cards, soft shadows) into named
// tokens without changing any existing utility class.  New CRM/analytics UI
// uses these tokens (e.g. `shadow-card`, `rounded-card`, `bg-brand-600`,
// `duration-fast`, `ease-spring`) instead of one-off values.
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // Brand scale aliased to the blue the app already uses, so existing
        // `blue-*` classes and new `brand-*` tokens render identically.
        brand: {
          50: '#eff6ff', 100: '#dbeafe', 200: '#bfdbfe', 300: '#93c5fd',
          400: '#60a5fa', 500: '#3b82f6', 600: '#2563eb', 700: '#1d4ed8',
        },
      },
      borderRadius: {
        card: '0.75rem',   // = rounded-xl (the standard card radius)
        pill: '9999px',
      },
      boxShadow: {
        card: '0 1px 2px 0 rgb(0 0 0 / 0.05)',
        'card-hover': '0 4px 12px -2px rgb(0 0 0 / 0.10)',
      },
      transitionDuration: {
        fast: '120ms',
        base: '200ms',
      },
      transitionTimingFunction: {
        // Gentle spring for drag/drop + reflow (honored only when motion is
        // allowed — components gate with motion-safe: / useReducedMotion).
        spring: 'cubic-bezier(0.34, 1.56, 0.64, 1)',
      },
    },
  },
  plugins: [],
}
