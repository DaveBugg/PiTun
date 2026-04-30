import { useAppStore } from '@/store'

/** Returns a translator: t(english, russian) → string based on current UI language. */
export function useT() {
  const lang = useAppStore((s) => s.lang)
  return (en: string, ru: string): string => (lang === 'ru' ? ru : en)
}
