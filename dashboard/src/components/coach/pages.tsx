'use client'

import type { ReactNode } from 'react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  ChartMarker,
  DashboardCard,
  DashboardChartTooltip,
  DetailHeader,
  DetailTag,
} from '@/components/watermelon/capitalio-dashboard/components/capitalio/shared'
import { cn } from '@/lib/utils'
import { BEHAVIOUR, junkReason, type Key } from './i18n'
import {
  STAGE_META,
  dateShort,
  dateTime,
  findDone,
  num,
  pct,
  pick,
  ratio,
  secs,
  usd,
  useCoach,
  usePeriodData,
  type Comparison,
  type Funnel,
  type Tech,
} from './lib'

const stripTags = (t: string) => t.replace(/\[[^\]\n]{1,40}\]\s*/g, '').trim()

const GOOD = 'var(--chart-4)'
const BAD = 'var(--chart-5)'
const WARN = 'var(--chart-3)'
const BLUE = 'var(--chart-1)'
const AXIS_TICK = { fontSize: 12, fill: 'var(--muted-foreground)' }

function Page({ children }: { children: ReactNode }) {
  return <div className="flex min-w-0 flex-col gap-3 px-4 pb-8 md:px-8">{children}</div>
}

function Empty({ text }: { text: string }) {
  return <div className="py-6 text-sm text-muted-foreground">{text}</div>
}

function Stat({ title, value, sub, tone, hint }: { title: string; value: string; sub?: string; tone?: 'good' | 'bad'; hint?: string }) {
  return (
    <DashboardCard className="flex min-h-36 flex-col justify-between gap-4">
      <h2 className="truncate text-base font-normal leading-6 text-muted-foreground">{title}</h2>
      <div className="flex flex-wrap items-end gap-2">
        <div className="text-3xl font-bold leading-none" dir="ltr">{value}</div>
        {sub ? (
          <span className="pb-0.5 text-sm font-medium" style={{ color: tone === 'good' ? GOOD : tone === 'bad' ? BAD : undefined }}>
            {sub}
          </span>
        ) : null}
      </div>
      {hint ? <div className="text-xs leading-snug text-muted-foreground">{hint}</div> : null}
    </DashboardCard>
  )
}

function Th({ children }: { children: ReactNode }) {
  return <th className="bg-foreground/5 px-3 py-2.5 text-start font-medium first:rounded-s-lg last:rounded-e-lg">{children}</th>
}

