'use client'

import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import {
  ChartMarker,
  DashboardCard,
  DashboardChartTooltip,
  DetailHeader,
} from '@/components/watermelon/capitalio-dashboard/components/capitalio/shared'
import { cn } from '@/lib/utils'
import type { Key } from './i18n'
import { dateShort, dateTime, num, useCoach, usd, type CostDay } from './lib'

type Range = 'today' | '7' | '30' | 'all'
const RANGES: Range[] = ['today', '7', '30', 'all']

const GRADING = 'var(--chart-1)'
const REPORTS = 'var(--chart-2)'

function isoDaysAgo(n: number) {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d.toLocaleDateString('en-CA') // YYYY-MM-DD in local time
}

function inRange(days: CostDay[], range: Range) {
  if (range === 'all') return days
  const from = range === 'today' ? isoDaysAgo(0) : isoDaysAgo(Number(range) - 1)
  return days.filter((d) => d.date >= from)
}

/** One bar per calendar day in the range, zero on days without spend, so gaps are visible. */
function fillDays(days: CostDay[], range: Range): { date: string; grading: number; reports: number }[] {
  const byDate = new Map(days.map((d) => [d.date, d]))
  const span = range === 'today' ? 1 : range === 'all' ? null : Number(range)
  if (span == null) return days.map((d) => ({ date: d.date, grading: d.grading_cost, reports: d.report_cost }))
  return Array.from({ length: span }, (_, i) => isoDaysAgo(span - 1 - i)).map((date) => ({
    date,
    grading: byDate.get(date)?.grading_cost ?? 0,
    reports: byDate.get(date)?.report_cost ?? 0,
  }))
}

function Stat({ title, value, hint, color }: { title: string; value: string; hint: string; color?: string }) {
  return (
    <DashboardCard className="flex min-h-36 flex-col justify-between gap-4">
      <h2 className="flex items-center gap-2 truncate text-base font-normal leading-6 text-muted-foreground">
        {color ? <ChartMarker color={color} /> : null}
        {title}
      </h2>
      <div className="text-3xl font-bold leading-none" dir="ltr">{value}</div>
      <div className="text-xs leading-snug text-muted-foreground">{hint}</div>
    </DashboardCard>
  )
}

