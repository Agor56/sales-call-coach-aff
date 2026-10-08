'use client'

import {
  DashboardCard,
  DetailHeader,
  DetailTag,
} from '@/components/watermelon/capitalio-dashboard/components/capitalio/shared'
import { BEHAVIOUR, type Key } from './i18n'
import { dateTime, pct, useCoach, type Change, type ChangeRow, type Verdict } from './lib'

const GOOD = 'var(--chart-4)'
const BAD = 'var(--chart-5)'
const WARN = 'var(--chart-3)'
const BLUE = 'var(--chart-1)'
const MUTED = 'var(--muted-foreground)'

export const VERDICT_COLOR: Record<Verdict, string> = {
  worked: GOOD,
  worse: BAD,
  mixed: WARN,
  no_clear_change: MUTED,
  not_clear_yet: MUTED,
  too_early: BLUE,
  not_enough_calls: MUTED,
  no_data_before: MUTED,
}

const SOURCE_KEY: Record<Change['source'], Key> = {
  detected: 'ch.src.detected',
  you: 'ch.src.you',
  'coach recommendation': 'ch.src.rec',
}

export function useVerdictText() {
  const { t, data } = useCoach()
  const d = data?.change_window_days ?? 7
  return (c: Change) => {
    const s = c.score
    if (!s) return t('ch.v.history')
    return t(`ch.v.${s.verdict}` as Key, { n: Math.min(d, Math.floor(s.days) + 1), d })
  }
}

function RowLabel({ r }: { r: ChangeRow }) {
  const { lang } = useCoach()
  const crit = BEHAVIOUR[lang][r.metric]
  return <>{(lang === 'he' && r.label_he) || crit || r.label}</>
}

