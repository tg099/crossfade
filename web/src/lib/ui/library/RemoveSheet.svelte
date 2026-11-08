<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { SERVICE_NAME } from '#lib/format.ts';
	import { app } from '#lib/state.svelte.ts';
	import type { Service } from '#lib/types.ts';
	import Glyph from '../Glyph.svelte';
	import Sheet from '../Sheet.svelte';
	import SheetBar from '../SheetBar.svelte';
	import { num, t } from '../i18n.svelte.ts';
	import { catalog as c, kindRank } from './catalog.svelte.ts';

	// Take songs out of collections, chosen per collection. Fades that use them follow on their
	// next run, so they are named before you confirm.
	let { onDone }: { onDone?: () => void } = $props();
	let from = $state<number[]>([]);
	let busy = $state(false);
	const songs = $derived(c.target.map((id) => c.songById.get(id)).filter((s) => !!s));
	const options = $derived.by(() => {
		const counts = new Map<number, number>();
		for (const s of songs) for (const src of new Set(s.in.map(([x]) => x))) counts.set(src, (counts.get(src) ?? 0) + 1);
		return [...counts]
			.map(([id, n]) => ({ src: c.sourceById.get(id)!, n }))
			.filter((o) => o.src)
			.sort((a, b) => c.collator.compare(a.src.service, b.src.service) || kindRank(a.src) - kindRank(b.src) || c.collator.compare(a.src.name, b.src.name));
	});
	let loadedFor = 0;
	$effect(() => {
		if (!c.removeOpen) return;
		const key = c.target.length + (c.target[0] ?? 0) * 7919;
		if (loadedFor === key) return;
		loadedFor = key;
		const only = c.targetService;
		from = options.filter((o) => o.src.editable && (!only || o.src.service === only)).map((o) => o.src.id);
	});
	const count = $derived(options.filter((o) => from.includes(o.src.id)).reduce((a, o) => a + o.n, 0));
	const fades = $derived.by(() => {
		const ids = new Set<number>();
		for (const o of options) if (from.includes(o.src.id)) for (const l of o.src.lines) ids.add(l);
		return [...ids].map((id) => c.lineById.get(id)?.name).filter(Boolean);
	});
	const toggle = (id: number) => (from = from.includes(id) ? from.filter((x) => x !== id) : [...from, id]);
	const valid = $derived(from.length > 0 && !busy && !app.catalogJob);
	async function go() {
		if (!valid) return;
		busy = true;
		if (await c.start('/api/catalog/remove', { song_ids: c.target, source_ids: from })) {
			for (const id of c.target) c.selected.delete(id);
			c.removeOpen = false;
			loadedFor = 0;
			onDone?.();
		}
		busy = false;
	}
</script>

<Sheet bind:open={c.removeOpen} label={t('lib.remove')} onclosed={() => (loadedFor = 0)}>
	{#snippet header()}
		<SheetBar title={t('lib.remove')} leading={{ label: t('common.cancel'), run: () => (c.removeOpen = false) }} />
	{/snippet}
	<div class="wrap">
		<p class="intro t-sub t-2">{songs.length === 1 ? `${songs[0].a} – ${songs[0].t}` : t('n.songs', { n: num(songs.length) })}</p>
		<div class="section">
			<div class="section-head"><span class="caps">{t('lib.from')}</span></div>
			<div class="group">
				{#each options as o (o.src.id)}
					{@const on = from.includes(o.src.id)}
					<button class="cell" style:--inset="52px" onclick={() => toggle(o.src.id)} disabled={!o.src.editable} aria-pressed={on}>
						<ServiceMark service={o.src.service as Service} size={24} title={false} />
						<span class="grow">
							<span class="b clip">{o.src.name}</span>
							<span class="b t-foot t-2">{SERVICE_NAME[o.src.service]}{o.src.editable ? '' : ` · ${t('editor.readOnly')}`}</span>
						</span>
						<span class="value">{num(o.n)}</span>
						<span class="tick" class:hide={!on}><Glyph name="check" size={18} weight={2.6} /></span>
					</button>
				{/each}
			</div>
			{#if fades.length}<p class="section-foot warn">{t('lib.removeFades', { fades: fades.join(', ') })}</p>{/if}
		</div>
		<div class="section">
			<div class="group">
				<button class="cell center" onclick={go} disabled={!valid}><span class="grow danger">{t('lib.removeN', { n: num(count) })}</span></button>
			</div>
			<p class="section-foot">{t('lib.removeHint')}</p>
		</div>
	</div>
</Sheet>

<style>
	.wrap {
		padding: 8px 0 calc(var(--safe-bottom) + 40px);
	}
	.intro {
		padding: 0 20px 18px;
		text-align: center;
	}
	.tick {
		color: var(--red);
	}
	.tick.hide {
		visibility: hidden;
	}
</style>
