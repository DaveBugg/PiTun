import { useState, useRef } from 'react'
import { Upload, Link } from 'lucide-react'
import { clsx } from 'clsx'
import { useQueryClient } from '@tanstack/react-query'
import { useImportNodes } from '@/hooks/useNodes'
import { nodesApi } from '@/api/client'

interface Props {
  onDone?: (count: number) => void
  onCancel?: () => void
}

type Tab = 'text' | 'file'

/** Detect whether the pasted/uploaded content is a PiTun JSON bundle
 *  vs. a list of proxy URIs. Returns `'json'` only when the parse
 *  succeeds AND the envelope carries the recognised `kind` — any
 *  other JSON-looking blob (random snippet that happens to start
 *  with `{`) gets treated as a URI list so the URI parser can do
 *  its own best-effort thing. */
function detectKind(text: string): 'json' | 'uri' {
  const t = text.trim()
  if (!t.startsWith('{') && !t.startsWith('[')) return 'uri'
  try {
    const parsed = JSON.parse(t)
    if (
      parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      && (parsed as { kind?: string }).kind === 'pitun-nodes-export'
    ) {
      return 'json'
    }
  } catch { /* not JSON — fall through */ }
  return 'uri'
}

export function UriImport({ onDone, onCancel }: Props) {
  const [tab, setTab] = useState<Tab>('text')
  const [uris, setUris] = useState('')
  const [result, setResult] = useState<{ imported: number; skipped: number; errors: string[] } | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const qc = useQueryClient()
  const [fileName, setFileName] = useState<string | null>(null)
  const [nameFromFile, setNameFromFile] = useState(true)
  const [dragActive, setDragActive] = useState(false)

  const { mutate: importNodes, isPending: importingUris } = useImportNodes()
  const [importingJson, setImportingJson] = useState(false)
  const isPending = importingUris || importingJson

  // Strip the last extension: "my-vpn.conf" -> "my-vpn".
  const stripExt = (n: string) => n.replace(/\.[^/.]+$/, '')

  // Shared by the file picker and drag-drop: read the file's text into the
  // editor, remember its name (for the "name from filename" toggle), and
  // flip to the text tab so the parsed content is visible/editable.
  const loadFile = (file: File) => {
    const reader = new FileReader()
    reader.onload = (ev) => setUris((ev.target?.result as string) ?? '')
    reader.readAsText(file)
    setFileName(stripExt(file.name))
    setNameFromFile(true)
    setTab('text')
  }

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) loadFile(file)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragActive(false)
    const file = e.dataTransfer.files?.[0]
    if (file) loadFile(file)
  }

  const handleSubmit = async () => {
    if (!uris.trim()) return
    const kind = detectKind(uris)
    if (kind === 'json') {
      // PiTun JSON bundle — route to /import-json (full-fidelity
      // round-trip). `replace=false` by default so it's an additive
      // merge — duplicates skipped on natural-key match.
      setImportingJson(true)
      try {
        const bundle = JSON.parse(uris)
        const data = await nodesApi.importJSON(bundle, false)
        qc.invalidateQueries({ queryKey: ['nodes'] })
        setResult({ imported: data.imported, skipped: data.skipped, errors: data.errors })
        onDone?.(data.imported)
      } catch (err) {
        setResult({
          imported: 0, skipped: 0,
          errors: [String((err as Error).message || err)],
        })
      } finally {
        setImportingJson(false)
      }
      return
    }
    // URI list — paste or file contents (.txt/.conf/.yaml/base64…).
    importNodes(
      { uris, nameOverride: nameFromFile && fileName ? fileName : undefined },
      {
        onSuccess: (data) => {
          setResult({ imported: data.imported, skipped: data.skipped, errors: data.errors })
          onDone?.(data.imported)
        },
      }
    )
  }

  return (
    <div className="space-y-4">
      {/* Tabs */}
      <div className="flex border-b border-gray-800">
        {(['text', 'file'] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={clsx(
              'flex items-center gap-2 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
              tab === t
                ? 'border-brand-500 text-brand-400'
                : 'border-transparent text-gray-500 hover:text-gray-300',
            )}
          >
            {t === 'text' ? <Link className="h-3.5 w-3.5" /> : <Upload className="h-3.5 w-3.5" />}
            {t === 'text' ? 'Paste URIs' : 'Upload File'}
          </button>
        ))}
      </div>

      {tab === 'file' && (
        <div
          className={clsx(
            'flex flex-col items-center justify-center rounded-xl border-2 border-dashed py-10 cursor-pointer transition-colors',
            dragActive ? 'border-brand-500 bg-brand-500/5' : 'border-gray-700 hover:border-gray-600',
          )}
          onClick={() => fileRef.current?.click()}
          onDragEnter={(e) => { e.preventDefault(); setDragActive(true) }}
          onDragOver={(e) => { e.preventDefault(); setDragActive(true) }}
          onDragLeave={(e) => { e.preventDefault(); setDragActive(false) }}
          onDrop={handleDrop}
        >
          <Upload className={clsx('h-8 w-8 mb-2', dragActive ? 'text-brand-400' : 'text-gray-600')} />
          <p className="text-sm text-gray-400">{dragActive ? 'Drop the file here' : 'Click to upload or drag & drop a nodes file'}</p>
          <p className="text-xs text-gray-600 mt-1">Auto-detect: PiTun JSON bundle, URI list, Clash YAML, WireGuard .conf, base64</p>
          <input ref={fileRef} type="file" className="hidden" accept=".txt,.yaml,.yml,.json,.conf,.ini" onChange={handleFile} />
        </div>
      )}

      {tab === 'text' && (
        <textarea
          value={uris}
          onChange={(e) => { setUris(e.target.value); setFileName(null) }}
          rows={10}
          placeholder={`Paste proxy URIs (one per line) or a PiTun JSON bundle — format is auto-detected:\n\nvless://...\nvmess://...\ntrojan://...\nnaive+https://user:pass@example.com:443/?padding=1#MyNaive`}
          className="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 font-mono focus:border-brand-500 focus:outline-hidden resize-none"
        />
      )}

      {uris.trim() && (
        <p className="text-[11px] text-gray-500">
          Detected format: <span className="font-mono text-gray-300">
            {detectKind(uris) === 'json' ? 'PiTun JSON bundle' : 'URI list / Clash / base64'}
          </span>
        </p>
      )}

      {fileName && detectKind(uris) !== 'json' && (
        <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={nameFromFile}
            onChange={(e) => setNameFromFile(e.target.checked)}
            className="h-3.5 w-3.5 rounded-sm border-gray-600 bg-gray-800 text-brand-600 focus:ring-brand-500"
          />
          Use filename as node name (<span className="font-mono text-gray-300">{fileName}</span>)
          <span className="text-gray-600">— single-config files only</span>
        </label>
      )}

      {result && (
        <div className="rounded-lg bg-gray-800 border border-gray-700 p-3 text-sm space-y-1">
          <p className="text-green-600 dark:text-green-400">Imported: {result.imported}</p>
          {result.skipped > 0 && <p className="text-yellow-600 dark:text-yellow-400">Skipped (duplicates): {result.skipped}</p>}
          {result.errors.length > 0 && (
            <details className="text-red-600 dark:text-red-400">
              <summary className="cursor-pointer">Errors: {result.errors.length}</summary>
              <ul className="mt-1 space-y-0.5 pl-4 text-xs">
                {result.errors.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            </details>
          )}
        </div>
      )}

      <div className="flex justify-end gap-3">
        {onCancel && (
          <button
            onClick={onCancel}
            className="rounded-lg px-4 py-2 text-sm text-gray-400 hover:text-gray-100 hover:bg-gray-800 transition-colors"
          >
            Cancel
          </button>
        )}
        <button
          onClick={handleSubmit}
          disabled={isPending || !uris.trim()}
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 transition-colors"
        >
          {isPending ? 'Importing…' : 'Import'}
        </button>
      </div>
    </div>
  )
}
