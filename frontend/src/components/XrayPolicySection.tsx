import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Clock, Loader2, RotateCcw, Save, Send } from 'lucide-react'

import { systemApi, xuiApi } from '@/api/client'
import { InfoTip } from '@/components/InfoTip'
import { useT } from '@/hooks/useT'
import { apiErrorText } from '@/lib/apiError'
import type { XuiServer } from '@/types'

/**
 * Xray connection-lifetime policy.
 *
 * Lives on the X-ui page because that's where the panels are, but the
 * values are global: they feed PiTun's own xray AND every panel template.
 * Xray's defaults kill an idle pooled connection after 5 minutes and cut
 * a half-closed stream after 2–5 seconds, which is what makes agents and
 * other long-lived clients drop for no visible reason.
 *
 * The fields are a curated set, not a raw JSON editor — these are easy to
 * set wrong and the symptom (an occasional drop) points nowhere near the
 * cause. `bufferSize` is deliberately absent: raising it multiplies
 * per-connection memory, which is the wrong trade on a Pi or a 1 GB VPS.
 */

type PolicyKey =
  | 'xray_handshake'
  | 'xray_conn_idle'
  | 'xray_uplink_only'
  | 'xray_downlink_only'
  | 'xray_tcp_keepalive_idle'
  | 'xray_tcp_keepalive_interval'

const DEFAULTS: Record<PolicyKey, number> = {
  xray_handshake: 10,
  xray_conn_idle: 3600,
  xray_uplink_only: 0,
  xray_downlink_only: 0,
  xray_tcp_keepalive_idle: 100,
  xray_tcp_keepalive_interval: 15,
}

// Keep in sync with core/xray_policy.BOUNDS — the backend rejects
// anything outside these, we just fail earlier and more kindly.
const BOUNDS: Record<PolicyKey, [number, number]> = {
  xray_handshake: [1, 600],
  xray_conn_idle: [30, 86400],
  xray_uplink_only: [0, 3600],
  xray_downlink_only: [0, 3600],
  xray_tcp_keepalive_idle: [0, 86400],
  xray_tcp_keepalive_interval: [0, 3600],
}

