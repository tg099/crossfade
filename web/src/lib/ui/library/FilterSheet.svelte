<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { labelMatches } from '#lib/search.ts';
	import { app } from '#lib/state.svelte.ts';
	import Glyph from '../Glyph.svelte';
	import Segmented from '../Segmented.svelte';
	import Sheet from '../Sheet.svelte';
	import SheetBar from '../SheetBar.svelte';
	import { num, t } from '../i18n.svelte.ts';
	import { artistsOf, catalog as c, kindRank } from './catalog.svelte.ts';

	// Pick artists, albums, collections or fades to narrow the library (the other filters sit right
	// in the library's filter bar). Counts are over the songs the other filters leave, so you never
	// pick into an empty list.
	const facet = $derived(c.facet);
	let q = $state('');
	let limit = $state(60);
	$effect(() => {
		facet;
		q;
		limit = 60;
	});
	const order = $derived(new Map(app.lines.map((l, i) => [l.id, i])));

	const entries = $derived.by(() => {
		if (!c.data || !c.filterOpen) return [];
		const base = c.data.songs.filter((s) => c.matches(s, facet));
		const counts = new Map<string, number>();
		const bump = (k: string) => k && counts.set(k, (counts.get(k) ?? 0) + 1);
		for (const s of base) {
			if (facet === 'artist') for (const a of new Set(artistsOf(s.a))) bump(a);
			else if (facet === 'album') bump(s.al);
			else if (facet === 'source') for (const src of new Set(s.in.map(([x]) => x))) bump(String(src));
			else for (const l of c.linesOfSong.get(s.id) ?? []) bump(String(l));
		}
		const label = (k: string) => (facet === 'source' ? (c.sourceById.get(+k)?.name ?? k) : facet === 'line' ? (c.lineById.get(+k)?.name ?? k) : k);
		let out = [...counts].map(([key, n]) => ({ key, n, label: label(key) }));
		for (const k of c.picked[facet]) if (!counts.has(k)) out.push({ key: k, n: 0, label: label(k) });
		if (q.trim()) out = out.filter((e) => labelMatches(e.label, q));
		const col = c.collator;
		if (facet === 'source')
			out.sort((a, b) => {
				const sa = c.sourceById.get(+a.key)!,
					sb = c.sourceById.get(+b.key)!;
				return col.compare(sa.service, sb.service) || kindRank(sa) - kindRank(sb) || col.compare(a.label, b.label);
			});
		else if (facet === 'line') out.sort((a, b) => (order.get(+a.key) ?? 0) - (order.get(+b.key) ?? 0));
		else out.sort((a, b) => b.n - a.n || col.compare(a.label, b.label));
		return out;
	});
	// pick every entry shown (the search narrows what "all" means), or none of them
	const allOn = $derived(entries.length > 0 && entries.every((e) => c.picked[facet].includes(e.key)));
	function toggleAll() {
		const keys = new Set(entries.map((e) => e.key));
		c.picked[facet] = allOn ? c.picked[facet].filter((k) => !keys.has(k)) : [...new Set([...c.picked[facet], ...keys])];
	}
</script>

<Sheet bind:open={c.filterOpen} label={t('lib.filter')}>
	{#snippet header()}
		<SheetBar title={t('lib.filter')} leading={{ label: t('lib.reset'), icon: 'undo' as never, run: () => (c.picked[facet] = []) }} confirm={{ label: t('lib.showN', { n: num(c.filtered.length) }), run: () => (c.filterOpen = false) }} />
	{/snippet}
	<div class="wrap">
		<div class="section">
			<div class="facet-tabs">
				<Segmented
					bind:value={c.facet}
					label={t('lib.by')}
					options={[
						{ value: 'artist', label: t('lib.artists') },
						{ value: 'album', label: t('lib.albums') },
						{ value: 'source', label: t('lib.collections') },
						{ value: 'line', label: t('fades.title') }
					]}
					small
				/>
			</div>
			<label class="field find">
				<Glyph name="search" size={15} weight={2.2} />
				<input bind:value={q} placeholder={t('lib.findIn')} autocomplete="off" />
			</label>
			{#if entries.length > 1}
				<div class="section-head">
					<span class="caps">{t('lib.nPicked', { n: num(c.picked[facet].length) })}</span>
					<button class="gbtn glass lq" onclick={toggleAll}>{allOn ? t('lib.selectNone') : t('lib.selectAllN', { n: num(entries.length) })}</button>
				</div>
			{/if}
			<ul class="group">
				{#each entries.slice(0, limit) as e (e.key)}
					{@const on = c.picked[facet].includes(e.key)}
					{@const src = facet === 'source' ? c.sourceById.get(+e.key) : undefined}
					{@const line = facet === 'line' ? c.lineById.get(+e.key) : undefined}
					<li>
						<button class="cell" onclick={() => c.togglePick(facet, e.key)} aria-pressed={on} style:--inset={src || line ? '50px' : undefined}>
							{#if src}<ServiceMark service={src.service} size={22} title={false} />{/if}
							{#if line}<span class="dot" style:background={line.color}></span>{/if}
							<span class="grow clip">{e.label}</span>
							<span class="value">{num(e.n)}</span>
							<span class="tick" class:hide={!on}><Glyph name="check" size={18} weight={2.6} /></span>
						</button>
					</li>
				{:else}
					<li class="cell"><span class="grow t-2">{t('lib.noMatch')}</span></li>
				{/each}
			</ul>
			{#if entries.length > limit}
				<button class="text-btn more" onclick={() => (limit += 120)}>{t('lib.moreN', { n: num(entries.length - limit) })}</button>
			{/if}
		</div>
	</div>
</Sheet>

<style>
	.wrap {
		padding: 8px 0 calc(var(--safe-bottom) + 40px);
	}
	.tick {
		color: var(--tint);
	}
	.tick.hide {
		visibility: hidden;
	}
	.facet-tabs {
		margin: 0 16px 10px;
	}
	.find {
		margin: 0 16px 10px;
	}
	.dot {
		width: 12px;
		height: 12px;
		margin: 0 5px;
		border-radius: 50%;
	}
	.more {
		margin: 8px 16px 0;
	}
</style>
