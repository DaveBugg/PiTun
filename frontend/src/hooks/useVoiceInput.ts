import { useRef, useState } from 'react'

import { assistantApi } from '@/api/client'

type Mode = 'browser' | 'provider' | 'server' | 'off'

// The Web Speech API is not in the TS DOM lib everywhere.
interface SpeechResultEvent { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }
interface Recognizer {
  lang: string
  interimResults: boolean
  continuous: boolean
  onresult: ((e: SpeechResultEvent) => void) | null
  onerror: ((e: { error: string }) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
}

function recognizerClass(): (new () => Recognizer) | null {
  const w = window as unknown as { SpeechRecognition?: new () => Recognizer; webkitSpeechRecognition?: new () => Recognizer }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

/** Why the mic cannot work here, or null. */
export function voiceUnavailable(mode: Mode): 'off' | 'insecure' | 'unsupported' | null {
  if (mode === 'off') return 'off'
  if (!window.isSecureContext) return 'insecure'
  if (mode === 'browser') return recognizerClass() ? null : 'unsupported'
  return typeof MediaRecorder !== 'undefined' && navigator.mediaDevices ? null : 'unsupported'
}

/**
 * Mic button logic. `browser` uses the browser's speech recognition (live
 * text); `provider` / `server` record audio and send it to the box. The text
 * lands in the input through `onText` for editing before it is sent.
 */
export function useVoiceInput(mode: Mode, lang: string, onText: (text: string, final: boolean) => void) {
  const [state, setState] = useState<'idle' | 'listening' | 'transcribing'>('idle')
  const [error, setError] = useState<string | null>(null)
  const rec = useRef<Recognizer | null>(null)
  const media = useRef<MediaRecorder | null>(null)

  const start = async () => {
    setError(null)
    if (mode === 'browser') {
      const Cls = recognizerClass()
      if (!Cls) return
      const r = new Cls()
      r.lang = lang
      r.interimResults = true
      r.continuous = true
      let done = ''
      r.onresult = (e) => {
        let interim = ''
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const res = e.results[i]
          if (res.isFinal) done += res[0].transcript
          else interim += res[0].transcript
        }
        onText((done + interim).trim(), false)
      }
      r.onerror = (e) => { if (e.error !== 'aborted' && e.error !== 'no-speech') setError(e.error) }
      r.onend = () => { setState('idle'); rec.current = null; if (done) onText(done.trim(), true) }
      rec.current = r
      r.start()
      setState('listening')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      const chunks: Blob[] = []
      mr.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data) }
      mr.onstop = async () => {
        stream.getTracks().forEach((tr) => tr.stop())
        media.current = null
        const blob = new Blob(chunks, { type: mr.mimeType || 'audio/webm' })
        if (!blob.size) { setState('idle'); return }
        setState('transcribing')
        try {
          const text = await assistantApi.transcribe(blob, lang.split('-')[0])
          if (text) onText(text, true)
        } catch (err) {
          setError((err as Error).message)
        } finally {
          setState('idle')
        }
      }
      media.current = mr
      mr.start()
      setState('listening')
    } catch (err) {
      setError((err as Error).message)
      setState('idle')
    }
  }

  const stop = () => {
    rec.current?.stop()
    if (media.current && media.current.state !== 'inactive') media.current.stop()
  }

  return { state, error, start, stop, toggle: () => (state === 'listening' ? stop() : void start()) }
}
