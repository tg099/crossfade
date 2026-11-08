<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { SERVICE_NAME } from '#lib/format.ts';
	import { highlight } from '#lib/search.ts';
	import { app } from '#lib/state.svelte.ts';
	import type { CatalogSong, Service } from '#lib/types.ts';
	import Cover from '../Cover.svelte';
	import Glyph from '../Glyph.svelte';
	import Menu from '../Menu.svelte';
	import Page from '../Page.svelte';
	import { hold, trackMenu } from '../ctx.ts';
	import { num, t, type Key } from '../i18n.svelte.ts';
	import AddSheet from '../library/AddSheet.svelte';
	import CleanupSheet from '../library/CleanupSheet.svelte';
	import FilterSheet from '../library/FilterSheet.svelte';
	import LibrarySongSheet from '../library/LibrarySongSheet.svelte';
	import RemoveSheet from '../library/RemoveSheet.svelte';
	import SourcesSheet from '../library/SourcesSheet.svelte';
	import { catalog as c, type Avail, type Facet, type Sort, type Special } from '../library/catalog.svelte.ts';
	import { BRAND, ago, mmss, ui } from '../ui.svelte.ts';

	// Everything in your collections on all services, as one list: search, filter, find gaps, and
	// add or remove songs in bulk. A scan reads the collections; Crossfade never changes anything
	// here unless you ask.
	$effect(() => {
		c.load();
	});
	$effect(() => {
		app.catalogFinished;
		c.sync();
	});

	const job = $derived(app.catalogJob);
	const pct = $derived(job && job.total ? Math.min(1, job.done / job.total) : 0);

	// list rendering in batches
	let limit = $state(120);
	$effect(() => {
		c.filtered;
		limit = 120;
	});
	const visible = $derived(c.filtered.slice(0, limit));
	let sentinel = $state<HTMLElement | null>(null);
	$effect(() => {
		limit;
		if (!sentinel) return;
		const io = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && limit < c.filtered.length && (limit += 120), { rootMargin: '800px' });
		io.observe(sentinel);
		return () => io.disconnect();
	});

	// selection
	let anchor: number | null = null;
	function tap(s: CatalogSong, ev: MouseEvent) {
		if (!c.selecting) return c.openSong(s);
		if (ev.shiftKey && anchor !== null) {
			const ids = c.filtered.map((x) => x.id);
			const [a, b] = [ids.indexOf(anchor), ids.indexOf(s.id)].sort((x, y) => x - y);
			if (a >= 0 && b >= 0) {
				const on = !c.selected.has(s.id);
				for (const x of ids.slice(a, b + 1)) on ? c.selected.add(x) : c.selected.delete(x);
				anchor = s.id;
				return;
			}
		}
		c.selected.has(s.id) ? c.selected.delete(s.id) : c.selected.add(s.id);
		anchor = s.id;
	}
	const allSelected = $derived(c.filtered.length > 0 && c.filtered.every((s) => c.selected.has(s.id)));
	function toggleAll() {
		if (allSelected) for (const s of c.filtered) c.selected.delete(s.id);
		else for (const s of c.filtered) c.selected.add(s.id);
	}
	function endSelect() {
		c.selecting = false;
		c.selected.clear();
	}
	const selectedSongs = $derived((c.data?.songs ?? []).filter((s) => c.selected.has(s.id)));
	function exportSongs(songs: CatalogSong[]) {
		c.exportCsv(
			songs,
			[t('lib.csv.title'), t('lib.csv.artist'), t('lib.csv.album'), t('lib.csv.duration'), ...c.services.map((s) => SERVICE_NAME[s]), t('lib.csv.collections'), t('fades.title')],
			t('common.yes')
		);
	}

	function songMenu(s: CatalogSong) {
		const m = trackMenu(c.track(s));
		m.items.push(
			{ label: t('lib.add'), icon: 'plus', run: () => c.openAdd([s.id]) },
			{ label: t('lib.select'), icon: 'check', run: () => ((c.selecting = true), c.selected.add(s.id)) },
			{ label: t('lib.remove'), icon: 'trash', role: 'destructive', run: () => c.openRemove([s.id]) }
		);
		return m;
	}

	// sort menu
	let sortBtn: HTMLButtonElement | undefined = $state();
	let sortOpen = $state(false);
	const SORTS: [Sort, Key][] = [
		['artist', 'lib.sort.artist'],
		['title', 'lib.sort.title'],
		['album', 'lib.sort.album'],
		['added', 'lib.sort.added'],
		['duration', 'lib.sort.duration']
	];

	// filter bar
	const FACETS: [Facet, Key][] = [
		['artist', 'lib.artists'],
		['album', 'lib.albums'],
		['source', 'lib.collections'],
		['line', 'fades.title']
	];
	const SPECIALS: Special[] = ['single', 'versions', 'noline', 'videos', 'explicit'];
	const picks = $derived(Object.values(c.picked).flat().length);
	let availBtn = $state<Partial<Record<Service, HTMLButtonElement>>>({});
	let availOpen = $state<Service | null>(null);
	const AVAILS: [Avail, Key][] = [
		['any', 'lib.avail.all'],
		['has', 'lib.on'],
		['missing', 'lib.notOn']
	];

	const lastAdd = $derived(app.catalogFinished?.action === 'add' && app.catalogFinished.result?.missing?.length ? app.catalogFinished : null);
	let hideMissing = $state(0);

	function onkeydown(e: KeyboardEvent) {
		const el = e.target as HTMLElement;
		if (el.closest('input, textarea, [role="dialog"]') || nav_hidden()) return;
		if ((e.metaKey || e.ctrlKey) && e.key === 'a' && c.selecting && c.filtered.length) {
			e.preventDefault();
			for (const s of c.filtered) c.selected.add(s.id);
		} else if (e.key === 'Escape' && c.selecting) endSelect();
	}
	let root: HTMLElement | undefined = $state();
	const nav_hidden = () => !root || root.closest('[inert]') !== null;
	const hl = (text: string) => highlight(text, c.query);
