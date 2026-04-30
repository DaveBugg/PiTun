import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'
import { Cpu, MemoryStick, HardDrive, Network } from 'lucide-react'
import { clsx } from 'clsx'
import { systemApi } from '@/api/client'
import type { SystemMetric } from '@/types'

const PERIODS = [
  { value: '15m', label: '15m' },
  { value: '1h',  label: '1h' },
  { value: '3h',  label: '3h' },
  { value: '6h',  label: '6h' },
  { value: '12h', label: '12h' },
  { value: '1d',  label: '1d' },
  { value: '3d',  label: '3d' },
] as const

function formatTime(iso: string, period: string) {
  const d = new Date(iso)
  if (period === '3d' || period === '1d')
    return d.toLocaleDateString([], { day: '2-digit', month: '2-digit' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function computeNetRate(data: SystemMetric[]): { ts: string; sent_rate: number; recv_rate: number }[] {
  if (data.length < 2) return []
  const out: { ts: string; sent_rate: number; recv_rate: number }[] = []
  for (let i = 1; i < data.length; i++) {
    const dt = (new Date(data[i].ts).getTime() - new Date(data[i - 1].ts).getTime()) / 1000
    if (dt <= 0) continue
    const sent_diff = data[i].net_sent - data[i - 1].net_sent
    const recv_diff = data[i].net_recv - data[i - 1].net_recv
    if (sent_diff < 0 || recv_diff < 0) continue
    out.push({
      ts: data[i].ts,
      sent_rate: Math.round(sent_diff / dt / 1024),
      recv_rate: Math.round(recv_diff / dt / 1024),
    })
  }
  return out
}

function formatKbps(v: number) {
  if (v > 1024) return `${(v / 1024).toFixed(1)} MB/s`
  return `${v} KB/s`
}

const chartStyle = {
  grid: 'rgba(55, 65, 81, 0.4)',
  axis: '#6b7280',
  tooltip: { bg: '#111827', border: '#374151' },
}

export function MetricsCharts() {
  const [period, setPeriod] = useState('1h')

  const { data: metrics = [], isLoading } = useQuery<SystemMetric[]>({
    queryKey: ['system', 'metrics', period],
    queryFn: () => systemApi.getMetrics(period),
    refetchInterval: 60_000,
  })

  const netData = computeNetRate(metrics)
  const latest = metrics.length > 0 ? metrics[metrics.length - 1] : null

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-medium text-gray-400">System Metrics</h2>
        <div className="flex rounded-lg overflow-hidden border border-gray-700">
          {PERIODS.map(p => (
            <button
              key={p.value}
              onClick={() => setPeriod(p.value)}
              className={clsx(
                'px-2.5 py-1 text-xs font-medium transition-colors',
                period === p.value
                  ? 'bg-brand-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:text-gray-200',
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-8 text-center text-sm text-gray-500">
          Loading metrics...
        </div>
      ) : metrics.length === 0 ? (
        <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-8 text-center text-sm text-gray-500">
          No metrics data yet. Collector samples every 60 seconds.
        </div>
      ) : (
        <>
          {/* Summary cards */}
          {latest && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
              <SummaryCard
                icon={Cpu}
                label="CPU"
                value={`${latest.cpu.toFixed(1)}%`}
                color="text-yellow-400"
              />
              <SummaryCard
                icon={MemoryStick}
                label="RAM"
                value={`${latest.ram_used.toFixed(0)} / ${latest.ram_total.toFixed(0)} MB`}
                color="text-blue-400"
              />
              <SummaryCard
                icon={HardDrive}
                label="Disk"
                value={`${latest.disk_used.toFixed(1)} / ${latest.disk_total.toFixed(1)} GB`}
                color="text-purple-400"
              />
              <SummaryCard
                icon={Network}
                label="Network"
                value={netData.length > 0
                  ? `↓${formatKbps(netData[netData.length - 1].recv_rate)} ↑${formatKbps(netData[netData.length - 1].sent_rate)}`
                  : '—'}
                color="text-green-400"
              />
            </div>
          )}

          {/* Charts grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <ChartCard title="CPU %" color="#eab308">
              <ResponsiveContainer width="100%" height={140}>
                <AreaChart data={metrics}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartStyle.grid} />
                  <XAxis
                    dataKey="ts"
                    tickFormatter={(v) => formatTime(v, period)}
                    tick={{ fontSize: 10, fill: chartStyle.axis }}
                    interval="preserveStartEnd"
                  />
                  <YAxis
                    domain={[0, 100]}
                    tick={{ fontSize: 10, fill: chartStyle.axis }}
                    width={35}
                  />
                  <Tooltip
                    contentStyle={{ background: chartStyle.tooltip.bg, border: `1px solid ${chartStyle.tooltip.border}`, borderRadius: 8, fontSize: 12 }}
                    labelFormatter={(v) => formatTime(v as string, period)}
                    formatter={(v) => [`${Number(v).toFixed(1)}%`, 'CPU']}
                  />
                  <Area type="monotone" dataKey="cpu" stroke="#eab308" fill="#eab30820" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </ChartCard>

            <ChartCard title="RAM (MB)" color="#3b82f6">
              <ResponsiveContainer width="100%" height={140}>
                <AreaChart data={metrics}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartStyle.grid} />
                  <XAxis
                    dataKey="ts"
                    tickFormatter={(v) => formatTime(v, period)}
                    tick={{ fontSize: 10, fill: chartStyle.axis }}
                    interval="preserveStartEnd"
                  />
                  <YAxis
                    domain={[0, 'auto']}
                    tick={{ fontSize: 10, fill: chartStyle.axis }}
                    width={45}
                  />
                  <Tooltip
                    contentStyle={{ background: chartStyle.tooltip.bg, border: `1px solid ${chartStyle.tooltip.border}`, borderRadius: 8, fontSize: 12 }}
                    labelFormatter={(v) => formatTime(v as string, period)}
                    formatter={(v) => [`${Number(v).toFixed(0)} MB`, 'RAM Used']}
                  />
                  <Area type="monotone" dataKey="ram_used" stroke="#3b82f6" fill="#3b82f620" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </ChartCard>

            <ChartCard title="Disk (GB)" color="#a855f7">
              <ResponsiveContainer width="100%" height={140}>
                <AreaChart data={metrics}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartStyle.grid} />
                  <XAxis
                    dataKey="ts"
                    tickFormatter={(v) => formatTime(v, period)}
                    tick={{ fontSize: 10, fill: chartStyle.axis }}
                    interval="preserveStartEnd"
                  />
                  <YAxis
                    domain={[0, 'auto']}
                    tick={{ fontSize: 10, fill: chartStyle.axis }}
                    width={35}
                  />
                  <Tooltip
                    contentStyle={{ background: chartStyle.tooltip.bg, border: `1px solid ${chartStyle.tooltip.border}`, borderRadius: 8, fontSize: 12 }}
                    labelFormatter={(v) => formatTime(v as string, period)}
                    formatter={(v) => [`${Number(v).toFixed(2)} GB`, 'Used']}
                  />
                  <Area type="monotone" dataKey="disk_used" stroke="#a855f7" fill="#a855f720" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </ChartCard>

            <ChartCard title="Network (KB/s)" color="#22c55e">
              <ResponsiveContainer width="100%" height={140}>
                <AreaChart data={netData}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartStyle.grid} />
                  <XAxis
                    dataKey="ts"
                    tickFormatter={(v) => formatTime(v, period)}
                    tick={{ fontSize: 10, fill: chartStyle.axis }}
                    interval="preserveStartEnd"
                  />
                  <YAxis
                    tick={{ fontSize: 10, fill: chartStyle.axis }}
                    width={45}
                  />
                  <Tooltip
                    contentStyle={{ background: chartStyle.tooltip.bg, border: `1px solid ${chartStyle.tooltip.border}`, borderRadius: 8, fontSize: 12 }}
                    labelFormatter={(v) => formatTime(v as string, period)}
                    formatter={(v, name) => [formatKbps(Number(v)), String(name) === 'recv_rate' ? '↓ Download' : '↑ Upload']}
                  />
                  <Area type="monotone" dataKey="recv_rate" stroke="#22c55e" fill="#22c55e20" strokeWidth={1.5} dot={false} name="recv_rate" isAnimationActive={false} />
                  <Area type="monotone" dataKey="sent_rate" stroke="#06b6d4" fill="#06b6d420" strokeWidth={1.5} dot={false} name="sent_rate" isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </ChartCard>
          </div>
        </>
      )}
    </div>
  )
}

function SummaryCard({ icon: Icon, label, value, color }: {
  icon: React.ElementType; label: string; value: string; color: string
}) {
  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-3">
      <div className="flex items-center gap-2 mb-1">
        <Icon className={clsx('h-4 w-4', color)} />
        <span className="text-xs text-gray-500">{label}</span>
      </div>
      <div className="text-sm font-semibold text-gray-100 font-mono">{value}</div>
    </div>
  )
}

function ChartCard({ title, color, children }: {
  title: string; color: string; children: React.ReactNode
}) {
  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-3">
      <div className="text-xs font-medium mb-2" style={{ color }}>{title}</div>
      {children}
    </div>
  )
}
