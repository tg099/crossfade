<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { SERVICE_NAME, trackUrl } from '#lib/format.ts';
	import { api } from '#lib/api.ts';
	import { app } from '#lib/state.svelte.ts';
	import type { Service, TrackInfo } from '#lib/types.ts';
	import Cover from '../Cover.svelte';
	import FadeBadge from '../FadeBadge.svelte';
	import Glyph from '../Glyph.svelte';
	import HearButton from '../HearButton.svelte';
	import Sheet from '../Sheet.svelte';
	import SheetBar from '../SheetBar.svelte';
	import { fadeIcon } from '../fade.ts';
	import { t } from '../i18n.svelte.ts';
	import { mmss, nav } from '../ui.svelte.ts';
	import { catalog as c, SERVICES } from './catalog.svelte.ts';

	// One song of the library: where it is, in which version, in which fades.
	type Ref = { service: Service; ref_id: string; track: TrackInfo | null; version: string | null; notes: string[]; collections: { source_id: number; name: string }[] };
	let detail = $state<{ origin: TrackInfo; refs: Ref[]; lines: { id: number; name: string; color: string }[] } | null>(null);
	let error = $state('');
	const s = $derived(c.song);

	$effect(() => {
		if (!c.songOpen || !s) return;
		const id = s.id;
		detail = null;
		error = '';
		api<NonNullable<typeof detail>>(`/api/catalog/songs/${id}`)
			.then((d) => c.song?.id === id && (detail = d))
			.catch((e) => (error = (e as Error).message));
	});
	const services = $derived(c.services.length ? c.services : SERVICES.filter((x) => app.connected.some((a) => a.service === x)));
	const openFade = (id: number) => {
		c.songOpen = false;
		nav.openFade(id);
	};
</script>

<Sheet bind:open={c.songOpen} label={s?.t ?? ''}>
	{#snippet header()}
		<SheetBar title="" leading={{ label: t('common.close'), run: () => (c.songOpen = false) }} />
	{/snippet}
	{#if s}
		<div class="wrap">
			<div class="hero">
				<span class="art">
					<Cover src={s.c} seed={String(s.id)} size={150} track={c.track(s)} song={s.id} />
					{#if detail}<span class="hear"><HearButton track={detail.origin} size={44} onArt /></span>{/if}
				</span>
				<h2 class="t-title2">{s.t}</h2>
				<p class="t-sub t-2">{s.a}{s.al ? ` · ${s.al}` : ''}{s.d ? ` · ${mmss(s.d)}` : ''}</p>
			</div>

			{#if detail?.lines.length}
				<div class="section">
					<div class="section-head"><span class="caps">{t('fades.title')}</span></div>
					<div class="group">
						{#each detail.lines as l (l.id)}
							{@const f = app.line(l.id)}
							<button class="cell" style:--inset="56px" onclick={() => openFade(l.id)}>
								<FadeBadge icon={f ? fadeIcon(f) : 'note'} color={l.color} size={28} />
								<span class="grow">{l.name}</span>
								<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
							</button>
						{/each}
					</div>
				</div>
			{/if}

			{#each services as svc}
				{@const refs = detail?.refs.filter((r) => r.service === svc) ?? []}
				<div class="section">
					<div class="section-head"><span class="caps head"><ServiceMark service={svc} size={16} /> {SERVICE_NAME[svc]}</span></div>
					<div class="group">
						{#if !detail}
							<div class="cell"><span class="grow t-2">{error || '…'}</span></div>
						{:else if !refs.length}
							<button class="cell" onclick={() => ((c.songOpen = false), c.openAdd([s.id], svc))} disabled={!!app.catalogJob}>
								<span class="grow t-2">{t('lib.notThere')}</span>
								<span class="tintc">{t('lib.addShort')}</span>
							</button>
						{:else}
							{#each refs as r (r.ref_id)}
								<div class="cell" style:--inset="68px">
									{#if r.track}<HearButton track={r.track} size={40} />{/if}
									<span class="grow">
										<a class="b clip ttl" href={trackUrl(svc, r.ref_id, r.track?.url)} target="_blank" rel="noopener">{r.track?.title ?? r.ref_id}</a>
										<span class="b t-foot t-2 clip">{r.collections.map((x) => x.name).join(', ')}{r.version ? ` · ${r.version}` : ''}</span>
										{#if r.notes.length}<span class="tag warn">{r.notes.join(', ')}</span>{/if}
									</span>
								</div>
							{/each}
							<button class="cell" onclick={() => ((c.songOpen = false), c.openRemove([s.id], svc))} disabled={!!app.catalogJob}>
								<span class="grow danger">{t('lib.removeOn', { service: SERVICE_NAME[svc] })}</span>
							</button>
						{/if}
					</div>
				</div>
			{/each}
		</div>
	{/if}
</Sheet>

<style>
	.wrap {
		padding: 0 0 calc(var(--safe-bottom) + 40px);
	}
	.hero {
		display: grid;
		justify-items: center;
		gap: 4px;
		padding: 0 24px 26px;
		text-align: center;
	}
	.art {
		position: relative;
		margin-bottom: 14px;
		border-radius: 14px;
		box-shadow: 0 18px 40px -18px rgb(0 0 0 / 0.45);
	}
	.hear {
		position: absolute;
		right: -10px;
		bottom: -10px;
	}
	.head {
		display: inline-flex;
		align-items: center;
		gap: 6px;
	}
	.ttl {
		color: var(--label);
		text-decoration: none;
	}
	.tag {
		margin-top: 3px;
	}
</style>
