import { createHash } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { connection } from 'next/server'

// Suggested fixes you marked as done. Stored next to the dashboard data (data/done/<account>.json) so it survives
// restarts and the daily report writer can read it (it is told not to suggest these again).
const DIR = path.join(process.env.COACH_DATA_DIR ?? path.join(process.cwd(), 'data'), 'done')

type DoneItem = {
  id: string
  marked_at: number
  report_created_at: number | null
  target: string
  current_text: string
  new_text: string
  why: string
}

const valid = (account: string) => /^[a-z0-9_-]{1,40}$/.test(account)
const file = (account: string) => path.join(DIR, `${account}.json`)

async function load(account: string): Promise<DoneItem[]> {
  try {
    return JSON.parse(await readFile(file(account), 'utf8')) as DoneItem[]
  } catch {
    return []
  }
}

async function save(account: string, items: DoneItem[]) {
  await mkdir(DIR, { recursive: true })
  await writeFile(file(account), JSON.stringify(items, null, 2), 'utf8')
}

function doneId(target: string, newText: string) {
  return createHash('sha1').update(`${target}\n${newText.trim()}`).digest('hex').slice(0, 12)
}

export async function GET(_req: Request, ctx: RouteContext<'/api/coach/done/[account]'>) {
  await connection()
  const { account } = await ctx.params
  if (!valid(account)) return Response.json({ error: 'bad account' }, { status: 400 })
  return Response.json(await load(account), { headers: { 'cache-control': 'no-store' } })
}

export async function POST(req: Request, ctx: RouteContext<'/api/coach/done/[account]'>) {
  const { account } = await ctx.params
  if (!valid(account)) return Response.json({ error: 'bad account' }, { status: 400 })
  const body = (await req.json().catch(() => null)) as Partial<DoneItem> | null
  const clip = (v: unknown, n: number) => (typeof v === 'string' ? v.slice(0, n) : '')
  if (!body || typeof body.new_text !== 'string' || !body.new_text.trim()) {
    return Response.json({ error: 'new_text required' }, { status: 400 })
  }
  const target = clip(body.target, 40) || 'unknown'
  const item: DoneItem = {
    id: doneId(target, body.new_text),
    marked_at: Math.floor(Date.now() / 1000),
    report_created_at: typeof body.report_created_at === 'number' ? body.report_created_at : null,
    target,
    current_text: clip(body.current_text, 4000),
    new_text: clip(body.new_text, 4000),
    why: clip(body.why, 2000),
  }
  const items = (await load(account)).filter((x) => x.id !== item.id)
  items.push(item)
  await save(account, items)
  return Response.json(items)
}

export async function DELETE(req: Request, ctx: RouteContext<'/api/coach/done/[account]'>) {
  const { account } = await ctx.params
  if (!valid(account)) return Response.json({ error: 'bad account' }, { status: 400 })
  const id = new URL(req.url).searchParams.get('id') ?? ''
  const items = (await load(account)).filter((x) => x.id !== id)
  await save(account, items)
  return Response.json(items)
}
