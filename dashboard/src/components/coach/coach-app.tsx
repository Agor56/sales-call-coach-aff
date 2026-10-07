'use client'

import type { CSSProperties, ComponentType, SVGProps } from 'react'
import {
  DashboardMoneyBagIcon,
  FileIcon,
  SidebarAiIcon,
  SidebarAnalyticsIcon,
  SidebarHomeMutedIcon,
  TopbarCalendarIcon,
  TopbarChevronIcon,
  UsersIcon,
} from '@/components/watermelon/capitalio-dashboard/components/capitalio/icons'
import { CapitalioLogo } from '@/components/watermelon/capitalio-dashboard/components/capitalio/logo'
import '@/components/watermelon/capitalio-dashboard/dashboard.css'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from '@/components/ui/sidebar'
import { cn } from '@/lib/utils'
import type { Key } from './i18n'
import { CoachContext, PERIODS, dateTime, useCoach, useCoachState, type PageKey } from './lib'
import { CostsPage } from './costs-page'
import { AgentsPage, AnalyticsPage, OpenersPage, OverviewPage, ReportPage } from './pages'

type IconType = ComponentType<SVGProps<SVGSVGElement> & { className?: string }>

const NAV: { section: Key; items: { key: PageKey; label: Key; icon: IconType }[] }[] = [
  {
    section: 'nav.optimize',
    items: [
      { key: 'overview', label: 'nav.overview', icon: SidebarHomeMutedIcon as IconType },
      { key: 'analytics', label: 'nav.analytics', icon: SidebarAnalyticsIcon as IconType },
      { key: 'agents', label: 'nav.agents', icon: UsersIcon as IconType },
    ],
  },
  {
    section: 'nav.change',
    items: [
      { key: 'report', label: 'nav.report', icon: FileIcon as IconType },
      { key: 'openers', label: 'nav.openers', icon: SidebarAiIcon as IconType },
    ],
  },
  {
    section: 'nav.account',
    items: [{ key: 'costs', label: 'nav.costs', icon: DashboardMoneyBagIcon as IconType }],
  },
]

const TITLES: Record<PageKey, Key> = {
  overview: 'nav.overview',
  analytics: 'nav.analytics',
  agents: 'nav.agents',
  report: 'nav.report',
  openers: 'nav.openers',
  costs: 'nav.costs',
}

function NavButton({ item }: { item: (typeof NAV)[number]['items'][number] }) {
  const { page, setPage, t } = useCoach()
  const { isMobile, setOpenMobile } = useSidebar()
  const active = page === item.key
  const Icon = item.icon
  return (
    <SidebarMenuItem>
      <SidebarMenuButton
        tooltip={t(item.label)}
        isActive={active}
        onClick={() => {
          setPage(item.key)
          if (isMobile) setOpenMobile(false)
        }}
        className={cn(
          'h-auto gap-2 rounded-lg px-3 py-3 text-base leading-5 text-muted-foreground transition-colors',
          'bg-transparent hover:bg-transparent active:bg-transparent data-active:bg-transparent!',
          'data-active:text-sidebar-accent-foreground [&_svg]:size-5!',
          active && 'active-sidebar-item',
        )}
      >
        <Icon />
        <span className="truncate">{t(item.label)}</span>
      </SidebarMenuButton>
    </SidebarMenuItem>
  )
}

