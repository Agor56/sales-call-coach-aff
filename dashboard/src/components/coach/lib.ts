'use client'

import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { translate, type Key, type Lang } from './i18n'

// ---- shape of dashboard/public/data/<account>.json (written by `./coach export`)
export type Stages = Record<'pending' | 'no_connect' | 'voicemail' | 'no_reply' | 'early_drop' | 'engaged', number>

export type Funnel = {
  total: number
  stages: Stages
  human_connected: number
  no_reply_rate: number | null
  spoke_rate: number | null
  engaged_rate: number | null
  no_reply_median_secs: number | null
  booking_tool_calls: number
  booked: number
  booking_calls_not_yet_fetched_by_coach: number
  booked_qualified: number
  booked_disqualified: number
  booked_callback_only: number
  booked_unknown_profile: number
  qualified_per_connected: number | null
}

export type Group = { yes: number; applicable: number; rate: number | null; ci95: [number, number] | null }
export type Comparison = {
  criterion_id: string
  success_group: Group
  failure_group: Group
  diff_pp: number | null
  excluded: Record<string, number>
  small_sample: boolean
}

export type Tech = {
  calls: number
  median_response_secs: number | null
  median_slowest_response_secs: number | null
  slow_calls: number
  slow_call_rate: number | null
  calls_with_interruption: number
  interruption_call_rate: number | null
  interruptions_per_agent_turn: number | null
}

export type Daily = {
  date: string
  calls: number
  connected: number
  no_reply: number
  spoke: number
  engaged: number
  booking_tool_calls: number
  booked_qualified: number
  junk: number
}

export type Version = {
  agent: string
  version_id: string
  first_call: number
  last_call: number
  calls: number
  connected: number
  no_reply_rate: number | null
  engaged_rate: number | null
  booking_rate: number | null
}

export type Period = {
  window: { since_unix: number; until_unix: number }
  // the same-length window right before this one (absent when there is no earlier data)
  previous?: { since_unix: number; until_unix: number; funnel: Funnel; per_agent: Record<string, Funnel> }
  success_definition: string
  grader: string
  funnel: Record<string, Funnel>
  comparisons: Record<string, Comparison[]>
  technical: {
    slow_threshold_secs: number
    by_outcome: Record<'success' | 'failure', Tech>
    by_stage: Record<'early_drop' | 'engaged', Tech>
    by_agent: Record<string, Tech>
  }
  coverage: {
    eligible_calls: number
    with_details: number
    outcomes: Record<string, number>
    with_checklist: number
    distinct_leads: number
  }
  disqualification_reasons: Record<string, number>
  daily: Daily[]
  versions: Version[]
  per_agent?: Record<string, PerAgent>
}

export type PerAgent = {
  funnel: Funnel
  comparisons: Comparison[]
  technical: { by_outcome: Record<'success' | 'failure', Tech>; by_stage: Record<'early_drop' | 'engaged', Tech> }
  coverage: Period['coverage']
  disqualification_reasons: Record<string, number>
  daily: Daily[]
}

export type Evidence = { conversation_id: string; turn: number; quote: string; why: string; why_he?: string; secs?: number | null }
export type Report = {
  created_at: number
  model: string | null
  path: string
  usage?: { model?: string; provider?: string; input_tokens?: number; output_tokens?: number; cost_usd?: number } | null
  headline: string
  headline_he?: string
  no_clear_pattern: boolean
  problems: { title: string; title_he?: string; explanation: string; explanation_he?: string; criterion_id: string; evidence: Evidence[] }[]
  proposed_edit: {
    target: string
    current_text: string
    new_text: string
    why: string
    why_he?: string
    how_to_test: string
    how_to_test_he?: string
    located_in_live_prompt: boolean
  }
}

export type Experiment = {
  id: number
  agent_key: string
  name: string
  change: { field: string; old: string; new: string }
  variant_pct: number
  started_at: number
  stopped_at: number | null
}

export type DoneItem = {
  id: string
  marked_at: number
  report_created_at: number | null
  target: string
  current_text: string
  new_text: string
  why: string
}

export type CostDay = {
  date: string
  graded_calls: number
  grading_cost: number
  grading_tokens: number
  reports: number
  report_cost: number
  report_tokens_in: number
  report_tokens_out: number
  total_cost: number
  models: string[]
}

export type Costs = {
  days: CostDay[]
  currency: string
  openrouter: { remaining: number | null; used_total: number | null; checked_at: number } | null
}

