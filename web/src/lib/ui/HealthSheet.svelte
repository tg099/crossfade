<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { api } from '#lib/api.ts';
	import type { Service } from '#lib/types.ts';
	import Glyph from './Glyph.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import { i18n, num, t } from './i18n.svelte.ts';
	import { ago, ui } from './ui.svelte.ts';

	// Admin only: how the instance and the services are doing. Counts only, nothing private.
	type Totals = { calls: number; errors: number; auth_errors: number; rate_limited: number; failed_items: number; avg_ms: number | null };
	type Svc = {
		service: Service;
		name: string;
		day: Totals;
		week: Totals;
		hours: { hour: number; calls: number; errors: number }[];
		accounts: { connected: number; failing: number; expiring: number; oldest_check: number | null };
		notes?: { search_blocked_until: number | null; web_token_expires: number | null };
	};
	type Health = {
		now: number;
		instance: {
			version: string;
			started_at: number;
			scheduler: boolean;
			workers: number;
			python: string;
			schema: number;
			database_bytes: number;
			disk_free_bytes: number;
			disk_total_bytes: number;
			backups: number;
			last_backup: { at: number; bytes: number } | null;
			counts: Record<string, number>;
		};
		services: Svc[];
		syncs: {
			day: Record<string, { n: number; avg_secs: number }>;
			running: number;
			queued: number;
			library_jobs: number;
			fades: { total: number; paused: number; failing: number; waiting: number };
			overdue: number;
		};
		errors: { ts: number; service: Service; op: string; kind: 'auth' | 'error'; message: string }[];
	};

	let h = $state<Health | null>(null);
	let timer: ReturnType<typeof setInterval> | null = null;
	async function load() {
		try {
			h = await api<Health>('/api/admin/health');
		} catch (e) {
			ui.fail(e);
		}
	}
	$effect(() => {
		if (!ui.healthOpen) return;
		load();
		timer = setInterval(load, 30_000);
		return () => {
			if (timer) clearInterval(timer);
		};
	});

	const bytes = (n: number) => {
		const u = ['B', 'KB', 'MB', 'GB', 'TB'];
		let i = 0;
		while (n >= 1024 && i < u.length - 1) (n /= 1024), i++;
		return `${n.toLocaleString(i18n.locale, { maximumFractionDigits: n < 10 && i ? 1 : 0 })} ${u[i]}`;
	};
	const pct = (part: number, all: number) =>
		all ? `${((part / all) * 100).toLocaleString(i18n.locale, { maximumFractionDigits: part / all < 0.1 ? 1 : 0 })} %` : '–';
	const hourLabel = (ts: number) => new Date(ts * 1000).toLocaleTimeString(i18n.locale, { hour: '2-digit', minute: '2-digit' });
	const dateTime = (ts: number) => new Date(ts * 1000).toLocaleString(i18n.locale, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

	// what needs a look, most important first
	const attention = $derived.by(() => {
		if (!h) return [] as string[];
		const out: string[] = [];
		for (const s of h.services) {
			if (s.accounts.failing) out.push(t('health.att.logins', { n: s.accounts.failing, service: s.name }));
			const fails = s.day.errors;
			if (s.day.calls >= 20 && fails / s.day.calls > 0.1) out.push(t('health.att.errors', { service: s.name, pct: pct(fails, s.day.calls) }));
			if (s.notes?.search_blocked_until) out.push(t('health.att.appleBlock', { time: hourLabel(s.notes.search_blocked_until) }));
		}
		if (h.syncs.fades.failing) out.push(t('health.att.fades', { n: h.syncs.fades.failing }));
		if (h.syncs.overdue) out.push(t('health.att.overdue', { n: h.syncs.overdue }));
		if (!h.instance.scheduler) out.push(t('health.att.scheduler'));
		if (h.instance.disk_free_bytes < 1024 ** 3) out.push(t('health.att.disk', { free: bytes(h.instance.disk_free_bytes) }));
		return out;
	});
	const runs = $derived(h ? Object.entries(h.syncs.day).reduce((a, [, v]) => a + v.n, 0) : 0);
	const runN = (k: string[]) => (h ? k.reduce((a, s) => a + (h!.syncs.day[s]?.n ?? 0), 0) : 0);
	const avgRun = $derived.by(() => {
		if (!h) return null;
		const all = Object.values(h.syncs.day);
		const n = all.reduce((a, v) => a + v.n, 0);
		return n ? all.reduce((a, v) => a + v.avg_secs * v.n, 0) / n : null;
	});
	const secs = (s: number | null) => (s == null ? '–' : s < 60 ? `${Math.round(s)} s` : `${Math.round(s / 60)} min`);
	const OPS: Record<string, string> = { read: 'health.op.read', add: 'health.op.add', remove: 'health.op.remove', search: 'health.op.search', whoami: 'health.op.whoami' };
</script>

<Sheet bind:open={ui.healthOpen} label={t('health.title')}>
	{#snippet header()}
		<SheetBar title={t('health.title')} leading={{ label: t('common.back'), icon: 'chevronLeft', run: () => (ui.healthOpen = false) }} />
	{/snippet}
	<div class="wrap">
		{#if h}
			<div class="status" class:good={!attention.length}>
				<span class="badge" aria-hidden="true"><Glyph name={attention.length ? 'alert' : 'check'} size={22} weight={2.6} /></span>
				<div>
					<p class="t-headline">{attention.length ? t('health.attention', { n: attention.length }) : t('health.allGood')}</p>
					<p class="t-foot t-2">{t('health.updated', { time: hourLabel(h.now) })}</p>
				</div>
			</div>
			{#if attention.length}
				<ul class="group att">
					{#each attention as a (a)}
						<li class="cell"><span class="warn" aria-hidden="true"><Glyph name="alert" size={17} weight={2.3} /></span><span class="grow">{a}</span></li>
					{/each}
				</ul>
			{/if}

			<div class="section">
				<div class="section-head"><span class="caps">{t('health.services')}</span><span class="t-foot t-2">{t('health.last24')}</span></div>
				{#each h.services as s (s.service)}
					{@const max = Math.max(1, ...s.hours.map((x) => x.calls))}
					<div class="group svc">
						<div class="svc-head">
							<ServiceMark service={s.service} size={28} />
							<span class="grow">
								<span class="b">{s.name}</span>
								<span class="b t-foot t-2">{t('health.accounts', { n: s.accounts.connected })}{s.accounts.failing ? ` · ${t('health.failingLogins', { n: s.accounts.failing })}` : ''}</span>
							</span>
						</div>
						<dl class="stats">
							<div><dt class="t-foot t-2">{t('health.calls')}</dt><dd>{num(s.day.calls)}</dd></div>
							<div><dt class="t-foot t-2">{t('health.errors')}</dt><dd class:bad={s.day.errors > 0}>{s.day.calls ? pct(s.day.errors, s.day.calls) : '–'}</dd></div>
							<div><dt class="t-foot t-2">{t('health.limits')}</dt><dd class:bad={s.day.rate_limited > 0}>{num(s.day.rate_limited)}</dd></div>
							<div><dt class="t-foot t-2">{t('health.avg')}</dt><dd>{s.day.avg_ms == null ? '–' : `${num(s.day.avg_ms)} ms`}</dd></div>
						</dl>
						<svg class="spark" viewBox="0 0 240 40" preserveAspectRatio="none" role="img" aria-label={t('health.sparkLabel', { service: s.name })}>
							{#each s.hours as x, i (x.hour)}
								{@const bh = x.calls ? Math.max(3, (x.calls / max) * 34) : 0}
								<g>
									<title>{hourLabel(x.hour)} · {t('health.tipCalls', { n: x.calls })}{x.errors ? ` · ${t('health.tipErrors', { n: x.errors })}` : ''}</title>
									<rect x={i * 10} y="0" width="10" height="40" fill="transparent" />
									{#if bh}<rect class="bar" x={i * 10 + 1} y={40 - bh} width="8" height={bh} rx="2" />{/if}
									{#if x.errors}<circle class="err" cx={i * 10 + 5} cy={Math.max(3, 40 - bh - 4)} r="2.5" />{/if}
								</g>
							{/each}
						</svg>
						<p class="axis t-foot t-2"><span>{hourLabel(s.hours[0].hour)}</span><span>{t('health.now')}</span></p>
						<p class="t-foot t-2 week">
							{t('health.week', { calls: num(s.week.calls), errors: pct(s.week.errors, s.week.calls) })}{s.week.auth_errors ? ` · ${t('health.authErrors', { n: s.week.auth_errors })}` : ''}{s.week.failed_items ? ` · ${t('health.failedItems', { n: s.week.failed_items })}` : ''}
						</p>
						{#if s.notes?.web_token_expires}<p class="t-foot t-2 week">{t('health.appleToken', { date: dateTime(s.notes.web_token_expires) })}</p>{/if}
					</div>
				{/each}
				<p class="section-foot">{t('health.servicesFoot')}</p>
			</div>

			<div class="section">
				<div class="section-head"><span class="caps">{t('health.syncs')}</span><span class="t-foot t-2">{t('health.last24')}</span></div>
				<dl class="group stats grid4">
					<div><dt class="t-foot t-2">{t('health.runs')}</dt><dd>{num(runs)}</dd></div>
					<div><dt class="t-foot t-2">{t('health.runsOk')}</dt><dd>{num(runN(['ok', 'partial']))}</dd></div>
					<div><dt class="t-foot t-2">{t('health.runsFailed')}</dt><dd class:bad={runN(['error', 'auth_error']) > 0}>{num(runN(['error', 'auth_error']))}</dd></div>
					<div><dt class="t-foot t-2">{t('health.avgRun')}</dt><dd>{secs(avgRun)}</dd></div>
				</dl>
				<ul class="group spaced">
					<li class="cell"><span class="grow">{t('health.running')}</span><span class="t-2">{num(h.syncs.running)}{h.syncs.queued ? ` · ${t('health.queued', { n: h.syncs.queued })}` : ''}</span></li>
					<li class="cell"><span class="grow">{t('health.fades')}</span><span class="t-2">{num(h.syncs.fades.total)}{h.syncs.fades.paused ? ` · ${t('health.paused', { n: h.syncs.fades.paused })}` : ''}</span></li>
					<li class="cell"><span class="grow">{t('health.waiting')}</span><span class="t-2">{num(h.syncs.fades.waiting)}</span></li>
					<li class="cell"><span class="grow">{t('health.libraryJobs')}</span><span class="t-2">{num(h.syncs.library_jobs)}</span></li>
				</ul>
			</div>

			<div class="section">
				<div class="section-head"><span class="caps">{t('health.instance')}</span></div>
				<ul class="group">
					<li class="cell"><span class="grow">{t('health.version')}</span><span class="t-2 mono">{h.instance.version}</span></li>
					<li class="cell"><span class="grow">{t('health.uptime')}</span><span class="t-2">{ago(h.instance.started_at)}</span></li>
					<li class="cell"><span class="grow">{t('health.scheduler')}</span><span class="t-2">{h.instance.scheduler ? t('health.on', { n: h.instance.workers }) : t('health.off')}</span></li>
					<li class="cell"><span class="grow">{t('health.database')}</span><span class="t-2">{bytes(h.instance.database_bytes)} · {t('health.schema', { n: h.instance.schema })}</span></li>
					<li class="cell"><span class="grow">{t('health.backup')}</span><span class="t-2">{h.instance.last_backup ? `${ago(h.instance.last_backup.at)} · ${bytes(h.instance.last_backup.bytes)}` : t('health.noBackup')}</span></li>
					<li class="cell"><span class="grow">{t('health.disk')}</span><span class="t-2">{t('health.diskFree', { free: bytes(h.instance.disk_free_bytes), total: bytes(h.instance.disk_total_bytes) })}</span></li>
					<li class="cell"><span class="grow">{t('health.songs')}</span><span class="t-2">{num(h.instance.counts.songs)} · {t('health.cached', { n: num(h.instance.counts.cached_searches) })}</span></li>
					<li class="cell"><span class="grow">{t('health.users')}</span><span class="t-2">{num(h.instance.counts.users)}</span></li>
				</ul>
				<p class="section-foot">{t('health.instanceFoot')}</p>
			</div>

			<div class="section">
				<div class="section-head"><span class="caps">{t('health.recent')}</span></div>
				{#if h.errors.length}
					<ul class="group">
						{#each h.errors as e, i (i)}
							<li class="cell err-row">
								<ServiceMark service={e.service} size={22} />
								<span class="grow">
									<span class="b t-foot t-2">{e.kind === 'auth' ? t('health.kindAuth') : t((OPS[e.op] ?? 'health.op.other') as never, { op: e.op })} · {ago(e.ts)}</span>
									<span class="b t-foot msg">{e.message}</span>
								</span>
							</li>
						{/each}
					</ul>
				{:else}
					<p class="section-foot">{t('health.noErrors')}</p>
				{/if}
			</div>
		{:else}
			<p class="intro t-sub t-2">{t('health.loading')}</p>
		{/if}
	</div>
</Sheet>

<style>
	.wrap {
		padding: 8px 0 calc(var(--safe-bottom) + 40px);
	}
	.intro {
		padding: 0 20px 22px;
	}
	.status {
		display: flex;
		align-items: center;
		gap: 14px;
		margin: 0 16px 14px;
		padding: 14px 16px;
		border-radius: var(--r-group);
		background: var(--cell-grouped);
		-webkit-backdrop-filter: blur(20px) saturate(1.6);
		backdrop-filter: blur(20px) saturate(1.6);
		box-shadow:
			var(--group-shadow),
			inset 0 0 0 0.5px var(--rim-edge),
			var(--group-rim);
	}
	.badge {
		display: grid;
		place-items: center;
		width: 40px;
		height: 40px;
		border-radius: 50%;
		background: var(--orange);
		color: #fff;
	}
	.status.good .badge {
		background: var(--green);
	}
	.att {
		margin-bottom: 26px;
	}
	.warn {
		color: var(--orange-text);
	}
	.svc {
		padding: 12px 16px 10px;
		margin-bottom: 12px;
	}
	.svc-head {
		display: flex;
		align-items: center;
		gap: 12px;
		margin-bottom: 10px;
	}
	.stats {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 8px;
		margin: 0;
	}
	.grid4 {
		padding: 12px 16px;
	}
	.stats dt {
		margin-bottom: 2px;
	}
	.stats dd {
		margin: 0;
		font-size: var(--f-t3, 1.25rem);
		font-weight: 600;
		font-variant-numeric: tabular-nums;
	}
	.stats dd.bad {
		color: var(--red);
	}
	.spark {
		display: block;
		width: 100%;
		height: 40px;
		margin-top: 12px;
	}
	.bar {
		fill: var(--label-3);
	}
	g:hover .bar {
		fill: var(--label-2);
	}
	.err {
		fill: var(--red);
	}
	.axis {
		display: flex;
		justify-content: space-between;
		margin: 4px 0 0;
	}
	.week {
		margin: 6px 0 0;
	}
	.spaced {
		margin-top: 12px;
	}
	.err-row {
		align-items: flex-start;
	}
	.msg {
		word-break: break-word;
		white-space: normal;
	}
	@media (max-width: 420px) {
		.stats {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
</style>