export function XrayPolicySection({ servers }: { servers: XuiServer[] }) {
  const t = useT()
  const qc = useQueryClient()
  const [form, setForm] = useState<Record<PolicyKey, string> | null>(null)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')

  const FIELDS: Array<{
    key: PolicyKey
    label: string
    unit: string
    help: string
  }> = [
    {
      key: 'xray_conn_idle',
      label: t('Idle timeout', 'Таймаут простоя'),
      unit: t('sec', 'сек'),
      help: t(
        "How long a connection may sit with no traffic before Xray closes it. Xray's default is 300s, which kills pooled connections that clients keep warm between requests — the next request on that socket then hangs. 3600 suits agents and SDK clients.",
        'Сколько соединение может простаивать без трафика, прежде чем Xray его закроет. Дефолт Xray — 300 с, и он убивает соединения из пула, которые клиент держит тёплыми между запросами: следующий запрос по такому сокету зависает. 3600 подходит для агентов и SDK-клиентов.',
      ),
    },
    {
      key: 'xray_downlink_only',
      label: t('Downlink-only timeout', 'Таймаут downlink-only'),
      unit: t('sec', 'сек'),
      help: t(
        'After the client half-closes its upload side, how long the response may still take. Default 5s cuts a long streaming answer mid-flight. 0 disables the timer — recommended.',
        'После того как клиент закрыл свою половину на отправку, сколько ещё может идти ответ. Дефолт 5 с обрывает длинный стриминговый ответ на середине. 0 отключает таймер — рекомендуется.',
      ),
    },
    {
      key: 'xray_uplink_only',
      label: t('Uplink-only timeout', 'Таймаут uplink-only'),
      unit: t('sec', 'сек'),
      help: t(
        'Mirror of the above for the other direction: after the server half-closes, how long the upload may continue. Default 2s. 0 disables it — recommended.',
        'Зеркало предыдущего для обратного направления: после полузакрытия со стороны сервера, сколько ещё может идти отправка. Дефолт 2 с. 0 отключает — рекомендуется.',
      ),
    },
    {
      key: 'xray_handshake',
      label: t('Handshake timeout', 'Таймаут рукопожатия'),
      unit: t('sec', 'сек'),
      help: t(
        'Budget for establishing a connection. Xray defaults to 4s, which is tight on a lossy mobile or satellite uplink.',
        'Бюджет на установку соединения. Дефолт Xray — 4 с, что мало на нестабильном мобильном или спутниковом канале.',
      ),
    },
    {
      key: 'xray_tcp_keepalive_idle',
      label: t('Keep-alive idle', 'Keep-alive простой'),
      unit: t('sec', 'сек'),
      help: t(
        "Inbound sockets only. After this much silence, TCP probes start to check the peer is still there. Xray leaves inbound keep-alive off, so with a 1-hour idle timeout a client that vanished (slept laptop, dropped NAT mapping) would hold its slot for that whole hour. Outbounds are left alone — Xray already probes those every 45s. 0 turns it off.",
        'Только для входящих соединений. После такого простоя начинаются TCP-пробы, проверяющие, жив ли собеседник. Xray по умолчанию не включает keep-alive на входящих, поэтому при часовом таймауте простоя пропавший клиент (уснувший ноутбук, отвалившийся NAT) занимал бы слот весь этот час. Исходящие не трогаем — там Xray и так зондирует каждые 45 с. 0 — выключить.',
      ),
    },
    {
      key: 'xray_tcp_keepalive_interval',
      label: t('Keep-alive interval', 'Keep-alive интервал'),
      unit: t('sec', 'сек'),
      help: t(
        'Gap between those probes once they start. Smaller means a dead peer is noticed sooner, at the cost of a little more chatter.',
        'Интервал между пробами после их начала. Меньше — быстрее заметим мёртвого собеседника, ценой чуть большего числа служебных пакетов.',
      ),
    },
  ]

  const { data: settings } = useQuery({
    queryKey: ['system', 'settings'],
    queryFn: () => systemApi.getSettings(),
  })

  useEffect(() => {
    if (!settings || form) return
    const next = {} as Record<PolicyKey, string>
    for (const key of Object.keys(DEFAULTS) as PolicyKey[]) {
      const value = (settings as unknown as Record<string, unknown>)[key]
      next[key] = String(value ?? DEFAULTS[key])
    }
    setForm(next)
  }, [settings, form])

  const save = useMutation({
    mutationFn: (patch: Record<string, number>) =>
      systemApi.updateSettings(patch as never),
    onSuccess: () => {
      setError('')
      setNote(t(
        'Saved. Restart the proxy (or reload config) to apply locally, and push to panels below.',
        'Сохранено. Перезапустите прокси (или перезагрузите конфиг), чтобы применить локально, и отправьте на панели ниже.',
      ))
      qc.invalidateQueries({ queryKey: ['system', 'settings'] })
      qc.invalidateQueries({ queryKey: ['settings'] })
    },
    onError: (err) => setError(apiErrorText(err, 'Save failed')),
  })

  const pushAll = useMutation({
    mutationFn: async () => {
      const results = await Promise.allSettled(
        servers.map((s) => xuiApi.applyPolicy(s.id)),
      )
      const ok = results.filter((r) => r.status === 'fulfilled').length
      const changed = results.filter(
        (r) => r.status === 'fulfilled' && r.value.changed,
      ).length
      return { ok, changed, total: results.length }
    },
    onSuccess: (r) => {
      setError('')
      setNote(t(
        `Pushed to ${r.ok}/${r.total} panel(s); ${r.changed} needed a change.`,
        `Отправлено на ${r.ok} из ${r.total} панел(и); изменений потребовалось: ${r.changed}.`,
      ))
    },
    onError: (err) => setError(apiErrorText(err, 'Push failed')),
  })

  if (!form) return null

  const invalid = (Object.keys(BOUNDS) as PolicyKey[]).filter((key) => {
    const n = Number(form[key])
    return !Number.isInteger(n) || n < BOUNDS[key][0] || n > BOUNDS[key][1]
  })

  const onSave = () => {
    if (invalid.length > 0) return
    const patch: Record<string, number> = {}
    for (const key of Object.keys(DEFAULTS) as PolicyKey[]) {
      patch[key] = Number(form[key])
    }
    save.mutate(patch)
  }

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Clock className="h-4 w-4 text-brand-400" />
        <h2 className="text-sm font-semibold text-gray-200">
          {t('Connection lifetime (Xray policy)', 'Время жизни соединений (политика Xray)')}
        </h2>
        <InfoTip text={t(
          "Applies to PiTun's own Xray and to every panel template. Xray's built-in defaults are tuned for short browser sessions and drop long-lived connections; these values keep agent and streaming traffic alive.",
          'Применяется к собственному Xray коробки и к шаблонам всех панелей. Встроенные дефолты Xray рассчитаны на короткие браузерные сессии и рвут долгоживущие соединения; эти значения сохраняют трафик агентов и стримов.',
        )} />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {FIELDS.map((f) => {
          const bad = invalid.includes(f.key)
          return (
            <div key={f.key} className="space-y-1">
              <div className="flex items-center gap-1.5">
                <label htmlFor={f.key} className="text-xs text-gray-400">
                  {f.label}
                </label>
                <InfoTip text={f.help} />
              </div>
              <div className="flex items-center gap-1.5">
                <input
                  id={f.key}
                  type="number"
                  value={form[f.key]}
                  onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                  className={`w-full rounded-lg bg-gray-900 border px-2.5 py-1.5 text-sm text-gray-100 focus:outline-none ${
                    bad ? 'border-red-700 focus:border-red-500'
                       : 'border-gray-800 focus:border-brand-500'
                  }`}
                />
                <span className="text-[11px] text-gray-600 shrink-0">{f.unit}</span>
              </div>
              {bad && (
                <p className="text-[11px] text-red-400">
                  {t(
                    `Allowed: ${BOUNDS[f.key][0]}–${BOUNDS[f.key][1]}`,
                    `Допустимо: ${BOUNDS[f.key][0]}–${BOUNDS[f.key][1]}`,
                  )}
                </p>
              )}
            </div>
          )
        })}
      </div>

      {error && (
        <div className="rounded-lg border border-red-900/50 bg-red-950/30 px-3 py-2 text-xs text-red-300">
          {error}
        </div>
      )}
      {note && !error && (
        <div className="rounded-lg border border-brand-700/40 bg-brand-900/20 px-3 py-2 text-xs text-brand-200">
          {note}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 pt-1">
        <button
          onClick={onSave}
          disabled={save.isPending || invalid.length > 0}
          className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-500 transition-colors disabled:opacity-50"
        >
          {save.isPending
            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
            : <Save className="h-3.5 w-3.5" />}
          {t('Save', 'Сохранить')}
        </button>
        <button
          onClick={() => pushAll.mutate()}
          disabled={pushAll.isPending || servers.length === 0}
          title={t(
            'Write these values into every registered panel and restart its Xray',
            'Записать эти значения во все зарегистрированные панели и перезапустить их Xray',
          )}
          className="flex items-center gap-1.5 rounded-lg bg-gray-800 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700 transition-colors disabled:opacity-50"
        >
          {pushAll.isPending
            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
            : <Send className="h-3.5 w-3.5" />}
          {t(
            `Apply to all panels (${servers.length})`,
            `Применить ко всем панелям (${servers.length})`,
          )}
        </button>
        <button
          onClick={() => {
            const next = {} as Record<PolicyKey, string>
            for (const key of Object.keys(DEFAULTS) as PolicyKey[]) {
              next[key] = String(DEFAULTS[key])
            }
            setForm(next)
            setNote('')
          }}
          className="flex items-center gap-1.5 rounded-lg border border-gray-800 px-3 py-1.5 text-sm text-gray-400 hover:text-gray-200 transition-colors"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          {t('Recommended', 'Рекомендованные')}
        </button>
      </div>
    </div>
  )
}
