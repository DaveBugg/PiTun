import { useEffect, useMemo, useRef, useState } from 'react'
import * as React from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  Sparkles, Rocket, Loader2, Terminal, AlertTriangle, CheckCircle2,
  Ban, ExternalLink, Shield, Network, Layers,
} from 'lucide-react'

import { serversApi } from '@/api/client'
import { ModalShell } from '@/components/ModalShell'
import { TemplatePicker } from '@/components/TemplatePicker'
import { SshPortField } from '@/components/SshPortField'
import { DirectToggle } from '@/components/DirectToggle'
import { useT } from '@/hooks/useT'
import { effectiveJobStatus } from '@/lib/jobStatus'
import { useDeployments } from '@/hooks/useServers'
import {
  useCancelServerTask,
  useServerTask,
  useServerTaskStream,
} from '@/hooks/useServerTasks'
import type {
  DeployJobResult,
  Server,
  ServerDeploymentProtocol,
  WireGuardDeploymentConfig,
} from '@/types'

/**
 * Deploy modal — runs a remote install script over SSH and streams the
 * output live (since v1.3.0-beta.1, Phase 3).
 *
 * Two phases visible to the user:
 *   1. **Form** — domain / email / naive_user / naive_pass, defaults
 *      pulled from any existing ServerDeployment for prefill. "Run
 *      install" submits → `POST /servers/{id}/deploy` → 202 + job_id.
 *   2. **Streaming** — terminal panel rendering live stdout/stderr from
 *      the WS, status badge ticks once the WS closes with a `done`
 *      frame. On success with `result.node_id` we surface a "Go to
 *      node" link.
 *
 * Cancel is always available while the job is running. The remote
 * script keeps running on the VPS — see backend's `core.jobs.cancel`
 * docstring for the rationale; we just stop pumping its output to
 * this client. The deployment row still gets its terminal status if
 * the script eventually finishes.
 *
 * Sister to NaiveScriptModal (in Servers.tsx) which generates a
 * downloadable script for users who don't want to give PiTun their
 * SSH key. Both modals share the same form fields + save-deployment
 * flow so the badges / Create-node UX stay consistent.
 */
