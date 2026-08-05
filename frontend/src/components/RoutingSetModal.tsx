import { useEffect, useState } from 'react'
import { Loader2, Tag, AlertCircle } from 'lucide-react'
import { isAxiosError } from 'axios'

import { ModalShell } from '@/components/ModalShell'
import { useT } from '@/hooks/useT'
import { useCreateRoutingSet, useUpdateRoutingSet } from '@/hooks/useRoutingSets'
import type { RoutingSet } from '@/types'

/**
 * Create / edit modal for a RoutingSet (v1.4).
 *
 * Two modes:
 *   * `set === undefined` → create new
 *   * `set === RoutingSet` → edit existing (name/description/order)
 *
 * `tproxy_port` is shown read-only in edit mode (auto-allocated, can't
 * be changed). Form is minimal — three text/number fields and a
 * submit button. The server-side limit (36 sets) is communicated to
 * the caller through the parent's capacity check; this modal just
 * surfaces the eventual 409 if someone bypasses that gate.
 */
export function RoutingSetModal({
  set,
  onClose,
}: {
  set?: RoutingSet
  onClose: () => void
}) {
  const t = useT()
  const isEdit = set !== undefined

  const [name, setName] = useState(set?.name ?? '')
  const [description, setDescription] = useState(set?.description ?? '')
  const [order, setOrder] = useState(set?.order ?? 0)
  const [error, setError] = useState<string | null>(null)

  // Re-sync when the `set` prop changes (e.g. user clicks Edit on a
  // different row without closing the modal first).
  useEffect(() => {
    setName(set?.name ?? '')
    setDescription(set?.description ?? '')
    setOrder(set?.order ?? 0)
    setError(null)
  }, [set])

  const createMut = useCreateRoutingSet()
  const updateMut = useUpdateRoutingSet()
  const submitting = createMut.isPending || updateMut.isPending

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    const trimmed = name.trim()
    if (!trimmed) {
      setError(t('Name cannot be empty', 'Имя не может быть пустым'))
      return
    }

    try {
      if (isEdit && set) {
        await updateMut.mutateAsync({
          id: set.id,
          data: {
            name: trimmed,
            description: description.trim() || null,
            order,
          },
        })
      } else {
        await createMut.mutateAsync({
          name: trimmed,
          description: description.trim() || null,
          order,
        })
      }
      onClose()
    } catch (err) {
      // Surface backend 400/409 messages verbatim — they're tuned for
      // operator readability ("limit reached: ...", "name already
      // exists", etc.).
      let msg = t('Save failed', 'Не удалось сохранить')
      if (isAxiosError(err)) {
        const detail = err.response?.data?.detail
        if (typeof detail === 'string') msg = detail
      }
      setError(msg)
    }
  }

  const titleId = 'routing-set-modal-title'

  return (
    <ModalShell onClose={onClose} labelledBy={titleId}>
      <div className="w-full max-w-md rounded-2xl bg-gray-950 border border-gray-800 shadow-xl">
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          <div className="flex items-center gap-2 mb-2">
            <Tag className="h-5 w-5 text-purple-600 dark:text-purple-400" />
            <h2 id={titleId} className="text-lg font-semibold text-white">
              {isEdit
                ? t('Edit routing set', 'Редактировать набор')
                : t('Create routing set', 'Создать набор')}
            </h2>
          </div>

          {/* Name */}
          <label className="block">
            <span className="text-sm text-gray-300">
              {t('Name', 'Название')}
            </span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={64}
              autoFocus
              required
              placeholder={t('e.g. Kids', 'например, Дети')}
              className="mt-1 w-full rounded-lg bg-gray-900 border border-gray-700 px-3 py-2 text-white focus:border-purple-500 focus:ring-1 focus:ring-purple-500 outline-hidden"
            />
          </label>

          {/* Description */}
          <label className="block">
            <span className="text-sm text-gray-300">
              {t('Description (optional)', 'Описание (необязательно)')}
            </span>
            <textarea
              value={description ?? ''}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              placeholder={t(
                'What this set does — visible only in the UI',
                'Что делает этот набор — видно только в UI',
              )}
              className="mt-1 w-full rounded-lg bg-gray-900 border border-gray-700 px-3 py-2 text-white focus:border-purple-500 focus:ring-1 focus:ring-purple-500 outline-hidden resize-none"
            />
          </label>

          {/* Order */}
          <label className="block">
            <span className="text-sm text-gray-300">
              {t(
                'Display order (lower = earlier tab)',
                'Порядок (меньше = левее)',
              )}
            </span>
            <input
              type="number"
              value={order}
              onChange={(e) => setOrder(Number(e.target.value) || 0)}
              className="mt-1 w-full rounded-lg bg-gray-900 border border-gray-700 px-3 py-2 text-white focus:border-purple-500 focus:ring-1 focus:ring-purple-500 outline-hidden"
            />
          </label>

          {/* TPROXY port (read-only in edit) */}
          {isEdit && set && (
            <div className="rounded-lg bg-gray-900 border border-gray-800 p-3 text-xs text-gray-400">
              <div className="flex items-center justify-between">
                <span>
                  {t('TPROXY port (auto-assigned)', 'TPROXY-порт (auto)')}
                </span>
                <code className="font-mono text-gray-300">{set.tproxy_port}</code>
              </div>
              <div className="mt-1 text-gray-500">
                {t(
                  'Loopback only. Serves TCP+UDP on one port via xray dokodemo-door.',
                  'Только loopback. TCP+UDP на одном порту через xray dokodemo-door.',
                )}
              </div>
            </div>
          )}

          {error && (
            <div className="flex items-start gap-2 rounded-lg bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/50 p-3 text-sm text-red-700 dark:text-red-300">
              <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="px-4 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 disabled:opacity-50"
            >
              {t('Cancel', 'Отмена')}
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-4 py-2 rounded-lg bg-purple-600 hover:bg-purple-500 text-white disabled:opacity-50 flex items-center gap-2"
            >
              {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
              {isEdit ? t('Save', 'Сохранить') : t('Create', 'Создать')}
            </button>
          </div>
        </form>
      </div>
    </ModalShell>
  )
}