export function CostsPage() {
  const { data, t, lang } = useCoach()
  const [range, setRange] = useState<Range>('30')
  const costs = data?.costs
  const all = costs?.days ?? []
  const days = inRange(all, range)
  const sum = (k: keyof CostDay) => days.reduce((s, d) => s + (d[k] as number), 0)
  const total = sum('total_cost')
  const graded = sum('graded_calls')
  const gradingCost = sum('grading_cost')
  const reports = sum('reports')
  const reportCost = sum('report_cost')
  const active = days.filter((d) => d.total_cost > 0).length
  const bars = fillDays(days, range).map((d) => ({ ...d, name: dateShort(d.date, lang) }))
  const ordered = [...days].reverse() // newest first in the table
  const or = costs?.openrouter

  return (
    <div className="flex min-w-0 flex-col gap-3 px-4 pb-8 md:px-8">
      <div className="flex flex-wrap items-center gap-2">
        {RANGES.map((r) => (
          <button
            key={r}
            type="button"
            onClick={() => setRange(r)}
            className={cn(
              'h-9 rounded-md border border-border px-3.5 text-sm transition-colors',
              range === r ? 'border-transparent bg-primary text-primary-foreground' : 'bg-card text-muted-foreground hover:text-foreground',
            )}
          >
            {t(`cost.range.${r}` as Key)}
          </button>
        ))}
      </div>

      <div className="grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat title={t('cost.total')} value={usd(total)} hint={t('cost.totalHint', { n: active, avg: usd(active ? total / active : 0) })} />
        <Stat
          title={t('cost.grading')}
          value={usd(gradingCost)}
          color={GRADING}
          hint={t('cost.gradingHint', { n: num(graded), per: usd(graded ? gradingCost / graded : 0) })}
        />
        <Stat
          title={t('cost.reports')}
          value={usd(reportCost)}
          color={REPORTS}
          hint={t('cost.reportsHint', { n: reports, tin: num(sum('report_tokens_in')), tout: num(sum('report_tokens_out')) })}
        />
        <Stat
          title={t('cost.balance')}
          value={or?.remaining != null ? `$${or.remaining.toFixed(2)}` : '—'}
          hint={or ? t('cost.balanceHint', { used: usd(or.used_total), d: dateTime(or.checked_at, lang) }) : t('cost.balanceNone')}
        />
      </div>

      <DashboardCard className="flex min-h-80 flex-col gap-4">
        <DetailHeader title={t('cost.chart')} subtitle={t('cost.chartSub')}>
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground"><ChartMarker color={GRADING} /> {t('cost.grading')}</span>
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground"><ChartMarker color={REPORTS} /> {t('cost.reports')}</span>
        </DetailHeader>
        {total > 0 ? (
          <div className="h-64 w-full" dir="ltr">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={lang === 'he' ? [...bars].reverse() : bars} margin={{ top: 8, right: 8, bottom: 0, left: 4 }}>
                <CartesianGrid vertical={false} stroke="var(--border)" />
                <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: 'var(--muted-foreground)' }} />
                <YAxis
                  axisLine={false}
                  tickLine={false}
                  width={56}
                  tick={{ fontSize: 12, fill: 'var(--muted-foreground)' }}
                  tickFormatter={(v: number) => usd(v)}
                  orientation={lang === 'he' ? 'right' : 'left'}
                />
                <Tooltip
                  cursor={{ fill: 'color-mix(in oklab, var(--foreground) 5%, transparent)' }}
                  content={
                    <DashboardChartTooltip
                      names={{ grading: t('cost.grading'), reports: t('cost.reports') }}
                      colors={{ grading: GRADING, reports: REPORTS }}
                      valueFormatter={(v) => usd(Number(v))}
                    />
                  }
                />
                <Bar dataKey="grading" stackId="c" fill={GRADING} radius={[0, 0, 0, 0]} maxBarSize={36} />
                <Bar dataKey="reports" stackId="c" fill={REPORTS} radius={[4, 4, 0, 0]} maxBarSize={36} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <div className="py-6 text-sm text-muted-foreground">{t('cost.none')}</div>
        )}
      </DashboardCard>

      <DashboardCard className="flex flex-col gap-4 overflow-x-auto">
        <DetailHeader title={t('cost.table')} />
        <table className="w-full min-w-[700px] text-sm">
          <thead>
            <tr className="text-foreground/70">
              {(['col.date', 'col.graded', 'col.gradingCost', 'col.reports', 'col.reportCost', 'col.total', 'col.models'] as Key[]).map((c) => (
                <th key={c} className="bg-foreground/5 px-3 py-2.5 text-start font-medium first:rounded-s-lg last:rounded-e-lg">{t(c)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ordered.length ? (
              ordered.map((d) => (
                <tr key={d.date} className="border-b border-border">
                  <td className="px-3 py-3 font-medium">{dateShort(d.date, lang)}</td>
                  <td className="px-3 py-3">{num(d.graded_calls)}</td>
                  <td className="px-3 py-3" dir="ltr">{usd(d.grading_cost)}</td>
                  <td className="px-3 py-3">{num(d.reports)}</td>
                  <td className="px-3 py-3" dir="ltr">{usd(d.report_cost)}</td>
                  <td className="px-3 py-3 font-semibold" dir="ltr">{usd(d.total_cost)}</td>
                  <td className="px-3 py-3 text-xs text-muted-foreground" dir="ltr">{d.models.join(', ')}</td>
                </tr>
              ))
            ) : (
              <tr><td colSpan={7} className="px-3 py-6 text-muted-foreground">{t('cost.none')}</td></tr>
            )}
          </tbody>
          {ordered.length ? (
            <tfoot>
              <tr className="font-semibold">
                <td className="px-3 py-3">{t('cost.sum')}</td>
                <td className="px-3 py-3">{num(graded)}</td>
                <td className="px-3 py-3" dir="ltr">{usd(gradingCost)}</td>
                <td className="px-3 py-3">{num(reports)}</td>
                <td className="px-3 py-3" dir="ltr">{usd(reportCost)}</td>
                <td className="px-3 py-3" dir="ltr">{usd(total)}</td>
                <td />
              </tr>
            </tfoot>
          ) : null}
        </table>
        <div className="text-xs text-muted-foreground">{t('cost.note')}</div>
      </DashboardCard>
    </div>
  )
}