export function DeployModal({
  server,
  initialDirect = false,
  onClose,
}: {
  server: Server
  initialDirect?: boolean
  onClose: () => void
}) {
  const t = useT()

  // Pre-fill from existing deployment plan (if any).
  const { data: deployments = [] } = useDeployments(server.id)
  const existingNaive = deployments.find((d) => d.protocol === 'naive')
  const existingWg = deployments.find((d) => d.protocol === 'wireguard')
  const existingXui = deployments.find((d) => d.protocol === 'xui')

  // 443-slot mutex (matches the backend guard in `POST /servers/{id}/
  // deploy`). Only deployments that actually bind :443 conflict:
  //   * naive → always (Caddy)
  //   * xui   → only when domain is set (xui-pro nginx). Bare-mode xui
  //             runs on a random high port and is slot-orthogonal.
  // So bare-xui + naive can coexist; only xui-pro blocks naive.
  const existingXuiHasDomain = !!(
    (existingXui?.config as { domain?: string } | undefined)?.domain
  )
  const naiveOn443Deployed = existingNaive?.status === 'deployed'
  const xuiProDeployed = existingXui?.status === 'deployed' && existingXuiHasDomain
  const xuiAnyDeployed = existingXui?.status === 'deployed'
  // naive can't be installed when xui-pro already holds :443.
  const naiveDisabled = xuiProDeployed
  // Only one x-ui panel per server — re-running the installer
  // rotates the admin creds + API token and would orphan the stored
  // XuiServer row. Switching modes (bare ↔ pro) requires uninstall
  // first.
  const xuiDisabled = xuiAnyDeployed
  // The domain field inside XuiFields warns when naive holds :443.
  const naiveBlocksXuiPro = naiveOn443Deployed

  // Protocol selector — defaults to whichever is already deployed; if
  // neither, default to naive (the older / more common path).
  const [protocol, setProtocol] = useState<ServerDeploymentProtocol>(
    existingNaive ? 'naive' : (existingXui ? 'xui' : (existingWg ? 'wireguard' : 'naive')),
  )

  // Naive fields
  const naiveCfg = (existingNaive?.config ?? {}) as {
    domain?: string; email?: string; naive_user?: string; naive_pass?: string
    template_id?: string; install_php?: boolean
  }
  const [domain, setDomain] = useState(naiveCfg.domain ?? '')
  const [email, setEmail] = useState(naiveCfg.email ?? '')
  const [naiveUser, setNaiveUser] = useState(naiveCfg.naive_user ?? 'pitun')
  const [naivePass, setNaivePass] = useState(naiveCfg.naive_pass ?? '')
  // Decoy-site template (since v1.3.0-beta.6). `undefined` = let
  // the script use its built-in default; the picker renders it as
  // first-card-selected so the user sees what's going to be used.
  const [naiveTemplateId, setNaiveTemplateId] = useState<string | undefined>(
    naiveCfg.template_id,
  )
  // INSTALL_PHP toggle (since v1.3.0-beta.6). Picking a template with
  // `requires_php=true` auto-flips this on; the backend forces it on
  // regardless when needed, but reflecting in the UI keeps the form
  // honest about what's about to happen on the VPS.
  const [naiveInstallPhp, setNaiveInstallPhp] = useState<boolean>(
    !!naiveCfg.install_php,
  )
  // SSH port move (since v1.3.0-beta.7). Empty = no change. Same value
  // is reused for the WG section below — there's only one sshd per VPS,
  // surfacing two independent fields would invite contradiction.
  const [sshPort, setSshPort] = useState<string>('')

  // WireGuard fields. Server-side ServerDeployment.config_json mirrors
  // the WireGuardDeploymentConfig shape; we pre-fill from it so re-deploys
  // are stable. `client_name` is per-deploy (the first peer to add) —
  // not stored on the deployment row, so it always starts blank.
  const wgCfg = (existingWg?.config ?? {}) as WireGuardDeploymentConfig
  const [wgClientName, setWgClientName] = useState('')
  const [wgServerPort, setWgServerPort] = useState(
    wgCfg.server_port?.toString() ?? '51820',
  )
  const [wgDns1, setWgDns1] = useState(wgCfg.dns_1 ?? '1.1.1.1')
  const [wgDns2, setWgDns2] = useState(wgCfg.dns_2 ?? '1.0.0.1')
  const [wgAllowedIps, setWgAllowedIps] = useState(
    wgCfg.allowed_ips ?? '0.0.0.0/0,::/0',
  )

  // x-ui fields (since v1.3.0-beta.7). `domain` empty → bare-mode
  // upstream 3x-ui (no nginx, panel on self-signed cert); set → full
  // x-ui-pro stack (nginx + Let's Encrypt + fakesite). `email` is
  // only meaningful when domain is set; defaults to `admin@<domain>`
  // server-side when omitted.
  const xuiCfg = (existingXui?.config ?? {}) as {
    domain?: string; email?: string
  }
  const [xuiDomain, setXuiDomain] = useState(xuiCfg.domain ?? '')
  const [xuiEmail, setXuiEmail] = useState(xuiCfg.email ?? '')

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [jobId, setJobId] = useState<string | null>(null)
  // Off = install runs THROUGH the active node (default); on = dial the
  // server directly (SO_MARK bypass) — reach it even over a dead tunnel.
  // Seeded from the Servers page toggle so the choice carries into deploy.
  const [direct, setDirect] = useState(initialDirect)
  const [submittedProtocol, setSubmittedProtocol] = useState<
    ServerDeploymentProtocol
  >('naive')

  const buildParams = (): Record<string, unknown> | null => {
    if (protocol === 'naive') {
      if (!domain.trim() || !email.trim()) {
        setError(t('Domain and email are required', 'Domain и email обязательны'))
        return null
      }
      return {
        domain: domain.trim(),
        email: email.trim(),
        naive_user: naiveUser.trim() || undefined,
        // Empty pass → backend will auto-generate `secrets.token_urlsafe(24)`
        naive_pass: naivePass.trim() || undefined,
        template_id: naiveTemplateId,
        install_php: naiveInstallPhp || undefined,
        ssh_port: sshPort.trim() ? Number(sshPort.trim()) : undefined,
      }
    }
    if (protocol === 'xui') {
      // Domain optional → bare-mode install (Reality-only). When
      // domain is set we DON'T require email — backend defaults to
      // `admin@<domain>` for Let's Encrypt registration.
      return {
        domain: xuiDomain.trim() || undefined,
        email: xuiEmail.trim() || undefined,
        ssh_port: sshPort.trim() ? Number(sshPort.trim()) : undefined,
      }
    }
    // wireguard
    const port = wgServerPort.trim() ? Number(wgServerPort.trim()) : undefined
    if (port !== undefined && (Number.isNaN(port) || port < 1 || port > 65535)) {
      setError(t('Server port must be 1–65535', 'Порт сервера должен быть 1–65535'))
      return null
    }
    return {
      server_port: port,
      dns_1: wgDns1.trim() || undefined,
      dns_2: wgDns2.trim() || undefined,
      allowed_ips: wgAllowedIps.trim() || undefined,
      client_name: wgClientName.trim() || undefined,
      ssh_port: sshPort.trim() ? Number(sshPort.trim()) : undefined,
    }
  }

  const onStart = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    const params = buildParams()
    if (!params) return
    setSubmitting(true)
    try {
      const accepted = await serversApi.deploy(server.id, {
        protocol,
        config: params,
      }, direct)
      setSubmittedProtocol(protocol)
      setJobId(accepted.job_id)
    } catch (err: unknown) {
      // 409 SlotBusy or 400/500 — surface server message
      const msg = extractAxiosError(err)
      setError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <ModalShell onClose={onClose} labelledBy="deploy-modal-title">
      <div className="w-full max-w-3xl rounded-2xl bg-gray-950/95 border border-gray-800 p-6 m-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-start gap-3 mb-4">
          <div className="rounded-lg bg-brand-50 dark:bg-brand-600/15 p-2 text-brand-400">
            <Rocket className="h-5 w-5" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 id="deploy-modal-title" className="text-lg font-semibold text-gray-100">
              {t('Install on', 'Установить на')}{' '}
              <span className="text-brand-400">{server.name}</span>
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              {t(
                `Runs the install script over SSH on ${server.user}@${server.host}:${server.port}, streams output live.`,
                `Запустит установщик по SSH на ${server.user}@${server.host}:${server.port}, покажет вывод в реальном времени.`,
              )}
            </p>
          </div>
        </div>

        {!jobId && (
          <DeployForm
            protocol={protocol}
            setProtocol={setProtocol}
            domain={domain}
            email={email}
            naiveUser={naiveUser}
            naivePass={naivePass}
            naiveTemplateId={naiveTemplateId}
            naiveInstallPhp={naiveInstallPhp}
            sshPort={sshPort}
            serverPort={server.port}
            xuiDomain={xuiDomain}
            xuiEmail={xuiEmail}
            naiveDisabled={naiveDisabled}
            xuiDisabled={xuiDisabled}
            naiveBlocksXuiPro={naiveBlocksXuiPro}
            wgClientName={wgClientName}
            wgServerPort={wgServerPort}
            wgDns1={wgDns1}
            wgDns2={wgDns2}
            wgAllowedIps={wgAllowedIps}
            error={error}
            submitting={submitting}
            direct={direct}
            setDirect={setDirect}
            setDomain={setDomain}
            setEmail={setEmail}
            setNaiveUser={setNaiveUser}
            setNaivePass={setNaivePass}
            setNaiveTemplateId={setNaiveTemplateId}
            setNaiveInstallPhp={setNaiveInstallPhp}
            setSshPort={setSshPort}
            setXuiDomain={setXuiDomain}
            setXuiEmail={setXuiEmail}
            setWgClientName={setWgClientName}
            setWgServerPort={setWgServerPort}
            setWgDns1={setWgDns1}
            setWgDns2={setWgDns2}
            setWgAllowedIps={setWgAllowedIps}
            onSubmit={onStart}
            onCancel={onClose}
          />
        )}

        {jobId && (
          <DeployRunning
            jobId={jobId}
            server={server}
            protocol={submittedProtocol}
            onClose={onClose}
          />
        )}
      </div>
    </ModalShell>
  )
}