export type CoachData = {
  generated_at: number
  account: string
  account_label: string
  agents: { key: string; label: string }[]
  grader: string
  checklist: { id: string; question: string }[]
  periods: Partial<Record<PeriodKey, Period>>
  report: Report | null
  experiments: Experiment[]
  changes?: Change[]
  change_window_days?: number
  costs?: Costs
}

// ---- change log: what changed on the agents and what happened to the calls after it
export type ChangeRow = {
  metric: string
  label: string
  label_he: string | null
  higher_is_better: boolean
  watched: boolean
  change: { now: number; before: number; pts: number; real: boolean; n: number; prev_n: number } | null
  better: boolean | null
}

export type Verdict =
  | 'worked' | 'worse' | 'mixed' | 'no_clear_change' | 'not_clear_yet' | 'too_early' | 'not_enough_calls' | 'no_data_before'

export type Change = {
  id: number
  kind: 'agent' | 'dictionary' | 'manual'
  agent_keys: string[]
  at: number
  title: string
  details: { label: string; summary: string; before?: string; after?: string; added?: string[]; removed?: string[]; added_n?: number; removed_n?: number }[]
  source: 'detected' | 'you' | 'coach recommendation'
  recommendation: { report_created_at: number | null; new_text: string; watch_metric: string | null; watch_direction: string | null } | null
  score: {
    days: number
    window_days: number
    verdict: Verdict
    final: boolean
    warning?: boolean
    before: [number, number]
    after: [number, number]
    rows: ChangeRow[]
    overlaps: { id: number; title: string; at: number }[]
    calls?: { before: number; after: number }
  } | null
}

export type PeriodKey = '1d' | '2d' | '7d' | '30d'
export const PERIODS: PeriodKey[] = ['1d', '2d', '7d', '30d']
export type Theme = 'light' | 'dark'

export type PageKey = 'overview' | 'analytics' | 'agents' | 'openers' | 'report' | 'changes' | 'costs'

// ---- app state
type CoachState = {
  page: PageKey
  setPage: (p: PageKey) => void
  period: PeriodKey
  setPeriod: (p: PeriodKey) => void
  account: string | null
  setAccount: (a: string) => void
  accounts: string[]
  accountLabels: Record<string, string>
  data: CoachData | null
  error: string | null
  lang: Lang
  setLang: (l: Lang) => void
  theme: Theme
  setTheme: (t: Theme) => void
  t: (key: Key, vars?: Record<string, string | number>) => string
  /** null = all agents */
  selectedAgents: string[] | null
  setSelectedAgents: (keys: string[] | null) => void
  done: DoneItem[]
  markDone: (item: Omit<DoneItem, 'id' | 'marked_at'>) => Promise<void>
  undoDone: (id: string) => Promise<void>
}

export const CoachContext = createContext<CoachState | null>(null)

export function useCoach() {
  const ctx = useContext(CoachContext)
  if (!ctx) throw new Error('useCoach must be used inside CoachProvider')
  return ctx
}

export function usePeriodData(): Period | null {
  const { data, period, selectedAgents } = useCoach()
  if (!data) return null
  const p = data.periods[period] ?? data.periods['7d'] ?? data.periods['2d'] ?? data.periods['30d'] ?? null
  if (!p || !selectedAgents || !p.per_agent) return p
  return combinePeriod(p, selectedAgents)
}

// ---- adding up a selection of agents (counts are summed, rates recomputed from the sums)
const COUNT_FIELDS = [
  'total', 'human_connected', 'booking_tool_calls', 'booked', 'booking_calls_not_yet_fetched_by_coach',
  'booked_qualified', 'booked_disqualified', 'booked_callback_only', 'booked_unknown_profile',
] as const

function sumBy<T>(items: T[], f: (x: T) => number) {
  return items.reduce((s, x) => s + (f(x) || 0), 0)
}

function weighted<T>(items: T[], value: (x: T) => number | null, weight: (x: T) => number) {
  const w = sumBy(items, (x) => (value(x) == null ? 0 : weight(x)))
  return w ? sumBy(items, (x) => (value(x) ?? 0) * weight(x)) / w : null
}

export function wilson(yes: number, n: number): [number, number] | null {
  if (!n) return null
  const z = 1.96
  const p = yes / n
  const denom = 1 + (z * z) / n
  const centre = (p + (z * z) / (2 * n)) / denom
  const half = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / denom
  return [Math.max(0, centre - half), Math.min(1, centre + half)]
}

