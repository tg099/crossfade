<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { SERVICE_NAME } from '#lib/format.ts';
	import { app } from '#lib/state.svelte.ts';
	import type { Service } from '#lib/types.ts';
	import Glyph from '../Glyph.svelte';
	import Segmented from '../Segmented.svelte';
	import Sheet from '../Sheet.svelte';
	import SheetBar from '../SheetBar.svelte';
	import { num, t } from '../i18n.svelte.ts';
	import { catalog as c, kindRank } from './catalog.svelte.ts';

	// Put songs into a collection on one service. Songs not there yet are searched for first.
	let service = $state<Service | null>(null);
	let target = $state(''); // source id or 'new'
	let name = $state('');
	let busy = $state(false);
	const services = $derived(app.connected.filter((s) => s.status !== 'error').map((s) => s.service));
	const songs = $derived(c.target.map((id) => c.songById.get(id)).filter((s) => !!s));
	const sources = $derived(
		(c.data?.sources ?? []).filter((s) => s.service === service && s.editable).sort((a, b) => kindRank(a) - kindRank(b) || c.collator.compare(a.name, b.name))
	);
	let loadedFor = 0;
	$effect(() => {
		if (!c.addOpen) return;
		const key = c.target.length + (c.target[0] ?? 0) * 7919;
		if (loadedFor === key) return;
		loadedFor = key;
		service = c.targetService ?? (services.includes(service as Service) ? service : (services[0] ?? null));
		name = '';
		pickDefault();
	});
	function pickDefault() {
		const liked = sources.find((s) => s.kind === 'liked') ?? sources[0];
		target = liked ? String(liked.id) : 'new';
	}
	const already = $derived(target && target !== 'new' ? songs.filter((s) => s.in.some(([src]) => src === +target)).length : 0);
	const toSearch = $derived(service ? songs.filter((s) => !c.presence(s).has(service!)).length : 0);
	const valid = $derived(!!service && (target === 'new' ? !!name.trim() : !!target) && !busy && !app.catalogJob);

	async function go() {
		if (!valid) return;
		busy = true;
		const acc = app.services.find((s) => s.service === service)?.account_id;
		const body = target === 'new' ? { song_ids: c.target, account_id: acc, new_playlist: name.trim() } : { song_ids: c.target, source_id: +target };
		if (await c.start('/api/catalog/add', body)) {
			c.addOpen = false;
			loadedFor = 0;
		}
		busy = false;
	}
</script>

<Sheet bind:open={c.addOpen} label={t('lib.add')} onclosed={() => (loadedFor = 0)}>
	{#snippet header()}
		<SheetBar title={t('lib.add')} leading={{ label: t('common.cancel'), run: () => (c.addOpen = false) }} confirm={{ label: t('lib.addShort'), run: go, disabled: !valid }} />
	{/snippet}
	<div class="wrap">
		<p class="intro t-sub t-2">{songs.length === 1 ? `${songs[0].a} – ${songs[0].t}` : t('n.songs', { n: num(songs.length) })}</p>
		{#if !services.length}
			<p class="section-foot pre">{t('lib.noServices')}</p>
		{:else}
			<div class="section">
				<div class="svc">
					<Segmented bind:value={service as Service} label={t('lib.service')} options={services.map((s) => ({ value: s, label: SERVICE_NAME[s] }))} />
				</div>
			</div>
			<div class="section">
				<div class="section-head"><span class="caps">{t('lib.into')}</span></div>
				<div class="group">
					{#each sources as s (s.id)}
						<button class="cell" onclick={() => (target = String(s.id))} aria-pressed={target === String(s.id)}>
							{#if service}<ServiceMark service={service} size={22} title={false} />{/if}
							<span class="grow clip">{s.name}</span>
							<span class="tick" class:hide={target !== String(s.id)}><Glyph name="check" size={18} weight={2.6} /></span>
						</button>
					{/each}
					<button class="cell" onclick={() => (target = 'new')} aria-pressed={target === 'new'}>
						<Glyph name="plus" size={20} weight={2.4} />
						<span class="grow">{t('editor.newPlaylist')}</span>
						<span class="tick" class:hide={target !== 'new'}><Glyph name="check" size={18} weight={2.6} /></span>
					</button>
					{#if target === 'new'}
						<label class="cell"><input class="in" bind:value={name} placeholder={t('lib.playlistName')} maxlength="100" /></label>
					{/if}
				</div>
				<p class="section-foot">
					{toSearch ? t('lib.willSearch', { n: num(toSearch), service: service ? SERVICE_NAME[service] : '' }) : ''}
					{already ? t('lib.already', { n: num(already) }) : ''}
					{!toSearch && !already ? t('lib.addHint') : ''}
				</p>
			</div>
		{/if}
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
	.svc {
		margin: 0 16px;
	}
	.tick {
		color: var(--tint);
	}
	.tick.hide {
		visibility: hidden;
	}
	.pre {
		margin: 0 16px;
	}
</style>