// ── Form (pre-start) ────────────────────────────────────────────────────────

function DeployForm(props: {
  protocol: ServerDeploymentProtocol
  setProtocol: (p: ServerDeploymentProtocol) => void
  domain: string; email: string; naiveUser: string; naivePass: string
  naiveTemplateId: string | undefined
  naiveInstallPhp: boolean
  sshPort: string
  serverPort: number
  xuiDomain: string
  xuiEmail: string
  naiveDisabled: boolean
  xuiDisabled: boolean
  naiveBlocksXuiPro: boolean
  wgClientName: string; wgServerPort: string; wgDns1: string
  wgDns2: string; wgAllowedIps: string
  error: string; submitting: boolean
  direct: boolean; setDirect: (v: boolean) => void
  setDomain: (v: string) => void
  setEmail: (v: string) => void
  setNaiveUser: (v: string) => void
  setNaivePass: (v: string) => void
  setNaiveTemplateId: (v: string | undefined) => void
  setNaiveInstallPhp: (v: boolean) => void
  setSshPort: (v: string) => void
  setXuiDomain: (v: string) => void
  setXuiEmail: (v: string) => void
  setWgClientName: (v: string) => void
  setWgServerPort: (v: string) => void
  setWgDns1: (v: string) => void
  setWgDns2: (v: string) => void
  setWgAllowedIps: (v: string) => void
  onSubmit: (e: React.FormEvent) => void
  onCancel: () => void
}) {
  const t = useT()
  const isNaive = props.protocol === 'naive'
  const isXui = props.protocol === 'xui'
  const isWg = props.protocol === 'wireguard'
  // Pre-empt the backend 409 when the operator typed a domain into
  // x-ui Fields but naive is already on :443. The warning banner in
  // XuiFields explains why; here we just gate the submit button.
  const xuiProConflict = isXui && !!props.xuiDomain.trim() && props.naiveBlocksXuiPro
  return (
    <form onSubmit={props.onSubmit}>
      {props.error && (
        <div className="mb-3 rounded-lg bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/50 px-3 py-2 text-sm text-red-700 dark:text-red-300 flex items-start gap-2">
          <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>{props.error}</span>
        </div>
      )}

      {/* Protocol toggle — naive + x-ui (443-slot mutex) + wireguard
          (UDP, orthogonal to the slot). Disabled buttons surface the
          slot conflict before the user submits and hits a 409. */}
      <div className="mb-4 grid grid-cols-3 gap-2">
        <ProtocolPick
          active={isNaive}
          icon={<Shield className="h-4 w-4" />}
          title={t('NaiveProxy', 'NaiveProxy')}
          subtitle={t('HTTPS, single tunnel', 'HTTPS, один туннель')}
          onClick={() => props.setProtocol('naive')}
          disabled={props.naiveDisabled}
          disabledHint={t(
            'x-ui-pro already holds :443 on this server. Uninstall it (or switch to bare-mode) first.',
            'x-ui-pro уже занимает :443 на этом сервере. Сначала удалите его (или переключите на bare-режим).',
          )}
        />
        <ProtocolPick
          active={isXui}
          icon={<Layers className="h-4 w-4" />}
          title={t('x-ui', 'x-ui')}
          subtitle={t('panel + Reality/TLS', 'панель + Reality/TLS')}
          onClick={() => props.setProtocol('xui')}
          disabled={props.xuiDisabled}
          disabledHint={t(
            'x-ui is already installed on this server. Uninstall it first to switch modes (bare ⇄ pro) or reinstall.',
            'x-ui уже установлен на этом сервере. Сначала удалите его, чтобы сменить режим (bare ⇄ pro) или переустановить.',
          )}
        />
        <ProtocolPick
          active={isWg}
          icon={<Network className="h-4 w-4" />}
          title={t('WireGuard', 'WireGuard')}
          subtitle={t('UDP, multi-client', 'UDP, много клиентов')}
          onClick={() => props.setProtocol('wireguard')}
        />
      </div>

      {isNaive && (
        <NaiveFields
          domain={props.domain}
          email={props.email}
          naiveUser={props.naiveUser}
          naivePass={props.naivePass}
          templateId={props.naiveTemplateId}
          installPhp={props.naiveInstallPhp}
          setDomain={props.setDomain}
          setEmail={props.setEmail}
          setNaiveUser={props.setNaiveUser}
          setNaivePass={props.setNaivePass}
          setTemplateId={props.setNaiveTemplateId}
          setInstallPhp={props.setNaiveInstallPhp}
        />
      )}
      {isXui && (
        <XuiFields
          domain={props.xuiDomain}
          email={props.xuiEmail}
          setDomain={props.setXuiDomain}
          setEmail={props.setXuiEmail}
          naiveBlocksXuiPro={props.naiveBlocksXuiPro}
        />
      )}
      {isWg && (
        <WireGuardFields
          clientName={props.wgClientName}
          serverPort={props.wgServerPort}
          dns1={props.wgDns1}
          dns2={props.wgDns2}
          allowedIps={props.wgAllowedIps}
          setClientName={props.setWgClientName}
          setServerPort={props.setWgServerPort}
          setDns1={props.setWgDns1}
          setDns2={props.setWgDns2}
          setAllowedIps={props.setWgAllowedIps}
        />
      )}

      {/* SSH port move — protocol-agnostic (there's one sshd per VPS).
          Empty = no change; otherwise the install script writes a
          sshd_config drop-in and restarts ssh. Backend persists the
          new port to Server.port on successful deploy. */}
      <div className="mt-4">
        <FieldL
          label={t('SSH port (optional)', 'SSH-порт (опционально)')}
          hint={t(
            'leave blank to keep current — applied at install time, persisted in PiTun',
            'оставьте пустым — текущий не трогаем; применяется на установке, PiTun запомнит',
          )}
        >
          <SshPortField
            value={props.sshPort}
            onChange={props.setSshPort}
            serverPort={props.serverPort}
          />
        </FieldL>
      </div>

      <div className="rounded-lg border border-yellow-200 dark:border-yellow-700/40 bg-yellow-50 dark:bg-yellow-900/10 px-3 py-2 mt-4 text-xs text-yellow-800 dark:text-yellow-200 flex items-start gap-2">
        <Sparkles className="h-3.5 w-3.5 mt-0.5 shrink-0 text-yellow-600 dark:text-yellow-400" />
        <span>
          {isNaive
            ? t(
                'The install takes 2–5 minutes (apt update, Caddy build, Let\'s Encrypt cert). Output streams live below.',
                'Установка займёт 2–5 минут (apt update, сборка Caddy, сертификат Let\'s Encrypt). Вывод появится ниже в реальном времени.',
              )
            : t(
                'The install takes 1–2 minutes (apt update, wireguard-tools, sysctl + first peer). Each peer is a separate "client" — manage them after install via the Clients button.',
                'Установка займёт 1–2 минуты (apt update, wireguard-tools, sysctl + первый клиент). Каждый клиент управляется после установки через кнопку Clients.',
              )}
        </span>
      </div>

      <div className="flex gap-2 pt-5">
        <button
          type="button"
          onClick={props.onCancel}
          className="rounded-lg border border-gray-700 px-4 py-2 text-sm text-gray-400 hover:bg-gray-800 transition-colors"
        >
          {t('Cancel', 'Отмена')}
        </button>
        <DirectToggle checked={props.direct} onChange={props.setDirect} className="px-1" />
        <button
          type="submit"
          disabled={props.submitting || xuiProConflict}
          title={xuiProConflict ? t(
            'Clear the domain to install in bare mode, or uninstall NaiveProxy first.',
            'Очистите домен для bare-режима или сначала удалите NaiveProxy.',
          ) : undefined}
          className="flex-1 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 transition-colors flex items-center justify-center gap-1.5"
        >
          {props.submitting ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              {t('Starting…', 'Запуск…')}
            </>
          ) : (
            <>
              <Rocket className="h-4 w-4" />
              {t('Run install', 'Запустить установку')}
            </>
          )}
        </button>
      </div>
    </form>
  )
}


