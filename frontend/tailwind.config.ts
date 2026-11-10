import forms from '@tailwindcss/forms'
import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#eef6ff',
          100: '#d9eaff',
          500: '#2f6fed',
          600: '#1f58d1',
          700: '#1a46a8',
        },
        surface: { DEFAULT: '#ffffff', muted: '#f4f5f7', border: '#e2e5ea' },
        danger: { 500: '#d93a3a', 600: '#b92d2d' },
        ok: { 500: '#1f9d55' },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
      // A4 page helpers used by the template builder and report editor later.
      width: { a4: '210mm' },
      height: { a4: '297mm' },
    },
  },
  plugins: [forms],
} satisfies Config
