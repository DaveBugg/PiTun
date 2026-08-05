import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Loader2, Globe, FileCode2, Briefcase, Newspaper, BookOpen, Wrench,
  Upload, Trash2, AlertTriangle, Plus, FileArchive, ShieldAlert,
} from 'lucide-react'

import { templatesApi } from '@/api/client'
import { useT } from '@/hooks/useT'
import { useConfirm } from '@/components/ConfirmModal'
import type { DecoyTemplate } from '@/types'

function formatKB(bytes?: number): string {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/**
 * Decoy-site template picker (since v1.3.0-beta.6).
 *
 * Renders a small card gallery so the user can choose what
 * non-authenticated visitors see at the proxy's domain when they
 * arrive without a valid Proxy-Authorization header. The default
 * (Pac-Man via daleharvey/pacman) is fine but doesn't always
 * match the domain — a corporate-y domain looks more plausible
 * with the "corporate" landing, a personal-blog domain with the
 * blog template, etc.
 *
 * No "None" option: the script needs SOMETHING to serve at the
 * root, otherwise visitors get the default Caddy page which
 * obviously screams "I am a proxy". The script-side `DECOY_REPO=
 * none` escape hatch stays available via direct env var override
 * for power users; the UI deliberately doesn't surface it.
 *
 * Used in two places:
 *   - DeployModal (auto-deploy via SSH)
 *   - ManualScriptModal (download .sh)
 * Both pass the picked id into `naive_install_script` env. */
export function TemplatePicker({
  value,
  onChange,
}: {
  value: string | undefined
  /** Called when the user selects a template. The second arg surfaces
   * the picked template's `requires_php` flag so the surrounding deploy
   * form can auto-enable its INSTALL_PHP toggle without us reaching into
   * the form's state from here. */
  onChange: (id: string, requiresPhp: boolean) => void
}) {
  const t = useT()
  const qc = useQueryClient()
  const confirm = useConfirm()

  // Custom uploads can change while the modal is open (user
  // uploads / deletes), so we DO want refetchOnWindowFocus and
  // a finite staleTime — but no aggressive polling. Built-ins
  // are static and ride along in the same list.
  const { data: templates, isLoading, error, refetch } = useQuery({
    queryKey: ['templates'],
    queryFn: () => templatesApi.list(),
    staleTime: 30_000,
  })

  // Local upload form state — toggled via the "Upload" card. Kept
  // here (not in the card itself) so the form persists across
  // a re-render of the gallery list after a successful upload.
  const [showUploadForm, setShowUploadForm] = useState(false)
  const [uploadLabel, setUploadLabel] = useState('')
  const [uploadDescription, setUploadDescription] = useState('')
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploadError, setUploadError] = useState('')
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const uploadMut = useMutation({
    mutationFn: ({ label, description, archive }: { label: string; description: string; archive: File }) =>
      templatesApi.upload(label, description, archive),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ['templates'] })
      // Auto-select the just-uploaded template — the user's intent
      // is unambiguous ("apply this cover").
      onChange(created.id, !!created.requires_php)
      setShowUploadForm(false)
      setUploadLabel('')
      setUploadDescription('')
      setUploadFile(null)
      setUploadError('')
    },
    onError: (err: unknown) => {
      // Surface the backend's validation detail verbatim so the
      // user knows whether it was the size, the extension list,
      // missing index.html, etc.
      let msg = 'Upload failed'
      if (typeof err === 'object' && err !== null) {
        const e = err as { response?: { data?: { detail?: unknown } }, message?: string }
        const detail = e.response?.data?.detail
        if (typeof detail === 'string') msg = detail
        else if (e.message) msg = e.message
      }
      setUploadError(msg)
    },
  })

  const removeMut = useMutation({
    mutationFn: (id: string) => templatesApi.remove(id),
    onSuccess: (_data, deletedId) => {
      qc.invalidateQueries({ queryKey: ['templates'] })
      // If the user deleted the currently-selected template,
      // bounce selection to the first built-in so the form's
      // submit doesn't ship a phantom id to backend.
      if (value === deletedId && templates) {
        const fallback = templates.find((t2) => t2.id !== deletedId)
        if (fallback) onChange(fallback.id, !!fallback.requires_php)
      }
    },
  })

  const handleSubmitUpload = (e: React.FormEvent) => {
    e.preventDefault()
    setUploadError('')
    if (!uploadLabel.trim()) {
      setUploadError(t('Label is required', 'Имя обязательно'))
      return
    }
    if (!uploadFile) {
      setUploadError(t('Pick a .zip archive', 'Выберите .zip архив'))
      return
    }
    uploadMut.mutate({
      label: uploadLabel.trim(),
      description: uploadDescription.trim(),
      archive: uploadFile,
    })
  }

  const handleDelete = async (tpl: DecoyTemplate) => {
    const ok = await confirm({
      title: t('Delete custom template?', 'Удалить кастомный шаблон?'),
      body: t(
        `"${tpl.label}" will be removed from PiTun. Servers currently using it will fall back to the script default on next deploy.`,
        `"${tpl.label}" будет удалён из PiTun. Серверы, использующие его, при следующем деплое получат шаблон по умолчанию.`,
      ),
      confirmLabel: t('Delete', 'Удалить'),
      danger: true,
    })
    if (ok) removeMut.mutate(tpl.id)
  }

  if (isLoading) {
    return (
      <div className="rounded-lg border border-gray-800 bg-gray-900/40 px-3 py-4 flex items-center gap-2 text-xs text-gray-500">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        {t('Loading templates…', 'Загрузка шаблонов…')}
      </div>
    )
  }
  if (error || !templates) {
    return (
      <div className="rounded-lg border border-red-200 dark:border-red-800/40 bg-red-50 dark:bg-red-950/20 px-3 py-2 text-xs text-red-700 dark:text-red-300 flex items-center justify-between">
        <span>{t('Failed to load templates', 'Не удалось загрузить шаблоны')}</span>
        <button
          type="button"
          onClick={() => refetch()}
          className="rounded-sm bg-gray-800 px-2 py-0.5 hover:bg-gray-700 text-gray-300"
        >
          {t('Retry', 'Повторить')}
        </button>
      </div>
    )
  }

  // Default to the first template if nothing selected — keeps the
  // "I never touched this" path identical to the script's own
  // built-in default (which currently is also pacman, position #0).
  const selected = value ?? templates[0]?.id

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {templates.map((tpl) => (
          <TemplateCard
            key={tpl.id}
            template={tpl}
            active={tpl.id === selected}
            onClick={() => onChange(tpl.id, !!tpl.requires_php)}
            onDelete={tpl.kind === 'custom' ? () => handleDelete(tpl) : undefined}
            removing={removeMut.isPending && removeMut.variables === tpl.id}
          />
        ))}
        {/* Upload card — last cell of the grid, dashed border to
            visually separate from selectable entries. Click flips
            into the inline upload form below; the form lands
            full-width under the grid so the file inputs aren't
            crammed into a 50%-width card. */}
        {!showUploadForm && (
          <button
            type="button"
            onClick={() => setShowUploadForm(true)}
            className="rounded-lg border border-dashed border-gray-700 bg-gray-900/30 hover:bg-gray-900/60 hover:border-gray-600 px-3 py-2 text-left transition-colors flex items-start gap-2 text-gray-400 hover:text-gray-200"
          >
            <div className="text-gray-500 mt-0.5"><Plus className="h-4 w-4" /></div>
            <div className="min-w-0">
              <div className="text-sm font-medium">{t('Upload your own', 'Загрузить свой')}</div>
              <div className="text-[11px] text-gray-500 mt-0.5 leading-snug">
                {t(
                  'Drop a .zip with index.html + assets. ≤10 MB.',
                  'Скиньте .zip с index.html + ассеты. До 10 МБ.',
                )}
              </div>
            </div>
          </button>
        )}
      </div>

      {showUploadForm && (
        <form
          onSubmit={handleSubmitUpload}
          className="rounded-lg border border-brand-700/40 bg-brand-50 dark:bg-brand-900/10 px-3 py-3 space-y-2"
        >
          <div className="text-xs text-brand-300 font-medium flex items-center gap-1.5">
            <Upload className="h-3.5 w-3.5" />
            {t('Upload custom template', 'Загрузить кастомный шаблон')}
          </div>

          {uploadError && (
            <div className="rounded-sm bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/40 px-2 py-1.5 text-xs text-red-700 dark:text-red-300 flex items-start gap-1.5">
              <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
              <span>{uploadError}</span>
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <input
              type="text"
              value={uploadLabel}
              onChange={(e) => setUploadLabel(e.target.value)}
              placeholder={t('Label, e.g. "My company"', 'Имя, напр. "Моя компания"')}
              className="rounded-lg bg-gray-900 border border-gray-800 px-2.5 py-1.5 text-xs text-gray-100 focus:border-brand-500 focus:outline-hidden"
              maxLength={80}
              required
            />
            <input
              type="text"
              value={uploadDescription}
              onChange={(e) => setUploadDescription(e.target.value)}
              placeholder={t('Short description (optional)', 'Краткое описание (необязательно)')}
              className="rounded-lg bg-gray-900 border border-gray-800 px-2.5 py-1.5 text-xs text-gray-100 focus:border-brand-500 focus:outline-hidden"
              maxLength={500}
            />
          </div>

          <div className="flex items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept=".zip"
              onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
              className="hidden"
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="rounded-lg border border-gray-700 hover:bg-gray-800 px-2.5 py-1.5 text-xs text-gray-300 inline-flex items-center gap-1.5"
            >
              <FileArchive className="h-3.5 w-3.5" />
              {uploadFile ? uploadFile.name : t('Choose .zip…', 'Выбрать .zip…')}
            </button>
            {uploadFile && (
              <span className="text-[11px] text-gray-500 font-mono">
                {formatKB(uploadFile.size)}
              </span>
            )}
            <div className="flex-1" />
            <button
              type="button"
              onClick={() => {
                setShowUploadForm(false)
                setUploadLabel('')
                setUploadDescription('')
                setUploadFile(null)
                setUploadError('')
              }}
              className="rounded-lg border border-gray-700 hover:bg-gray-800 px-2.5 py-1.5 text-xs text-gray-400"
            >
              {t('Cancel', 'Отмена')}
            </button>
            <button
              type="submit"
              disabled={uploadMut.isPending}
              className="rounded-lg bg-brand-600 hover:bg-brand-500 text-white px-3 py-1.5 text-xs font-medium inline-flex items-center gap-1.5 disabled:opacity-50"
            >
              {uploadMut.isPending ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {t('Uploading…', 'Загрузка…')}
                </>
              ) : (
                <>
                  <Upload className="h-3.5 w-3.5" />
                  {t('Upload', 'Загрузить')}
                </>
              )}
            </button>
          </div>

          <p className="text-[10px] text-gray-500 leading-snug">
            {t(
              'Static assets (html / css / js / images / fonts) and .php — no .sh / .exe etc. Must contain index.html or index.php at the root. Archives with .php auto-enable a hardened php-fpm jail on deploy.',
              'Статические ассеты (html / css / js / картинки / шрифты) и .php — нельзя .sh / .exe. Должен быть index.html или index.php в корне. Если в архиве есть .php — на сервере поднимется ужесточённый php-fpm.',
            )}
          </p>
        </form>
      )}
    </div>
  )
}