// ── Running view (post-start) ───────────────────────────────────────────────

function DeployRunning({
  jobId,
  server,
  protocol,
  onClose,
}: {
  jobId: string
  server: Server
  protocol: ServerDeploymentProtocol
  onClose: () => void
}) {
  const t = useT()
  const qc = useQueryClient()
  const { frames, done, error: wsError } = useServerTaskStream(jobId)
  // Keep polling until the DETAIL ROW itself reports a terminal status.
  //
  // Two failure modes this closes. (1) `done === 'unknown'` means the
  // socket dropped without a done frame — treating that as finished
  // switched off the only remaining channel, so a deploy that kept
  // running server-side left the modal spinning forever. (2) the backend
  // commits the terminal row microseconds before it emits `done`, so
  // stopping at the frame left `jobRow` on a `running` snapshot with
  // result=null — no node link on success, no reason on failure, and a
  // script that exited non-zero rendered as a green "Install succeeded".
  const { data: jobRow } = useServerTask(jobId, { polling: true })
  const rowTerminal =
    jobRow != null && jobRow.status !== 'running'
  const isRunning = !rowTerminal && (done === null || done === 'unknown')

  const cancel = useCancelServerTask()
  const onCancel = () => cancel.mutate(jobId)

  // After finalize, refresh side-effects: nodes list (new naive node)
  // + servers (new ServerDeployment) + WG clients (new peer added on
  // first WG install — Clients modal cache should pick it up
  // immediately on next open).
  // Keyed off the persisted row, not the WS frame: a dropped socket
  // (`done === 'unknown'`) used to skip these entirely, so a deploy that
  // actually succeeded left the Nodes/Servers lists stale.
  useEffect(() => {
    if (!rowTerminal) return
    qc.invalidateQueries({ queryKey: ['nodes'] })
    qc.invalidateQueries({ queryKey: ['servers'] })
    qc.invalidateQueries({ queryKey: ['servers', server.id, 'deployments'] })
    qc.invalidateQueries({ queryKey: ['servers', server.id, 'wg-clients'] })
  }, [rowTerminal, qc, server.id])

  const result = useMemo<DeployJobResult | null>(() => {
    const r = jobRow?.result
    if (!r) return null
    return r as DeployJobResult
  }, [jobRow])

  // The persisted row wins once it reaches a terminal state, and it is
  // read through the same projection the tasks page uses: a script that
  // exited non-zero finalizes the JOB as `succeeded` while its RESULT
  // says failed. Without the projection this modal painted a green
  // "Install succeeded" over a broken install. The WS frame is only a
  // fallback for the window before the row lands.
  const finalStatus = rowTerminal && jobRow
    ? effectiveJobStatus(jobRow)
    : (done && done !== 'unknown' ? done : 'running')

  return (
    <div>
      {/* Status banner */}
      <StatusBanner
        status={finalStatus}
        result={result}
        protocol={protocol}
        error={jobRow?.error || wsError}
      />

      {/* Live log terminal */}
      <div className="mt-3 rounded-xl border border-gray-800 bg-black/60 overflow-hidden">
        <div className="flex items-center gap-2 px-3 py-1.5 border-b border-gray-800/60 bg-gray-900/50 text-xs text-gray-500">
          <Terminal className="h-3.5 w-3.5" />
          <span className="font-mono">job {jobId.slice(0, 12)}…</span>
          {isRunning && (
            <Loader2 className="h-3 w-3 animate-spin text-brand-400 ml-1" />
          )}
          <Link
            to={`/server-tasks?server_id=${server.id}`}
            className="ml-auto text-[11px] text-gray-500 hover:text-brand-400 inline-flex items-center gap-1"
            title={t('Open in Server-Tasks page', 'Открыть на странице Server-Tasks')}
          >
            {t('All tasks', 'Все задачи')}
            <ExternalLink className="h-3 w-3" />
          </Link>
        </div>
        <LogPanel frames={frames} fallbackTail={jobRow?.log_tail ?? null} />
      </div>

      {/* Footer actions */}
      <div className="flex gap-2 pt-4">
        {isRunning ? (
          <button
            type="button"
            onClick={onCancel}
            disabled={cancel.isPending}
            className="rounded-lg border border-red-200 dark:border-red-800/60 bg-red-50 dark:bg-red-900/20 hover:bg-red-50 dark:hover:bg-red-900/30 text-red-700 dark:text-red-300 px-3 py-1.5 text-sm flex items-center gap-1.5 disabled:opacity-50 transition-colors"
            title={t(
              'Cancel local stream — remote script keeps running on the VPS',
              'Отменить локальный поток — скрипт продолжит работу на VPS',
            )}
          >
            <Ban className="h-4 w-4" />
            {cancel.isPending ? t('Cancelling…', 'Отмена…') : t('Cancel', 'Отменить')}
          </button>
        ) : (
          // Finalized — offer "Open Nodes" for naive (a node was created);
          // for WG point at the Servers page where Clients modal lives
          // (the freshly-added peer is one row in DeploymentClient, not
          // a Node — admin chooses to export it manually).
          result?.node_id ? (
            <Link
              to={`/nodes`}
              onClick={onClose}
              className="rounded-lg bg-brand-600 hover:bg-brand-500 text-white px-3 py-1.5 text-sm font-medium flex items-center gap-1.5 transition-colors"
            >
              {t('Open Nodes', 'Открыть Nodes')}
              <ExternalLink className="h-4 w-4" />
            </Link>
          ) : (result?.client_id != null ? (
            <Link
              to={`/servers`}
              onClick={onClose}
              className="rounded-lg bg-brand-600 hover:bg-brand-500 text-white px-3 py-1.5 text-sm font-medium flex items-center gap-1.5 transition-colors"
            >
              {t('Open Servers', 'Открыть Серверы')}
              <ExternalLink className="h-4 w-4" />
            </Link>
          ) : null)
        )}

        <div className="flex-1" />

        <button
          type="button"
          onClick={onClose}
          className="rounded-lg border border-gray-700 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-800 transition-colors"
        >
          {isRunning ? t('Hide (keeps running)', 'Скрыть (продолжит работу)') : t('Close', 'Закрыть')}
        </button>
      </div>
    </div>
  )
}