// =============================================================================== Overview
export function OverviewPage() {
  const p = usePeriodData()
  const { data, setPage, t, lang, done } = useCoach()
  if (!p || !data) return <Page><Empty text={t('state.noCalls')} /></Page>
  const f = p.funnel.all
  const r = data.report
  return (
    <Page>
      <div className="grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          title={t('ov.calls')}
          value={num(f.total)}
          sub={t('ov.pickedUpN', { n: num(f.human_connected) })}
          hint={t('ov.dialsReached', { p: pct(ratio(f.human_connected, f.total)) })}
        />
        <Stat
          title={t('ov.silent')}
          value={pct(f.no_reply_rate)}
          sub={t('ov.nCalls', { n: num(f.stages.no_reply) })}
          tone="bad"
          hint={t('ov.goneAfter', { s: f.no_reply_median_secs ?? '—' })}
        />
        <Stat
          title={t('ov.conversations')}
          value={pct(f.engaged_rate)}
          sub={t('ov.nCalls', { n: num(f.stages.engaged) })}
          tone="good"
          hint={t('ov.convHint')}
        />
        <Stat
          title={t('ov.bookings')}
          value={num(f.booking_tool_calls)}
          sub={f.booked ? t('ov.goodJunk', { g: f.booked_qualified, j: f.booked_disqualified }) : undefined}
          tone={f.booked_disqualified ? 'bad' : 'good'}
          hint={
            f.booked
              ? t('ov.bookingsHintChecked', { n: num(f.booked), p: pct(ratio(f.booking_tool_calls, f.human_connected), 2) })
              : t('ov.bookingsHint', { p: pct(ratio(f.booking_tool_calls, f.human_connected), 2) })
          }
        />
      </div>

      <div className="grid min-w-0 gap-3 lg:grid-cols-3">
        <DailyPanel />
        <WhereCallsEnd funnel={f} />
      </div>

      <div className="grid min-w-0 gap-3 lg:grid-cols-3">
        <AgentsTable className="lg:col-span-2" />
        <DashboardCard className="flex flex-col gap-5">
          <DetailHeader title={t('fix.title')}>
            {r && findDone(done, r.proposed_edit.target, r.proposed_edit.new_text) ? <DetailTag color={GOOD}>{t('done.tag')}</DetailTag> : null}
            <DetailTag color={r ? BLUE : 'var(--muted-foreground)'}>{r ? dateTime(r.created_at, lang) : t('fix.noReport')}</DetailTag>
          </DetailHeader>
          {r ? (
            <>
              <p className="text-sm leading-relaxed">{pick(lang, r.headline, r.headline_he)}</p>
              <div className="flex flex-col gap-4">
                {r.problems.map((pr, i) => (
                  <div key={pr.title} className={cn('flex gap-3', i > 0 && 'border-t border-border pt-4')}>
                    <div className="dashboard-icon-badge flex size-7 shrink-0 items-center justify-center rounded-lg text-sm font-semibold text-white">{i + 1}</div>
                    <div className="min-w-0 font-medium leading-snug">{pick(lang, pr.title, pr.title_he)}</div>
                  </div>
                ))}
              </div>
              <button type="button" onClick={() => setPage('report')} className="mt-auto self-end rounded-md bg-foreground/5 px-3 py-1.5 text-sm hover:bg-foreground/10">
                {t('fix.see')}
              </button>
            </>
          ) : (
            <Empty text={t('fix.runReport')} />
          )}
        </DashboardCard>
      </div>
    </Page>
  )
}