</script>

<svelte:window {onkeydown} />

{#snippet actions()}
	{#if c.data?.songs.length}
		<button class="gbtn glass lq" onclick={() => (c.selecting ? endSelect() : (c.selecting = true))}>{c.selecting ? t('common.done') : t('lib.select')}</button>
	{/if}
	<button class="gbtn round glass lq" onclick={() => (c.sourcesOpen = true)} aria-label={t('lib.sources')}><Glyph name="settings" size={19} weight={2.1} /></button>
{/snippet}

<div class="lib" bind:this={root}>
	<Page title={t('tab.library')} {actions} glow={BRAND}>
		<div class="body">
			{#if job}
				<div class="card glass job" style:--p={pct}>
					<span class="spin" aria-hidden="true"></span>
					<span class="grow">
						<span class="b">{t(`lib.job.${job.action}` as Key)}</span>
						<span class="b t-foot t-2 clip">{job.phase}{job.total ? ` · ${num(job.done)} / ${num(job.total)}` : ''}</span>
					</span>
					<button class="gbtn glass lq" onclick={c.stop}>{t('common.stop')}</button>
					<span class="meter" aria-hidden="true"></span>
				</div>
			{/if}

			{#if lastAdd && hideMissing !== lastAdd.at}
				<div class="card glass note">
					<span class="tile-icon" style:background="var(--orange)"><Glyph name="alert" size={17} weight={2.2} /></span>
					<span class="grow">
						<span class="b">{t('lib.missing', { n: lastAdd.result.missing.length, target: lastAdd.result.target })}</span>
						<span class="b t-foot t-2">
							{lastAdd.result.missing
								.slice(0, 6)
								.map((m: { artist: string; title: string }) => `${m.artist} – ${m.title}`)
								.join(' · ')}{lastAdd.result.missing.length > 6 ? ' …' : ''}
						</span>
					</span>
					<button class="icon-btn" onclick={() => (hideMissing = lastAdd.at)} aria-label={t('common.close')}><Glyph name="x" size={16} weight={2.4} /></button>
				</div>
			{/if}

			{#if c.data?.songs.length && (c.cleanupCount || c.cleanup?.undo)}
				<button class="card glass tidy" onclick={() => (c.cleanupOpen = true)}>
					<span class="tile-icon" style:background="#5e5ce6"><Glyph name="sparkle" size={17} weight={2.2} /></span>
					<span class="grow">
						<span class="b">{c.cleanupCount ? t('clean.entry', { n: num(c.cleanupCount) }) : t('clean.title')}</span>
						<span class="b t-foot t-2 clip">{c.cleanupCount ? t('clean.entrySub') : t('clean.undo')}</span>
					</span>
					<Glyph name="chevronRight" size={18} weight={2.4} />
				</button>
			{/if}

			{#if c.error}
				<p class="empty t-sub danger">{c.error}</p>
			{:else if !c.data}
				<p class="empty t-sub t-2">{t('lib.loading')}</p>
			{:else if !c.data.songs.length}
				<div class="first">
					<span class="first-ic glass"><Glyph name="library" size={34} weight={1.8} /></span>
					<h2 class="t-title2">{t('lib.emptyTitle')}</h2>
					<p class="t-sub t-2">{app.connected.length ? t('lib.emptyText') : t('lib.emptyNoServices')}</p>
					{#if app.connected.length}
						<button class="pb glass tint lq" onclick={c.scan} disabled={!!job}>{t('lib.scan')}</button>
					{:else}
						<button class="pb glass tint lq" onclick={() => ui.openAccount()}>{t('services.connect')}</button>
					{/if}
				</div>
			{:else}
				<div class="tools">
					<label class="search glass">
						<Glyph name="search" size={16} weight={2.2} />
						<input bind:value={c.query} placeholder={t('lib.searchPh', { n: num(c.data.songs.length) })} autocomplete="off" spellcheck="false" onkeydown={(e) => e.key === 'Escape' && (c.query = '')} />
						{#if c.query}<button class="clear" onclick={() => (c.query = '')} aria-label={t('search.clear')}><Glyph name="x" size={11} weight={3} /></button>{/if}
					</label>
					<button class="gbtn round glass lq" bind:this={sortBtn} onclick={() => (sortOpen = true)} aria-label={t('lib.sort')} aria-haspopup="menu"><Glyph name="updown" size={18} weight={2.2} /></button>
				</div>

				<!-- the filters sit right here, not behind a button: services, lists to pick from, extras -->
				<div class="fbar" role="toolbar" aria-label={t('lib.filter')}>
					{#if c.filterCount}
						<button class="fp reset" onclick={() => c.resetFilters()} aria-label={t('lib.reset')}><Glyph name="x" size={12} weight={3} /></button>
					{/if}
					{#each c.services as svc}
						{@const a = c.avail[svc]}
						<button class="fp" class:on={a !== 'any'} bind:this={availBtn[svc]} onclick={() => (availOpen = svc)} aria-haspopup="menu">
							<ServiceMark service={svc} size={16} title={false} />
							{a === 'any' ? SERVICE_NAME[svc] : t(a === 'has' ? 'lib.on' : 'lib.notOn', { service: SERVICE_NAME[svc] })}
							<Glyph name="chevronDown" size={12} weight={2.6} />
						</button>
					{/each}
					{#each FACETS as [f, k]}
						{@const n = c.picked[f].length}
						<button class="fp" class:on={n > 0} onclick={() => ((c.facet = f), (c.filterOpen = true))} aria-haspopup="dialog">
							{t(k)}{#if n}<span class="fn">{n}</span>{/if}
							<Glyph name="chevronDown" size={12} weight={2.6} />
						</button>
					{/each}
					{#each SPECIALS as sp}
						{@const on = c.special.includes(sp)}
						<button class="fp" class:on onclick={() => c.toggleSpecial(sp)} aria-pressed={on} title={t(`lib.special.${sp}` as Key)}>{t(`lib.chip.${sp}` as Key)}</button>
					{/each}
				</div>

				<div class="summary t-foot t-2">
					<span>
						{c.filtered.length === c.data.songs.length ? t('n.songs', { n: num(c.data.songs.length) }) : t('lib.shown', { n: num(c.filtered.length), all: num(c.data.songs.length) })}
						· {t('lib.scanned', { ago: ago(c.data.scanned_at) })}
						{#if c.unscanned}· <span class="warn-text">{t('lib.unscanned', { n: c.unscanned })}</span>{/if}
					</span>
					<button class="text-btn t-foot" onclick={c.scan} disabled={!!job}>{t('lib.rescan')}</button>
				</div>

				{#if picks}
					<div class="chips">
						{#each c.picked.artist as k}<button class="chip" onclick={() => c.togglePick('artist', k)}>{k} <Glyph name="x" size={10} weight={3} /></button>{/each}
						{#each c.picked.album as k}<button class="chip" onclick={() => c.togglePick('album', k)}>{k} <Glyph name="x" size={10} weight={3} /></button>{/each}
						{#each c.picked.source as k}<button class="chip" onclick={() => c.togglePick('source', k)}>{c.sourceById.get(+k)?.name ?? k} <Glyph name="x" size={10} weight={3} /></button>{/each}
						{#each c.picked.line as k}{@const l = c.lineById.get(+k)}<button class="chip" style:--c={l?.color} onclick={() => c.togglePick('line', k)}>{l?.name ?? k} <Glyph name="x" size={10} weight={3} /></button>{/each}
					</div>
				{/if}

				{#if c.selecting}
					<div class="selhead t-foot">
						<button class="text-btn t-foot" onclick={toggleAll}>{allSelected ? t('lib.selectNone') : t('lib.selectAll', { n: num(c.filtered.length) })}</button>
					</div>
				{/if}

				{#if !c.filtered.length}
					<p class="empty t-sub t-2">{t('lib.noMatch')}</p>
				{:else}
					<ul class="group songs">
						{#each visible as s (s.id)}
							{@const pres = c.presence(s)}
							{@const on = c.selected.has(s.id)}
							<li>
								<button class="cell song" style:--inset={c.selecting ? "108px" : "74px"} class:on onclick={(e) => tap(s, e)} use:hold={() => songMenu(s)} aria-pressed={c.selecting ? on : undefined}>
									{#if c.selecting}<span class="check" class:on aria-hidden="true">{#if on}<Glyph name="check" size={13} weight={3} />{/if}</span>{/if}
									<Cover src={s.c} seed={String(s.id)} size={44} track={c.track(s)} song={s.id} />
									<span class="grow">
										<span class="b clip t1">{#each hl(s.t) as seg}{#if seg.hit}<mark>{seg.s}</mark>{:else}{seg.s}{/if}{/each}{#if s.x}<span class="ex" title="Explicit">E</span>{/if}</span>
										<span class="b clip t-foot t-2">{#each hl(s.a) as seg}{#if seg.hit}<mark>{seg.s}</mark>{:else}{seg.s}{/if}{/each}<span class="al-inline">{s.al ? ' · ' : ''}{#each hl(s.al) as seg}{#if seg.hit}<mark>{seg.s}</mark>{:else}{seg.s}{/if}{/each}</span></span>
									</span>
									<!-- wide windows: the album gets its own column, like a music library table -->
									<span class="al-col clip t-sub t-2">{#each hl(s.al) as seg}{#if seg.hit}<mark>{seg.s}</mark>{:else}{seg.s}{/if}{/each}</span>
									<span class="marks" aria-label={c.services.filter((x) => pres.has(x)).map((x) => SERVICE_NAME[x]).join(', ')}>
										{#each c.services as svc}
											<span class="mk" class:off={!pres.has(svc)}><ServiceMark service={svc} size={16} title={false} /></span>
										{/each}
									</span>
									{#if !ui.phone}<span class="dur t-foot t-2">{mmss(s.d)}</span>{/if}
								</button>
							</li>
						{/each}
					</ul>
					{#if limit < c.filtered.length}<div bind:this={sentinel} class="more t-foot t-2">{t('lib.more')}</div>{/if}
				{/if}
			{/if}
		</div>
	</Page>

	{#if c.selecting}
		<div class="selbar glass strong">
			<span class="t-sub grow clip">{t('lib.selected', { n: num(c.selected.size) })}</span>
			<button class="gbtn glass lq" disabled={!c.selected.size || !!job} onclick={() => c.openAdd([...c.selected])} aria-label={t('lib.addShort')}><Glyph name="plus" size={16} weight={2.4} /><span class="sl">{t('lib.addShort')}</span></button>
			<button class="gbtn glass lq" disabled={!c.selected.size || !!job} onclick={() => c.openRemove([...c.selected])} aria-label={t('lib.removeShort')}><Glyph name="trash" size={16} weight={2.2} /><span class="sl">{t('lib.removeShort')}</span></button>
			<button class="gbtn round glass lq" disabled={!c.selected.size} onclick={() => exportSongs(selectedSongs)} aria-label={t('lib.export')}><Glyph name="external" size={16} weight={2.2} /></button>
		</div>
	{/if}
</div>

<Menu bind:open={sortOpen} anchor={sortBtn} label={t('lib.sort')} items={SORTS.map(([v, k]) => ({ label: t(k), checked: c.sort === v, run: () => (c.sort = v) }))} />
{#each c.services as svc}
	<Menu
		bind:open={() => availOpen === svc, (v) => (availOpen = v ? svc : null)}
		anchor={availBtn[svc]}
		label={SERVICE_NAME[svc]}
		items={AVAILS.map(([v, k]) => ({ label: t(k, { service: SERVICE_NAME[svc] }), checked: c.avail[svc] === v, run: () => (c.avail[svc] = v) }))}
	/>
{/each}
<FilterSheet />
<SourcesSheet />
<LibrarySongSheet />
<AddSheet />
<CleanupSheet />
<RemoveSheet onDone={() => c.selecting && endSelect()} />

<style>
	.lib {
		position: absolute;
		inset: 0;
	}
	.body {
		padding: 0 16px;
	}
	@media (min-width: 721px) {
		.body {
			padding: 0 36px;
			max-width: calc(var(--content-max) + 72px);
		}
	}
	.card {
		position: relative;
		display: flex;
		align-items: center;
		gap: 12px;
		margin-bottom: 12px;
		padding: 12px 14px;
		border-radius: var(--r-group);
		overflow: hidden;
	}
	.grow {
		flex: 1;
		min-width: 0;
	}
	.tidy {
		width: 100%;
		text-align: start;
		color: inherit;
	}
	.tidy > :global(svg:last-child) {
		color: var(--label-3);
	}
	.meter {
		position: absolute;
		left: 0;
		bottom: 0;
		height: 3px;
		width: calc(var(--p) * 100%);
		background: var(--tint-fill);
		transition: width 400ms ease;
	}
	.spin {
		width: 18px;
		height: 18px;
		border: 2.2px solid var(--fill-2);
		border-top-color: var(--label);
		border-radius: 50%;
		animation: spin 0.9s linear infinite;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
	.tools {
		display: flex;
		gap: 8px;
		align-items: center;
	}
	.search {
		flex: 1;
		display: flex;
		align-items: center;
		gap: 8px;
		height: 44px;
		padding: 0 8px 0 14px;
		border-radius: 999px;
		color: var(--label-2);
	}
	.search input {
		flex: 1;
		min-width: 0;
		border: 0;
		outline: none;
		background: none;
		color: var(--label);
		font: inherit;
	}
	.clear {
		display: grid;
		place-items: center;
		width: 20px;
		height: 20px;
		border-radius: 50%;
		background: var(--label-3);
		color: var(--bg);
	}
	.gbtn.round {
		position: relative;
		width: 44px;
		height: 44px;
	}
	.fbar {
		display: flex;
		gap: 6px;
		margin: 12px -16px 0;
		padding: 2px 16px;
		overflow-x: auto;
		scrollbar-width: none;
		-webkit-overflow-scrolling: touch;
	}
	.fbar::-webkit-scrollbar {
		display: none;
	}
	@media (min-width: 721px) {
		/* one row on desktop too: a lone chip wrapping into a second row looks broken. What doesn't
		   fit scrolls sideways and fades out at the edge. */
		.fbar {
			margin: 12px 0 0;
			padding: 0 32px 0 0;
			-webkit-mask-image: linear-gradient(to right, #000 calc(100% - 32px), transparent);
			mask-image: linear-gradient(to right, #000 calc(100% - 32px), transparent);
		}
	}
	.fp {
		display: inline-flex;
		flex: none;
		align-items: center;
		gap: 6px;
		height: 32px;
		padding: 0 11px;
		border-radius: 999px;
		background: var(--ctl);
		box-shadow: var(--ctl-shadow);
		color: var(--label);
		font-size: var(--f-sub);
		font-weight: 600;
		white-space: nowrap;
		transition: background 160ms ease, color 160ms ease;
	}
	.fp :global(svg:last-child:not(:first-child)) {
		opacity: 0.55;
	}
	.fp.on {
		background: var(--tint-fill);
		color: var(--on-tint);
	}
	.fp.reset {
		width: 32px;
		padding: 0;
		justify-content: center;
		color: var(--label-2);
	}
	.fn {
		min-width: 18px;
		height: 18px;
		padding: 0 5px;
		border-radius: 9px;
		background: color-mix(in srgb, var(--on-tint) 25%, transparent);
		font-size: 11px;
		font-weight: 700;
		line-height: 18px;
		text-align: center;
	}
	.summary {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 10px;
		flex-wrap: wrap;
		min-height: 20px;
		margin: 16px 4px 10px;
	}
	/* a quiet link in the caption line: caption size, no 44px box pushing the list away */
	.summary .text-btn,
	.selhead .text-btn {
		min-height: 0;
		padding: 0;
		font-size: inherit;
		font-weight: 600;
		/* the tap area stays comfortable without taking up layout space */
		position: relative;
	}
	.summary .text-btn::after,
	.selhead .text-btn::after {
		content: '';
		position: absolute;
		inset: -12px -8px;
	}
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		align-items: center;
		margin: 0 0 10px;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 5px 10px;
		border-radius: 999px;
		background: color-mix(in srgb, var(--c, var(--tint-fill)) 15%, transparent);
		color: color-mix(in srgb, var(--c, var(--tint-fill)) 75%, var(--label));
		font-size: var(--f-foot);
		font-weight: 600;
	}
	.selhead {
		margin: 0 4px 10px;
	}
	.songs :global(.cell) {
		min-height: 62px;
	}
	.al-col {
		display: none;
	}
	@media (min-width: 1200px) {
		.al-inline {
			display: none;
		}
		.al-col {
			display: block;
			flex: 0 1 34%;
			min-width: 0;
			padding-right: 12px;
		}
	}
	.song.on {
		background: color-mix(in srgb, var(--tint-fill) 10%, transparent);
	}
	.check {
		display: grid;
		flex: none;
		place-items: center;
		width: 22px;
		height: 22px;
		margin-right: -2px;
		border-radius: 50%;
		box-shadow: inset 0 0 0 1.5px var(--label-3);
		color: var(--on-tint);
	}
	.check.on {
		background: var(--tint-fill);
		box-shadow: none;
	}
	.t1 {
		font-weight: 500;
	}
	.ex {
		display: inline-grid;
		place-items: center;
		width: 15px;
		height: 15px;
		margin-left: 6px;
		border-radius: 3px;
		background: var(--fill-2);
		color: var(--label-2);
		font-size: 9px;
		font-weight: 800;
		vertical-align: 2px;
	}
	mark {
		background: color-mix(in srgb, var(--yellow, #ffd60a) 45%, transparent);
		color: inherit;
		border-radius: 3px;
	}
	.marks {
		display: inline-flex;
		gap: 4px;
	}
	.mk.off {
		opacity: 0.18;
		filter: grayscale(1);
	}
	.dur {
		width: 3em;
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	.more,
	.empty {
		padding: 30px 16px;
		text-align: center;
	}
	.first {
		display: grid;
		justify-items: center;
		gap: 10px;
		max-width: 420px;
		margin: 40px auto;
		text-align: center;
	}
	.first-ic {
		display: grid;
		place-items: center;
		width: 76px;
		height: 76px;
		border-radius: 24px;
		margin-bottom: 6px;
	}
	.first .pb {
		margin-top: 10px;
	}
	/* phone: the actions shrink to round icon buttons so the count keeps its room */
	@media (max-width: 520px) {
		.selbar .sl {
			display: none;
		}
		.selbar .gbtn {
			width: 44px;
			padding: 0;
		}
	}
	.selbar .grow {
		min-width: 0;
	}
	.selbar {
		position: absolute;
		left: calc(var(--side) + 16px);
		right: 16px;
		bottom: calc(var(--chrome-bottom, 24px) - 4px);
		z-index: 5;
		display: flex;
		align-items: center;
		gap: 8px;
		max-width: 640px;
		margin: 0 auto;
		padding: 8px 8px 8px 18px;
		border-radius: 999px;
		animation: rise 360ms cubic-bezier(0.25, 1.3, 0.45, 1);
	}
	@keyframes rise {
		from {
			opacity: 0;
			transform: translateY(16px) scale(0.96);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.selbar {
			animation: none;
		}
	}
</style>