// ── Per-protocol field groups ───────────────────────────────────────────────

function NaiveFields(props: {
  domain: string; email: string; naiveUser: string; naivePass: string
  templateId: string | undefined
  installPhp: boolean
  setDomain: (v: string) => void
  setEmail: (v: string) => void
  setNaiveUser: (v: string) => void
  setNaivePass: (v: string) => void
  setTemplateId: (v: string | undefined) => void
  setInstallPhp: (v: boolean) => void
}) {
  const t = useT()
  return (
    <div className="space-y-3">
      <FieldL label={t('Domain', 'Домен')} hint={t('A-record points to the VPS', 'A-запись указывает на VPS')}>
        <input
          type="text"
          value={props.domain}
          onChange={(e) => props.setDomain(e.target.value)}
          placeholder="proxy.example.com"
          className={inputCls}
          required
          autoFocus
        />
      </FieldL>
      <FieldL label={t("Let's Encrypt email", 'Email для Let\'s Encrypt')}>
        <input
          type="email"
          value={props.email}
          onChange={(e) => props.setEmail(e.target.value)}
          placeholder="me@example.com"
          className={inputCls}
          required
        />
      </FieldL>
      <FieldL label={t('Naive username', 'Имя пользователя Naive')} hint={t('default "pitun"', 'по умолчанию "pitun"')}>
        <input
          type="text"
          value={props.naiveUser}
          onChange={(e) => props.setNaiveUser(e.target.value)}
          className={inputCls}
        />
      </FieldL>
      <FieldL
        label={t('Naive password', 'Пароль Naive')}
        hint={t(
          'leave blank — the server auto-generates one',
          'оставьте пустым — сервер сгенерирует автоматически',
        )}
      >
        <input
          type="text"
          value={props.naivePass}
          onChange={(e) => props.setNaivePass(e.target.value)}
          className={inputCls}
        />
      </FieldL>
      <FieldL
        label={t('Decoy site (cover page)', 'Обложка-приманка')}
        hint={t(
          'what unauthenticated visitors see at the proxy domain',
          'что увидит случайный посетитель на домене прокси',
        )}
      >
        <TemplatePicker
          value={props.templateId}
          onChange={(id, requiresPhp) => {
            props.setTemplateId(id)
            // Auto-enable PHP toggle when the picked template ships PHP.
            // Backend forces it on regardless, but reflecting it here
            // keeps the UI honest about what's about to land on the VPS.
            if (requiresPhp) props.setInstallPhp(true)
          }}
        />
      </FieldL>
      <label className="flex items-start gap-2 cursor-pointer rounded-lg border border-gray-800 bg-gray-900/40 px-3 py-2 hover:border-gray-700">
        <input
          type="checkbox"
          checked={props.installPhp}
          onChange={(e) => props.setInstallPhp(e.target.checked)}
          className="mt-0.5 h-3.5 w-3.5 rounded-sm border-gray-600 bg-gray-800 text-brand-500 focus:ring-brand-500"
        />
        <div className="min-w-0 flex-1">
          <div className="text-xs font-medium text-gray-200">
            {t('Install hardened PHP-FPM', 'Установить ужесточённый PHP-FPM')}
          </div>
          <div className="text-[11px] text-gray-500 mt-0.5 leading-snug">
            {t(
              'Required for dynamic decoys (e.g. fake-2FA) that need real server-side roundtrips. Jail blocks exec/network/FS escapes. Skip if your decoy is pure HTML.',
              'Нужно для динамических обложек (напр. фейковая 2FA), которым требуется реальный серверный обработчик. Jail блокирует exec/сеть/FS. Пропустите, если обложка — обычный HTML.',
            )}
          </div>
        </div>
      </label>
    </div>
  )
}