function TemplateCard({
  template, active, onClick, onDelete, removing,
}: {
  template: DecoyTemplate
  active: boolean
  onClick: () => void
  /** Custom templates only — built-ins return undefined so the
   * delete button isn't rendered at all (the click target is the
   * whole card). */
  onDelete?: () => void
  removing?: boolean
}) {
  const Icon = template.kind === 'custom'
    ? FileArchive
    : (ICON_BY_ID[template.id] ?? FileCode2)
  const kindLabel = template.kind === 'git_repo'
    ? 'git'
    : template.kind === 'single_html'
    ? 'html'
    : template.kind === 'single_php'
    ? 'php'
    : 'zip'
  return (
    <div
      className={
        'rounded-lg border transition-colors relative ' +
        (active
          ? 'border-brand-500/60 bg-brand-50 dark:bg-brand-600/10'
          : 'border-gray-800 bg-gray-900/40 hover:border-gray-700')
      }
    >
      <button
        type="button"
        onClick={onClick}
        className={
          'w-full px-3 py-2 text-left flex items-start gap-2 ' +
          (active ? 'text-brand-700 dark:text-brand-200' : 'text-gray-400 hover:text-gray-200')
        }
      >
        <div className={active ? 'text-brand-400 mt-0.5' : 'text-gray-500 mt-0.5'}>
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium flex items-center gap-1.5">
            <span className="truncate">{template.label}</span>
            <span className="text-[10px] uppercase tracking-wider text-gray-600 font-mono shrink-0">
              {kindLabel}
            </span>
            {template.requires_php && (
              <span
                className="text-[10px] uppercase tracking-wider text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-700/40 rounded-sm px-1 py-px font-mono shrink-0 inline-flex items-center gap-0.5"
                title="Selecting this template provisions a hardened php-fpm jail on the VPS so the decoy can roundtrip POSTs."
              >
                <ShieldAlert className="h-2.5 w-2.5" />php
              </span>
            )}
          </div>
          <div className="text-[11px] text-gray-500 mt-0.5 leading-snug">
            {template.description}
          </div>
        </div>
      </button>
      {onDelete && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onDelete() }}
          disabled={removing}
          title="Delete custom template"
          className="absolute top-1 right-1 rounded-sm p-1 text-gray-600 hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors disabled:opacity-50"
        >
          {removing
            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
            : <Trash2 className="h-3.5 w-3.5" />}
        </button>
      )}
    </div>
  )
}


// Lucide icon per template id — purely cosmetic, falls back to
// FileCode2 if unknown so adding a new template doesn't require
// editing this map.
const ICON_BY_ID: Record<string, React.ComponentType<{ className?: string }>> = {
  pacman: Globe,
  corporate: Briefcase,
  blog: Newspaper,
  docs: BookOpen,
  maintenance: Wrench,
  'fake-2fa': ShieldAlert,
}
