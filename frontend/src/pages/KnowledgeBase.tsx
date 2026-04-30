import { useState, useRef, useCallback } from 'react'
import { BookOpen, ChevronDown } from 'lucide-react'
import { clsx } from 'clsx'
import { useAppStore } from '@/store'

type Lang = 'en' | 'ru'

/* ------------------------------------------------------------------ */
/*  Section component                                                  */
/* ------------------------------------------------------------------ */

function Section({
  id,
  title,
  children,
  open,
  onToggle,
}: {
  id: string
  title: string
  children: React.ReactNode
  open: boolean
  onToggle: () => void
}) {
  return (
    <div id={id} className="rounded-xl border border-gray-800 bg-gray-900">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between p-4 text-left"
      >
        <h3 className="text-sm font-semibold text-gray-100">{title}</h3>
        <ChevronDown
          className={clsx(
            'h-4 w-4 text-gray-500 transition-transform',
            open && 'rotate-180',
          )}
        />
      </button>
      {open && (
        <div className="px-4 pb-4 text-sm text-gray-300 leading-relaxed space-y-3">
          {children}
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Reusable mini-components                                           */
/* ------------------------------------------------------------------ */

function Code({ children }: { children: React.ReactNode }) {
  return (
    <pre className="rounded-lg bg-gray-950 border border-gray-800 p-3 text-xs font-mono text-gray-400 overflow-x-auto">
      {children}
    </pre>
  )
}

function P({ children }: { children: React.ReactNode }) {
  return <p>{children}</p>
}

function B({ children }: { children: React.ReactNode }) {
  return <strong className="text-gray-100 font-medium">{children}</strong>
}

function Ul({ children }: { children: React.ReactNode }) {
  return <ul className="list-disc list-inside space-y-1">{children}</ul>
}

/* ------------------------------------------------------------------ */
/*  Section definitions                                                */
/* ------------------------------------------------------------------ */

interface SectionDef {
  id: string
  title: Record<Lang, string>
  content: Record<Lang, React.ReactNode>
}

const SECTIONS: SectionDef[] = [
  /* 1. Getting Started */
  {
    id: 'getting-started',
    title: { en: 'Getting Started', ru: 'Начало работы' },
    content: {
      en: (
        <>
          <P>
            <B>PiTun</B> is a self-hosted transparent proxy manager for Raspberry Pi 4/5.
            It sits on your LAN alongside the router, intercepts traffic from devices that set
            their gateway to the RPi, and routes it through VPN nodes (xray-core) based on
            configurable rules.
          </P>
          <Code>{`Devices (gateway=192.168.1.100)
  |
RPi4 (192.168.1.100)
  |
nftables TPROXY -> xray-core -> routing rules
  |                               |
  |- geoip:ru, bypass -> direct -> router -> internet
  |- geosite:ads      -> block
  '- everything else  -> VLESS/VMess/Trojan -> VPN server -> internet`}</Code>
          <Ul>
            <li>No client-side configuration needed — devices just change their gateway IP</li>
            <li>IoT, phones, consoles, PCs — everything works transparently</li>
            <li>Three simultaneous proxy endpoints: TPROXY, SOCKS5, HTTP</li>
            <li>Default credentials: <code className="text-gray-200">admin / password</code> — change after first login</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <P>
            <B>PiTun</B> — self-hosted менеджер прозрачного прокси для Raspberry Pi 4/5.
            Устанавливается в локальную сеть рядом с роутером, перехватывает трафик устройств,
            у которых шлюз указан на RPi, и маршрутизирует его через VPN-ноды (xray-core)
            по настраиваемым правилам.
          </P>
          <Code>{`Устройства (шлюз=192.168.1.100)
  |
RPi4 (192.168.1.100)
  |
nftables TPROXY -> xray-core -> правила маршрутизации
  |                               |
  |- geoip:ru, bypass -> напрямую -> роутер -> интернет
  |- geosite:ads      -> блок
  '- всё остальное    -> VLESS/VMess/Trojan -> VPN сервер -> интернет`}</Code>
          <Ul>
            <li>Не нужно настраивать клиенты — устройства просто меняют шлюз</li>
            <li>IoT, телефоны, консоли, ПК — всё работает прозрачно</li>
            <li>Три одновременных прокси-эндпоинта: TPROXY, SOCKS5, HTTP</li>
            <li>Логин по умолчанию: <code className="text-gray-200">admin / password</code> — смените после первого входа</li>
          </Ul>
        </>
      ),
    },
  },

  /* 2. Network Modes */
  {
    id: 'network-modes',
    title: { en: 'Network Modes', ru: 'Сетевые режимы' },
    content: {
      en: (
        <>
          <P>PiTun supports three inbound (network) modes for intercepting traffic:</P>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Mode</th>
                <th className="py-2 pr-4">How it works</th>
                <th className="py-2">When to use</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">TPROXY</td><td className="py-2 pr-4">nftables + dokodemo-door. Kernel-level transparent proxy.</td><td className="py-2">Default, recommended. Devices set gateway=RPi.</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">TUN</td><td className="py-2 pr-4">Virtual tun0 interface. xray routes traffic internally.</td><td className="py-2">When TPROXY unavailable. Requires xray-core &ge; 1.8.</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Both</td><td className="py-2 pr-4">TPROXY + TUN simultaneously.</td><td className="py-2">Rarely needed, specific compatibility scenarios.</td></tr>
            </tbody>
          </table>
          <P>Additionally, two explicit proxy inbounds run on the LAN:</P>
          <Ul>
            <li><B>SOCKS5 :1080</B> — configure in browser/app proxy settings, host = RPi IP</li>
            <li><B>HTTP :8080</B> — for apps that only support HTTP proxy</li>
          </Ul>
          <P>All three inbounds (TPROXY/TUN + SOCKS5 + HTTP) share the same outbound nodes and routing rules.</P>
        </>
      ),
      ru: (
        <>
          <P>PiTun поддерживает три входящих (сетевых) режима перехвата трафика:</P>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Режим</th>
                <th className="py-2 pr-4">Как работает</th>
                <th className="py-2">Когда использовать</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">TPROXY</td><td className="py-2 pr-4">nftables + dokodemo-door. Прозрачный прокси на уровне ядра.</td><td className="py-2">По умолчанию. Устройства ставят шлюз=RPi.</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">TUN</td><td className="py-2 pr-4">Виртуальный интерфейс tun0. xray маршрутизирует трафик.</td><td className="py-2">Когда TPROXY недоступен. Требуется xray &ge; 1.8.</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Both</td><td className="py-2 pr-4">TPROXY + TUN одновременно.</td><td className="py-2">Редко нужно, для специфических сценариев.</td></tr>
            </tbody>
          </table>
          <P>Дополнительно на LAN работают два явных прокси:</P>
          <Ul>
            <li><B>SOCKS5 :1080</B> — настройте в браузере/приложении, хост = IP RPi</li>
            <li><B>HTTP :8080</B> — для приложений без поддержки SOCKS5</li>
          </Ul>
          <P>Все три входа (TPROXY/TUN + SOCKS5 + HTTP) используют общие исходящие ноды и правила маршрутизации.</P>
        </>
      ),
    },
  },

  /* 3. Proxy Modes */
  {
    id: 'proxy-modes',
    title: { en: 'Proxy Modes', ru: 'Режимы прокси' },
    content: {
      en: (
        <>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Mode</th>
                <th className="py-2 pr-4">Behavior</th>
                <th className="py-2">Use case</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">Global</td><td className="py-2 pr-4">All traffic goes through the active VPN node</td><td className="py-2">Full VPN, privacy, all traffic encrypted</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Rules</td><td className="py-2 pr-4">Traffic routed based on configured rules (domain, IP, geoip, etc.)</td><td className="py-2">Selective routing — bypass local, proxy blocked sites</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Bypass</td><td className="py-2 pr-4">All traffic goes direct, proxy inactive</td><td className="py-2">Temporarily disable proxy without stopping xray</td></tr>
            </tbody>
          </table>
          <P>Switch modes from the Dashboard. The change takes effect immediately (xray config is regenerated and reloaded).</P>
        </>
      ),
      ru: (
        <>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Режим</th>
                <th className="py-2 pr-4">Поведение</th>
                <th className="py-2">Когда</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">Global</td><td className="py-2 pr-4">Весь трафик через активную VPN-ноду</td><td className="py-2">Полный VPN, приватность</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Rules</td><td className="py-2 pr-4">Трафик маршрутизируется по правилам (домен, IP, geoip и др.)</td><td className="py-2">Селективная маршрутизация</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Bypass</td><td className="py-2 pr-4">Весь трафик напрямую, прокси неактивен</td><td className="py-2">Временное отключение без остановки xray</td></tr>
            </tbody>
          </table>
          <P>Переключение на Dashboard. Изменение применяется мгновенно (конфиг xray пересоздаётся и перезагружается).</P>
        </>
      ),
    },
  },

  /* 4. Protocols */
  {
    id: 'protocols',
    title: { en: 'Protocols', ru: 'Протоколы' },
    content: {
      en: (
        <>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Protocol</th>
                <th className="py-2 pr-4">Transports</th>
                <th className="py-2">TLS</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">VLESS</td><td className="py-2 pr-4">TCP, WS, gRPC, H2, XHTTP, HTTPUpgrade, KCP, QUIC</td><td className="py-2">none, TLS, Reality</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">VMess</td><td className="py-2 pr-4">TCP, WS, gRPC, H2, XHTTP, HTTPUpgrade, KCP, QUIC</td><td className="py-2">none, TLS, Reality</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Trojan</td><td className="py-2 pr-4">TCP, WS, gRPC, H2, XHTTP, HTTPUpgrade, KCP, QUIC</td><td className="py-2">none, TLS, Reality</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Shadowsocks</td><td className="py-2 pr-4">TCP</td><td className="py-2">-</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">WireGuard</td><td className="py-2 pr-4">native</td><td className="py-2">native</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">SOCKS5</td><td className="py-2 pr-4">TCP</td><td className="py-2">-</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Hysteria2</td><td className="py-2 pr-4">QUIC</td><td className="py-2">native</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">NaiveProxy</td><td className="py-2 pr-4">HTTPS (Caddy + forwardproxy)</td><td className="py-2">native (sidecar)</td></tr>
            </tbody>
          </table>
          <P>URI import formats: <code className="text-gray-200">vless://</code> <code className="text-gray-200">vmess://</code> <code className="text-gray-200">trojan://</code> <code className="text-gray-200">ss://</code> <code className="text-gray-200">wg://</code> <code className="text-gray-200">socks5://</code> <code className="text-gray-200">hy2://</code> <code className="text-gray-200">naive+https://</code></P>
          <P>Clash YAML import is also supported.</P>
        </>
      ),
      ru: (
        <>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Протокол</th>
                <th className="py-2 pr-4">Транспорт</th>
                <th className="py-2">TLS</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">VLESS</td><td className="py-2 pr-4">TCP, WS, gRPC, H2, XHTTP, HTTPUpgrade, KCP, QUIC</td><td className="py-2">нет, TLS, Reality</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">VMess</td><td className="py-2 pr-4">TCP, WS, gRPC, H2, XHTTP, HTTPUpgrade, KCP, QUIC</td><td className="py-2">нет, TLS, Reality</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Trojan</td><td className="py-2 pr-4">TCP, WS, gRPC, H2, XHTTP, HTTPUpgrade, KCP, QUIC</td><td className="py-2">нет, TLS, Reality</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Shadowsocks</td><td className="py-2 pr-4">TCP</td><td className="py-2">-</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">WireGuard</td><td className="py-2 pr-4">native</td><td className="py-2">native</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">SOCKS5</td><td className="py-2 pr-4">TCP</td><td className="py-2">-</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Hysteria2</td><td className="py-2 pr-4">QUIC</td><td className="py-2">native</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">NaiveProxy</td><td className="py-2 pr-4">HTTPS (Caddy + forwardproxy)</td><td className="py-2">native (sidecar)</td></tr>
            </tbody>
          </table>
          <P>Форматы URI-импорта: <code className="text-gray-200">vless://</code> <code className="text-gray-200">vmess://</code> <code className="text-gray-200">trojan://</code> <code className="text-gray-200">ss://</code> <code className="text-gray-200">wg://</code> <code className="text-gray-200">socks5://</code> <code className="text-gray-200">hy2://</code> <code className="text-gray-200">naive+https://</code></P>
          <P>Также поддерживается импорт из Clash YAML.</P>
        </>
      ),
    },
  },

  /* 4b. NaiveProxy */
  {
    id: 'naiveproxy',
    title: { en: 'NaiveProxy (sidecar)', ru: 'NaiveProxy (sidecar)' },
    content: {
      en: (
        <>
          <P>
            <B>NaiveProxy</B> (by klzgrad) is an HTTPS forward-proxy client that masquerades its traffic as normal Chrome-to-Caddy HTTPS. This makes it highly resistant to DPI — the traffic is literally the same handshake, TLS fingerprint, and HTTP/2 behavior as Chrome.
          </P>
          <P>
            Unlike other protocols, NaiveProxy is <B>not built into xray-core</B>. PiTun runs it as a <B>Docker sidecar</B> — one small container per naive node, bound to <code className="text-gray-200">127.0.0.1:&lt;internal_port&gt;</code>. xray routes outbound traffic through it as a local SOCKS5 outbound.
          </P>
          <P><B>How it works:</B></P>
          <Ul>
            <li>You add a naive node → backend allocates a free loopback port (20800–20899)</li>
            <li>A container <code className="text-gray-200">pitun-naive-&lt;id&gt;</code> starts with <code className="text-gray-200">network_mode: host</code> (loopback only)</li>
            <li>xray outbound: <code className="text-gray-200">socks → 127.0.0.1:&lt;port&gt;</code> → naive sidecar → HTTPS to your server</li>
            <li>Sidecar auto-restarts on node edit; sync happens on backend startup</li>
          </Ul>
          <P><B>Server side:</B> you need a Caddy server with the <code className="text-gray-200">forwardproxy</code> plugin and a real TLS certificate. A helper script <code className="text-gray-200">scripts/setup-naive-server.sh</code> is provided for VPS setup.</P>
          <P><B>URI format:</B></P>
          <Code>naive+https://user:pass@example.com:443/?padding=1#MyNaive</Code>
          <P>
            <B>Requirements:</B> Address <B>must be a real domain</B> (not an IP) with a valid TLS certificate — otherwise the disguise fails and the connection is easily fingerprinted. Padding (HTTP/2 frame padding) is enabled by default and recommended.
          </P>
          <P><B>Security hardening of the sidecar:</B></P>
          <Ul>
            <li>read-only filesystem, all capabilities dropped, no-new-privileges</li>
            <li>64 MB memory limit, JSON log rotation (10 MB × 3)</li>
            <li>Docker API access via tecnativa/docker-socket-proxy (restricted to containers/images/networks only, bound to 127.0.0.1:2375)</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <P>
            <B>NaiveProxy</B> (автор — klzgrad) — это HTTPS forward-proxy клиент, который маскирует свой трафик под обычное HTTPS-соединение Chrome → Caddy. Это делает его крайне устойчивым к DPI: трафик имеет тот же TLS-handshake, fingerprint и поведение HTTP/2, что и у Chrome.
          </P>
          <P>
            В отличие от остальных протоколов, NaiveProxy <B>не встроен в xray-core</B>. PiTun запускает его как <B>Docker sidecar</B> — по одному небольшому контейнеру на naive-нод, слушающему на <code className="text-gray-200">127.0.0.1:&lt;internal_port&gt;</code>. xray маршрутизирует исходящий трафик через него как обычный SOCKS5-outbound.
          </P>
          <P><B>Как это работает:</B></P>
          <Ul>
            <li>Добавляете naive-нод → бэкенд выделяет свободный loopback-порт (20800–20899)</li>
            <li>Запускается контейнер <code className="text-gray-200">pitun-naive-&lt;id&gt;</code> с <code className="text-gray-200">network_mode: host</code> (только loopback)</li>
            <li>xray outbound: <code className="text-gray-200">socks → 127.0.0.1:&lt;port&gt;</code> → naive sidecar → HTTPS до сервера</li>
            <li>Sidecar автоматически перезапускается при редактировании нода; синхронизация — на старте бэкенда</li>
          </Ul>
          <P><B>На сервере:</B> нужен Caddy с плагином <code className="text-gray-200">forwardproxy</code> и валидным TLS-сертификатом. Скрипт <code className="text-gray-200">scripts/setup-naive-server.sh</code> автоматизирует развёртывание на VPS.</P>
          <P><B>Формат URI:</B></P>
          <Code>naive+https://user:pass@example.com:443/?padding=1#MyNaive</Code>
          <P>
            <B>Требования:</B> адрес <B>обязательно реальный домен</B> (не IP) с валидным TLS-сертификатом — иначе маскировка не работает и соединение легко фингерпринтится. Padding (HTTP/2 frame padding) включён по умолчанию и рекомендуется.
          </P>
          <P><B>Усиление безопасности sidecar:</B></P>
          <Ul>
            <li>read-only файловая система, сброс всех capabilities, no-new-privileges</li>
            <li>лимит памяти 64 МБ, ротация JSON-логов (10 МБ × 3)</li>
            <li>доступ к Docker API через tecnativa/docker-socket-proxy (только containers/images/networks, привязан к 127.0.0.1:2375)</li>
          </Ul>
        </>
      ),
    },
  },

  /* 5. Routing Rules */
  {
    id: 'routing-rules',
    title: { en: 'Routing Rules', ru: 'Правила маршрутизации' },
    content: {
      en: (
        <>
          <P>Rules determine how traffic is routed in <B>Rules</B> mode. They are evaluated top to bottom by priority.</P>
          <P><B>Rule types:</B></P>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Type</th>
                <th className="py-2">Example</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">mac</td><td className="py-2"><code className="text-gray-400">AA:BB:CC:DD:EE:FF</code> — match by device MAC address</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">src_ip</td><td className="py-2"><code className="text-gray-400">192.168.1.50/32</code> — match by source IP/CIDR</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">dst_ip</td><td className="py-2"><code className="text-gray-400">10.0.0.0/8</code> — match by destination IP/CIDR</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">domain</td><td className="py-2"><code className="text-gray-400">google.com</code>, <code className="text-gray-400">geosite:category-ads</code></td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">port</td><td className="py-2"><code className="text-gray-400">443</code>, <code className="text-gray-400">80,443</code></td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">protocol</td><td className="py-2"><code className="text-gray-400">bittorrent</code>, <code className="text-gray-400">http</code></td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">geoip</td><td className="py-2"><code className="text-gray-400">geoip:ru</code>, <code className="text-gray-400">geoip:cn</code></td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">geosite</td><td className="py-2"><code className="text-gray-400">geosite:google</code>, <code className="text-gray-400">geosite:netflix</code></td></tr>
            </tbody>
          </table>
          <P><B>Actions:</B></P>
          <Ul>
            <li><B>proxy</B> — send through active VPN node</li>
            <li><B>direct</B> — connect directly (bypass VPN)</li>
            <li><B>block</B> — drop the connection</li>
            <li><B>node:&lt;id&gt;</B> — route through a specific node</li>
            <li><B>balancer:&lt;id&gt;</B> — route through a balancer group</li>
          </Ul>
          <P><B>Features:</B> drag-and-drop reorder, bulk import (paste domains/IPs one per line), Quick Add presets (Bypass RU/CN, Block ads, Proxy streaming).</P>
        </>
      ),
      ru: (
        <>
          <P>Правила определяют маршрутизацию трафика в режиме <B>Rules</B>. Проверяются сверху вниз по приоритету.</P>
          <P><B>Типы правил:</B></P>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Тип</th>
                <th className="py-2">Пример</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">mac</td><td className="py-2"><code className="text-gray-400">AA:BB:CC:DD:EE:FF</code> — по MAC-адресу устройства</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">src_ip</td><td className="py-2"><code className="text-gray-400">192.168.1.50/32</code> — по IP-источнику</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">dst_ip</td><td className="py-2"><code className="text-gray-400">10.0.0.0/8</code> — по IP-назначению</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">domain</td><td className="py-2"><code className="text-gray-400">google.com</code>, <code className="text-gray-400">geosite:category-ads</code></td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">port</td><td className="py-2"><code className="text-gray-400">443</code>, <code className="text-gray-400">80,443</code></td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">protocol</td><td className="py-2"><code className="text-gray-400">bittorrent</code>, <code className="text-gray-400">http</code></td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">geoip</td><td className="py-2"><code className="text-gray-400">geoip:ru</code>, <code className="text-gray-400">geoip:cn</code></td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">geosite</td><td className="py-2"><code className="text-gray-400">geosite:google</code>, <code className="text-gray-400">geosite:netflix</code></td></tr>
            </tbody>
          </table>
          <P><B>Действия:</B></P>
          <Ul>
            <li><B>proxy</B> — через активную VPN-ноду</li>
            <li><B>direct</B> — напрямую (мимо VPN)</li>
            <li><B>block</B> — сбросить соединение</li>
            <li><B>node:&lt;id&gt;</B> — через конкретную ноду</li>
            <li><B>balancer:&lt;id&gt;</B> — через группу балансировки</li>
          </Ul>
          <P><B>Возможности:</B> drag-and-drop сортировка, массовый импорт (домены/IP построчно), пресеты Quick Add (Bypass RU/CN, Block ads, Proxy streaming).</P>
        </>
      ),
    },
  },

  /* 6. DNS Management */
  {
    id: 'dns',
    title: { en: 'DNS Management', ru: 'Управление DNS' },
    content: {
      en: (
        <>
          <P>PiTun manages DNS through xray's built-in DNS module. DNS queries from LAN devices flow through xray, which resolves them using configurable servers and per-domain rules.</P>
          <P><B>Server types:</B></P>
          <Ul>
            <li><B>Plain</B> — standard UDP DNS (e.g. <code className="text-gray-400">8.8.8.8</code>)</li>
            <li><B>DoH</B> — DNS over HTTPS (e.g. <code className="text-gray-400">https://dns.google/dns-query</code>)</li>
            <li><B>DoT</B> — labelled "DNS-over-TCP (not encrypted)" in the UI. xray-core doesn't support native DoT (see <a href="https://github.com/XTLS/Xray-core/issues/786" className="text-brand-400 underline">issue #786</a>), so this mode falls back to plaintext DNS-over-TCP on port 53 (<code className="text-gray-400">tcp://host:53</code>). Use DoH if you need encryption.</li>
          </Ul>
          <P><B>Per-domain DNS rules:</B> The DNS Rules table lets you assign a specific DNS server to specific domains. The domain match field accepts comma-separated entries using xray domain syntax:</P>
          <Ul>
            <li><code className="text-gray-400">domain:youtube.com</code> — youtube.com and all subdomains</li>
            <li><code className="text-gray-400">domain:ru</code> — the .ru TLD and all subdomains (note: <code className="text-gray-400">domain:.ru</code> with a leading dot is invalid in xray)</li>
            <li><code className="text-gray-400">geosite:category-ads</code> — a geo category</li>
          </Ul>
          <P><B>Disable DNS fallback (recommended ON):</B> When ON, each DNS server is used strictly for its configured domains — rule-specific servers are never queried for unmatched domains. When OFF, all servers are queried simultaneously for every domain, so rule-specific servers (e.g. 94.140.14.14 for a YouTube rule) will appear in the query log for unrelated domains too.</P>
          <P><B>Bypass CN/RU DNS:</B> Routes .cn/.ru TLD domains through the plain Primary/Secondary servers, bypassing DoH/DoT, to reduce latency for local domains. Internally uses <code className="text-gray-400">domain:cn</code> / <code className="text-gray-400">domain:ru</code> matching.</P>
          <P><B>FakeDNS:</B> Returns synthetic IPs to capture the real domain name before routing. Required for accurate domain-based routing when traffic arrives as raw IP addresses.</P>
          <P><B>DNS sniffing:</B> Extracts the real domain from TLS SNI / HTTP Host headers to improve routing accuracy without FakeDNS.</P>
          <P><B>DNS Query Log:</B> Found on the DNS page (not the Logs page). Records every DNS query resolved by xray: domain, server used, resolved IPs, latency, and whether it was a cache hit. Filterable by domain or server. Enable with the toggle on the DNS page — takes effect after xray restarts.</P>
          <P><B>DNS Test Tool:</B> Also on the DNS page — enter any domain to test resolution. "Via xray" mode shows which DNS server xray actually used (respecting your rules), compared to direct resolution from the RPi itself.</P>
        </>
      ),
      ru: (
        <>
          <P>PiTun управляет DNS через встроенный DNS-модуль xray. DNS-запросы устройств в LAN идут через xray, который резолвит их через настраиваемые серверы и правила per-domain.</P>
          <P><B>Типы серверов:</B></P>
          <Ul>
            <li><B>Plain</B> — обычный UDP DNS (напр. <code className="text-gray-400">8.8.8.8</code>)</li>
            <li><B>DoH</B> — DNS over HTTPS (напр. <code className="text-gray-400">https://dns.google/dns-query</code>)</li>
            <li><B>DoT</B> — в UI помечен как «DNS-over-TCP (not encrypted)». xray-core не поддерживает нативный DoT (см. <a href="https://github.com/XTLS/Xray-core/issues/786" className="text-brand-400 underline">issue #786</a>), поэтому режим падает до plaintext DNS-over-TCP на порту 53 (<code className="text-gray-400">tcp://host:53</code>). Для шифрования используй DoH.</li>
          </Ul>
          <P><B>Правила DNS per-domain:</B> В таблице DNS Rules можно назначить конкретный DNS-сервер для конкретных доменов. Поле domain match принимает значения через запятую в синтаксисе xray:</P>
          <Ul>
            <li><code className="text-gray-400">domain:youtube.com</code> — youtube.com и все поддомены</li>
            <li><code className="text-gray-400">domain:ru</code> — домены .ru и все поддомены (важно: <code className="text-gray-400">domain:.ru</code> с точкой — неверный синтаксис в xray)</li>
            <li><code className="text-gray-400">geosite:category-ads</code> — geo-категория</li>
          </Ul>
          <P><B>Отключить DNS fallback (рекомендуется включить):</B> При включении каждый DNS-сервер используется строго для своих доменов — rule-specific серверы не запрашиваются для остальных доменов. При выключении все серверы опрашиваются одновременно для каждого домена, и специфические серверы (напр. 94.140.14.14 для YouTube) будут появляться в логе для несвязанных доменов.</P>
          <P><B>Bypass CN/RU DNS:</B> Домены .cn/.ru резолвятся через Plain-серверы (Primary/Secondary), минуя DoH/DoT, для снижения задержки. Внутри используется синтаксис <code className="text-gray-400">domain:cn</code> / <code className="text-gray-400">domain:ru</code>.</P>
          <P><B>FakeDNS:</B> Возвращает синтетические IP для захвата реального доменного имени до маршрутизации. Необходим для точной маршрутизации по домену когда трафик поступает как «голые» IP-адреса.</P>
          <P><B>DNS sniffing:</B> Извлекает домен из TLS SNI / HTTP Host заголовков для улучшения точности маршрутизации без FakeDNS.</P>
          <P><B>DNS Query Log:</B> Находится на странице DNS (не Logs). Фиксирует каждый DNS-запрос через xray: домен, использованный сервер, полученные IP, задержку и кэш-хит. Фильтруется по домену или серверу. Включается переключателем на странице DNS — вступает в силу после перезапуска xray.</P>
          <P><B>Инструмент тестирования DNS:</B> Тоже на странице DNS — введите любой домен для проверки. Режим «Via xray» показывает какой DNS-сервер реально использовал xray (с учётом правил), в отличие от прямого резолва с RPi.</P>
        </>
      ),
    },
  },

  /* 7. Balancer Groups */
  {
    id: 'balancers',
    title: { en: 'Balancer Groups', ru: 'Группы балансировки' },
    content: {
      en: (
        <>
          <P>Balancers distribute traffic across multiple nodes using xray's built-in balancer.</P>
          <P><B>Strategies:</B></P>
          <Ul>
            <li><B>leastPing</B> — route to the node with lowest latency (measured by health checks)</li>
            <li><B>random</B> — randomly pick a node from the group</li>
          </Ul>
          <P><B>How to use:</B></P>
          <Ul>
            <li>Create a balancer group in the Balancers page — add nodes to it</li>
            <li>In routing rules, set action to <code className="text-gray-400">balancer:&lt;id&gt;</code></li>
            <li>Traffic matching that rule will be distributed across the group's nodes</li>
            <li>If a node in the group goes offline, traffic automatically routes to remaining nodes</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <P>Балансировщики распределяют трафик между несколькими нодами через встроенный балансировщик xray.</P>
          <P><B>Стратегии:</B></P>
          <Ul>
            <li><B>leastPing</B> — направлять на ноду с наименьшей задержкой</li>
            <li><B>random</B> — случайный выбор ноды из группы</li>
          </Ul>
          <P><B>Как использовать:</B></P>
          <Ul>
            <li>Создайте группу на странице Balancers — добавьте ноды</li>
            <li>В правилах маршрутизации укажите действие <code className="text-gray-400">balancer:&lt;id&gt;</code></li>
            <li>Трафик по этому правилу распределяется между нодами группы</li>
            <li>Если нода уходит в офлайн, трафик автоматически идёт на оставшиеся</li>
          </Ul>
        </>
      ),
    },
  },

  /* 8. Node Circles (Rotation) */
  {
    id: 'node-circles',
    title: { en: 'Node Circles (Rotation)', ru: 'Node Circles (Ротация нод)' },
    content: {
      en: (
        <>
          <P>Node Circles automatically rotate the active proxy node on a schedule — without restarting xray or dropping active connections.</P>
          <P><B>How it works:</B></P>
          <Ul>
            <li>Create a circle with a list of nodes and a rotation interval</li>
            <li>PiTun uses xray's gRPC API to add the new outbound and remove the old one</li>
            <li>Existing connections finish naturally — no disconnects</li>
            <li>If the gRPC API is unavailable, falls back to a full xray restart</li>
          </Ul>
          <P><B>Modes:</B></P>
          <Ul>
            <li><B>Sequential</B> — rotates nodes in order (1 &rarr; 2 &rarr; 3 &rarr; 1 &rarr; ...)</li>
            <li><B>Random</B> — picks a random node from the circle each time</li>
          </Ul>
          <P><B>Interval:</B> set min/max minutes. In sequential mode, rotates every <code className="text-gray-400">interval_min</code> minutes. In random mode, picks a random interval between min and max.</P>
          <P><B>Use cases:</B></P>
          <Ul>
            <li>Distribute load across multiple VPN servers</li>
            <li>Avoid IP-based blocking by rotating exit IPs</li>
            <li>Automatic failover-like behavior without health check dependency</li>
          </Ul>
          <P>You can also manually trigger rotation from the NodeCircles page with the rotate button.</P>
        </>
      ),
      ru: (
        <>
          <P>Node Circles автоматически ротируют активную прокси-ноду по расписанию — без перезапуска xray и без обрыва активных соединений.</P>
          <P><B>Как работает:</B></P>
          <Ul>
            <li>Создайте circle со списком нод и интервалом ротации</li>
            <li>PiTun использует gRPC API xray для добавления нового outbound и удаления старого</li>
            <li>Существующие соединения завершаются штатно — без обрывов</li>
            <li>Если gRPC API недоступен, происходит полный перезапуск xray</li>
          </Ul>
          <P><B>Режимы:</B></P>
          <Ul>
            <li><B>Sequential</B> — ротация по порядку (1 &rarr; 2 &rarr; 3 &rarr; 1 &rarr; ...)</li>
            <li><B>Random</B> — случайный выбор ноды из круга каждый раз</li>
          </Ul>
          <P><B>Интервал:</B> задайте мин/макс минуты. В sequential режиме ротация каждые <code className="text-gray-400">interval_min</code> минут. В random режиме — случайный интервал между min и max.</P>
          <P><B>Случаи использования:</B></P>
          <Ul>
            <li>Распределение нагрузки между VPN-серверами</li>
            <li>Избежание блокировки по IP через ротацию выходных IP</li>
            <li>Автоматическое переключение без зависимости от health check</li>
          </Ul>
          <P>Также можно вручную запустить ротацию на странице NodeCircles кнопкой rotate.</P>
        </>
      ),
    },
  },

  /* 9. Chain Tunnel */
  {
    id: 'chain-tunnel',
    title: { en: 'Chain Tunnel (Double VPN)', ru: 'Chain Tunnel (Двойной VPN)' },
    content: {
      en: (
        <>
          <P>Chain tunneling nests one protocol inside another using xray's <code className="text-gray-400">proxySettings.transportLayer</code>.</P>
          <P><B>Example:</B> WireGuard wrapped inside VLESS+Reality</P>
          <Code>{`Your device -> RPi (xray)
  -> VLESS+Reality (outer, to CDN/edge server)
    -> WireGuard (inner, to final VPN server)
      -> internet`}</Code>
          <P><B>What the network sees:</B> only VLESS+Reality traffic to the outer server. The WireGuard tunnel is invisible.</P>
          <P><B>How to configure:</B></P>
          <Ul>
            <li>Create both nodes (outer VLESS and inner WireGuard)</li>
            <li>On the inner node (WireGuard), set <B>Chain Node</B> to the outer node (VLESS)</li>
            <li>Use the inner node as your active node or in routing rules</li>
          </Ul>
          <P><B>Use cases:</B> bypass DPI that blocks WireGuard, add an extra layer of encryption, use Reality fingerprinting to hide VPN traffic.</P>
        </>
      ),
      ru: (
        <>
          <P>Chain tunnel вкладывает один протокол в другой через <code className="text-gray-400">proxySettings.transportLayer</code> xray.</P>
          <P><B>Пример:</B> WireGuard внутри VLESS+Reality</P>
          <Code>{`Устройство -> RPi (xray)
  -> VLESS+Reality (внешний, до CDN/edge-сервера)
    -> WireGuard (внутренний, до финального VPN)
      -> интернет`}</Code>
          <P><B>Что видит сеть:</B> только VLESS+Reality трафик до внешнего сервера. WireGuard-туннель невидим.</P>
          <P><B>Как настроить:</B></P>
          <Ul>
            <li>Создайте обе ноды (внешний VLESS и внутренний WireGuard)</li>
            <li>На внутренней ноде (WG) укажите <B>Chain Node</B> = внешняя нода (VLESS)</li>
            <li>Используйте внутреннюю ноду как активную или в правилах</li>
          </Ul>
          <P><B>Случаи:</B> обход DPI, блокирующего WireGuard; дополнительный слой шифрования; маскировка VPN через Reality.</P>
        </>
      ),
    },
  },

  /* 9. Kill Switch */
  {
    id: 'kill-switch',
    title: { en: 'Kill Switch', ru: 'Kill Switch' },
    content: {
      en: (
        <>
          <P>When enabled, the kill switch blocks ALL internet traffic if xray stops or crashes, preventing traffic leaks.</P>
          <P><B>How it works:</B></P>
          <Ul>
            <li>Uses nftables DROP rules to block all outgoing traffic</li>
            <li>LAN traffic (192.168.0.0/16, 10.0.0.0/8) remains accessible</li>
            <li>VPN server IPs are whitelisted so xray can reconnect</li>
            <li>Activates on: manual stop, xray crash, unexpected exit</li>
          </Ul>
          <P><B>When to enable:</B> if you need to guarantee that no traffic leaks direct (unencrypted) when the proxy is down.</P>
          <P><B>When to disable:</B> if you want internet to work normally when proxy is off (e.g., during maintenance).</P>
        </>
      ),
      ru: (
        <>
          <P>Kill switch блокирует ВЕСЬ интернет-трафик, если xray останавливается или падает, предотвращая утечки.</P>
          <P><B>Как работает:</B></P>
          <Ul>
            <li>Использует правила nftables DROP для блокировки исходящего трафика</li>
            <li>LAN (192.168.0.0/16, 10.0.0.0/8) остаётся доступным</li>
            <li>IP VPN-серверов в белом списке — xray может переподключиться</li>
            <li>Срабатывает при: ручной остановке, краше xray, неожиданном завершении</li>
          </Ul>
          <P><B>Когда включать:</B> если нужна гарантия, что трафик не пойдёт напрямую при падении прокси.</P>
          <P><B>Когда выключать:</B> если интернет должен работать без прокси (напр. при обслуживании).</P>
        </>
      ),
    },
  },

  /* 10. Device Management */
  {
    id: 'device-management',
    title: { en: 'Device Management', ru: 'Управление устройствами' },
    content: {
      en: (
        <>
          <P>PiTun automatically discovers and manages LAN devices, giving you per-device control over proxy routing.</P>
          <P><B>Device discovery:</B></P>
          <Ul>
            <li>Background scanner runs every 60s using a fallback chain: <code className="text-gray-400">arp-scan</code> &rarr; <code className="text-gray-400">ip neigh</code> &rarr; <code className="text-gray-400">/proc/net/arp</code></li>
            <li>New devices are automatically added with <code className="text-gray-400">default</code> routing policy</li>
            <li>Devices not seen on the network are marked offline</li>
            <li>Manual scan available via the "Scan LAN" button</li>
          </Ul>
          <P><B>Device routing modes</B> (set in the Devices page header):</P>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Mode</th>
                <th className="py-2 pr-4">Behavior</th>
                <th className="py-2">Use case</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">All devices</td><td className="py-2 pr-4">All traffic from all devices is proxied</td><td className="py-2">Default. Proxy the entire LAN.</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Include only</td><td className="py-2 pr-4">Only devices marked "include" are proxied</td><td className="py-2">Whitelist: only specific devices use VPN</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Exclude list</td><td className="py-2 pr-4">All devices proxied except those marked "exclude"</td><td className="py-2">Blacklist: exclude specific devices from VPN</td></tr>
            </tbody>
          </table>
          <P><B>Per-device routing policy:</B></P>
          <Ul>
            <li><B>Default</B> — follows the global device routing mode</li>
            <li><B>Include</B> — device is explicitly included (used in "include_only" mode)</li>
            <li><B>Exclude</B> — device is explicitly excluded (used in "exclude_list" mode)</li>
          </Ul>
          <P>Click the policy badge on any device row to cycle through policies. Use checkboxes for bulk policy changes.</P>
          <P><B>How it works technically:</B></P>
          <P>Device filtering is a <B>pre-filter layer at the nftables level</B>, before traffic reaches xray:</P>
          <Code>{`Packet arrives at RPi
  -> [nftables] check MAC against include/exclude set
  -> If device should NOT be proxied -> return (direct to router)
  -> [nftables TPROXY] -> xray -> [Routing Rules] -> proxy/direct/block`}</Code>
          <P>This means routing rules (domain, geoip, etc.) only apply to traffic from allowed devices.</P>
          <P><B>UI features:</B></P>
          <Ul>
            <li>Inline rename — click pencil icon, type name, press Enter</li>
            <li>Filters — search by MAC/IP/name/hostname/vendor, filter by online/offline, filter by policy</li>
            <li>Bulk actions — select multiple devices with checkboxes, apply policy to all at once</li>
            <li>"Reset All" button — resets all devices to "default" policy</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <P>PiTun автоматически обнаруживает и управляет устройствами в LAN, давая контроль маршрутизации на уровне каждого устройства.</P>
          <P><B>Обнаружение устройств:</B></P>
          <Ul>
            <li>Фоновый сканер каждые 60с по цепочке: <code className="text-gray-400">arp-scan</code> &rarr; <code className="text-gray-400">ip neigh</code> &rarr; <code className="text-gray-400">/proc/net/arp</code></li>
            <li>Новые устройства добавляются с политикой <code className="text-gray-400">default</code></li>
            <li>Устройства, не замеченные в сети, помечаются как offline</li>
            <li>Ручное сканирование — кнопка "Scan LAN"</li>
          </Ul>
          <P><B>Режимы маршрутизации устройств</B> (задаётся в шапке страницы Devices):</P>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Режим</th>
                <th className="py-2 pr-4">Поведение</th>
                <th className="py-2">Случай</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">All devices</td><td className="py-2 pr-4">Весь трафик от всех устройств проксируется</td><td className="py-2">По умолчанию. Проксировать всю LAN.</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Include only</td><td className="py-2 pr-4">Проксируются только устройства с пометкой "include"</td><td className="py-2">Белый список: только определённые устройства используют VPN</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Exclude list</td><td className="py-2 pr-4">Все устройства проксируются кроме помеченных "exclude"</td><td className="py-2">Чёрный список: исключить конкретные устройства из VPN</td></tr>
            </tbody>
          </table>
          <P><B>Политика маршрутизации устройства:</B></P>
          <Ul>
            <li><B>Default</B> — следует глобальному режиму маршрутизации устройств</li>
            <li><B>Include</B> — устройство явно включено (используется в режиме "include_only")</li>
            <li><B>Exclude</B> — устройство явно исключено (используется в режиме "exclude_list")</li>
          </Ul>
          <P>Кликните на бейдж политики в строке устройства для переключения. Используйте чекбоксы для массового изменения политики.</P>
          <P><B>Как это работает технически:</B></P>
          <P>Фильтрация устройств — <B>pre-filter слой на уровне nftables</B>, до передачи трафика в xray:</P>
          <Code>{`Пакет приходит на RPi
  -> [nftables] проверяет MAC по include/exclude множеству
  -> Если устройство НЕ должно проксироваться -> return (напрямую на роутер)
  -> [nftables TPROXY] -> xray -> [Правила маршрутизации] -> proxy/direct/block`}</Code>
          <P>Это значит, что правила маршрутизации (домен, geoip и т.д.) применяются только к трафику от разрешённых устройств.</P>
          <P><B>Функции UI:</B></P>
          <Ul>
            <li>Inline rename — нажмите карандаш, введите имя, нажмите Enter</li>
            <li>Фильтры — поиск по MAC/IP/имени/hostname/vendor, фильтр по online/offline, фильтр по политике</li>
            <li>Массовые действия — выберите устройства чекбоксами, примените политику ко всем сразу</li>
            <li>Кнопка "Reset All" — сброс всех устройств на политику "default"</li>
          </Ul>
        </>
      ),
    },
  },

  /* 11. Subscriptions */
  {
    id: 'subscriptions',
    title: { en: 'Subscriptions', ru: 'Подписки' },
    content: {
      en: (
        <>
          <P>Import proxy nodes from subscription URLs (providers, self-hosted panels).</P>
          <P><B>Supported formats:</B> Clash YAML, base64-encoded URI list, plain URI list</P>
          <P><B>Features:</B></P>
          <Ul>
            <li><B>Auto-update</B> — set an interval (e.g. every 6h), nodes refresh automatically</li>
            <li><B>User-Agent</B> — customize the UA sent to subscription provider (some providers filter by UA)</li>
            <li><B>Regex filter</B> — only import nodes whose names match a pattern (e.g. <code className="text-gray-400">US|UK|DE</code>)</li>
            <li>Subscription nodes are tagged and can be bulk-deleted when the subscription is removed</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <P>Импорт прокси-нод из URL подписок (провайдеры, self-hosted панели).</P>
          <P><B>Поддерживаемые форматы:</B> Clash YAML, base64 URI, обычный список URI</P>
          <P><B>Возможности:</B></P>
          <Ul>
            <li><B>Автообновление</B> — задайте интервал (напр. каждые 6ч), ноды обновятся автоматически</li>
            <li><B>User-Agent</B> — кастомный UA для провайдера (некоторые фильтруют по UA)</li>
            <li><B>Regex-фильтр</B> — импортировать только ноды с именами по паттерну (напр. <code className="text-gray-400">US|UK|DE</code>)</li>
            <li>Ноды подписки отмечены тегом и удаляются массово при удалении подписки</li>
          </Ul>
        </>
      ),
    },
  },

  /* 11. Health Checks & Failover */
  {
    id: 'health-checks',
    title: { en: 'Health Checks & Failover', ru: 'Health Checks и Failover' },
    content: {
      en: (
        <>
          <P>PiTun performs background TCP health checks on all enabled nodes every 30 seconds.</P>
          <Ul>
            <li>Nodes are marked online/offline based on TCP connectivity and measured latency</li>
            <li><B>Automatic failover:</B> if the active node goes offline, PiTun switches to a backup node from the failover list</li>
            <li>Latency is displayed on the Dashboard and Nodes page</li>
            <li>Health check results feed into the <code className="text-gray-400">leastPing</code> balancer strategy</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <P>PiTun выполняет фоновые TCP health checks всех включённых нод каждые 30 секунд.</P>
          <Ul>
            <li>Ноды помечаются online/offline по TCP-подключению и замеренной задержке</li>
            <li><B>Автоматический failover:</B> если активная нода уходит в офлайн, PiTun переключается на резервную</li>
            <li>Задержка отображается на Dashboard и странице Nodes</li>
            <li>Результаты проверок используются стратегией <code className="text-gray-400">leastPing</code> балансировщика</li>
          </Ul>
        </>
      ),
    },
  },

  /* 12. Security */
  {
    id: 'security',
    title: { en: 'Security', ru: 'Безопасность' },
    content: {
      en: (
        <>
          <Ul>
            <li><B>JWT authentication</B> — HS256, 24h token lifetime. All API endpoints protected except <code className="text-gray-400">/health</code> and <code className="text-gray-400">/auth/login</code></li>
            <li><B>WebSocket auth</B> — log stream requires JWT token via <code className="text-gray-400">?token=</code> query param</li>
            <li><B>Password</B> — bcrypt hashing, minimum 8 characters, changeable via UI (sidebar key icon)</li>
            <li><B>CLI reset</B> — <code className="text-gray-400">docker exec pitun-backend bash /app/scripts/reset-password.sh newpassword</code></li>
            <li><B>SSRF protection</B> — subscription URLs are validated: hostname is resolved via DNS and all resolved IPs are checked against private/loopback/link-local ranges</li>
            <li><B>nftables sanitization</B> — MAC/CIDR inputs validated with regex before passing to nft</li>
            <li><B>No shell injection</B> — subprocess_exec with stdin pipe for nft commands</li>
            <li><B>xray checksum</B> — SHA256 verification of xray binary during installation</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <Ul>
            <li><B>JWT-аутентификация</B> — HS256, время жизни токена 24ч. Все API-эндпоинты защищены кроме <code className="text-gray-400">/health</code> и <code className="text-gray-400">/auth/login</code></li>
            <li><B>WebSocket-авторизация</B> — поток логов требует JWT через параметр <code className="text-gray-400">?token=</code></li>
            <li><B>Пароль</B> — bcrypt-хеширование, минимум 8 символов, можно сменить через UI (иконка ключа)</li>
            <li><B>CLI-сброс</B> — <code className="text-gray-400">docker exec pitun-backend bash /app/scripts/reset-password.sh newpassword</code></li>
            <li><B>SSRF-защита</B> — URL подписок проверяется: hostname резолвится через DNS и все полученные IP проверяются на приватность/loopback/link-local</li>
            <li><B>Санитизация nftables</B> — MAC/CIDR проверяются regex перед передачей в nft</li>
            <li><B>Нет shell injection</B> — subprocess_exec с stdin pipe для nft-команд</li>
            <li><B>Контрольная сумма xray</B> — SHA256 верификация бинарника при установке</li>
          </Ul>
        </>
      ),
    },
  },

  /* 13. QUIC Blocking */
  {
    id: 'quic-blocking',
    title: { en: 'QUIC Blocking', ru: 'Блокировка QUIC' },
    content: {
      en: (
        <>
          <P><B>Problem:</B> QUIC (HTTP/3) is UDP-based. TPROXY intercepts it but the IP path changes, breaking connections.</P>
          <P><B>Solution:</B> PiTun blocks UDP port 443 via nftables, forcing browsers to fall back to TCP/443 (HTTP/2) which TPROXY handles correctly.</P>
          <Ul>
            <li>Only affects traffic routed through the proxy</li>
            <li>Bypassed destinations (direct rules) keep QUIC working</li>
            <li>Toggle on Dashboard: <B>Block QUIC (UDP/443)</B> checkbox</li>
            <li>Only shown when inbound mode is TPROXY or Both</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <P><B>Проблема:</B> QUIC (HTTP/3) работает по UDP. TPROXY перехватывает его, но IP-путь меняется, ломая соединения.</P>
          <P><B>Решение:</B> PiTun блокирует UDP порт 443 через nftables, заставляя браузеры откатиться на TCP/443 (HTTP/2), который TPROXY обрабатывает корректно.</P>
          <Ul>
            <li>Затрагивает только трафик, идущий через прокси</li>
            <li>Обходимые направления (direct-правила) сохраняют QUIC</li>
            <li>Переключатель на Dashboard: <B>Block QUIC (UDP/443)</B></li>
            <li>Отображается только в режиме TPROXY или Both</li>
          </Ul>
        </>
      ),
    },
  },

  /* 14. Traffic Stats */
  {
    id: 'traffic-stats',
    title: { en: 'Traffic Stats', ru: 'Статистика трафика' },
    content: {
      en: (
        <>
          <P>PiTun reads per-node traffic statistics from the xray stats API.</P>
          <Ul>
            <li>Uplink and downlink bytes per node, updated every 5 seconds on the Dashboard</li>
            <li>Stats are collected while xray is running and reset on restart</li>
            <li>Each node's outbound is tagged as <code className="text-gray-400">node-&lt;id&gt;</code> for stats tracking</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <P>PiTun читает посистемную статистику трафика из xray stats API.</P>
          <Ul>
            <li>Uplink и downlink в байтах по каждой ноде, обновляется каждые 5 секунд на Dashboard</li>
            <li>Статистика собирается пока xray работает и сбрасывается при перезапуске</li>
            <li>Каждый outbound помечен как <code className="text-gray-400">node-&lt;id&gt;</code> для отслеживания</li>
          </Ul>
        </>
      ),
    },
  },

  /* 15. HomeProxy Integration */
  {
    id: 'homeproxy',
    title: { en: 'HomeProxy Integration', ru: 'Интеграция с HomeProxy' },
    content: {
      en: (
        <>
          <P>PiTun works alongside OpenWrt HomeProxy for routers with limited resources.</P>
          <P><B>Setup:</B></P>
          <Ul>
            <li>On your OpenWrt router, install HomeProxy</li>
            <li>Add a SOCKS5 node pointing to <code className="text-gray-400">RPi_IP:1080</code> (PiTun's SOCKS5 inbound)</li>
            <li>Route traffic through that SOCKS5 node in HomeProxy</li>
            <li>The router sends traffic to RPi, which applies all routing rules and forwards through VPN</li>
          </Ul>
          <P>This approach offloads heavy crypto and routing to the RPi while the router just forwards.</P>
        </>
      ),
      ru: (
        <>
          <P>PiTun работает совместно с OpenWrt HomeProxy для роутеров с ограниченными ресурсами.</P>
          <P><B>Настройка:</B></P>
          <Ul>
            <li>На роутере OpenWrt установите HomeProxy</li>
            <li>Добавьте SOCKS5-ноду, указывающую на <code className="text-gray-400">RPi_IP:1080</code> (SOCKS5-вход PiTun)</li>
            <li>Маршрутизируйте трафик через эту SOCKS5-ноду в HomeProxy</li>
            <li>Роутер отправляет трафик на RPi, который применяет правила и пересылает через VPN</li>
          </Ul>
          <P>Такой подход переносит тяжёлую криптографию и маршрутизацию на RPi, а роутер только форвардит.</P>
        </>
      ),
    },
  },

  /* 16. Router Setup Guide */
  {
    id: 'router-setup',
    title: { en: 'Router Setup Guide', ru: 'Настройка роутера' },
    content: {
      en: (
        <>
          <P>How to route your entire network through PiTun:</P>
          <P><B>Option 1: Change DHCP Gateway (all devices)</B></P>
          <P>In your router's admin panel, change the DHCP settings so the default gateway points to RPi4's IP address. This routes ALL devices on the network through PiTun automatically.</P>
          <Ul>
            <li>Xiaomi/Redmi: Settings &rarr; LAN &rarr; DHCP Server &rarr; Gateway = 192.168.1.109</li>
            <li>TP-Link: Advanced &rarr; Network &rarr; DHCP Server &rarr; Default Gateway = 192.168.1.109</li>
            <li>ASUS: LAN &rarr; DHCP Server &rarr; Default Gateway = 192.168.1.109</li>
            <li>Keenetic: Home Network &rarr; DHCP &rarr; Gateway = 192.168.1.109</li>
            <li>OpenWrt: Network &rarr; Interfaces &rarr; LAN &rarr; DHCP &rarr; Advanced &rarr; Gateway = 192.168.1.109</li>
            <li>MikroTik: IP &rarr; DHCP Server &rarr; Networks &rarr; Gateway = 192.168.1.109</li>
          </Ul>
          <P>After changing, all devices that renew their DHCP lease will route through RPi4.</P>
          <P><B>Option 2: Static route on specific device</B></P>
          <P>On a phone/PC, set the gateway manually:</P>
          <Ul>
            <li>Windows: Network Settings &rarr; IPv4 &rarr; Gateway = 192.168.1.109</li>
            <li>macOS: System Settings &rarr; Network &rarr; Wi-Fi &rarr; Details &rarr; TCP/IP &rarr; Router = 192.168.1.109</li>
            <li>iOS: Wi-Fi &rarr; (i) &rarr; Configure IP &rarr; Manual &rarr; Router = 192.168.1.109</li>
            <li>Android: Wi-Fi &rarr; Long press &rarr; Modify &rarr; Advanced &rarr; Gateway = 192.168.1.109</li>
          </Ul>
          <P><B>Option 3: SOCKS5/HTTP Proxy (apps only)</B></P>
          <P>Configure in browser or app settings:</P>
          <Ul>
            <li>SOCKS5: <code className="text-gray-200">192.168.1.109:1080</code></li>
            <li>HTTP: <code className="text-gray-200">192.168.1.109:8080</code></li>
          </Ul>
          <P>No gateway change needed — only the configured app uses PiTun.</P>
          <P><B>Important:</B></P>
          <Ul>
            <li>RPi4 must have a static IP (use DHCP reservation in router)</li>
            <li>RPi4's own gateway must point to the real router (192.168.1.1)</li>
            <li>Enable IP forwarding on RPi4 (done by setup script)</li>
          </Ul>
          <div className="rounded-lg bg-red-900/20 border border-red-700/40 px-3 py-2 text-xs text-red-300 mt-2">
            <B>Warning: RPi4 must NOT use itself as gateway!</B> If RPi4 gets its gateway via DHCP (like other devices), and you set DHCP gateway=192.168.1.109, RPi4 will route its own traffic to itself — infinite loop, network dies. RPi4 must have a <B>static configuration</B> with gateway=192.168.1.1 (your real router). The setup script does this automatically. nftables also marks RPi4's own traffic with mark=255 to skip TPROXY interception.
          </div>
          <Code>{`# RPi4 static config (done by setup script):
nmcli con mod "Wired connection 1" \\
  ipv4.addresses 192.168.1.109/24 \\
  ipv4.gateway 192.168.1.1 \\
  ipv4.method manual`}</Code>
        </>
      ),
      ru: (
        <>
          <P>Как направить всю домашнюю сеть через PiTun:</P>
          <P><B>Вариант 1: Изменить шлюз в DHCP (все устройства)</B></P>
          <P>В панели администрирования роутера измените настройки DHCP так, чтобы шлюз по умолчанию указывал на IP-адрес RPi4. Это автоматически направит ВСЕ устройства в сети через PiTun.</P>
          <Ul>
            <li>Xiaomi/Redmi: Настройки &rarr; LAN &rarr; DHCP-сервер &rarr; Шлюз = 192.168.1.109</li>
            <li>TP-Link: Дополнительно &rarr; Сеть &rarr; DHCP-сервер &rarr; Шлюз по умолчанию = 192.168.1.109</li>
            <li>ASUS: LAN &rarr; DHCP-сервер &rarr; Шлюз по умолчанию = 192.168.1.109</li>
            <li>Keenetic: Домашняя сеть &rarr; DHCP &rarr; Шлюз = 192.168.1.109</li>
            <li>OpenWrt: Network &rarr; Interfaces &rarr; LAN &rarr; DHCP &rarr; Advanced &rarr; Gateway = 192.168.1.109</li>
            <li>MikroTik: IP &rarr; DHCP Server &rarr; Networks &rarr; Gateway = 192.168.1.109</li>
          </Ul>
          <P>После изменения все устройства, обновившие DHCP-аренду, будут маршрутизироваться через RPi4.</P>
          <P><B>Вариант 2: Статический маршрут на конкретном устройстве</B></P>
          <P>На телефоне/ПК задайте шлюз вручную:</P>
          <Ul>
            <li>Windows: Настройки сети &rarr; IPv4 &rarr; Шлюз = 192.168.1.109</li>
            <li>macOS: Системные настройки &rarr; Сеть &rarr; Wi-Fi &rarr; Подробнее &rarr; TCP/IP &rarr; Маршрутизатор = 192.168.1.109</li>
            <li>iOS: Wi-Fi &rarr; (i) &rarr; Настройка IP &rarr; Вручную &rarr; Маршрутизатор = 192.168.1.109</li>
            <li>Android: Wi-Fi &rarr; Долгое нажатие &rarr; Изменить &rarr; Дополнительно &rarr; Шлюз = 192.168.1.109</li>
          </Ul>
          <P><B>Вариант 3: SOCKS5/HTTP прокси (только приложения)</B></P>
          <P>Настройте в браузере или приложении:</P>
          <Ul>
            <li>SOCKS5: <code className="text-gray-200">192.168.1.109:1080</code></li>
            <li>HTTP: <code className="text-gray-200">192.168.1.109:8080</code></li>
          </Ul>
          <P>Менять шлюз не нужно — через PiTun пойдёт только настроенное приложение.</P>
          <P><B>Важно:</B></P>
          <Ul>
            <li>RPi4 должен иметь статический IP (используйте резервирование DHCP в роутере)</li>
            <li>Шлюз самого RPi4 должен указывать на реальный роутер (192.168.1.1)</li>
            <li>IP-форвардинг на RPi4 должен быть включён (делается скриптом установки)</li>
          </Ul>
          <div className="rounded-lg bg-red-900/20 border border-red-700/40 px-3 py-2 text-xs text-red-300 mt-2">
            <B>Внимание: RPi4 НЕ должен использовать себя как шлюз!</B> Если RPi4 получает шлюз через DHCP (как остальные устройства), и вы поставите DHCP gateway=192.168.1.109, RPi4 будет маршрутизировать свой трафик на себя — бесконечная петля, сеть ляжет. RPi4 должен иметь <B>статическую конфигурацию</B> с gateway=192.168.1.1 (ваш реальный роутер). Скрипт установки делает это автоматически. nftables также помечает собственный трафик RPi4 меткой mark=255 чтобы пропускать его мимо TPROXY.
          </div>
          <Code>{`# Статическая конфигурация RPi4 (делается скриптом установки):
nmcli con mod "Wired connection 1" \\
  ipv4.addresses 192.168.1.109/24 \\
  ipv4.gateway 192.168.1.1 \\
  ipv4.method manual`}</Code>
        </>
      ),
    },
  },

  /* 17. TPROXY vs TUN Comparison */
  {
    id: 'tproxy-vs-tun',
    title: { en: 'TPROXY vs TUN Comparison', ru: 'Сравнение TPROXY и TUN' },
    content: {
      en: (
        <>
          <P>Both modes intercept traffic and apply the same routing rules. The difference is HOW they intercept.</P>
          <P><B>TPROXY (Recommended for gateway)</B></P>
          <Ul>
            <li>Uses Linux kernel nftables to intercept packets</li>
            <li>Works at network layer — transparent to all devices</li>
            <li>Supports MAC-based bypass (skip specific devices)</li>
            <li>Kill switch via nftables DROP rules</li>
            <li>QUIC blocking via nftables (forces TCP fallback)</li>
            <li>Slightly faster — kernel-level packet handling</li>
            <li>Requires nftables support (all modern Linux)</li>
          </Ul>
          <P><B>TUN Mode</B></P>
          <Ul>
            <li>xray creates a virtual tun0 interface</li>
            <li>xray's autoRoute adds system routes</li>
            <li>No nftables needed for routing (xray handles it)</li>
            <li>No MAC-based bypass (no nftables layer)</li>
            <li>Kill switch: fallback nftables rule blocks traffic if tun0 dies</li>
            <li>QUIC: xray can sniff QUIC natively (destOverride: ["quic"])</li>
            <li>Slightly slower — userspace packet processing</li>
          </Ul>
          <P><B>When to use what:</B></P>
          <Ul>
            <li>RPi4 as LAN gateway &rarr; TPROXY (best performance, full feature set)</li>
            <li>RPi4 as standalone device &rarr; TUN works fine</li>
            <li>nftables not available &rarr; TUN is the only option</li>
            <li>Need MAC bypass &rarr; must use TPROXY</li>
          </Ul>
          <P><B>Feature comparison table:</B></P>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Feature</th>
                <th className="py-2 pr-4">TPROXY</th>
                <th className="py-2">TUN</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">Routing rules</td><td className="py-2 pr-4">&#10003;</td><td className="py-2">&#10003;</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Domain sniffing</td><td className="py-2 pr-4">&#10003;</td><td className="py-2">&#10003;</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">GeoIP/GeoSite</td><td className="py-2 pr-4">&#10003;</td><td className="py-2">&#10003;</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">MAC bypass</td><td className="py-2 pr-4">&#10003;</td><td className="py-2">&#10007;</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Kill switch</td><td className="py-2 pr-4">Native</td><td className="py-2">Fallback</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">QUIC handling</td><td className="py-2 pr-4">Block (TCP fallback)</td><td className="py-2">Sniff natively</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Performance</td><td className="py-2 pr-4">Faster (kernel)</td><td className="py-2">Good (userspace)</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Setup complexity</td><td className="py-2 pr-4">nftables required</td><td className="py-2">Simpler</td></tr>
            </tbody>
          </table>
        </>
      ),
      ru: (
        <>
          <P>Оба режима перехватывают трафик и применяют одни и те же правила маршрутизации. Разница в том, КАК они перехватывают.</P>
          <P><B>TPROXY (рекомендуется для шлюза)</B></P>
          <Ul>
            <li>Использует nftables ядра Linux для перехвата пакетов</li>
            <li>Работает на сетевом уровне — прозрачно для всех устройств</li>
            <li>Поддерживает обход по MAC-адресу (пропуск конкретных устройств)</li>
            <li>Kill switch через правила nftables DROP</li>
            <li>Блокировка QUIC через nftables (принудительный откат на TCP)</li>
            <li>Чуть быстрее — обработка пакетов на уровне ядра</li>
            <li>Требуется поддержка nftables (все современные Linux)</li>
          </Ul>
          <P><B>Режим TUN</B></P>
          <Ul>
            <li>xray создаёт виртуальный интерфейс tun0</li>
            <li>autoRoute xray добавляет системные маршруты</li>
            <li>nftables не нужен для маршрутизации (xray справляется сам)</li>
            <li>Нет обхода по MAC (нет слоя nftables)</li>
            <li>Kill switch: резервное правило nftables блокирует трафик при падении tun0</li>
            <li>QUIC: xray может нативно анализировать QUIC (destOverride: ["quic"])</li>
            <li>Чуть медленнее — обработка пакетов в пространстве пользователя</li>
          </Ul>
          <P><B>Когда что использовать:</B></P>
          <Ul>
            <li>RPi4 как шлюз LAN &rarr; TPROXY (лучшая производительность, полный набор функций)</li>
            <li>RPi4 как отдельное устройство &rarr; TUN подходит</li>
            <li>nftables недоступен &rarr; TUN — единственный вариант</li>
            <li>Нужен обход по MAC &rarr; только TPROXY</li>
          </Ul>
          <P><B>Сравнительная таблица:</B></P>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Функция</th>
                <th className="py-2 pr-4">TPROXY</th>
                <th className="py-2">TUN</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">Правила маршрутизации</td><td className="py-2 pr-4">&#10003;</td><td className="py-2">&#10003;</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Анализ доменов</td><td className="py-2 pr-4">&#10003;</td><td className="py-2">&#10003;</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">GeoIP/GeoSite</td><td className="py-2 pr-4">&#10003;</td><td className="py-2">&#10003;</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Обход по MAC</td><td className="py-2 pr-4">&#10003;</td><td className="py-2">&#10007;</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Kill switch</td><td className="py-2 pr-4">Нативный</td><td className="py-2">Резервный</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Обработка QUIC</td><td className="py-2 pr-4">Блокировка (откат на TCP)</td><td className="py-2">Нативный анализ</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Производительность</td><td className="py-2 pr-4">Быстрее (ядро)</td><td className="py-2">Хорошо (userspace)</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Сложность настройки</td><td className="py-2 pr-4">Нужен nftables</td><td className="py-2">Проще</td></tr>
            </tbody>
          </table>
        </>
      ),
    },
  },

  /* 18. Network Architecture */
  {
    id: 'network-architecture',
    title: { en: 'Network Architecture', ru: 'Сетевая архитектура' },
    content: {
      en: (
        <>
          <P><B>Typical home network with PiTun:</B></P>
          <Code>{`Internet
  |
Router (192.168.1.1)
  |
LAN Switch
  |-------- RPi4 (192.168.1.109) — PiTun
  |-------- PC (gateway=192.168.1.109)
  |-------- Phone (gateway=192.168.1.109)
  |-------- Smart TV (gateway=192.168.1.1 — direct)`}</Code>
          <P><B>Traffic flow:</B></P>
          <Ul>
            <li>1. Device sends packet (dst=youtube.com)</li>
            <li>2. Packet arrives at RPi4 (device's gateway)</li>
            <li>3. nftables TPROXY intercepts &rarr; sends to xray</li>
            <li>4. xray sniffs TLS SNI &rarr; sees "youtube.com"</li>
            <li>5. Routing rule: youtube.com &rarr; proxy</li>
            <li>6. xray encrypts and sends through VPN to server</li>
            <li>7. VPN server forwards to youtube.com</li>
            <li>8. Response comes back through VPN &rarr; xray &rarr; device</li>
          </Ul>
          <P><B>Direct traffic (bypassed):</B></P>
          <Ul>
            <li>1. Device sends packet (dst=local-service.ru)</li>
            <li>2. RPi4 &rarr; xray &rarr; routing rule: geoip:ru &rarr; direct</li>
            <li>3. xray sends through "freedom" outbound &rarr; router &rarr; internet</li>
            <li>4. No VPN, no extra latency</li>
          </Ul>
          <P><B>Three proxy endpoints (all share same rules):</B></P>
          <Ul>
            <li>TPROXY :7893 — transparent (change gateway)</li>
            <li>SOCKS5 :1080 — explicit proxy (configure in app)</li>
            <li>HTTP :8080 — for apps without SOCKS5</li>
          </Ul>
          <P><B>RPi4 requirements:</B></P>
          <Ul>
            <li>Static IP (DHCP reservation recommended)</li>
            <li>IP forwarding enabled (<code className="text-gray-400">net.ipv4.ip_forward=1</code>)</li>
            <li>Docker running (containers: backend, frontend, nginx)</li>
            <li>xray-core installed (<code className="text-gray-400">/usr/local/bin/xray</code>)</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <P><B>Типичная домашняя сеть с PiTun:</B></P>
          <Code>{`Интернет
  |
Роутер (192.168.1.1)
  |
LAN-коммутатор
  |-------- RPi4 (192.168.1.109) — PiTun
  |-------- ПК (шлюз=192.168.1.109)
  |-------- Телефон (шлюз=192.168.1.109)
  |-------- Smart TV (шлюз=192.168.1.1 — напрямую)`}</Code>
          <P><B>Путь трафика:</B></P>
          <Ul>
            <li>1. Устройство отправляет пакет (dst=youtube.com)</li>
            <li>2. Пакет приходит на RPi4 (шлюз устройства)</li>
            <li>3. nftables TPROXY перехватывает &rarr; передаёт в xray</li>
            <li>4. xray анализирует TLS SNI &rarr; видит "youtube.com"</li>
            <li>5. Правило маршрутизации: youtube.com &rarr; proxy</li>
            <li>6. xray шифрует и отправляет через VPN на сервер</li>
            <li>7. VPN-сервер пересылает на youtube.com</li>
            <li>8. Ответ возвращается через VPN &rarr; xray &rarr; устройство</li>
          </Ul>
          <P><B>Прямой трафик (обход):</B></P>
          <Ul>
            <li>1. Устройство отправляет пакет (dst=local-service.ru)</li>
            <li>2. RPi4 &rarr; xray &rarr; правило: geoip:ru &rarr; direct</li>
            <li>3. xray отправляет через "freedom" outbound &rarr; роутер &rarr; интернет</li>
            <li>4. Без VPN, без дополнительной задержки</li>
          </Ul>
          <P><B>Три прокси-эндпоинта (общие правила):</B></P>
          <Ul>
            <li>TPROXY :7893 — прозрачный (смена шлюза)</li>
            <li>SOCKS5 :1080 — явный прокси (настройка в приложении)</li>
            <li>HTTP :8080 — для приложений без поддержки SOCKS5</li>
          </Ul>
          <P><B>Требования к RPi4:</B></P>
          <Ul>
            <li>Статический IP (рекомендуется резервирование DHCP)</li>
            <li>IP-форвардинг включён (<code className="text-gray-400">net.ipv4.ip_forward=1</code>)</li>
            <li>Docker запущен (контейнеры: backend, frontend, nginx)</li>
            <li>xray-core установлен (<code className="text-gray-400">/usr/local/bin/xray</code>)</li>
          </Ul>
        </>
      ),
    },
  },

  /* 19. CLI Commands */
  {
    id: 'cli-commands',
    title: { en: 'CLI Commands', ru: 'CLI-команды' },
    content: {
      en: (
        <>
          <P><B>Password reset:</B></P>
          <Code>docker exec pitun-backend bash /app/scripts/reset-password.sh newpassword</Code>
          <P><B>Docker commands:</B></P>
          <Code>{`# Start
docker compose up -d

# Stop
docker compose down

# Rebuild after code changes
docker compose up --build -d

# View backend logs
docker compose logs -f backend

# View xray logs
docker compose exec backend cat /tmp/xray.log`}</Code>
          <P><B>Debugging:</B></P>
          <Code>{`# Check nftables rules
docker compose exec backend nft list ruleset

# Test node connectivity
docker compose exec backend curl -x socks5://127.0.0.1:1080 https://ifconfig.me

# Check xray process
docker compose exec backend ps aux | grep xray`}</Code>
        </>
      ),
      ru: (
        <>
          <P><B>Сброс пароля:</B></P>
          <Code>docker exec pitun-backend bash /app/scripts/reset-password.sh newpassword</Code>
          <P><B>Docker-команды:</B></P>
          <Code>{`# Запуск
docker compose up -d

# Остановка
docker compose down

# Пересборка после изменений
docker compose up --build -d

# Логи бэкенда
docker compose logs -f backend

# Логи xray
docker compose exec backend cat /tmp/xray.log`}</Code>
          <P><B>Отладка:</B></P>
          <Code>{`# Проверить правила nftables
docker compose exec backend nft list ruleset

# Тест подключения через ноду
docker compose exec backend curl -x socks5://127.0.0.1:1080 https://ifconfig.me

# Проверить процесс xray
docker compose exec backend ps aux | grep xray`}</Code>
        </>
      ),
    },
  },
]

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export function KnowledgeBase() {
  const lang = useAppStore((s) => s.lang)
  const [openSections, setOpenSections] = useState<Set<string>>(
    () => new Set(['getting-started']),
  )

  const mainRef = useRef<HTMLDivElement>(null)

  const toggle = useCallback((id: string) => {
    setOpenSections((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const scrollTo = useCallback(
    (id: string) => {
      // make sure section is open
      setOpenSections((prev) => {
        const next = new Set(prev)
        next.add(id)
        return next
      })
      // scroll after state update
      requestAnimationFrame(() => {
        const el = document.getElementById(id)
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      })
    },
    [],
  )

  const expandAll = useCallback(() => {
    setOpenSections(new Set(SECTIONS.map((s) => s.id)))
  }, [])

  const collapseAll = useCallback(() => {
    setOpenSections(new Set())
  }, [])

  return (
    <div className="flex h-full">
      {/* Sidebar TOC */}
      <aside className="hidden lg:flex w-56 flex-col border-r border-gray-800 bg-gray-900/50 overflow-y-auto sticky top-0 h-full shrink-0">
        <div className="px-4 py-4 border-b border-gray-800">
          <div className="flex items-center gap-2 text-sm font-semibold text-gray-100">
            <BookOpen className="h-4 w-4 text-brand-400" />
            {lang === 'en' ? 'Contents' : 'Содержание'}
          </div>
        </div>
        <nav className="flex-1 px-2 py-2 space-y-0.5 overflow-y-auto">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              onClick={() => scrollTo(s.id)}
              className={clsx(
                'w-full text-left rounded-lg px-3 py-1.5 text-xs transition-colors truncate',
                openSections.has(s.id)
                  ? 'text-brand-400 bg-brand-900/20'
                  : 'text-gray-500 hover:text-gray-300 hover:bg-gray-800',
              )}
            >
              {s.title[lang]}
            </button>
          ))}
        </nav>
        <div className="px-3 py-3 border-t border-gray-800 space-y-1">
          <button
            onClick={expandAll}
            className="w-full text-left rounded-lg px-3 py-1.5 text-xs text-gray-500 hover:text-gray-300 hover:bg-gray-800 transition-colors"
          >
            {lang === 'en' ? 'Expand all' : 'Развернуть все'}
          </button>
          <button
            onClick={collapseAll}
            className="w-full text-left rounded-lg px-3 py-1.5 text-xs text-gray-500 hover:text-gray-300 hover:bg-gray-800 transition-colors"
          >
            {lang === 'en' ? 'Collapse all' : 'Свернуть все'}
          </button>
        </div>
      </aside>

      {/* Main content */}
      <div ref={mainRef} className="flex-1 overflow-y-auto">
        <div className="p-6 space-y-4">
          {/* Header */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <BookOpen className="h-5 w-5 text-brand-400" />
              <h1 className="text-xl font-bold text-gray-100">
                {lang === 'en' ? 'Knowledge Base' : 'База знаний'}
              </h1>
            </div>

          </div>

          <p className="text-sm text-gray-500">
            {lang === 'en'
              ? 'Reference documentation for PiTun transparent proxy manager.'
              : 'Справочная документация по менеджеру прозрачного прокси PiTun.'}
          </p>

          {/* Sections */}
          {SECTIONS.map((s) => (
            <Section
              key={s.id}
              id={s.id}
              title={s.title[lang]}
              open={openSections.has(s.id)}
              onToggle={() => toggle(s.id)}
            >
              {s.content[lang]}
            </Section>
          ))}
        </div>
      </div>
    </div>
  )
}