function combineFunnel(fs: Funnel[]): Funnel {
  const stages = Object.fromEntries(
    (['pending', 'no_connect', 'voicemail', 'no_reply', 'early_drop', 'engaged'] as const).map((k) => [k, sumBy(fs, (f) => f.stages[k])]),
  ) as Stages
  const out = Object.fromEntries(COUNT_FIELDS.map((k) => [k, sumBy(fs, (f) => f[k])])) as unknown as Funnel
  const hc = out.human_connected
  return {
    ...out,
    stages,
    no_reply_rate: hc ? stages.no_reply / hc : null,
    spoke_rate: hc ? (stages.early_drop + stages.engaged) / hc : null,
    engaged_rate: hc ? stages.engaged / hc : null,
    qualified_per_connected: hc ? out.booked_qualified / hc : null,
    no_reply_median_secs: weighted(fs, (f) => f.no_reply_median_secs, (f) => f.stages.no_reply),
  }
}

function combineComparisons(lists: Comparison[][], minGroup = 20): Comparison[] {
  const ids = [...new Set(lists.flat().map((c) => c.criterion_id))]
  return ids.map((id) => {
    const cs = lists.flat().filter((c) => c.criterion_id === id)
    const group = (g: 'success_group' | 'failure_group') => {
      const yes = sumBy(cs, (c) => c[g].yes)
      const applicable = sumBy(cs, (c) => c[g].applicable)
      return { yes, applicable, rate: applicable ? yes / applicable : null, ci95: wilson(yes, applicable) }
    }
    const s = group('success_group')
    const f = group('failure_group')
    const excluded: Record<string, number> = {}
    cs.forEach((c) => Object.entries(c.excluded).forEach(([k, v]) => (excluded[k] = (excluded[k] ?? 0) + v)))
    return {
      criterion_id: id,
      success_group: s,
      failure_group: f,
      diff_pp: s.rate != null && f.rate != null ? Math.round((s.rate - f.rate) * 1000) / 10 : null,
      excluded,
      small_sample: Math.min(s.applicable, f.applicable) < minGroup,
    }
  })
}

function combineTech(ts: (Tech | undefined)[]): Tech {
  const list = ts.filter((t): t is Tech => !!t && t.calls > 0)
  const calls = sumBy(list, (t) => t.calls)
  // calls whose slowest-answer time is known = slow_calls / slow_call_rate (fallback: all calls)
  const p90n = sumBy(list, (t) => (t.slow_call_rate ? t.slow_calls / t.slow_call_rate : t.calls))
  const slow = sumBy(list, (t) => t.slow_calls)
  const inter = sumBy(list, (t) => t.calls_with_interruption)
  return {
    calls,
    median_response_secs: weighted(list, (t) => t.median_response_secs, (t) => t.calls),
    median_slowest_response_secs: weighted(list, (t) => t.median_slowest_response_secs, (t) => t.calls),
    slow_calls: slow,
    slow_call_rate: p90n ? slow / p90n : null,
    calls_with_interruption: inter,
    interruption_call_rate: calls ? inter / calls : null,
    interruptions_per_agent_turn: weighted(list, (t) => t.interruptions_per_agent_turn, (t) => t.calls),
  }
}

function combineDaily(lists: Daily[][]): Daily[] {
  const by = new Map<string, Daily>()
  for (const d of lists.flat()) {
    const cur = by.get(d.date)
    if (!cur) by.set(d.date, { ...d })
    else
      (['calls', 'connected', 'no_reply', 'spoke', 'engaged', 'booking_tool_calls', 'booked_qualified', 'junk'] as const).forEach(
        (k) => (cur[k] += d[k]),
      )
  }
  return [...by.values()].sort((a, b) => a.date.localeCompare(b.date))
}

