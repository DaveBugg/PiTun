import { useRef, useState } from 'react'
import { Download, Upload, Loader2, AlertTriangle, CheckCircle2, XCircle } from 'lucide-react'

import { backupApi, type BackupRestorePlan } from '@/api/client'
import { useT } from '@/hooks/useT'
import { useConfirm } from '@/components/ConfirmModal'

/**
 * Whole-box configuration backup (Settings → Backup).
 *
 * Export writes one JSON file; restore is deliberately two-step — pick a file,
 * read the plan, then confirm — because a restore rewrites config the whole LAN
 * depends on. Secrets are opt-in on export, so the default file is safe to
 * share, and restoring such a file never blanks working credentials.
 */
export default function BackupSection() {
  const t = useT()
  const confirm = useConfirm()
  const fileRef = useRef<HTMLInputElement>(null)

  const [includeSecrets, setIncludeSecrets] = useState(false)
  const [busy, setBusy] = useState<'export' | 'preview' | 'restore' | null>(null)
  const [bundle, setBundle] = useState<unknown>(null)
  const [fileName, setFileName] = useState('')
  const [mode, setMode] = useState<'merge' | 'replace'>('merge')
  const [plan, setPlan] = useState<BackupRestorePlan | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  const reset = () => { setBundle(null); setFileName(''); setPlan(null); setError(null) }

  const showError = (e: unknown) => {
    const x = e as Error & { response?: { data?: { detail?: string } } }
    setError(x.response?.data?.detail || x.message || 'Operation failed')
  }

  const doExport = async () => {
    setBusy('export'); setError(null); setDone(null)
    try {
      const data = await backupApi.export(includeSecrets)
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')
      a.download = `pitun-backup-${stamp}.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) { showError(e) } finally { setBusy(null) }
  }

  const onFile = async (f: File) => {
    reset(); setDone(null)
    setBusy('preview')
    try {
      const parsed = JSON.parse(await f.text())
      setBundle(parsed)
      setFileName(f.name)
      setPlan(await backupApi.preview(parsed, mode))
    } catch (e) { showError(e) } finally { setBusy(null) }
  }

  // Re-plan when the mode flips — "replace" adds deletions the operator must see.
  const changeMode = async (next: 'merge' | 'replace') => {
    setMode(next)
    if (!bundle) return
    setBusy('preview'); setError(null)
    try { setPlan(await backupApi.preview(bundle, next)) }
    catch (e) { showError(e) } finally { setBusy(null) }
  }

  const doRestore = async () => {
    if (!bundle || !plan) return
    const deletions = plan.plan.reduce((n, p) => n + p.would_delete, 0)
    const ok = await confirm({
      title: t('Restore configuration?', 'Восстановить конфигурацию?'),
      body: t(
        `This rewrites the sections listed above${deletions ? ` and DELETES ${deletions} row(s) missing from the backup` : ''}. The dataplane is re-applied afterwards, so routing changes take effect immediately.`,
        `Это перезапишет перечисленные выше секции${deletions ? ` и УДАЛИТ ${deletions} строк(и), которых нет в бэкапе` : ''}. После восстановления конфиг применяется заново, так что маршрутизация изменится сразу.`,
      ),
      confirmLabel: t('Restore', 'Восстановить'),
      danger: true,
    })
    if (!ok) return
    setBusy('restore'); setError(null)
    try {
      const res = await backupApi.restore(bundle, mode)
      setDone(t(
        `Restored. ${res.plan.length} section(s) applied.`,
        `Восстановлено. Применено секций: ${res.plan.length}.`,
      ))
      reset()
    } catch (e) { showError(e) } finally { setBusy(null) }
  }

  return (
    <div className="space-y-4">
      {/* Export */}
      <div className="space-y-2">
        <p className="text-[12px] text-gray-400">
          {t(
            'Download every configuration section — settings, nodes, subscriptions, routing, DNS, circles, devices, UA templates — as one JSON file.',
            'Скачать все секции конфигурации — настройки, ноды, подписки, роутинг, DNS, круги, устройства, UA-шаблоны — одним JSON-файлом.',
          )}
        </p>
        <label className="flex items-center gap-2 text-[12px] text-gray-300">
          <input
            type="checkbox"
            checked={includeSecrets}
            onChange={(e) => setIncludeSecrets(e.target.checked)}
            className="rounded-sm border-gray-600 bg-gray-800 text-brand-500"
          />
          {t('Include secrets (node credentials, subscription URLs)', 'Включить секреты (креды нод, URL подписок)')}
        </label>
        {includeSecrets && (
          <div className="flex items-start gap-2 rounded-md border border-amber-200 dark:border-amber-800/50 bg-amber-50 dark:bg-amber-950/30 p-2 text-[11px] text-amber-800 dark:text-amber-300">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            {t(
              'The file will contain working credentials in plain text. Needed to restore onto a fresh box — keep it somewhere safe.',
              'Файл будет содержать рабочие креды в открытом виде. Нужно для восстановления на чистой машине — храни надёжно.',
            )}
          </div>
        )}
        <button
          type="button"
          onClick={doExport}
          disabled={busy !== null}
          className="inline-flex items-center gap-2 rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 px-3 py-2 text-[12px] text-white font-medium"
        >
          {busy === 'export' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
          {t('Download backup', 'Скачать бэкап')}
        </button>
      </div>

      {/* Restore */}
      <div className="border-t border-gray-800 pt-4 space-y-2">
        <div className="text-[11px] uppercase tracking-wider text-gray-500">
          {t('Restore', 'Восстановление')}
        </div>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = '' }}
        />
        <div className="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={busy !== null}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-700 hover:border-brand-400/50 hover:bg-brand-50 dark:hover:bg-brand-500/12 px-3 py-2 text-[12px] text-gray-300 disabled:opacity-50"
          >
            {busy === 'preview' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
            {t('Choose backup file…', 'Выбрать файл бэкапа…')}
          </button>
          {fileName && <span className="text-[11px] text-gray-500 font-mono">{fileName}</span>}
        </div>

        {plan && (
          <div className="space-y-2">
            <div className="flex items-center gap-3 text-[12px] text-gray-300">
              <label className="flex items-center gap-1.5">
                <input type="radio" checked={mode === 'merge'} onChange={() => changeMode('merge')} />
                {t('Merge (add + update)', 'Слить (добавить + обновить)')}
              </label>
              <label className="flex items-center gap-1.5">
                <input type="radio" checked={mode === 'replace'} onChange={() => changeMode('replace')} />
                {t('Replace (also delete extras)', 'Заменить (удалить лишнее)')}
              </label>
            </div>
            <div className="text-[11px] text-gray-500">
              {t('From', 'Из')} PiTun {plan.pitun_version ?? '?'} · {plan.exported_at?.slice(0, 16).replace('T', ' ')}
            </div>
            <div className="rounded-lg border border-gray-800 overflow-hidden">
              <table className="w-full text-[11px]">
                <thead className="bg-gray-800/40 text-gray-500">
                  <tr>
                    <th className="text-left px-2 py-1 font-medium">{t('Section', 'Секция')}</th>
                    <th className="text-right px-2 py-1 font-medium">{t('add', 'доб.')}</th>
                    <th className="text-right px-2 py-1 font-medium">{t('update', 'обн.')}</th>
                    <th className="text-right px-2 py-1 font-medium">{t('delete', 'удал.')}</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.plan.map((p) => (
                    <tr key={p.section} className="border-t border-gray-800">
                      <td className="px-2 py-1 text-gray-300 font-mono">{p.section}</td>
                      <td className="px-2 py-1 text-right text-emerald-600 dark:text-emerald-400">{p.would_add || ''}</td>
                      <td className="px-2 py-1 text-right text-gray-400">{p.would_update || ''}</td>
                      <td className="px-2 py-1 text-right text-red-600 dark:text-red-400">{p.would_delete || ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {plan.warnings.map((w, i) => (
              <div key={i} className="flex items-start gap-2 rounded-md border border-amber-200 dark:border-amber-800/50 bg-amber-50 dark:bg-amber-950/30 p-2 text-[11px] text-amber-800 dark:text-amber-300">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                {w}
              </div>
            ))}
            <button
              type="button"
              onClick={doRestore}
              disabled={busy !== null}
              className="inline-flex items-center gap-2 rounded-lg bg-red-600 hover:bg-red-500 disabled:opacity-50 px-3 py-2 text-[12px] text-white font-medium"
            >
              {busy === 'restore' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
              {t('Apply restore', 'Применить восстановление')}
            </button>
          </div>
        )}
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-md border border-red-200 dark:border-red-800/50 bg-red-50 dark:bg-red-950/30 p-2 text-[12px] text-red-700 dark:text-red-300">
          <XCircle className="h-4 w-4 shrink-0 mt-0.5" />
          {error}
        </div>
      )}
      {done && (
        <div className="flex items-start gap-2 rounded-md border border-emerald-200 dark:border-emerald-800/50 bg-emerald-50 dark:bg-emerald-950/30 p-2 text-[12px] text-emerald-700 dark:text-emerald-300">
          <CheckCircle2 className="h-4 w-4 shrink-0 mt-0.5" />
          {done}
        </div>
      )}
    </div>
  )
}
