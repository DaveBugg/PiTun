import { useState } from 'react'
import { Loader2, ShieldCheck } from 'lucide-react'
import { nodesApi } from '@/api/client'
import { apiErrorText } from '@/lib/apiError'
import { useT } from '@/hooks/useT'

interface Props {
  value: string
  onChange: (v: string) => void
  address: string
  port: number
  sni?: string
  /** TCP/TLS servers can be read directly; QUIC (hysteria) cannot. */
  canFetch: boolean
  /** The node carries the legacy "allow insecure" flag. */
  legacyInsecure: boolean
  onClearLegacy: () => void
}

interface Fetched {
  pin: string
  subject: string
  issuer: string
  not_after: string
  self_signed: boolean
}

const HEX_PIN = /^[0-9a-fA-F:\s,;]*$/

// What replaced "allow insecure": trust one specific certificate by its hash.
export function CertPinField({
  value, onChange, address, port, sni, canFetch, legacyInsecure, onClearLegacy,
}: Props) {
  const t = useT()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [found, setFound] = useState<Fetched | null>(null)

  const fetchPin = async () => {
    setBusy(true)
    setError(null)
    setFound(null)
    try {
      setFound(await nodesApi.certPin(address, port, sni))
    } catch (e) {
      setError(apiErrorText(e, t('Could not read the certificate', 'Не удалось прочитать сертификат')))
    } finally {
      setBusy(false)
    }
  }

  const invalid = value.trim() !== '' && !HEX_PIN.test(value)

  return (
    <div className="col-span-2 space-y-2">
      <label className="block text-xs text-gray-400">
        {t('Pinned certificate (SHA-256)', 'Закреплённый сертификат (SHA-256)')}
      </label>
      <div className="flex gap-2">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={t('64 hex characters — leave empty to verify normally', '64 hex-символа — оставьте пустым для обычной проверки')}
          spellCheck={false}
          className={`w-full rounded-sm bg-gray-800 border px-3 py-1.5 text-xs font-mono text-gray-100 focus:outline-hidden ${invalid ? 'border-red-600' : 'border-gray-700 focus:border-brand-500'}`}
        />
        {canFetch && (
          <button
            type="button"
            onClick={fetchPin}
            disabled={busy || !address}
            className="shrink-0 inline-flex items-center gap-1.5 rounded-lg bg-gray-800 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-700 disabled:opacity-50"
          >
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
            {t('Take from server', 'Взять с сервера')}
          </button>
        )}
      </div>
      <p className="text-[11px] text-gray-500">
        {t(
          'The server is accepted when its certificate hashes to this value — how a self-signed server is trusted now that xray no longer has "allow insecure".',
          'Сервер принимается, если хэш его сертификата совпадает с этим значением. Так теперь доверяют самоподписанному серверу, потому что в xray больше нет «allow insecure».',
        )}
      </p>
      {!canFetch && (
        <p className="text-[11px] text-gray-500">
          {t('QUIC servers can’t be read from here — paste the pin the server owner gives you.', 'QUIC-серверы отсюда прочитать нельзя — вставьте отпечаток от владельца сервера.')}
        </p>
      )}
      {error && <p className="text-xs text-red-400">{error}</p>}
      {found && (
        <div className="rounded-lg border border-gray-700 bg-gray-900/60 px-3 py-2 text-xs space-y-1">
          <div className="text-gray-300">
            {found.self_signed
              ? t('Self-signed certificate', 'Самоподписанный сертификат')
              : t('Certificate', 'Сертификат')}
            {found.subject && <span className="text-gray-500"> · {found.subject}</span>}
          </div>
          {found.not_after && (
            <div className="text-gray-500">{t('Valid until', 'Действителен до')} {found.not_after.slice(0, 10)}</div>
          )}
          <div className="font-mono break-all text-gray-400">{found.pin}</div>
          <button
            type="button"
            onClick={() => { onChange(found.pin); setFound(null) }}
            className="rounded-lg bg-brand-600 px-3 py-1 text-white hover:bg-brand-500"
          >
            {t('Trust this certificate', 'Доверять этому сертификату')}
          </button>
        </div>
      )}
      {legacyInsecure && (
        <div className="rounded-lg bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-700/40 px-3 py-2 text-xs text-yellow-700 dark:text-yellow-300">
          {t(
            'This node was marked “allow insecure”, which xray no longer supports: its certificate is verified normally. Pin the certificate above to trust a self-signed server.',
            'Этот узел помечен «allow insecure», что xray больше не поддерживает: сертификат проверяется как обычно. Закрепите сертификат выше, чтобы доверять самоподписанному серверу.',
          )}{' '}
          <button type="button" onClick={onClearLegacy} className="underline">
            {t('Clear the flag', 'Снять флаг')}
          </button>
        </div>
      )}
    </div>
  )
}