export function combinePeriod(p: Period, keys: string[]): Period {
  const parts = keys.map((k) => p.per_agent?.[k]).filter((x): x is PerAgent => !!x)
  const outcomes: Record<string, number> = {}
  const reasons: Record<string, number> = {}
  parts.forEach((a) => {
    Object.entries(a.coverage.outcomes).forEach(([k, v]) => (outcomes[k] = (outcomes[k] ?? 0) + v))
    Object.entries(a.disqualification_reasons).forEach(([k, v]) => (reasons[k] = (reasons[k] ?? 0) + v))
  })
  return {
    ...p,
    funnel: { all: combineFunnel(parts.map((a) => a.funnel)), ...Object.fromEntries(keys.filter((k) => p.funnel[k]).map((k) => [k, p.funnel[k]])) },
    comparisons: { all: combineComparisons(parts.map((a) => a.comparisons)) },
    technical: {
      ...p.technical,
      by_outcome: {
        success: combineTech(parts.map((a) => a.technical.by_outcome.success)),
        failure: combineTech(parts.map((a) => a.technical.by_outcome.failure)),
      },
      by_stage: {
        early_drop: combineTech(parts.map((a) => a.technical.by_stage.early_drop)),
        engaged: combineTech(parts.map((a) => a.technical.by_stage.engaged)),
      },
      by_agent: Object.fromEntries(keys.filter((k) => p.technical.by_agent[k]).map((k) => [k, p.technical.by_agent[k]])),
    },
    coverage: {
      eligible_calls: sumBy(parts, (a) => a.coverage.eligible_calls),
      with_details: sumBy(parts, (a) => a.coverage.with_details),
      with_checklist: sumBy(parts, (a) => a.coverage.with_checklist),
      distinct_leads: sumBy(parts, (a) => a.coverage.distinct_leads),
      outcomes,
    },
    disqualification_reasons: reasons,
    daily: combineDaily(parts.map((a) => a.daily)),
    versions: p.versions.filter((v) => keys.includes(v.agent)),
    previous: combinePrevious(p.previous, keys),
  }
}

function combinePrevious(prev: Period['previous'], keys: string[]): Period['previous'] {
  const parts = prev ? keys.map((k) => prev.per_agent?.[k]).filter((x): x is Funnel => !!x) : []
  return prev && parts.length ? { ...prev, funnel: combineFunnel(parts) } : undefined
}

// ---- "better or worse than before": change of a rate between two windows, and whether it is more than noise
export type RateChange = { now: number; before: number; pts: number; real: boolean } | null

export function rateChange(yes: number, n: number, prevYes: number, prevN: number, minN = 100): RateChange {
  if (n < minN || prevN < minN) return null
  const a = yes / n
  const b = prevYes / prevN
  const pooled = (yes + prevYes) / (n + prevN)
  const se = Math.sqrt(pooled * (1 - pooled) * (1 / n + 1 / prevN))
  // two-proportion z-test at 95%: smaller moves happen by chance from day to day
  return { now: a, before: b, pts: (a - b) * 100, real: se > 0 && Math.abs(a - b) / se > 1.96 }
}

function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStored(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    /* private mode etc. — not important */
  }
}