function DailyPanel() {
  const p = usePeriodData()!
  const { t, lang } = useCoach()
  const rows = p.daily.map((d) => ({ name: dateShort(d.date, lang), connected: d.connected, spoke: d.spoke, engaged: d.engaged }))
  const keys = ['connected', 'spoke', 'engaged'] as const
  const names = { connected: t('daily.pickedUp'), spoke: t('daily.spoke'), engaged: t('daily.engaged') }
  const colors = { connected: BLUE, spoke: WARN, engaged: GOOD }
  return (
    <DashboardCard className="flex min-h-96 flex-col gap-4 lg:col-span-2">
      <DetailHeader title={t('daily.title')} subtitle={t('daily.sub')}>
        {keys.map((k) => (
          <span key={k} className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <ChartMarker color={colors[k]} /> {names[k]}
          </span>
        ))}
      </DetailHeader>
      {rows.length < 2 ? (
        <Empty text={t('daily.need2')} />
      ) : (
        <div className="dashboard-dot-grid min-h-72 flex-1 rounded-md" dir="ltr">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={lang === 'he' ? [...rows].reverse() : rows} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
              <defs>
                {keys.map((k) => (
                  <linearGradient key={k} id={`fill-${k}`} x1="0" x2="0" y1="0" y2="1">
                    <stop offset="0%" stopColor={colors[k]} stopOpacity="0.32" />
                    <stop offset="100%" stopColor={colors[k]} stopOpacity="0" />
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid vertical={false} stroke="var(--border)" />
              <XAxis dataKey="name" axisLine={false} tickLine={false} tick={AXIS_TICK} />
              <YAxis axisLine={false} tickLine={false} tick={AXIS_TICK} orientation={lang === 'he' ? 'right' : 'left'} />
              <Tooltip content={<DashboardChartTooltip names={names} colors={colors} />} />
              {keys.map((k) => (
                <Area key={k} type="monotone" dataKey={k} stroke={colors[k]} strokeWidth={2} fill={`url(#fill-${k})`} />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </DashboardCard>
  )
}

function WhereCallsEnd({ funnel }: { funnel: Funnel }) {
  const { t } = useCoach()
  const total = STAGE_META.reduce((s, m) => s + funnel.stages[m.key], 0)
  const rows = STAGE_META.map((m) => ({ name: t(`stage.${m.key}` as Key), value: funnel.stages[m.key], fill: m.color }))
  return (
    <DashboardCard className="flex flex-col gap-6">
      <DetailHeader title={t('where.title')}>
        <DetailTag color={BAD}>{t('where.silentTag', { p: pct(funnel.no_reply_rate, 0) })}</DetailTag>
      </DetailHeader>
      <div className="relative mx-auto aspect-square w-full max-w-52">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={rows} dataKey="value" cornerRadius={4} innerRadius="68%" outerRadius="88%" paddingAngle={4} startAngle={90} endAngle={-270} stroke="none" />
            <Tooltip cursor={false} content={<DashboardChartTooltip valueFormatter={(v) => num(Number(v))} />} />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <div className="text-3xl font-semibold leading-none">{num(total)}</div>
          <div className="mt-2 text-sm text-muted-foreground">{t('where.finished')}</div>
        </div>
      </div>
      <div className="space-y-3">
        {STAGE_META.map((m) => (
          <div key={m.key} className="flex items-center justify-between gap-4 text-sm">
            <div className="flex min-w-0 items-center gap-2.5">
              <ChartMarker color={m.color} />
              <span className="truncate text-foreground/80">{t(`stage.${m.key}` as Key)}</span>
            </div>
            <div className="flex shrink-0 items-center gap-3">
              <span className="font-medium">{num(funnel.stages[m.key])}</span>
              <span className="w-12 text-end text-muted-foreground">{pct(ratio(funnel.stages[m.key], total), 0)}</span>
            </div>
          </div>
        ))}
      </div>
    </DashboardCard>
  )
}

function AgentsTable({ className }: { className?: string }) {
  const p = usePeriodData()!
  const { data, t } = useCoach()
  const agents = data!.agents.filter((a) => p.funnel[a.key])
  const cols: Key[] = ['col.agent', 'col.calls', 'col.silent', 'col.conversation', 'col.bookings', 'col.junk', 'col.answerTime', 'col.slowCalls']
  return (
    <DashboardCard className={cn('flex flex-col gap-4 overflow-x-auto', className)}>
      <DetailHeader title={t('agents.title')} subtitle={t('agents.sub')} />
      <table className="w-full min-w-[640px] text-sm">
        <thead>
          <tr className="text-foreground/70">{cols.map((c) => <Th key={c}>{t(c)}</Th>)}</tr>
        </thead>
        <tbody>
          {agents.map((a) => {
            const f = p.funnel[a.key]
            const tech = p.technical.by_agent[a.key]
            const all = p.funnel.all
            const worseSilent = (f.no_reply_rate ?? 0) > (all.no_reply_rate ?? 0) + 0.02
            const worseEngaged = (f.engaged_rate ?? 1) < (all.engaged_rate ?? 0) - 0.02
            const slow = tech && (tech.slow_call_rate ?? 0) > (p.technical.by_outcome.failure?.slow_call_rate ?? 1) + 0.1
            return (
              <tr key={a.key} className="border-b border-border last:border-b-0">
                <td className="px-3 py-3">
                  <div className="font-medium">{a.label}</div>
                  <div className="text-xs text-muted-foreground">{a.key}</div>
                </td>
                <td className="px-3 py-3">{num(f.total)}</td>
                <td className="px-3 py-3" style={{ color: worseSilent ? BAD : undefined }}>{pct(f.no_reply_rate)}</td>
                <td className="px-3 py-3" style={{ color: worseEngaged ? BAD : undefined }}>{pct(f.engaged_rate)}</td>
                <td className="px-3 py-3">{num(f.booking_tool_calls)}</td>
                <td className="px-3 py-3" style={{ color: f.booked_disqualified ? BAD : undefined }}>{num(f.booked_disqualified)}</td>
                <td className="px-3 py-3" dir="ltr">{secs(tech?.median_response_secs)}</td>
                <td className="px-3 py-3" style={{ color: slow ? BAD : undefined }}>{pct(tech?.slow_call_rate, 0)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </DashboardCard>
  )
}

// =============================================================================== What works
function BehaviourRow({ c }: { c: Comparison }) {
  const { t, lang, data } = useCoach()
  const name = BEHAVIOUR[lang][c.criterion_id] ?? data?.checklist.find((q) => q.id === c.criterion_id)?.question ?? c.criterion_id
  const gap = c.diff_pp
  const groups: [string, Comparison['success_group'], string][] = [
    [t('beh.booked'), c.success_group, GOOD],
    [t('beh.failed'), c.failure_group, BAD],
  ]
  return (
    <div className="grid gap-3 border-b border-border py-4 last:border-b-0 md:grid-cols-[1.4fr_1fr_1fr_auto] md:items-center">
      <div className="min-w-0">
        <div className="font-medium leading-snug">{name}</div>
        {c.small_sample ? <div className="mt-1 text-xs text-muted-foreground">{t('beh.small')}</div> : null}
      </div>
      {groups.map(([label, grp, color]) => (
        <div key={label} className="flex flex-col gap-1.5">
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>{label}</span>
            <span className="text-foreground">
              {pct(grp.rate, 0)} <span className="text-muted-foreground" dir="ltr">({grp.yes}/{grp.applicable})</span>
            </span>
          </div>
          <div className="h-2.5 w-full overflow-hidden rounded-full bg-foreground/5">
            <div className="h-full rounded-full" style={{ width: `${(grp.rate ?? 0) * 100}%`, backgroundColor: color }} />
          </div>
        </div>
      ))}
      <div className="w-20 text-end text-sm font-semibold" style={{ color: gap == null ? undefined : gap > 0 ? GOOD : BAD }}>
        <span dir="ltr">{gap == null ? '—' : t('beh.pts', { n: `${gap > 0 ? '+' : ''}${gap.toFixed(0)}` })}</span>
      </div>
    </div>
  )
}

function TechTable({ rows }: { rows: [string, Tech | undefined][] }) {
  const { t } = useCoach()
  const cols = ['', t('col.calls'), t('tech.typical'), t('tech.slow'), t('tech.talkedOver')]
  return (
    <table className="w-full min-w-[560px] text-sm">
      <thead>
        <tr className="text-foreground/70">{cols.map((c, i) => <Th key={i}>{c}</Th>)}</tr>
      </thead>
      <tbody>
        {rows.map(([name, tech]) =>
          tech && tech.calls ? (
            <tr key={name} className="border-b border-border last:border-b-0">
              <td className="px-3 py-3 font-medium">{name}</td>
              <td className="px-3 py-3">{num(tech.calls)}</td>
              <td className="px-3 py-3" dir="ltr">{secs(tech.median_response_secs)}</td>
              <td className="px-3 py-3">{pct(tech.slow_call_rate, 0)}</td>
              <td className="px-3 py-3">{pct(tech.interruption_call_rate, 0)}</td>
            </tr>
          ) : null,
        )}
      </tbody>
    </table>
  )
}

export function AnalyticsPage() {
  const p = usePeriodData()
  const { data, t, lang } = useCoach()
  if (!p || !data) return <Page><Empty text={t('state.noCalls')} /></Page>
  const comps = [...(p.comparisons.all ?? [])].sort((a, b) => Math.abs(b.diff_pp ?? 0) - Math.abs(a.diff_pp ?? 0))
  const junk = Object.entries(p.disqualification_reasons).map(([k, v]) => ({ name: junkReason(lang, k), value: v }))
  return (
    <Page>
      <DashboardCard className="flex flex-col gap-2">
        <DetailHeader title={t('beh.title')} subtitle={t('beh.sub', { n: num(p.coverage.with_checklist), g: p.grader })} />
        {comps.length ? comps.map((c) => <BehaviourRow key={c.criterion_id} c={c} />) : <Empty text={t('beh.none')} />}
      </DashboardCard>

      <DashboardCard className="flex flex-col gap-4 overflow-x-auto">
        <DetailHeader title={t('tech.title')} subtitle={t('tech.sub', { s: p.technical.slow_threshold_secs })} />
        <TechTable
          rows={[
            [t('tech.booked'), p.technical.by_outcome.success],
            [t('tech.failed'), p.technical.by_outcome.failure],
            [t('tech.early'), p.technical.by_stage.early_drop],
            [t('tech.engaged'), p.technical.by_stage.engaged],
            ...data.agents.map((a) => [t('tech.agent', { name: a.label }), p.technical.by_agent[a.key]] as [string, Tech | undefined]),
          ]}
        />
      </DashboardCard>

      <DashboardCard className="flex flex-col gap-4">
        <DetailHeader title={t('junk.title')} subtitle={t('junk.sub')} />
        {junk.length ? (
          <div className="h-48" dir="ltr">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={junk} layout="vertical" margin={{ left: 8, right: 16 }}>
                <XAxis type="number" allowDecimals={false} axisLine={false} tickLine={false} tick={AXIS_TICK} reversed={lang === 'he'} />
                <YAxis type="category" dataKey="name" width={240} axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: 'var(--foreground)' }} orientation={lang === 'he' ? 'right' : 'left'} />
                <Tooltip cursor={false} content={<DashboardChartTooltip names={{ value: t('junk.series') }} colors={{ value: BAD }} />} />
                <Bar dataKey="value" fill={BAD} radius={4} barSize={18} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <Empty text={t('junk.none')} />
        )}
      </DashboardCard>
    </Page>
  )
}

// =============================================================================== Agents
export function AgentsPage() {
  const p = usePeriodData()
  const { data, t } = useCoach()
  if (!p || !data) return <Page><Empty text={t('state.noCalls')} /></Page>
  return (
    <Page>
      <AgentsTable />
      <div className="grid min-w-0 gap-3 md:grid-cols-2">
        {data.agents
          .filter((a) => p.funnel[a.key])
          .map((a) => {
            const f = p.funnel[a.key]
            return (
              <DashboardCard key={a.key} className="flex flex-col gap-4">
                <DetailHeader title={a.label} subtitle={t('agents.callsPicked', { n: num(f.total), m: num(f.human_connected) })} />
                <div className="flex h-3 w-full overflow-hidden rounded-full">
                  {STAGE_META.map((m) => (
                    <div key={m.key} title={`${t(`stage.${m.key}` as Key)}: ${f.stages[m.key]}`} style={{ width: `${(ratio(f.stages[m.key], f.total) ?? 0) * 100}%`, backgroundColor: m.color }} />
                  ))}
                </div>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  {STAGE_META.map((m) => (
                    <div key={m.key} className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-2 text-muted-foreground"><ChartMarker color={m.color} />{t(`stage.${m.key}` as Key)}</span>
                      <span className="font-medium">{num(f.stages[m.key])}</span>
                    </div>
                  ))}
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-muted-foreground">{t('agents.goodBookings')}</span>
                    <span className="font-medium" style={{ color: GOOD }}>{num(f.booked_qualified)}</span>
                  </div>
                </div>
              </DashboardCard>
            )
          })}
      </div>
    </Page>
  )
}

// =============================================================================== Fix next (report)
export function ReportPage() {
  const { data, t, lang, selectedAgents, done, markDone, undoDone } = useCoach()
  const r = data?.report
  if (!r) return <Page><Empty text={t('rep.none')} /></Page>
  const pe = r.proposed_edit
  const thisDone = findDone(done, pe.target, pe.new_text)
  return (
    <Page>
      {selectedAgents ? <div className="rounded-lg bg-foreground/5 px-3 py-2 text-xs text-muted-foreground">{t('rep.allAgentsNote')}</div> : null}
      <DashboardCard className="flex flex-col gap-3">
        <DetailHeader title={t('rep.headline')} subtitle={`${t('rep.from', { d: dateTime(r.created_at, lang) })}${r.model ? ` · ${r.model}` : ''}`} />
        <p className="text-base leading-relaxed">{pick(lang, r.headline, r.headline_he)}</p>
      </DashboardCard>

      <div className="grid min-w-0 gap-3 lg:grid-cols-3">
        {r.problems.map((pr, i) => (
          <DashboardCard key={pr.title} className="flex flex-col gap-3">
            <div className="flex items-start gap-3">
              <div className="dashboard-icon-badge flex size-7 shrink-0 items-center justify-center rounded-lg text-sm font-semibold text-white">{i + 1}</div>
              <h3 className="font-medium leading-snug">{pick(lang, pr.title, pr.title_he)}</h3>
            </div>
            <p className="text-sm leading-relaxed text-muted-foreground">{pick(lang, pr.explanation, pr.explanation_he)}</p>
            {pr.evidence.map((e) => (
              <blockquote key={`${e.conversation_id}-${e.turn}`} className="rounded-lg bg-foreground/5 p-3 text-sm">
                <span dir="rtl" className="block font-medium">“{e.quote}”</span>
                <div className="mt-2 text-xs text-muted-foreground">
                  {pick(lang, e.why, e.why_he)} · <code className="text-[11px]" dir="ltr">{e.conversation_id}</code> {t('rep.turn')} {e.turn}
                  {e.secs != null ? ` @ ${e.secs}s` : ''}
                </div>
              </blockquote>
            ))}
          </DashboardCard>
        ))}
      </div>

      <DashboardCard className="flex flex-col gap-4">
        <DetailHeader title={t('rep.oneChange')} subtitle={t('rep.target', { t: t(`rep.target.${pe.target}` as Key) })}>
          {!pe.located_in_live_prompt && pe.target !== 'none' && !thisDone ? <DetailTag color={WARN}>{t('rep.notFound')}</DetailTag> : null}
          {pe.target !== 'none' ? (
            thisDone ? (
              <span className="flex items-center gap-2">
                <DetailTag color={GOOD}>{t('done.marked', { d: dateTime(thisDone.marked_at, lang) })}</DetailTag>
                <button type="button" onClick={() => undoDone(thisDone.id)} className="text-xs text-muted-foreground underline hover:text-foreground">
                  {t('done.undo')}
                </button>
              </span>
            ) : (
              <button
                type="button"
                title={t('done.hint')}
                onClick={() =>
                  markDone({ report_created_at: r.created_at, target: pe.target, current_text: pe.current_text, new_text: pe.new_text, why: pe.why })
                }
                className="rounded-md bg-primary px-3.5 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90"
              >
                ✓ {t('done.mark')}
              </button>
            )
          ) : null}
        </DetailHeader>
        {pe.target !== 'none' ? (
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">{t('rep.now')}</div>
              <pre dir="rtl" className="whitespace-pre-wrap rounded-lg border p-3 font-sans text-sm leading-relaxed" style={{ borderColor: `color-mix(in oklab, ${BAD} 35%, transparent)` }}>{stripTags(pe.current_text)}</pre>
            </div>
            <div>
              <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">{t('rep.changeTo')}</div>
              <pre dir="rtl" className="whitespace-pre-wrap rounded-lg border p-3 font-sans text-sm leading-relaxed" style={{ borderColor: `color-mix(in oklab, ${GOOD} 45%, transparent)` }}>{stripTags(pe.new_text)}</pre>
            </div>
          </div>
        ) : null}
        <div className="grid gap-4 text-sm md:grid-cols-2">
          <div><div className="mb-1 font-medium">{t('rep.why')}</div><p className="leading-relaxed text-muted-foreground">{pick(lang, pe.why, pe.why_he)}</p></div>
          <div><div className="mb-1 font-medium">{t('rep.howToTest')}</div><p className="leading-relaxed text-muted-foreground">{pick(lang, pe.how_to_test, pe.how_to_test_he)}</p></div>
        </div>
        <div className="rounded-lg bg-foreground/5 p-3 text-xs text-muted-foreground">{t('rep.apply')}</div>
      </DashboardCard>

      <DashboardCard className="flex flex-col gap-3">
        <DetailHeader title={t('done.listTitle')} subtitle={t('done.listSub')} />
        {done.length ? (
          [...done].reverse().map((d) => (
            <div key={d.id} className="grid gap-2 border-b border-border pb-3 last:border-b-0 md:grid-cols-[150px_1fr_auto] md:items-center">
              <div className="text-sm">
                <div className="font-medium" style={{ color: GOOD }}>{t('done.tag')}</div>
                <div className="text-xs text-muted-foreground">{dateTime(d.marked_at, lang)} · {t(`rep.target.${d.target}` as Key)}</div>
              </div>
              <div dir="rtl" className="text-sm leading-relaxed">{stripTags(d.new_text)}</div>
              <button type="button" onClick={() => undoDone(d.id)} className="justify-self-end text-xs text-muted-foreground underline hover:text-foreground">
                {t('done.undo')}
              </button>
            </div>
          ))
        ) : (
          <Empty text={t('done.none')} />
        )}
      </DashboardCard>

      <WhoMadeThis />
    </Page>
  )
}

function WhoMadeThis() {
  const { data, t } = useCoach()
  const p = usePeriodData()
  const r = data?.report
  if (!data || !r) return null
  const days = data.costs?.days ?? []
  const graded = days.reduce((s, d) => s + d.graded_calls, 0)
  const gradingCost = days.reduce((s, d) => s + d.grading_cost, 0)
  const graderModel = days.flatMap((d) => d.models).find((m) => m.startsWith('typesafe/')) ?? data.grader
  const u = r.usage
  const rows: { step: string; by: string; what: string; cost: string; color: string }[] = [
    {
      step: t('who.grading'),
      by: graderModel,
      what: t('who.gradingWhat', { n: num(p?.coverage.with_checklist ?? graded) }),
      cost: graded ? t('who.gradingCost', { per: usd(gradingCost / graded) }) : '—',
      color: 'var(--chart-1)',
    },
    { step: t('who.numbers'), by: t('who.numbersBy'), what: t('who.numbersWhat'), cost: t('who.numbersCost'), color: 'var(--muted-foreground)' },
    {
      step: t('who.writing'),
      by: r.model ?? '—',
      what: t('who.writingWhat'),
      cost: u?.cost_usd != null ? t('who.writingCost', { cost: usd(u.cost_usd), tin: num(u.input_tokens ?? 0), tout: num(u.output_tokens ?? 0) }) : '—',
      color: 'var(--chart-2)',
    },
  ]
  return (
    <DashboardCard className="flex flex-col gap-4 overflow-x-auto">
      <DetailHeader title={t('who.title')} />
      <div className="flex flex-col">
        {rows.map((row) => (
          <div key={row.step} className="grid gap-2 border-b border-border py-3 last:border-b-0 md:grid-cols-[110px_260px_1fr_auto] md:items-center">
            <div className="flex items-center gap-2 text-sm font-medium"><ChartMarker color={row.color} />{row.step}</div>
            <div className="text-sm font-semibold" dir="auto">{row.by}</div>
            <div className="text-sm leading-snug text-muted-foreground">{row.what}</div>
            <div className="text-sm md:text-end" dir="ltr">{row.cost}</div>
          </div>
        ))}
      </div>
    </DashboardCard>
  )
}

// =============================================================================== Opener tests
export function OpenersPage() {
  const p = usePeriodData()
  const { data, t, lang, selectedAgents } = useCoach()
  if (!p || !data) return <Page><Empty text={t('state.noCalls')} /></Page>
  const byAgent = data.agents
    .map((a) => ({ agent: a, versions: p.versions.filter((v) => v.agent === a.key && v.connected >= 20) }))
    .filter((x) => x.versions.length)
  const cols: Key[] = ['col.version', 'col.firstCall', 'col.pickedUp', 'col.silent', 'col.conversation', 'col.bookingRate']
  return (
    <Page>
      <DashboardCard className="flex flex-col gap-4">
        <DetailHeader title={t('op.changes')} subtitle={t('op.changesSub')} />
        {data.experiments.filter((e) => !selectedAgents || selectedAgents.includes(e.agent_key)).length ? (
          data.experiments.filter((e) => !selectedAgents || selectedAgents.includes(e.agent_key)).map((e) => (
            <div key={e.id} className="grid gap-3 border-b border-border pb-4 last:border-b-0 md:grid-cols-[180px_1fr_1fr]">
              <div className="text-sm">
                <div className="font-medium">{data.agents.find((a) => a.key === e.agent_key)?.label ?? e.agent_key}</div>
                <div className="text-xs text-muted-foreground">{dateTime(e.started_at, lang)}</div>
                <div className="mt-1">
                  <DetailTag color={e.stopped_at && e.variant_pct < 100 ? 'var(--muted-foreground)' : GOOD}>
                    {e.variant_pct >= 100 ? t('op.set100') : e.stopped_at ? t('op.testClosed') : t('op.testing', { p: e.variant_pct })}
                  </DetailTag>
                </div>
              </div>
              <div dir="rtl" className="rounded-lg bg-foreground/5 p-3 text-xs leading-relaxed text-muted-foreground line-through decoration-foreground/30">
                {stripTags(e.change.old.split('\n').slice(-1)[0])}
              </div>
              <div dir="rtl" className="rounded-lg border p-3 text-xs leading-relaxed" style={{ borderColor: `color-mix(in oklab, ${GOOD} 45%, transparent)` }}>
                {stripTags(e.change.new.split('\n').slice(-1)[0])}
              </div>
            </div>
          ))
        ) : (
          <Empty text={t('op.noChanges')} />
        )}
      </DashboardCard>

      <DashboardCard className="flex flex-col gap-4 overflow-x-auto">
        <DetailHeader title={t('op.versions')} subtitle={t('op.versionsSub')} />
        {byAgent.length ? (
          byAgent.map(({ agent, versions }) => (
            <div key={agent.key} className="flex flex-col gap-2">
              <div className="text-sm font-medium">{agent.label}</div>
              <table className="w-full min-w-[620px] text-sm">
                <thead>
                  <tr className="text-foreground/70">{cols.map((c) => <Th key={c}>{t(c)}</Th>)}</tr>
                </thead>
                <tbody>
                  {versions.map((v, i) => {
                    const prev = versions[i - 1]
                    const better = (a: number | null, b: number | null | undefined, lowerIsBetter = false) =>
                      a == null || b == null ? undefined : (lowerIsBetter ? a < b : a > b) ? GOOD : BAD
                    return (
                      <tr key={v.version_id} className="border-b border-border last:border-b-0">
                        <td className="px-3 py-2.5">
                          <code className="text-xs" dir="ltr">…{v.version_id.slice(-6)}</code>
                          {i === versions.length - 1 ? <span className="ms-2 text-xs" style={{ color: BLUE }}>{t('op.current')}</span> : null}
                        </td>
                        <td className="px-3 py-2.5">{dateTime(v.first_call, lang)}</td>
                        <td className="px-3 py-2.5">{num(v.connected)}</td>
                        <td className="px-3 py-2.5" style={{ color: prev ? better(v.no_reply_rate, prev.no_reply_rate, true) : undefined }}>{pct(v.no_reply_rate)}</td>
                        <td className="px-3 py-2.5" style={{ color: prev ? better(v.engaged_rate, prev.engaged_rate) : undefined }}>{pct(v.engaged_rate)}</td>
                        <td className="px-3 py-2.5" style={{ color: prev ? better(v.booking_rate, prev.booking_rate) : undefined }}>{pct(v.booking_rate, 2)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          ))
        ) : (
          <Empty text={t('op.notEnough')} />
        )}
        <div className="text-xs text-muted-foreground">{t('op.caveat')}</div>
      </DashboardCard>
    </Page>
  )
}
