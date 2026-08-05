import { useEffect, useMemo, useState } from 'react'
import {
  AlertCircle, ArrowLeft, Fingerprint, Loader2, Pencil, Plus, Trash2, X,
} from 'lucide-react'
import { isAxiosError } from 'axios'
import { clsx } from 'clsx'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ModalShell } from '@/components/ModalShell'
import { InfoTip } from '@/components/InfoTip'
import { useConfirm } from '@/components/ConfirmModal'
import { useT } from '@/hooks/useT'
import { apiErrorText } from '@/lib/apiError'
import { uaTemplatesApi } from '@/api/client'
import type { UserAgentTemplate, UserAgentTemplateCreate } from '@/types'

/**
 * Manage the User-Agent template catalogue — the list behind the
 * subscription form's UA dropdown.
 *
 * Two views in one dialog rather than a nested `ModalShell`: the list is
 * where you return after a save, and stacked backdrops are miserable on
 * mobile. Seeded rows carry `builtin: true`, which only earns a badge and
 * a louder delete confirmation — they are editable and deletable.
 */

const KEY_PATTERN = /^[a-z0-9][a-z0-9._-]*$/
// Mirrors `FORBIDDEN_HEADER_NAMES` server-side. Checked here only so the
// error lands next to the field instead of as a 422; the backend decides.
const FORBIDDEN_HEADERS = new Set([
  'user-agent', 'host', 'content-length', 'transfer-encoding',
  'connection', 'upgrade', 'expect',
])
// RFC 7230 token — same regex the backend enforces.
const HEADER_NAME_PATTERN = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/

const INPUT_CLASS =
  'w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm ' +
  'text-gray-100 focus:border-brand-500 focus:outline-hidden'

/** A headers row while it's being edited — order-preserving, name may be blank. */
interface HeaderRow {
  name: string
  value: string
}

function toHeaderRows(headers: Record<string, string>): HeaderRow[] {
  return Object.entries(headers).map(([name, value]) => ({ name, value }))
}

/**
 * Field label wired to its input with `htmlFor`. The `InfoTip` sits
 * outside the `<label>`: nested, its copy joins the input's accessible
 * name and a screen reader reads a paragraph where "Key" was expected.
 */
function FieldLabel({
  htmlFor, label, hint,
}: {
  htmlFor: string
  label: string
  hint?: string
}) {
  return (
    <div className="flex items-center gap-1 mb-1">
      <label htmlFor={htmlFor} className="text-xs font-medium text-gray-400">
        {label}
      </label>
      {hint && <InfoTip text={hint} />}
    </div>
  )
}

// ── Editor ────────────────────────────────────────────────────────────────────

