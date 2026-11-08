<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { SERVICE_NAME } from '#lib/format.ts';
	import { api, post } from '#lib/api.ts';
	import { app } from '#lib/state.svelte.ts';
	import type { Kind, Service } from '#lib/types.ts';
	import Cover from './Cover.svelte';
	import Glyph from './Glyph.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import { i18n, num, t } from './i18n.svelte.ts';
	import { ui } from './ui.svelte.ts';

	// Time travel: earlier states of your own collections. Pick a collection, a point in time, see
	// what is gone since then and bring it back – or the whole state exactly as it was.
	type Coll = { account_id: number; service: Service; kind: Kind; collection_id: string; name: string; snapshots: number; first: number; last_seen: number; count: number; fades: number };
	type Snap = { id: number; taken_at: number; last_seen: number; count: number; origin: string; added: number | null; removed: number | null };
	type Timeline = { name: string; service: Service; fades: number; snapshots: Snap[] };
	type Song = { ref: string; song_id: number | null; title: string; artist: string; album: string; artwork: string | null; service: Service };
	type Detail = { id: number; taken_at: number; count: number; service: Service; latest: { id: number; taken_at: number; last_seen: number }; missing: Song[]; extra: Song[] };

	let list = $state<Coll[] | null>(null);
	let tl = $state<Timeline | null>(null);
	let detail = $state<Detail | null>(null);
	let picked = $state<Set<string>>(new Set());
	let showAll = $state({ missing: false, extra: false });
	let busy = $state(false);
	let kind = $state<Kind>('playlist');
	const LIMIT = 150;

	async function loadList() {
		list = await api<Coll[]>('/api/history');
	}
	async function openCollection(c: { account_id: number; kind: Kind; collection_id: string }) {
		detail = null;
		kind = c.kind;
		tl = await api<Timeline>(`/api/history/timeline?account_id=${c.account_id}&kind=${c.kind}&collection_id=${encodeURIComponent(c.collection_id)}`);
	}
	async function openSnap(s: Snap) {
		detail = await api<Detail>(`/api/history/snapshots/${s.id}`);
		picked = new Set(detail.missing.map((x) => x.ref));
		showAll = { missing: false, extra: false };
	}

	$effect(() => {
		if (!ui.historyOpen) return;
		const target = ui.historyTarget;
		(target ? openCollection(target) : loadList()).catch(ui.fail);
	});
	function closed() {
		tl = detail = null;
		list = null;
		ui.historyTarget = null;
	}
	function back() {
		if (detail) detail = null;
		else if (tl && !ui.historyTarget) {
			tl = null;
			if (!list) loadList().catch(ui.fail);
		} else ui.historyOpen = false;
	}

	const when = (ts: number) => {
		const d = new Date(ts * 1000);
		const today = new Date();
		const y = new Date(Date.now() - 86400_000);
		const time = d.toLocaleTimeString(i18n.locale, { hour: '2-digit', minute: '2-digit' });
		if (d.toDateString() === today.toDateString()) return t('tt.today', { time });
		if (d.toDateString() === y.toDateString()) return t('tt.yesterday', { time });
		return d.toLocaleString(i18n.locale, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
	};
	const dayOf = (ts: number) => new Date(ts * 1000).toLocaleDateString(i18n.locale, { day: 'numeric', month: 'long', year: 'numeric' });
	const countLabel = (kind: Kind, n: number) => t(kind === 'liked' ? 'n.likes' : 'n.songs', { n });

	function toggle(ref: string) {
		const next = new Set(picked);
		if (next.has(ref)) next.delete(ref);
		else next.add(ref);
		picked = next;
	}

	async function start(mode: 'missing' | 'exact') {
		if (!detail) return;
		busy = true;
		try {
			const only = mode === 'missing' && picked.size < detail.missing.length ? [...picked] : null;
			await post(`/api/history/snapshots/${detail.id}/restore`, { mode, only });
			ui.say(t('tt.started'), 'history');
			ui.historyOpen = false;
		} catch (e) {
			ui.fail(e);
		} finally {
			busy = false;
		}
	}
	function exact() {
		if (!detail) return;
		const d = detail;
		ui.ask = {
			title: t('tt.exactQ', { when: when(d.taken_at) }),
			message: [t('tt.exactMsg', { add: d.missing.length, remove: d.extra.length }), tl?.fades ? t('tt.fadeNote', { n: tl.fades }) : '']
				.filter(Boolean)
				.join(' '),
			label: t('tt.exactGo'),
			run: () => start('exact')
		};
	}
	const title = $derived(detail ? when(detail.taken_at) : tl ? tl.name : t('tt.title'));
</script>

<Sheet bind:open={ui.historyOpen} label={t('tt.title')} onclosed={closed}>
	{#snippet header()}
		<SheetBar {title} leading={{ label: t('common.back'), icon: 'chevronLeft', run: back }} />
	{/snippet}
	<div class="wrap">
		{#if detail && tl}
			{@const nothing = !detail.missing.length && !detail.extra.length}
			<div class="hero">
				<ServiceMark service={detail.service} size={44} />
				<p class="t-headline">{tl.name}</p>
				<p class="t-sub t-2">{t('tt.then', { count: countLabel(kind, detail.count) })}</p>
			</div>
			{#if nothing}
				<p class="intro t-sub t-2 center">{t('tt.same')}</p>
			{:else}
				{#if detail.missing.length}
					<div class="section">
						<div class="section-head">
							<span class="caps">{t('tt.missing', { n: detail.missing.length })}</span>
							<button class="text-btn t-sub" onclick={() => (picked = picked.size === detail!.missing.length ? new Set() : new Set(detail!.missing.map((x) => x.ref)))}>
								{picked.size === detail.missing.length ? t('lib.selectNone') : t('lib.selectAllShort')}
							</button>
						</div>
						<ul class="group">
							{#each showAll.missing ? detail.missing : detail.missing.slice(0, LIMIT) as s (s.ref)}
								<li>
									<button class="cell" style:--inset="74px" onclick={() => toggle(s.ref)} aria-pressed={picked.has(s.ref)}>
										<Cover src={s.artwork} seed={s.ref} size={44} song={s.song_id} />
										<span class="grow"><span class="b clip">{s.title}</span><span class="b t-foot t-2 clip">{s.artist}</span></span>
										<span class="pick" class:on={picked.has(s.ref)} aria-hidden="true">{#if picked.has(s.ref)}<Glyph name="check" size={14} weight={3} />{/if}</span>
									</button>
								</li>
							{/each}
							{#if detail.missing.length > LIMIT && !showAll.missing}
								<li><button class="cell" onclick={() => (showAll.missing = true)}><span class="grow tintc">{t('tt.showAll', { n: detail.missing.length })}</span></button></li>
							{/if}
						</ul>
						<div class="actions">
							<button class="pb glass tint lq" onclick={() => start('missing')} disabled={busy || !picked.size || !!app.catalogJob}>
								<Glyph name="undo" size={18} weight={2.4} />
								{t('tt.bringBack', { n: picked.size })}
							</button>
						</div>
						<p class="section-foot">{tl.fades ? t('tt.fadeNote', { n: tl.fades }) : t('tt.missingFoot')}</p>
					</div>
				{/if}
				{#if detail.extra.length}
					<div class="section">
						<div class="section-head"><span class="caps">{t('tt.extra', { n: detail.extra.length })}</span></div>
						<ul class="group">
							{#each showAll.extra ? detail.extra : detail.extra.slice(0, LIMIT) as s (s.ref)}
								<li class="cell" style:--inset="74px">
									<Cover src={s.artwork} seed={s.ref} size={44} song={s.song_id} />
									<span class="grow"><span class="b clip">{s.title}</span><span class="b t-foot t-2 clip">{s.artist}</span></span>
								</li>
							{/each}
							{#if detail.extra.length > LIMIT && !showAll.extra}
								<li><button class="cell" onclick={() => (showAll.extra = true)}><span class="grow tintc">{t('tt.showAll', { n: detail.extra.length })}</span></button></li>
							{/if}
						</ul>
					</div>
				{/if}
				<div class="section">
					<div class="group">
						<button class="cell center" onclick={exact} disabled={busy || !!app.catalogJob}><span class="grow tintc">{t('tt.exact')}</span></button>
					</div>
					<p class="section-foot">{t('tt.exactFoot')}</p>
				</div>
			{/if}
			{#if app.catalogJob}<p class="section-foot">{t('tt.busy')}</p>{/if}
		{:else if tl}
			<p class="intro t-sub t-2">{t('tt.timelineIntro')}</p>
			<div class="section">
				<ul class="group">
					{#each tl.snapshots as s, i (s.id)}
						<li>
							<button class="cell" onclick={() => openSnap(s)} disabled={i === 0}>
								<span class="dot" class:now={i === 0} aria-hidden="true"></span>
								<span class="grow">
									<span class="b">{i === 0 ? t('tt.now') : when(s.taken_at)}</span>
									<span class="b t-foot t-2">
										{countLabel(kind, s.count)}{#if s.added || s.removed} · {#if s.added}<span class="plus">+{num(s.added)}</span>{/if}{#if s.added && s.removed}&nbsp;{/if}{#if s.removed}<span class="minus">−{num(s.removed)}</span>{/if}{/if}{#if s.origin === 'restore'} · {t('tt.restored')}{/if}
									</span>
								</span>
								{#if i > 0}<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>{/if}
							</button>
						</li>
					{/each}
				</ul>
				<p class="section-foot">{tl.snapshots.length ? t('tt.since', { date: dayOf(tl.snapshots[tl.snapshots.length - 1].taken_at) }) : t('tt.none')} {t('tt.keep')}</p>
			</div>
		{:else if list}
			<p class="intro t-sub t-2">{t('tt.intro')}</p>
			{#if list.length}
				<div class="section">
					<ul class="group">
						{#each list as c (`${c.account_id}:${c.kind}:${c.collection_id}`)}
							<li>
								<button class="cell" style:--inset="58px" onclick={() => openCollection(c).catch(ui.fail)}>
									<ServiceMark service={c.service} size={30} />
									<span class="grow">
										<span class="b clip">{c.name}</span>
										<span class="b t-foot t-2 clip">{SERVICE_NAME[c.service]} · {t('tt.states', { n: c.snapshots })}</span>
									</span>
									<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
								</button>
							</li>
						{/each}
					</ul>
				</div>
			{:else}
				<p class="intro t-sub t-2">{t('tt.empty')}</p>
			{/if}
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
	.center {
		text-align: center;
	}
	.hero {
		display: grid;
		justify-items: center;
		gap: 4px;
		padding: 4px 24px 22px;
		text-align: center;
	}
	.hero :global(svg),
	.hero :global(img) {
		margin-bottom: 6px;
	}
	.dot {
		flex: none;
		width: 10px;
		height: 10px;
		margin: 0 4px;
		border-radius: 50%;
		background: var(--label-3);
	}
	.dot.now {
		background: var(--tint);
		box-shadow: 0 0 0 4px var(--tint-fill);
	}
	.plus {
		color: var(--green-text, var(--green));
		font-variant-numeric: tabular-nums;
	}
	.minus {
		color: var(--red);
		font-variant-numeric: tabular-nums;
	}
	.pick {
		display: grid;
		flex: none;
		place-items: center;
		width: 24px;
		height: 24px;
		border-radius: 50%;
		box-shadow: inset 0 0 0 1.5px var(--label-3);
		color: var(--on-tint);
	}
	.pick.on {
		background: var(--tint);
		box-shadow: none;
	}
	.actions {
		display: flex;
		justify-content: center;
		margin-top: 14px;
	}
</style>
