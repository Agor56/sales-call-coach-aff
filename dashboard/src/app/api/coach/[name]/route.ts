import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { connection } from 'next/server'

// Data files written by `./coach export` (daily at 7:00 by the scheduler). Read from disk on every request,
// so the dashboard shows new numbers without a rebuild or restart. Not in /public: only served through here.
// COACH_DATA_DIR is set by the launchd job: the standalone server runs from .next/standalone, not from dashboard/.
const DATA_DIR = process.env.COACH_DATA_DIR ?? path.join(process.cwd(), 'data')

export async function GET(_req: Request, ctx: RouteContext<'/api/coach/[name]'>) {
  await connection() // always at request time, never prerendered
  const { name } = await ctx.params
  if (!/^[a-z0-9_-]{1,40}$/.test(name)) {
    return Response.json({ error: 'bad name' }, { status: 400 })
  }
  try {
    const body = await readFile(path.join(DATA_DIR, `${name}.json`), 'utf8')
    return new Response(body, { headers: { 'content-type': 'application/json', 'cache-control': 'no-store' } })
  } catch {
    return Response.json({ error: 'not found' }, { status: 404 })
  }
}