function WireGuardFields(props: {
  clientName: string; serverPort: string; dns1: string; dns2: string
  allowedIps: string
  setClientName: (v: string) => void
  setServerPort: (v: string) => void
  setDns1: (v: string) => void
  setDns2: (v: string) => void
  setAllowedIps: (v: string) => void
}) {
  const t = useT()
  return (
    <div className="space-y-3">
      <FieldL
        label={t('First client name', 'Имя первого клиента')}
        hint={t(
          'leave blank → auto "client1"; alphanumeric + _-',
          'оставьте пустым → автоматически "client1"; буквы/цифры/_/-',
        )}
      >
        <input
          type="text"
          value={props.clientName}
          onChange={(e) => props.setClientName(e.target.value)}
          placeholder="phone-1"
          className={inputCls}
          autoFocus
        />
      </FieldL>
      <FieldL
        label={t('Server port (UDP)', 'Порт сервера (UDP)')}
        hint={t('default 51820', 'по умолчанию 51820')}
      >
        <input
          type="number"
          min={1}
          max={65535}
          value={props.serverPort}
          onChange={(e) => props.setServerPort(e.target.value)}
          className={inputCls}
        />
      </FieldL>
      <div className="grid grid-cols-2 gap-3">
        <FieldL label={t('DNS 1', 'DNS 1')}>
          <input
            type="text"
            value={props.dns1}
            onChange={(e) => props.setDns1(e.target.value)}
            placeholder="1.1.1.1"
            className={inputCls}
          />
        </FieldL>
        <FieldL label={t('DNS 2', 'DNS 2')}>
          <input
            type="text"
            value={props.dns2}
            onChange={(e) => props.setDns2(e.target.value)}
            placeholder="1.0.0.1"
            className={inputCls}
          />
        </FieldL>
      </div>
      <FieldL
        label={t('AllowedIPs', 'AllowedIPs')}
        hint={t(
          'what client routes through tunnel — full tunnel by default',
          'что клиент маршрутизирует в туннель — по умолчанию весь трафик',
        )}
      >
        <input
          type="text"
          value={props.allowedIps}
          onChange={(e) => props.setAllowedIps(e.target.value)}
          placeholder="0.0.0.0/0,::/0"
          className={inputCls}
        />
      </FieldL>
    </div>
  )
}


function ProtocolPick(props: {
  active: boolean
  icon: React.ReactNode
  title: string
  subtitle: string
  onClick: () => void
  /** Greyed out + non-clickable when the 443-slot is already taken
   *  by the other protocol. */
  disabled?: boolean
  disabledHint?: string
}) {
  const cls = props.disabled
    ? 'border-gray-800 bg-gray-900/20 text-gray-600 cursor-not-allowed opacity-50'
    : (props.active
      ? 'border-brand-500/60 bg-brand-50 dark:bg-brand-600/10 text-brand-700 dark:text-brand-200'
      : 'border-gray-800 bg-gray-900/40 text-gray-400 hover:border-gray-700 hover:text-gray-200')
  return (
    <button
      type="button"
      onClick={props.disabled ? undefined : props.onClick}
      disabled={props.disabled}
      title={props.disabled ? props.disabledHint : undefined}
      className={
        'rounded-lg border px-3 py-2 text-left transition-colors flex items-start gap-2 ' + cls
      }
    >
      <div className={
        props.disabled
          ? 'text-gray-700 mt-0.5'
          : (props.active ? 'text-brand-400 mt-0.5' : 'text-gray-500 mt-0.5')
      }>
        {props.icon}
      </div>
      <div className="min-w-0">
        <div className="text-sm font-medium">{props.title}</div>
        <div className="text-[11px] text-gray-500 mt-0.5">{props.subtitle}</div>
      </div>
    </button>
  )
}


// ── XuiFields ─────────────────────────────────────────────────────────────
/** Strip the leftmost label from a domain when it has 3+ parts, so
 *  Let's Encrypt registration uses the apex domain rather than the
 *  subdomain the panel sits on. `panel.example.com` →
 *  `example.com`; `example.com` → `example.com` (kept as-is on
 *  2-label hosts). Matches the symmetric logic in fake-2fa.php's
 *  brand-derivation. Doesn't handle multi-label TLDs (`.co.uk` etc.)
 *  precisely — that needs the public-suffix list, overkill for this
 *  placeholder. */
