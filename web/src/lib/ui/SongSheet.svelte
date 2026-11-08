<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { SERVICE_NAME } from '#lib/format.ts';
	import { api, post } from '#lib/api.ts';
	import { app } from '#lib/state.svelte.ts';
	import type { SongDetail, TrackInfo, VersionStop } from '#lib/types.ts';
	import Cover from './Cover.svelte';
	import Glyph from './Glyph.svelte';
	import HearButton from './HearButton.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import { t } from './i18n.svelte.ts';
	import { tintVars } from './tint.ts';
	import { mmss, ui } from './ui.svelte.ts';

	// One song in a fade: which version sits in each collection, compared with the version it came
	// in as; listen to alternatives and swap the one that doesn't fit.
	const fade = $derived(ui.song ? app.line(ui.song.fadeId) : null);
	/** only the person a collection belongs to swaps versions in it */
	const mine = (memberId: number) => fade?.members.find((m) => m.id === memberId)?.mine ?? true;
	let detail = $state<SongDetail | null>(null);
	let picking = $state<number | null>(null);
	let candidates = $state<TrackInfo[] | null>(null);
	let link = $state('');
	let busy = $state(false);

	$effect(() => {
		const s = ui.song;
		if (!ui.songOpen || !s) return;
		detail = null;
		picking = null;
		api<SongDetail>(`/api/lines/${s.fadeId}/songs/${s.songId}`)
			.then((d) => (detail = d))
			.catch(ui.fail);
	});

	async function pick(stop: VersionStop) {
		if (!ui.song) return;
		picking = stop.member_id;
		candidates = null;
		link = '';
		try {
			candidates = await post<TrackInfo[]>(`/api/lines/${ui.song.fadeId}/songs/${ui.song.songId}/candidates`, { member_id: stop.member_id });
		} catch (e) {
			ui.fail(e);
			candidates = [];
		}
	}

	async function replace(stop: VersionStop, target: string) {
		if (!ui.song) return;
		busy = true;
		try {
			detail = await post<SongDetail>(`/api/lines/${ui.song.fadeId}/songs/${ui.song.songId}/replace`, { member_id: stop.member_id, target });
			ui.say(t('song.swapped', { name: stop.collection_name }));
			picking = null;
			ui.songChanged++;
		} catch (e) {
			ui.fail(e);
		} finally {
			busy = false;
		}
	}
	const meta = (x: TrackInfo) => [x.album, mmss(x.duration), x.explicit ? 'Explicit' : ''].filter(Boolean).join(' · ');
</script>

<Sheet bind:open={ui.songOpen} label={t('song.versions')} class="tinted" style={fade ? tintVars(fade.color) : ''}>
	{#snippet header()}
		<SheetBar title={t('song.versions')} leading={{ label: t('common.close'), run: () => (ui.songOpen = false) }} />
	{/snippet}
	{#if detail}
		<div class="wrap">
			<div class="origin">
				<span class="o-art">
					<Cover src={detail.origin.artwork} seed={detail.origin.id} size={88} track={detail.origin} song={detail.song_id} />
					<span class="o-hear"><HearButton track={detail.origin} size={36} onArt /></span>
				</span>
				<div>
					<p class="t-title3">{detail.origin.title}</p>
					<p class="t-sub t-2">{detail.origin.artist}</p>
					<p class="t-foot t-2 o-meta">{meta(detail.origin)} <span class="tag">{detail.origin_version}</span></p>
				</div>
			</div>
			<p class="explain t-foot t-2">{t('song.explain')}</p>

			{#each detail.stops as s (s.member_id)}
				<section class="section">
					<div class="section-head">
						<span class="caps head"><ServiceMark service={s.service} size={16} /> {s.collection_name}</span>
						{#if s.track}
							{#if s.exact}<span class="tag good">{t('song.same')}</span>{:else}<span class="tag warn">{s.notes.join(', ') || t('song.other')}</span>{/if}
						{/if}
					</div>
					<div class="group">
						{#if s.track}
							<div class="cell" style:--inset="72px">
								<HearButton track={s.track} size={44} />
								<span class="grow">
									<span class="clip b">{s.track.title}</span>
									<span class="t-foot t-2 clip b">{meta(s.track)}{s.version ? ` · ${s.version}` : ''}</span>
								</span>
							</div>
							{#if picking !== s.member_id && mine(s.member_id)}
								<button class="cell" onclick={() => pick(s)}><Glyph name="refresh" size={20} weight={2} /><span class="grow tintc">{t('song.choose')}</span></button>
							{/if}
						{:else}
							<div class="cell"><span class="grow t-2">{s.state === 'excluded' ? t('song.excluded') : s.state === 'pending' ? t('song.pending') : t('song.missing')}</span></div>
						{/if}
					</div>
					{#if picking === s.member_id}
						<div class="choose">
							{#if candidates === null}
								<p class="t-sub t-2 wait">{t('song.searching', { service: SERVICE_NAME[s.service] })}</p>
							{:else}
								<div class="group">
									{#each candidates.filter((c) => c.id !== s.track?.id) as c (c.id)}
										<div class="cell" style:--inset="72px">
											<HearButton track={c} size={44} />
											<span class="grow">
												<span class="clip b">{c.title}</span>
												<span class="t-foot t-2 clip b">{meta(c)}{c.version ? ` · ${c.version}` : ''}</span>
												{#if c.exact}<span class="tag good">{t('issue.exact')}</span>{:else if c.notes?.length}<span class="tag warn">{c.notes.join(', ')}</span>{/if}
											</span>
											<button class="gbtn glass lq take" disabled={busy} onclick={() => replace(s, c.id)}>{t('song.take')}</button>
										</div>
									{:else}
										<div class="cell"><span class="grow t-2">{t('song.noOthers')}</span></div>
									{/each}
									<form class="cell" onsubmit={(e) => (e.preventDefault(), link.trim() && replace(s, link.trim()))}>
										<Glyph name="link" size={20} weight={2} />
										<input class="grow in" bind:value={link} placeholder={t('issue.linkPh', { service: SERVICE_NAME[s.service] })} autocomplete="off" />
										<button class="gbtn glass lq take" disabled={busy || !link.trim()}>{t('song.take')}</button>
									</form>
								</div>
								<button class="text-btn cancel" onclick={() => (picking = null)}>{t('common.cancel')}</button>
							{/if}
						</div>
					{/if}
				</section>
			{/each}
		</div>
	{:else}
		<p class="wait t-sub t-2">{t('song.loading')}</p>
	{/if}
</Sheet>

<style>
	.wrap {
		padding: 8px 0 40px;
	}
	.origin {
		display: flex;
		gap: 16px;
		align-items: center;
		padding: 8px 20px 12px;
	}
	.o-art {
		position: relative;
		flex: none;
	}
	.o-hear {
		position: absolute;
		right: -8px;
		bottom: -8px;
	}
	.o-meta {
		margin-top: 4px;
	}
	.explain {
		padding: 0 20px 22px;
	}
	.head {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 0 !important;
	}
	.b {
		display: block;
	}
	.grow {
		flex: 1;
		min-width: 0;
	}
	.choose {
		margin-top: 10px;
	}
	.take {
		height: 34px;
		font-size: var(--f-sub);
	}
	.in {
		border: 0;
		outline: none;
		background: none;
		color: var(--label);
		font: inherit;
		min-width: 0;
	}
	.cancel {
		margin: 4px 8px 0;
	}
	.wait {
		padding: 30px 24px;
		text-align: center;
	}
	.tag {
		margin-top: 3px;
	}
</style>