function ClientSwitcher() {
  const { accounts, accountLabels, account, setAccount, data, t } = useCoach()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <button
            type="button"
            className="flex w-full min-w-0 items-center gap-3 rounded-lg p-1 text-start transition-colors hover:bg-[var(--card-hover)]"
          />
        }
      >
        <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-foreground/5 text-sm font-semibold uppercase">
          {(account ?? '?').slice(0, 2)}
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <span className="truncate text-base leading-none font-medium text-sidebar-accent-foreground">
            {data?.account_label ?? account ?? t('client.none')}
          </span>
          <span className="truncate text-xs text-muted-foreground">{t('client.switch')}</span>
        </div>
        <TopbarChevronIcon className="h-auto w-3 shrink-0 rotate-180 text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="top">
        {accounts.map((a) => (
          <DropdownMenuItem key={a} onClick={() => setAccount(a)}>
            {accountLabels[a] ?? a}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function CoachSidebar() {
  const { t, lang } = useCoach()
  return (
    <Sidebar side={lang === 'he' ? 'right' : 'left'} className="border-none! p-0.5 capitalio-dashboard">
      <SidebarHeader className="mb-6 flex-row items-start gap-2 px-4 pt-6 pb-0">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-lg border border-primary bg-linear-to-t from-[color-mix(in_oklab,var(--primary)_80%,black)] to-primary">
            <CapitalioLogo className="size-6 shrink-0 text-white" />
          </div>
          <span className="truncate text-lg font-medium text-sidebar-accent-foreground">Sales Call Coach</span>
        </div>
      </SidebarHeader>
      <SidebarContent className="gap-3 px-4 pb-4">
        {NAV.map((s) => (
          <SidebarGroup key={s.section} className="gap-2 p-0">
            <SidebarGroupLabel className="px-2 py-3 font-section text-base font-normal tracking-tight text-muted-foreground/70">
              {t(s.section)}
            </SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu className="gap-2">
                {s.items.map((it) => (
                  <NavButton key={it.key} item={it} />
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>
      <SidebarFooter className="gap-3 px-4 pt-0 pb-5">
        <div className="border-t" />
        <ClientSwitcher />
      </SidebarFooter>
    </Sidebar>
  )
}

function IconToggle({ onClick, label, children }: { onClick: () => void; label: string; children: React.ReactNode }) {
  return (
    <Button type="button" variant="outline" size="sm" onClick={onClick} aria-label={label} title={label} className="h-9 bg-card px-3 font-normal text-muted-foreground hover:text-foreground">
      {children}
    </Button>
  )
}

function AgentPicker() {
  const { data, t, selectedAgents, setSelectedAgents } = useCoach()
  if (!data) return null
  const all = data.agents.map((a) => a.key)
  const chosen = selectedAgents ?? all
  const label =
    !selectedAgents ? t('pick.all') : chosen.length === 1 ? data.agents.find((a) => a.key === chosen[0])?.label ?? chosen[0] : t('pick.n', { n: chosen.length })
  const toggle = (key: string) => {
    const next = chosen.includes(key) ? chosen.filter((k) => k !== key) : [...chosen, key]
    if (next.length) setSelectedAgents(next.length === all.length ? null : next) // never empty
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            type="button"
            variant="outline"
            className={`h-9 max-w-64 shrink-0 gap-2 bg-card px-3 font-normal hover:bg-card hover:text-foreground ${selectedAgents ? 'border-primary text-foreground' : 'text-muted-foreground'}`}
          />
        }
      >
        <UsersIcon className="size-4 shrink-0" />
        <span className="truncate">{label}</span>
        <TopbarChevronIcon className="h-auto w-3 shrink-0 rotate-180" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-60">
        <DropdownMenuGroup>
          <DropdownMenuLabel>{t('pick.title')}</DropdownMenuLabel>
          <DropdownMenuCheckboxItem checked={!selectedAgents} onCheckedChange={() => setSelectedAgents(null)}>
            {t('pick.all')}
          </DropdownMenuCheckboxItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          {data.agents.map((a) => (
            <DropdownMenuCheckboxItem key={a.key} checked={chosen.includes(a.key)} onCheckedChange={() => toggle(a.key)}>
              {a.label}
            </DropdownMenuCheckboxItem>
          ))}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function CoachTopbar() {
  const { page, period, setPeriod, data, t, lang, setLang, theme, setTheme } = useCoach()
  const available = PERIODS.filter((p) => data?.periods[p])
  return (
    <header className="flex shrink-0 flex-col gap-3 px-4 py-3 md:flex-row md:items-center md:justify-between md:px-8">
      <div className="flex min-w-0 items-center gap-3">
        <SidebarTrigger className="size-8.5 shrink-0 md:hidden [&_svg]:size-5!" />
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <h1 className="truncate font-section text-lg leading-none font-semibold text-foreground md:text-xl">
            {t(TITLES[page])}
          </h1>
          <div className="flex items-center gap-2 font-section text-xs text-muted-foreground md:text-sm">
            <span className="whitespace-nowrap">
              {t('top.dataFrom')} <span className="text-foreground">{data ? dateTime(data.generated_at, lang) : '—'}</span>
            </span>
            <span className="size-2 shrink-0 rounded-full bg-muted-foreground/50" />
            <span className="truncate">
              {t('top.grader')} <span className="text-foreground">{data?.grader ?? '—'}</span>
            </span>
          </div>
        </div>
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <AgentPicker />
        <IconToggle onClick={() => setLang(lang === 'he' ? 'en' : 'he')} label={t('top.otherLang')}>
          {t('top.otherLang')}
        </IconToggle>
        <IconToggle onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} label={theme === 'dark' ? t('top.light') : t('top.dark')}>
          <span aria-hidden className="text-base leading-none">{theme === 'dark' ? '☀' : '☾'}</span>
        </IconToggle>
        <div className="flex min-w-0 items-center">
          <div className="flex h-9 min-w-0 items-center gap-2 rounded-s-md border border-e-0 border-border bg-background px-3 text-sm text-muted-foreground shadow-xs">
            <TopbarCalendarIcon className="size-4 shrink-0" />
            <span className="truncate">{t('top.period')}</span>
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button
                  type="button"
                  variant="outline"
                  className="h-9 shrink-0 gap-2 rounded-s-none bg-card px-3 font-normal text-muted-foreground hover:bg-card hover:text-foreground"
                />
              }
            >
              <span>{t(`period.${period}` as Key)}</span>
              <TopbarChevronIcon className="h-auto w-3 rotate-180" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {(available.length ? available : PERIODS).map((p) => (
                <DropdownMenuItem key={p} onClick={() => setPeriod(p)}>
                  {t(`period.${p}` as Key)}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </header>
  )
}

function PageBody() {
  const { page, data, error, t } = useCoach()
  if (error) {
    return <div className="p-8 text-sm text-muted-foreground">{error.startsWith('state.') ? t(error as Key) : error}</div>
  }
  if (!data) {
    return <div className="p-8 text-sm text-muted-foreground">{t('state.loading')}</div>
  }
  if (page === 'analytics') return <AnalyticsPage />
  if (page === 'agents') return <AgentsPage />
  if (page === 'openers') return <OpenersPage />
  if (page === 'report') return <ReportPage />
  if (page === 'costs') return <CostsPage />
  return <OverviewPage />
}

export default function CoachApp() {
  const state = useCoachState()
  return (
    <CoachContext.Provider value={state}>
      <SidebarProvider
        defaultOpen
        className="capitalio-dashboard h-svh overflow-hidden no-scrollbar"
        style={{ '--sidebar-width': '15.625rem' } as CSSProperties}
      >
        <CoachSidebar />
        <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden p-0.5 ps-0">
          <div className="flex min-h-0 min-w-0 flex-1 flex-col bg-background shadow-custom">
            <CoachTopbar />
            <div className="min-h-0 flex-1 overflow-y-auto">
              <PageBody />
            </div>
          </div>
        </main>
      </SidebarProvider>
    </CoachContext.Provider>
  )
}