function TemplateForm({
  template,
  onDone,
  onCancel,
}: {
  /** `undefined` → create a new template. */
  template?: UserAgentTemplate
  onDone: () => void
  onCancel: () => void
}) {
  const t = useT()
  const qc = useQueryClient()
  const isEdit = template !== undefined

  const [name, setName] = useState(template?.name ?? '')
  const [key, setKey] = useState(template?.key ?? '')
  const [userAgent, setUserAgent] = useState(template?.user_agent ?? '')
  const [description, setDescription] = useState(template?.description ?? '')
  const [order, setOrder] = useState(template?.order ?? 100)
  const [rows, setRows] = useState<HeaderRow[]>(
    template ? toHeaderRows(template.headers) : [],
  )
  const [error, setError] = useState<string | null>(null)

  // Re-sync if the caller swaps which template is being edited without
  // unmounting the form first.
  useEffect(() => {
    setName(template?.name ?? '')
    setKey(template?.key ?? '')
    setUserAgent(template?.user_agent ?? '')
    setDescription(template?.description ?? '')
    setOrder(template?.order ?? 100)
    setRows(template ? toHeaderRows(template.headers) : [])
    setError(null)
  }, [template])

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['ua-templates'] })
    // A key rename re-points subscriptions server-side.
    qc.invalidateQueries({ queryKey: ['subscriptions'] })
  }

  const createMut = useMutation({
    mutationFn: (d: UserAgentTemplateCreate) => uaTemplatesApi.create(d),
    onSuccess: invalidate,
  })
  const updateMut = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<UserAgentTemplateCreate> }) =>
      uaTemplatesApi.update(id, data),
    onSuccess: invalidate,
  })
  const saving = createMut.isPending || updateMut.isPending

  const setRow = (i: number, patch: Partial<HeaderRow>) =>
    setRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))

  /** Local pre-flight so problems land next to the field, not after submit. */
  const validate = (): string | null => {
    if (!name.trim()) return t('Name is required', 'Укажите название')
    const k = key.trim().toLowerCase()
    if (!k) return t('Key is required', 'Укажите ключ')
    if (!KEY_PATTERN.test(k)) {
      return t(
        'Key must start with a letter or digit and use only lowercase letters, digits, . - _',
        'Ключ должен начинаться с буквы или цифры и содержать только строчные буквы, цифры, . - _',
      )
    }
    const ua = userAgent.trim()
    if (!ua) return t('User-Agent is required', 'Укажите User-Agent')
    // eslint-disable-next-line no-control-regex
    if (/[\r\n\0]/.test(ua)) {
      return t(
        'User-Agent must not contain line breaks',
        'User-Agent не должен содержать переводы строк',
      )
    }
    if (!/^[\x20-\x7E]*$/.test(ua)) {
      return t(
        'User-Agent must be ASCII-only — HTTP headers cannot carry other characters',
        'User-Agent должен содержать только ASCII — другие символы в HTTP-заголовках недопустимы',
      )
    }

    const seen = new Set<string>()
    for (const row of rows) {
      const hname = row.name.trim()
      if (!hname) continue
      if (!HEADER_NAME_PATTERN.test(hname)) {
        return t(
          `Invalid header name "${hname}" — no spaces or colons`,
          `Некорректное имя заголовка «${hname}» — без пробелов и двоеточий`,
        )
      }
      if (FORBIDDEN_HEADERS.has(hname.toLowerCase())) {
        return t(
          `Header "${hname}" cannot be overridden`,
          `Заголовок «${hname}» переопределить нельзя`,
        )
      }
      if (seen.has(hname.toLowerCase())) {
        return t(
          `Duplicate header "${hname}"`,
          `Дублирующийся заголовок «${hname}»`,
        )
      }
      seen.add(hname.toLowerCase())
      if (!/^[\x20-\x7E]*$/.test(row.value)) {
        return t(
          `Header "${hname}" value must be ASCII-only without line breaks`,
          `Значение заголовка «${hname}» должно быть ASCII без переводов строк`,
        )
      }
    }
    return null
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    const problem = validate()
    if (problem) {
      setError(problem)
      return
    }

    // Blank-named rows are scaffolding from clicking "Add header" —
    // silently dropped rather than treated as an error.
    const headers: Record<string, string> = {}
    for (const row of rows) {
      const hname = row.name.trim()
      if (hname) headers[hname] = row.value
    }

    const payload: UserAgentTemplateCreate = {
      key: key.trim().toLowerCase(),
      name: name.trim(),
      user_agent: userAgent.trim(),
      headers,
      description: description.trim() || null,
      order,
    }

    try {
      if (isEdit && template) {
        await updateMut.mutateAsync({ id: template.id, data: payload })
      } else {
        await createMut.mutateAsync(payload)
      }
      onDone()
    } catch (err) {
      setError(apiErrorText(err, t('Save failed', 'Не удалось сохранить')))
    }
  }

  const keyRenamed = isEdit && template && key.trim().toLowerCase() !== template.key

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="sm:col-span-2">
          <FieldLabel htmlFor="ua-tpl-name" label={t('Name', 'Название')} />
          <input
            id="ua-tpl-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            autoFocus
            maxLength={128}
            placeholder={t('e.g. My Panel', 'например, Моя панель')}
            className={INPUT_CLASS}
          />
        </div>
        <div>
          <FieldLabel
            htmlFor="ua-tpl-order"
            label={t('Order', 'Порядок')}
            hint={t(
              'Position in the subscription UA dropdown. Lower number = higher up.',
              'Позиция в списке UA у подписки. Меньше число — выше в списке.',
            )}
          />
          <input
            id="ua-tpl-order"
            type="number"
            value={order}
            onChange={(e) => setOrder(Number(e.target.value) || 0)}
            className={INPUT_CLASS}
          />
        </div>
      </div>

      <div>
        <FieldLabel
          htmlFor="ua-tpl-key"
          label={t('Key', 'Ключ')}
          hint={t(
            'Stable identifier stored on each subscription. Lowercase letters, digits, dot, dash, underscore. Renaming it automatically re-points every subscription using this template.',
            'Постоянный идентификатор, который хранится в подписке. Строчные буквы, цифры, точка, дефис, подчёркивание. При переименовании все подписки с этим шаблоном обновятся автоматически.',
          )}
        />
        <input
          id="ua-tpl-key"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          required
          maxLength={64}
          placeholder="my-panel"
          className={clsx(INPUT_CLASS, 'font-mono')}
        />
        {keyRenamed && (template?.usage_count ?? 0) > 0 && (
          <p className="mt-1 text-[11px] text-yellow-500">
            {t(
              `${template?.usage_count} subscription(s) will be re-pointed to the new key.`,
              `${template?.usage_count} подписк(а/и) будут переключены на новый ключ.`,
            )}
          </p>
        )}
      </div>

      <div>
        <FieldLabel
          htmlFor="ua-tpl-ua"
          label="User-Agent"
          hint={t(
            'The exact User-Agent string sent when fetching the subscription. Providers serve different formats per UA — and stricter panels reject anything they do not recognise. ASCII only.',
            'Строка User-Agent, отправляемая при загрузке подписки. Провайдеры выдают разные форматы в зависимости от UA, а строгие панели отклоняют незнакомые. Только ASCII.',
          )}
        />
        <textarea
          id="ua-tpl-ua"
          value={userAgent}
          onChange={(e) => setUserAgent(e.target.value)}
          required
          rows={2}
          maxLength={512}
          placeholder="MyPanel/1.0 (compatible)"
          className={clsx(INPUT_CLASS, 'font-mono resize-none')}
        />
      </div>

      <div>
        <FieldLabel
          htmlFor="ua-tpl-desc"
          label={t('Description (optional)', 'Описание (необязательно)')}
        />
        <input
          id="ua-tpl-desc"
          value={description ?? ''}
          onChange={(e) => setDescription(e.target.value)}
          maxLength={512}
          placeholder={t(
            'What this fingerprint is for — shown only here',
            'Для чего этот отпечаток — видно только здесь',
          )}
          className={INPUT_CLASS}
        />
      </div>

      {/* Custom headers */}
      <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-3">
        <div className="flex items-center justify-between mb-2">
          <span className="flex items-center gap-1 text-xs font-medium text-gray-400">
            {t('Custom headers', 'Свои заголовки')}
            <InfoTip
              className="ml-0.5"
              text={t(
                'Extra request headers sent with this User-Agent — for panels that also gate on an API key, a Referer, or a device fingerprint. Leave a value empty to REMOVE that header from the request (useful for Accept-Encoding on panels that mis-handle gzip). User-Agent, Host and other transport headers cannot be set here.',
                'Дополнительные заголовки запроса вместе с этим User-Agent — для панелей, которые проверяют ещё и API-ключ, Referer или отпечаток устройства. Пустое значение УДАЛЯЕТ заголовок из запроса (полезно для Accept-Encoding на панелях, которые неверно обрабатывают gzip). User-Agent, Host и другие транспортные заголовки задать нельзя.',
              )}
            />
          </span>
          <button
            type="button"
            onClick={() => setRows((rs) => [...rs, { name: '', value: '' }])}
            className="flex items-center gap-1 rounded-sm px-2 py-1 text-xs text-brand-400 hover:bg-gray-800 transition-colors"
          >
            <Plus className="h-3.5 w-3.5" />
            {t('Add header', 'Добавить заголовок')}
          </button>
        </div>

        {rows.length === 0 ? (
          <p className="text-[11px] text-gray-600">
            {t(
              'None — only the standard headers will be sent.',
              'Нет — будут отправлены только стандартные заголовки.',
            )}
          </p>
        ) : (
          <div className="space-y-2">
            {rows.map((row, i) => (
              <div key={i} className="flex items-center gap-2">
                <input
                  value={row.name}
                  onChange={(e) => setRow(i, { name: e.target.value })}
                  placeholder="X-Api-Key"
                  aria-label={t(`Header ${i + 1} name`, `Заголовок ${i + 1}: имя`)}
                  className={clsx(INPUT_CLASS, 'font-mono flex-1 min-w-0')}
                />
                <input
                  value={row.value}
                  onChange={(e) => setRow(i, { value: e.target.value })}
                  placeholder={t('value', 'значение')}
                  aria-label={t(`Header ${i + 1} value`, `Заголовок ${i + 1}: значение`)}
                  className={clsx(INPUT_CLASS, 'font-mono flex-1 min-w-0')}
                />
                <button
                  type="button"
                  onClick={() => setRows((rs) => rs.filter((_, idx) => idx !== i))}
                  title={t('Remove header', 'Убрать заголовок')}
                  aria-label={t(`Remove header ${i + 1}`, `Убрать заголовок ${i + 1}`)}
                  className="rounded-sm p-1.5 text-gray-500 hover:text-red-400 hover:bg-gray-800 transition-colors shrink-0"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {error && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-lg bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/50 p-3 text-sm text-red-700 dark:text-red-300"
        >
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="flex justify-end gap-3 pt-2 border-t border-gray-800">
        <button
          type="button"
          onClick={onCancel}
          disabled={saving}
          className="rounded-lg px-4 py-2 text-sm text-gray-400 hover:text-gray-100 hover:bg-gray-800 transition-colors disabled:opacity-50"
        >
          {t('Cancel', 'Отмена')}
        </button>
        <button
          type="submit"
          disabled={saving}
          className="flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 transition-colors"
        >
          {saving && <Loader2 className="h-4 w-4 animate-spin" />}
          {isEdit ? t('Save', 'Сохранить') : t('Create', 'Создать')}
        </button>
      </div>
    </form>
  )
}

// ── List ──────────────────────────────────────────────────────────────────────

function TemplateTable({
  templates,
  onEdit,
  onDelete,
  deletingId,
}: {
  templates: UserAgentTemplate[]
  onEdit: (tpl: UserAgentTemplate) => void
  onDelete: (tpl: UserAgentTemplate) => void
  deletingId: number | null
}) {
  const t = useT()

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-800">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-800 bg-gray-900/60 text-left text-xs text-gray-500">
            <th scope="col" className="px-3 py-2 font-medium">{t('Name', 'Название')}</th>
            <th scope="col" className="px-3 py-2 font-medium">{t('Key', 'Ключ')}</th>
            <th scope="col" className="px-3 py-2 font-medium">User-Agent</th>
            <th scope="col" className="px-3 py-2 font-medium whitespace-nowrap">
              {t('Headers', 'Заголовки')}
            </th>
            <th scope="col" className="px-3 py-2 font-medium whitespace-nowrap">
              {t('In use', 'Исп.')}
            </th>
            <th scope="col" className="px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          {templates.map((tpl) => {
            const headerCount = Object.keys(tpl.headers).length
            return (
              <tr key={tpl.id} className="border-b border-gray-800/60 last:border-0">
                <td className="px-3 py-2 align-top">
                  <div className="flex items-center gap-1.5">
                    <span className="text-gray-100">{tpl.name}</span>
                    {tpl.builtin && (
                      <span className="rounded-sm bg-gray-800 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-gray-500">
                        {t('default', 'станд.')}
                      </span>
                    )}
                  </div>
                  {tpl.description && (
                    <div className="mt-0.5 text-[11px] text-gray-600 max-w-[18rem]">
                      {tpl.description}
                    </div>
                  )}
                </td>
                <td className="px-3 py-2 align-top font-mono text-xs text-gray-400">
                  {tpl.key}
                </td>
                <td className="px-3 py-2 align-top">
                  <span
                    title={tpl.user_agent}
                    className="block max-w-[16rem] truncate font-mono text-xs text-gray-400"
                  >
                    {tpl.user_agent}
                  </span>
                </td>
                <td className="px-3 py-2 align-top">
                  {headerCount > 0 ? (
                    <span
                      title={Object.entries(tpl.headers)
                        .map(([k, v]) => `${k}: ${v || '(removed)'}`)
                        .join('\n')}
                      className="rounded-sm bg-brand-50 dark:bg-brand-600/20 px-1.5 py-0.5 text-xs text-brand-300"
                    >
                      +{headerCount}
                    </span>
                  ) : (
                    <span className="text-xs text-gray-600">—</span>
                  )}
                </td>
                <td className="px-3 py-2 align-top text-xs text-gray-400">
                  {tpl.usage_count > 0 ? tpl.usage_count : <span className="text-gray-600">—</span>}
                </td>
                <td className="px-3 py-2 align-top">
                  <div className="flex items-center justify-end gap-1">
                    <button
                      onClick={() => onEdit(tpl)}
                      title={t('Edit', 'Изменить')}
                      aria-label={t(`Edit ${tpl.name}`, `Изменить ${tpl.name}`)}
                      className="rounded-sm p-1.5 text-gray-500 hover:text-gray-200 hover:bg-gray-800 transition-colors"
                    >
                      <Pencil className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() => onDelete(tpl)}
                      disabled={deletingId === tpl.id}
                      title={t('Delete', 'Удалить')}
                      aria-label={t(`Delete ${tpl.name}`, `Удалить ${tpl.name}`)}
                      className="rounded-sm p-1.5 text-gray-500 hover:text-red-400 hover:bg-gray-800 transition-colors disabled:opacity-50"
                    >
                      {deletingId === tpl.id
                        ? <Loader2 className="h-4 w-4 animate-spin" />
                        : <Trash2 className="h-4 w-4" />}
                    </button>
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Shell ─────────────────────────────────────────────────────────────────────

export function UserAgentTemplatesModal({
  onClose,
  /** 60, not ModalShell's 50: this can open over the subscription form. */
  z = 60,
}: {
  onClose: () => void
  z?: number
}) {
  const t = useT()
  const qc = useQueryClient()
  const confirm = useConfirm()

  const [view, setView] = useState<'list' | 'form'>('list')
  const [editing, setEditing] = useState<UserAgentTemplate | undefined>(undefined)
  const [listError, setListError] = useState<string | null>(null)

  const { data: templates = [], isLoading } = useQuery({
    queryKey: ['ua-templates'],
    queryFn: () => uaTemplatesApi.list(),
  })

  const del = useMutation({
    mutationFn: ({ id, force }: { id: number; force: boolean }) =>
      uaTemplatesApi.delete(id, force),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ua-templates'] })
      qc.invalidateQueries({ queryKey: ['subscriptions'] })
    },
  })

  const sorted = useMemo(
    () => [...templates].sort((a, b) => a.order - b.order || a.id - b.id),
    [templates],
  )

  const handleDelete = async (tpl: UserAgentTemplate) => {
    setListError(null)
    const ok = await confirm({
      title: t(`Delete template "${tpl.name}"?`, `Удалить шаблон «${tpl.name}»?`),
      body: tpl.builtin
        ? t(
            'This is one of the built-in templates. Nothing re-creates it — you would have to add it back by hand or re-import a bundle.',
            'Это один из стандартных шаблонов. Он не восстановится сам — придётся добавить его вручную или импортировать из файла.',
          )
        : t('Cannot be undone.', 'Отменить будет нельзя.'),
      confirmLabel: t('Delete', 'Удалить'),
      danger: true,
    })
    if (!ok) return

    try {
      await del.mutateAsync({ id: tpl.id, force: false })
      return
    } catch (err) {
      // 409 = still in use. The backend names the subscriptions in
      // `detail`; re-ask with that message before forcing.
      if (!isAxiosError(err) || err.response?.status !== 409) {
        setListError(apiErrorText(err, t('Delete failed', 'Не удалось удалить')))
        return
      }
      const forceOk = await confirm({
        title: t('Template is still in use', 'Шаблон ещё используется'),
        body: apiErrorText(
          err,
          t(
            'Subscriptions still use this template.',
            'Этот шаблон ещё используется подписками.',
          ),
        ),
        confirmLabel: t('Delete anyway', 'Всё равно удалить'),
        danger: true,
      })
      if (!forceOk) return
      try {
        await del.mutateAsync({ id: tpl.id, force: true })
      } catch (forceErr) {
        setListError(apiErrorText(forceErr, t('Delete failed', 'Не удалось удалить')))
      }
    }
  }

  const titleId = 'ua-templates-modal-title'

  return (
    <ModalShell onClose={onClose} labelledBy={titleId} z={z}>
      <div className="w-[min(56rem,calc(100vw-2rem))] max-h-[calc(100vh-3rem)] overflow-y-auto rounded-2xl bg-gray-950 border border-gray-800 p-6 shadow-xl">
        <div className="flex items-center justify-between gap-3 mb-5">
          <div className="flex items-center gap-2 min-w-0">
            {view === 'form' ? (
              <button
                onClick={() => setView('list')}
                title={t('Back to list', 'Назад к списку')}
                aria-label={t('Back to list', 'Назад к списку')}
                className="rounded-sm p-1 text-gray-500 hover:text-gray-200 hover:bg-gray-800 transition-colors"
              >
                <ArrowLeft className="h-4 w-4" />
              </button>
            ) : (
              <Fingerprint className="h-5 w-5 text-brand-400 shrink-0" />
            )}
            <h2 id={titleId} className="text-base font-semibold text-gray-100 truncate">
              {view === 'list'
                ? t('User-Agent templates', 'Шаблоны User-Agent')
                : editing
                  ? t(`Edit "${editing.name}"`, `Изменить «${editing.name}»`)
                  : t('New template', 'Новый шаблон')}
            </h2>
          </div>
          {view === 'list' && (
            <button
              onClick={() => { setEditing(undefined); setView('form') }}
              className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-500 transition-colors shrink-0"
            >
              <Plus className="h-4 w-4" />
              {t('Add', 'Добавить')}
            </button>
          )}
        </div>

        {view === 'form' ? (
          <TemplateForm
            template={editing}
            onDone={() => setView('list')}
            onCancel={() => setView('list')}
          />
        ) : (
          <div className="space-y-4">
            <p className="text-xs text-gray-500">
              {t(
                'The fingerprint each subscription presents when fetching. Pick one per subscription in its User-Agent field; add extra headers here when a panel checks more than the UA string.',
                'Отпечаток, с которым подписка обращается к панели. Выбирается в поле User-Agent у подписки; добавьте свои заголовки, если панель проверяет не только строку UA.',
              )}
            </p>

            {listError && (
              <div
                role="alert"
                className="flex items-start gap-2 rounded-lg bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/50 p-3 text-sm text-red-700 dark:text-red-300"
              >
                <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
                <span>{listError}</span>
              </div>
            )}

            {isLoading ? (
              <div className="flex items-center justify-center gap-2 py-10 text-sm text-gray-500">
                <Loader2 className="h-4 w-4 animate-spin" />
                {t('Loading…', 'Загрузка…')}
              </div>
            ) : sorted.length === 0 ? (
              <div className="py-10 text-center text-sm text-gray-500">
                {t(
                  'No templates yet. Subscriptions fall back to a built-in User-Agent until you add one.',
                  'Пока нет шаблонов. Пока их нет, подписки используют встроенный User-Agent.',
                )}
              </div>
            ) : (
              <TemplateTable
                templates={sorted}
                onEdit={(tpl) => { setEditing(tpl); setView('form') }}
                onDelete={handleDelete}
                deletingId={del.isPending ? (del.variables?.id ?? null) : null}
              />
            )}

            <div className="flex justify-end pt-2 border-t border-gray-800">
              <button
                onClick={onClose}
                className="rounded-lg px-4 py-2 text-sm text-gray-400 hover:text-gray-100 hover:bg-gray-800 transition-colors"
              >
                {t('Close', 'Закрыть')}
              </button>
            </div>
          </div>
        )}
      </div>
    </ModalShell>
  )
}
