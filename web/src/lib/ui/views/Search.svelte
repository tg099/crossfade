<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { highlight, search } from '#lib/search.ts';
	import { app } from '#lib/state.svelte.ts';
	import Cover from '../Cover.svelte';
	import FadeBadge from '../FadeBadge.svelte';
	import Glyph from '../Glyph.svelte';
	import Mosaic from '../Mosaic.svelte';
	import Page from '../Page.svelte';
	import { fadeMenu, hold, trackMenu } from '../ctx.ts';
	import { fadeCovers, fadeIcon } from '../fade.ts';
	import { num, t } from '../i18n.svelte.ts';
	import { catalog as c } from '../library/catalog.svelte.ts';
	import { BRAND, nav, ui } from '../ui.svelte.ts';

	// Results only. You type in the search field of the tab bar (phone) or the sidebar (desktop),
	// which is focused the moment you tap it. Songs come from your library.
	$effect(() => {
		c.load();
	});
	$effect(() => {
		app.catalogFinished;
		c.sync();
	});
	const norm = (s: string) => s.toLocaleLowerCase().normalize('NFD').replace(/\p{M}/gu, '');
	const raw = $derived(ui.query.trim());
	const q = $derived(norm(raw));
	const fades = $derived(q ? app.lines.filter((f) => norm(`${f.name} ${f.members.map((m) => m.collection_name).join(' ')}`).includes(q)) : []);
	const hits = $derived(raw ? search(c.data?.songs ?? [], c.index, raw) : null);
	const songs = $derived.by(() => {
		if (!hits) return [];
		const sc = hits.scores;
		return (c.data?.songs ?? [])
			.filter((s) => sc.has(s.id))
			.sort((a, b) => (sc.get(b.id) ?? 0) - (sc.get(a.id) ?? 0))
			.slice(0, 60);
	});
	const total = $derived(hits?.scores.size ?? 0);
	const hl = (text: string) => highlight(text, raw);
	function showAll() {
		c.query = raw;
		nav.select('library');
	}
</script>

<!-- phone: the search field floats at the top while you search (the keyboard never covers it);
     results start right below it -->
<Page title={t('tab.search')} avatar glow={BRAND} large={!ui.phone} chrome={!ui.phone} top={68}>
	{#if !q}
		<div class="empty">
			<span class="ic glass"><Glyph name="search" size={30} weight={1.8} /></span>
			<p class="t-sub t-2">{t('search.empty')}</p>
		</div>
	{:else if !songs.length && !fades.length}
		<p class="empty t-sub t-2">{c.data ? t('search.none', { q: raw }) : t('lib.loading')}</p>
	{:else}
		<div class="res" aria-live="polite">
			{#if fades.length}
				<div class="section">
					<div class="section-head"><span class="caps">{t('fades.title')}</span></div>
					<ul class="group">
						{#each fades as f (f.id)}
							<li>
								<button class="cell" style:--inset="74px" onclick={() => nav.openFade(f.id)} use:hold={() => fadeMenu(f.id)}>
									<Mosaic covers={fadeCovers(f)} seed={f.name} color={f.color} size={44} />
									<span class="grow"><span class="clip b">{f.name}</span><span class="t-foot t-2 b fl"><FadeBadge icon={fadeIcon(f)} color={f.color} size={14} /> {t('fade.one')}</span></span>
								</button>
							</li>
						{/each}
					</ul>
				</div>
			{/if}
			{#if songs.length}
				<div class="section">
					<div class="section-head">
						<span class="caps">{t('lib.songs')}</span>
						{#if total > songs.length}<button class="text-btn t-foot" onclick={showAll}>{t('search.all', { n: num(total) })}</button>{/if}
					</div>
					<ul class="group">
						{#each songs as s (s.id)}
							{@const pres = c.presence(s)}
							<li>
								<button class="cell" style:--inset="74px" onclick={() => c.openSong(s)} use:hold={() => trackMenu(c.track(s))}>
									<Cover src={s.c} seed={String(s.id)} size={44} track={c.track(s)} song={s.id} />
									<span class="grow">
										<span class="clip b">{#each hl(s.t) as seg}{#if seg.hit}<mark>{seg.s}</mark>{:else}{seg.s}{/if}{/each}</span>
										<span class="t-foot t-2 clip b">{#each hl(s.a) as seg}{#if seg.hit}<mark>{seg.s}</mark>{:else}{seg.s}{/if}{/each}</span>
									</span>
									<span class="marks">{#each c.services as svc}{#if pres.has(svc)}<ServiceMark service={svc} size={14} title={false} />{/if}{/each}</span>
								</button>
							</li>
						{/each}
					</ul>
				</div>
			{/if}
		</div>
	{/if}
</Page>

<style>
	.empty {
		display: grid;
		justify-items: center;
		gap: 14px;
		padding: 60px 24px;
		text-align: center;
	}
	.ic {
		display: grid;
		place-items: center;
		width: 72px;
		height: 72px;
		border-radius: 50%;
		color: var(--label-2);
	}
	.res {
		padding-top: 4px;
	}
	.fl {
		display: inline-flex;
		align-items: center;
		gap: 6px;
	}
	.marks {
		display: inline-flex;
		gap: 4px;
	}
	mark {
		background: color-mix(in srgb, var(--yellow, #ffd60a) 45%, transparent);
		color: inherit;
		border-radius: 3px;
	}
</style>
