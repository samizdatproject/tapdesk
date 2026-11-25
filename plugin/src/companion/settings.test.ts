import { describe, expect, it } from 'vitest'
import { MAX_WATCH, addAlert, addSymbol, tonAddressError } from './settings'

const ADDR = 'EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N'

describe('TON address check', () => {
  it('accepts friendly and raw mainnet addresses, and empty', () => {
    expect(tonAddressError(ADDR)).toBeNull()
    expect(tonAddressError(` ${ADDR} `)).toBeNull()
    expect(tonAddressError('0:83dfd552e63729b472fcbcc8c45ebcc6691702558b68ec7527e1ba403a0f31a8')).toBeNull()
    expect(tonAddressError('')).toBeNull()
  })

  it('catches typos with the checksum', () => {
    expect(tonAddressError(ADDR.slice(0, -1) + 'M')).toContain('checksum')
    expect(tonAddressError('hello')).toContain("doesn't look like")
  })
})

describe('watchlist and alerts', () => {
  it('adds coins with human errors', () => {
    expect(addSymbol(['BTC'], ' sol ')).toEqual({ list: ['BTC', 'SOL'], error: null })
    expect(addSymbol(['BTC'], 'btc').error).toContain('already')
    expect(addSymbol(['BTC'], 'usdt').error).toContain('quote currency')
    expect(addSymbol(['BTC'], 'b!').error).toContain('ticker')
    const full = Array.from({ length: MAX_WATCH }, (_, i) => `C${i}`)
    expect(addSymbol(full, 'BTC').error).toContain('At most')
  })

  it('adds alerts with a numeric level', () => {
    expect(addAlert([], { symbol: 'btc', op: 'below', level: '57,000' })).toEqual({
      list: [{ symbol: 'BTC', op: 'below', level: 57_000 }],
      error: null,
    })
    expect(addAlert([], { symbol: 'BTC', op: 'below', level: 'abc' }).error).toContain('number')
    expect(addAlert([], { symbol: 'BTC', op: 'near', level: '1' }).error).toContain('above or below')
    const one = [{ symbol: 'BTC', op: 'below' as const, level: 1 }]
    expect(addAlert(one, { symbol: 'BTC', op: 'below', level: '1' }).error).toContain('already')
  })
})