function apexDomain(domain: string): string {
  const cleaned = domain.trim().toLowerCase()
  if (!cleaned) return ''
  const parts = cleaned.split('.')
  if (parts.length <= 2) return cleaned
  return parts.slice(1).join('.')
}


function XuiFields(props: {
  domain: string
  email: string
  setDomain: (v: string) => void
  setEmail: (v: string) => void
  naiveBlocksXuiPro: boolean
}) {
  const t = useT()
  const hasDomain = !!props.domain.trim()
  const proConflict = hasDomain && props.naiveBlocksXuiPro
  return (
    <div className="space-y-3">
      {proConflict && (
        <div className="rounded-lg border border-red-200 dark:border-red-700/50 bg-red-50 dark:bg-red-900/20 px-3 py-2 text-xs text-red-800 dark:text-red-200 flex items-start gap-2">
          <Sparkles className="h-3.5 w-3.5 mt-0.5 shrink-0 text-red-600 dark:text-red-400" />
          <span>
            {t(
              'NaiveProxy already binds :443 on this VPS. Domain mode (x-ui-pro) needs :443 for nginx + Let\'s Encrypt — clear the domain to install in bare mode, or uninstall NaiveProxy first.',
              'NaiveProxy уже занимает :443 на этом VPS. Domain-режим (x-ui-pro) требует :443 для nginx + Let\'s Encrypt — очистите домен для установки в bare-режиме, либо сначала удалите NaiveProxy.',
            )}
          </span>
        </div>
      )}
      <div className="rounded-lg border border-blue-200 dark:border-blue-700/40 bg-blue-50 dark:bg-blue-900/10 px-3 py-2 text-xs text-blue-800 dark:text-blue-200 flex items-start gap-2">
        <Sparkles className="h-3.5 w-3.5 mt-0.5 shrink-0 text-blue-600 dark:text-blue-400" />
        <span>
          {hasDomain
            ? t(
                'Domain set → full x-ui-pro install: nginx + Let\'s Encrypt + random fakesite on :443 + panel behind nginx. Reality inbounds will still work alongside the fakesite via SNI.',
                'Домен задан → полный x-ui-pro: nginx + Let\'s Encrypt + рандом-фейксайт на :443 + панель за nginx. Reality-инбаунды работают параллельно через SNI.',
              )
            : t(
                'Domain empty → bare upstream 3x-ui install: no nginx, no Let\'s Encrypt, panel on a random port with self-signed cert. Reality-only setups (no fakesite needed).',
                'Без домена → голый 3x-ui: без nginx и Let\'s Encrypt, панель на случайном порту с self-signed сертификатом. Подходит для Reality-only без фейксайта.',
              )}
        </span>
      </div>

      <FieldL
        label={t('Domain (optional)', 'Домен (необязательно)')}
        hint={t(
          'leave blank for Reality-only / bare-mode install',
          'оставьте пустым для голой установки (только Reality)',
        )}
      >
        <input
          type="text"
          value={props.domain}
          onChange={(e) => props.setDomain(e.target.value)}
          placeholder="panel.example.com"
          className={inputCls}
        />
      </FieldL>

      {hasDomain && (
        <FieldL
          label={t("Let's Encrypt email (optional)", 'Email для Let\'s Encrypt (необязательно)')}
          hint={t(
            'defaults to admin@<apex-domain>',
            'по умолчанию admin@<домен-2-уровня>',
          )}
        >
          <input
            type="email"
            value={props.email}
            onChange={(e) => props.setEmail(e.target.value)}
            placeholder={`admin@${apexDomain(props.domain) || 'example.com'}`}
            className={inputCls}
          />
        </FieldL>
      )}
    </div>
  )
}


// ── Helpers ─────────────────────────────────────────────────────────────────

