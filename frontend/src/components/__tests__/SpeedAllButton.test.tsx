/**
 * "Speed All" becomes "Stop" in the same slot once a sweep runs, shows
 * progress, and reverts when the sweep ends.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { SpeedAllButton } from '@/components/SpeedAllButton'

const base = {
  sweeping: false, starting: false, done: 0, total: 0,
  cancelling: false, stopping: false, onRun: vi.fn(), onStop: vi.fn(),
}

describe('<SpeedAllButton>', () => {
  it('idle: only the run button, and it starts a sweep', async () => {
    const onRun = vi.fn()
    render(<SpeedAllButton {...base} onRun={onRun} />)
    expect(screen.queryByText(/Stop|Стоп/)).toBeNull()
    await userEvent.setup().click(screen.getByRole('button', { name: /Speed All|Скорость всех/ }))
    expect(onRun).toHaveBeenCalledOnce()
  })

  it('sweeping: the run button is replaced by Stop with progress', async () => {
    const onStop = vi.fn()
    render(<SpeedAllButton {...base} sweeping done={12} total={340} onStop={onStop} />)
    expect(screen.queryByText(/Speed All|Скорость всех/)).toBeNull()
    const stop = screen.getByRole('button', { name: /(Stop|Стоп) 12\/340/ })
    await userEvent.setup().click(stop)
    expect(onStop).toHaveBeenCalledOnce()
  })

  it('stopping: disabled and says so', () => {
    render(<SpeedAllButton {...base} sweeping done={5} total={9} cancelling />)
    const btn = screen.getByRole('button', { name: /Stopping|Останавливаю/ })
    expect(btn).toBeDisabled()
  })
})