export function useCoachState(): CoachState {
  const [page, setPage] = useState<PageKey>('overview')
  const [period, setPeriodState] = useState<PeriodKey>('7d')
  const [accounts, setAccounts] = useState<string[]>([])
  const [accountLabels, setAccountLabels] = useState<Record<string, string>>({})
  const [account, setAccountState] = useState<string | null>(null)
  const [data, setData] = useState<CoachData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lang, setLangState] = useState<Lang>('en')
  const [theme, setThemeState] = useState<Theme>('light')
  const [selectedAgents, setSelectedState] = useState<string[] | null>(null)
  const [done, setDone] = useState<DoneItem[]>([])

  // reflect language + theme on <html> (direction, font shaping, dark palette)
  useEffect(() => {
    const html = document.documentElement
    html.lang = lang
    html.dir = lang === 'he' ? 'rtl' : 'ltr'
  }, [lang])
  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme])

  const t = useCallback((key: Key, vars?: Record<string, string | number>) => translate(lang, key, vars), [lang])

  useEffect(() => {
    const storedPeriod = readStored('coach.period') as PeriodKey | null
    if (storedPeriod && PERIODS.includes(storedPeriod)) setPeriodState(storedPeriod)
    const storedLang = readStored('coach.lang')
    if (storedLang === 'he' || storedLang === 'en') setLangState(storedLang)
    const storedTheme = readStored('coach.theme')
    if (storedTheme === 'dark' || storedTheme === 'light') setThemeState(storedTheme)
    fetch('/api/coach/accounts', { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`accounts.json: HTTP ${r.status}`))))
      .then((raw: (string | { key: string; label: string })[]) => {
        const list = raw.map((a) => (typeof a === 'string' ? a : a.key))
        setAccountLabels(Object.fromEntries(raw.map((a) => (typeof a === 'string' ? [a, a] : [a.key, a.label]))))
        setAccounts(list)
        const stored = readStored('coach.account')
        setAccountState(stored && list.includes(stored) ? stored : (list[0] ?? null))
        if (!list.length) setError('state.noData')
      })
      .catch(() => setError('state.noData'))
  }, [])

  useEffect(() => {
    if (!account) return
    fetch(`/api/coach/done/${account}`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : []))
      .then((list: DoneItem[]) => setDone(Array.isArray(list) ? list : []))
      .catch(() => setDone([]))
  }, [account])

  const markDone = useCallback(
    async (item: Omit<DoneItem, 'id' | 'marked_at'>) => {
      if (!account) return
      const r = await fetch(`/api/coach/done/${account}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(item),
      })
      if (r.ok) setDone(await r.json())
    },
    [account],
  )
  const undoDone = useCallback(
    async (id: string) => {
      if (!account) return
      const r = await fetch(`/api/coach/done/${account}?id=${encodeURIComponent(id)}`, { method: 'DELETE' })
      if (r.ok) setDone(await r.json())
    },
    [account],
  )

  useEffect(() => {
    if (!account) return
    setData(null)
    fetch(`/api/coach/${account}`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: CoachData) => {
        setData(d)
        setError(null)
        // restore this client's agent selection, dropping agents that no longer exist
        try {
          const stored = JSON.parse(readStored(`coach.agents.${account}`) ?? 'null') as string[] | null
          const valid = stored?.filter((k) => d.agents.some((a) => a.key === k)) ?? null
          setSelectedState(valid && valid.length && valid.length < d.agents.length ? valid : null)
        } catch {
          setSelectedState(null)
        }
      })
      .catch((e) => setError(`Could not load data for ${account}: ${e.message}`))
  }, [account])

  return {
    page,
    setPage,
    period,
    setPeriod: (p) => {
      setPeriodState(p)
      writeStored('coach.period', p)
    },
    account,
    setAccount: (a) => {
      setAccountState(a)
      writeStored('coach.account', a)
    },
    accounts,
    accountLabels,
    data,
    error,
    lang,
    setLang: (l) => {
      setLangState(l)
      writeStored('coach.lang', l)
    },
    theme,
    setTheme: (th) => {
      setThemeState(th)
      writeStored('coach.theme', th)
    },
    t,
    done,
    markDone,
    undoDone,
    selectedAgents,
    setSelectedAgents: (keys) => {
      const normal = keys && data && keys.length < data.agents.length && keys.length > 0 ? keys : null
      setSelectedState(normal)
      if (account) writeStored(`coach.agents.${account}`, JSON.stringify(normal))
    },
  }
}

// ---- formatting
export const pct = (v: number | null | undefined, digits = 1) => (v == null ? '—' : `${(v * 100).toFixed(digits)}%`)
export const num = (v: number | null | undefined) => (v == null ? '—' : v.toLocaleString('en-US'))
/** Dollars: small amounts keep 4 decimals so a few cents stay readable ($0.0465), bigger ones 2. */
export const usd = (v: number | null | undefined) =>
  v == null ? '—' : v === 0 ? '$0' : Math.abs(v) < 1 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`
export const secs = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(2)}s`)
export const ratio = (a: number, b: number) => (b ? a / b : null)
const locale = (lang: Lang) => (lang === 'he' ? 'he-IL' : 'en-GB')
export const dateShort = (iso: string, lang: Lang = 'en') =>
  new Date(`${iso}T12:00:00`).toLocaleDateString(locale(lang), { day: 'numeric', month: 'short' })
export const dateTime = (unix: number, lang: Lang = 'en') =>
  new Date(unix * 1000).toLocaleString(locale(lang), { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

/** Hebrew text from the report when the dashboard is in Hebrew and the report has it; English otherwise. */
export const pick = (lang: Lang, en: string, he?: string) => (lang === 'he' && he ? he : en)

export const STAGE_META: { key: keyof Stages; color: string }[] = [
  { key: 'no_connect', color: 'color-mix(in oklab, var(--muted-foreground) 45%, transparent)' },
  { key: 'voicemail', color: 'var(--chart-2)' },
  { key: 'no_reply', color: 'var(--chart-5)' },
  { key: 'early_drop', color: 'var(--chart-3)' },
  { key: 'engaged', color: 'var(--chart-4)' },
]


/** The done-list entry matching the report's current suggestion, if you already marked it done. */
export function findDone(done: DoneItem[], target: string, newText: string) {
  return done.find((d) => d.target === target && d.new_text.trim() === newText.trim()) ?? null
}
