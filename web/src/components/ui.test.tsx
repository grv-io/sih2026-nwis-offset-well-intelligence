import { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { Segmented } from './ui'

// The basin switch (OfficePage.tsx: "Upper Assam (synthetic)" / "Volve, North
// Sea (real)") is built on this generic control, so exercising it here with
// basin-shaped options covers that usage without dragging in react-query/router.
type Basin = 'assam' | 'volve'
const BASIN_OPTIONS: { value: Basin; label: string; title?: string }[] = [
  { value: 'assam', label: 'Upper Assam (synthetic)' },
  { value: 'volve', label: 'Volve, North Sea (real)', title: 'needs formation tops' },
]

function BasinSwitch({ onChange }: { onChange?: (v: Basin) => void }) {
  const [value, setValue] = useState<Basin>('assam')
  return (
    <Segmented
      value={value}
      options={BASIN_OPTIONS}
      label="Basin"
      onChange={(v) => {
        setValue(v)
        onChange?.(v)
      }}
    />
  )
}

describe('Segmented (basin switch)', () => {
  it('renders both basins as a radiogroup with the default selected', () => {
    render(<BasinSwitch />)
    const group = screen.getByRole('radiogroup', { name: 'Basin' })
    expect(group).toBeInTheDocument()

    const assam = screen.getByRole('radio', { name: 'Upper Assam (synthetic)' })
    const volve = screen.getByRole('radio', { name: 'Volve, North Sea (real)' })
    expect(assam).toHaveAttribute('aria-checked', 'true')
    expect(volve).toHaveAttribute('aria-checked', 'false')
  })

  it('switching to Volve calls onChange with "volve" and flips the checked state', async () => {
    const onChange = vi.fn()
    render(<BasinSwitch onChange={onChange} />)

    fireEvent.click(screen.getByRole('radio', { name: 'Volve, North Sea (real)' }))

    expect(onChange).toHaveBeenCalledWith('volve')
    expect(screen.getByRole('radio', { name: 'Volve, North Sea (real)' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('radio', { name: 'Upper Assam (synthetic)' })).toHaveAttribute('aria-checked', 'false')
  })

  it('carries the "needs formation tops" hint as a title on the Volve option', () => {
    render(<BasinSwitch />)
    expect(screen.getByRole('radio', { name: 'Volve, North Sea (real)' })).toHaveAttribute('title', 'needs formation tops')
  })
})