function Results({ c }: { c: Change }) {
  const { t } = useCoach()
  const rows = (c.score?.rows ?? []).filter((r) => r.change)
  if (!rows.length) return <div className="text-xs text-muted-foreground">{t('ch.noNumbers')}</div>
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-xs text-muted-foreground">
          <th className="py-1 text-start font-normal">{t('ch.number')}</th>
          <th className="py-1 text-end font-normal">{t('ch.before')}</th>
          <th className="py-1 text-end font-normal">{t('ch.after')}</th>
          <th className="py-1 text-end font-normal">{t('ch.change')}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const ch = r.change!
          const color = r.better === true ? GOOD : r.better === false ? BAD : MUTED
          return (
            <tr key={r.metric} className="border-t border-border">
              <td className="py-1.5">
                <RowLabel r={r} />
                {r.watched ? <span title={t('ch.watched')}> ★</span> : null}
              </td>
              <td className="py-1.5 text-end tabular-nums" dir="ltr">{pct(ch.before)}</td>
              <td className="py-1.5 text-end tabular-nums" dir="ltr">{pct(ch.now)}</td>
              <td className="py-1.5 text-end font-medium tabular-nums" style={{ color }}>
                <span dir="ltr">{`${ch.pts > 0 ? '+' : ''}${ch.pts.toFixed(1)} pts`}</span>{' '}
                <span className="text-xs font-normal">
                  {r.better === true ? t('ch.better') : r.better === false ? t('ch.worse') : t('ch.noise')}
                </span>
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function Details({ c }: { c: Change }) {
  const { t } = useCoach()
  const parts = c.details.filter((p) => p.label !== 'Saves')
  if (!parts.length) return null
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-muted-foreground">{t('ch.details')}</summary>
      <div className="mt-2 flex flex-col gap-3">
        {parts.map((p, i) => (
          <div key={i} className="flex flex-col gap-1">
            <div className="font-medium">{p.summary}</div>
            {p.added?.length ? (
              <div className="rounded-md p-2" dir="auto" style={{ background: `color-mix(in oklab, ${GOOD} 10%, transparent)` }}>
                {p.added.map((l, j) => (
                  <div key={j}>+ {l}</div>
                ))}
                {(p.added_n ?? 0) > p.added.length ? <div className="text-muted-foreground">… {t('ch.more', { n: (p.added_n ?? 0) - p.added.length })}</div> : null}
              </div>
            ) : null}
            {p.removed?.length ? (
              <div className="rounded-md bg-foreground/5 p-2 text-muted-foreground line-through decoration-foreground/30" dir="auto">
                {p.removed.map((l, j) => (
                  <div key={j}>− {l}</div>
                ))}
              </div>
            ) : null}
            {p.before !== undefined && p.after !== undefined && !p.added?.length ? (
              <div className="grid gap-2 md:grid-cols-2">
                <div className="rounded-md bg-foreground/5 p-2 text-muted-foreground line-through decoration-foreground/30" dir="auto">{p.before || '—'}</div>
                <div className="rounded-md border p-2" dir="auto" style={{ borderColor: `color-mix(in oklab, ${GOOD} 45%, transparent)` }}>{p.after || '—'}</div>
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </details>
  )
}

function ChangeCard({ c }: { c: Change }) {
  const { t, lang, data } = useCoach()
  const verdict = useVerdictText()
  const s = c.score
  const color = s ? VERDICT_COLOR[s.verdict] : MUTED
  const name = (k: string) => data?.agents.find((a) => a.key === k)?.label ?? k
  return (
    <div className="grid gap-4 border-b border-border pb-5 last:border-b-0 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <DetailTag color={color}>{verdict(c)}</DetailTag>
          {s?.warning ? <DetailTag color={BAD}>{t('ch.warning')}</DetailTag> : null}
          <span className="text-xs text-muted-foreground">{dateTime(c.at, lang)}</span>
        </div>
        <div className="text-sm font-medium leading-snug" dir="auto">{c.title}</div>
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span>{c.agent_keys.map(name).join(' · ')}</span>
          <span>{t(SOURCE_KEY[c.source])}</span>
        </div>
        {s && s.rows.length ? (
          <div className="text-xs text-muted-foreground" dir="ltr">
            {t('ch.windows', {
              b: `${dateTime(s.before[0], lang)} → ${dateTime(s.before[1], lang)}`,
              a: `${dateTime(s.after[0], lang)} → ${dateTime(Math.min(s.after[1], data?.generated_at ?? s.after[1]), lang)}`,
            })}
          </div>
        ) : null}
        {s?.overlaps.length ? (
          <div className="text-xs" style={{ color: WARN }}>
            {t('ch.overlap', { t: s.overlaps.slice(0, 3).map((o) => o.title).join(' · ') })}
          </div>
        ) : null}
        <Details c={c} />
      </div>
      <div className="min-w-0">
        <Results c={c} />
      </div>
    </div>
  )
}

export function ChangesPage() {
  const { data, t, selectedAgents } = useCoach()
  const all = data?.changes ?? []
  const list = all.filter((c) => !selectedAgents || c.agent_keys.some((k) => selectedAgents.includes(k)))
  const counts = list.reduce<Partial<Record<Verdict, number>>>((acc, c) => {
    if (c.score) acc[c.score.verdict] = (acc[c.score.verdict] ?? 0) + 1
    return acc
  }, {})
  return (
    <div className="flex min-w-0 flex-col gap-3 px-4 pb-8 md:px-8">
      <DashboardCard className="flex flex-col gap-5">
        <DetailHeader title={t('ch.title')} subtitle={t('ch.sub', { d: data?.change_window_days ?? 7 })}>
          {(Object.entries(counts) as [Verdict, number][]).map(([v, n]) => (
            <DetailTag key={v} color={VERDICT_COLOR[v]}>
              {t(`ch.c.${v}` as Key)} · {n}
            </DetailTag>
          ))}
        </DetailHeader>
        {list.length ? list.map((c) => <ChangeCard key={c.id} c={c} />) : <div className="py-6 text-sm text-muted-foreground">{t('ch.none')}</div>}
      </DashboardCard>
    </div>
  )
}