function StatusBanner({
  status,
  result,
  protocol,
  error,
}: {
  status: string
  result: DeployJobResult | null
  protocol: ServerDeploymentProtocol
  error: string | null | undefined
}) {
  const t = useT()
  if (status === 'running') {
    return (
      <div className="rounded-lg border border-brand-700/40 bg-brand-50 dark:bg-brand-900/10 px-3 py-2 text-sm text-brand-300 flex items-center gap-2">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span>{t('Running install…', 'Идёт установка…')}</span>
      </div>
    )
  }
  if (status === 'cancelled') {
    return (
      <div className="rounded-lg border border-yellow-200 dark:border-yellow-700/40 bg-yellow-50 dark:bg-yellow-900/10 px-3 py-2 text-sm text-yellow-700 dark:text-yellow-300 flex items-start gap-2">
        <Ban className="h-4 w-4 mt-0.5" />
        <div>
          <div className="font-medium">{t('Cancelled', 'Отменено')}</div>
          <div className="text-xs text-yellow-700 dark:text-yellow-300/80 mt-0.5">
            {t(
              'The remote script may still be running on the VPS. Re-run when ready, or check the server manually.',
              'Скрипт может всё ещё выполняться на VPS. Повторите запуск или проверьте сервер вручную.',
            )}
          </div>
        </div>
      </div>
    )
  }
  if (status === 'failed') {
    return (
      <div className="rounded-lg border border-red-200 dark:border-red-700/40 bg-red-50 dark:bg-red-900/10 px-3 py-2 text-sm text-red-700 dark:text-red-300 flex items-start gap-2">
        <AlertTriangle className="h-4 w-4 mt-0.5" />
        <div className="min-w-0">
          <div className="font-medium">{t('Install failed', 'Установка не удалась')}</div>
          {error && (
            <div className="text-xs text-red-700 dark:text-red-300/80 mt-0.5 wrap-break-word font-mono">{error}</div>
          )}
        </div>
      </div>
    )
  }
  // succeeded
  if (result?.status === 'deployed_no_uri') {
    return (
      <div className="rounded-lg border border-yellow-200 dark:border-yellow-700/40 bg-yellow-50 dark:bg-yellow-900/10 px-3 py-2 text-sm text-yellow-700 dark:text-yellow-300 flex items-start gap-2">
        <AlertTriangle className="h-4 w-4 mt-0.5" />
        <div>
          <div className="font-medium">{t('Script ran but no URI was emitted', 'Скрипт отработал, но URI не выдан')}</div>
          <div className="text-xs text-yellow-700 dark:text-yellow-300/80 mt-0.5">
            {t(
              'Check the log below — you may need to add the Node manually.',
              'Проверьте лог ниже — возможно, придётся добавить Node вручную.',
            )}
          </div>
        </div>
      </div>
    )
  }
  if (result?.status === 'failed') {
    return (
      <div className="rounded-lg border border-red-200 dark:border-red-700/40 bg-red-50 dark:bg-red-900/10 px-3 py-2 text-sm text-red-700 dark:text-red-300 flex items-start gap-2">
        <AlertTriangle className="h-4 w-4 mt-0.5" />
        <div>
          <div className="font-medium">{t('Install failed', 'Установка не удалась')}</div>
          {result?.error && (
            <div className="text-xs text-red-700 dark:text-red-300/80 mt-0.5 wrap-break-word font-mono">{result.error}</div>
          )}
        </div>
      </div>
    )
  }
  return (
    <div className="rounded-lg border border-emerald-200 dark:border-emerald-700/40 bg-emerald-50 dark:bg-emerald-900/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300 flex items-start gap-2">
      <CheckCircle2 className="h-4 w-4 mt-0.5" />
      <div className="min-w-0">
        <div className="font-medium">{t('Install succeeded', 'Установка прошла успешно')}</div>
        {protocol === 'naive' && result?.node_id != null && (
          <div className="text-xs text-emerald-700 dark:text-emerald-300/80 mt-0.5">
            {t('Node created: ', 'Создана нода: ')}
            <span className="font-mono">#{result.node_id}</span>
            {result.duration_sec ? (
              <span className="text-emerald-700 dark:text-emerald-300/60"> · {Math.round(result.duration_sec)}s</span>
            ) : null}
          </div>
        )}
        {protocol === 'wireguard' && result?.client_id != null && (
          <div className="text-xs text-emerald-700 dark:text-emerald-300/80 mt-0.5">
            {t('First client added: ', 'Создан первый клиент: ')}
            <span className="font-mono">#{result.client_id}</span>
            {result.duration_sec ? (
              <span className="text-emerald-700 dark:text-emerald-300/60"> · {Math.round(result.duration_sec)}s</span>
            ) : null}
            <div className="text-[11px] text-emerald-700 dark:text-emerald-300/60 mt-1">
              {t(
                'Open the server in Servers → Clients to download conf or export it as a Node.',
                'Откройте сервер в разделе Серверы → Клиенты, чтобы скачать конфиг или экспортировать как Ноду.',
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}


function LogPanel({
  frames,
  fallbackTail,
}: {
  frames: ReturnType<typeof useServerTaskStream>['frames']
  fallbackTail: string | null
}) {
  const ref = useRef<HTMLDivElement | null>(null)

  // Auto-scroll to bottom on new lines (the standard log-viewer UX).
  // Skip if user has scrolled up — implemented via a near-bottom check:
  // if they're within 60px of bottom, follow; otherwise stay put.
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60
    if (nearBottom) el.scrollTop = el.scrollHeight
  }, [frames.length])

  const usingFallback = frames.length === 0 && fallbackTail
  const lines = usingFallback
    ? fallbackTail.split('\n').map((line, i) => ({
        kind: 'stdout' as const,
        line,
        idx: i,
      }))
    : frames.map((f, i) => ({
        kind: f.event === 'log' ? f.kind : 'stdout',
        line: f.event === 'log' ? f.line : '',
        idx: i,
      }))

  return (
    <div
      ref={ref}
      className="font-mono text-[11px] leading-snug max-h-72 overflow-y-auto p-3"
      // Keep the typical terminal vibe — soft scrollbar, monospace.
      style={{ scrollbarWidth: 'thin' }}
    >
      {lines.length === 0 ? (
        <div className="text-gray-600">…</div>
      ) : (
        lines.map((l) => (
          <div
            key={l.idx}
            className={l.kind === 'stderr' ? 'text-red-600 dark:text-red-400' : 'text-gray-300'}
          >
            {l.line || ' '}
          </div>
        ))
      )}
    </div>
  )
}


// ── Misc ────────────────────────────────────────────────────────────────────

const inputCls =
  'w-full rounded-lg bg-gray-900 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden'

function FieldL({
  label, hint, children,
}: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="mb-1.5 flex items-center gap-2 text-xs">
        <span className="text-gray-500">{label}</span>
        {hint && <span className="text-gray-600">— {hint}</span>}
      </div>
      {children}
    </label>
  )
}

/** Best-effort message extraction from an axios error so we surface
 * whatever the backend's HTTPException.detail said (e.g. "Deploy
 * already running on server_id=7 protocol='naive'"). Falls back to
 * a generic "Failed to start". */
function extractAxiosError(err: unknown): string {
  if (typeof err === 'object' && err !== null) {
    // axios error shape — `response.data.detail` is FastAPI's standard.
    const e = err as { response?: { data?: { detail?: unknown } }, message?: string }
    const detail = e.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      // 422 from Pydantic — array of {loc, msg, ...}
      return detail
        .map((d: { msg?: string; loc?: unknown }) => (d?.msg ?? '') + (d?.loc ? ' (' + JSON.stringify(d.loc) + ')' : ''))
        .filter(Boolean)
        .join('; ')
    }
    if (e.message) return e.message
  }
  return 'Failed to start deploy'
}

// Suppress "imported but unused" during the (rare) case where a type
// alias goes through without instantiation in this file.
export type { ServerDeploymentProtocol as _ServerDeploymentProtocol }
