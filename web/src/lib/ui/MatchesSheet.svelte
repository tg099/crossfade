<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { SERVICE_NAME } from '#lib/format.ts';
	import { api, del, post, put } from '#lib/api.ts';
	import type { ManualMatch, TrackInfo } from '#lib/types.ts';
	import Cover from './Cover.svelte';
	import Glyph from './Glyph.svelte';
	import HearButton from './HearButton.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import { t } from './i18n.svelte.ts';
	import { mmss, ui } from './ui.svelte.ts';

	// Songs matched by hand. A choice counts everywhere (every fade, friends' too, library adds);
	// here the person who made it looks at it, picks another version or takes it back.
	let items = $state<ManualMatch[] | null>(null);
	let current = $state<ManualMatch | null>(null);
	let candidates = $state<TrackInfo[] | null>(null);
	let picking = $state(false);
	let link = $state('');
	let busy = $state(false);

	$effect(() => {
		if (!ui.matchesOpen) return;
		items = null;
		api<ManualMatch[]>('/api/matches')
			.then((r) => (items = r))
			.catch((e) => {
				items = [];
				ui.fail(e);
			});
	});

	const path = (m: ManualMatch) => `/api/matches/${m.song_id}/${m.service}`;
	const meta = (x: TrackInfo) => [x.album, mmss(x.duration), x.explicit ? 'Explicit' : ''].filter(Boolean).join(' · ');

	function show(m: ManualMatch | null) {
		current = m;
		picking = false;
		candidates = null;
		link = '';
	}

	async function choose() {
		if (!current) return;
		picking = true;
		candidates = null;
		link = '';
		try {
			candidates = await post<TrackInfo[]>(`${path(current)}/candidates`);
		} catch (e) {
			ui.fail(e);
			candidates = [];
		}
	}

	async function take(target: string) {
		const m = current;
		if (!m || busy) return;
		busy = true;
		try {
			const next = await put<ManualMatch>(path(m), { target });
			items = (items ?? []).map((x) => (x.song_id === m.song_id && x.service === m.service ? next : x));
			show(next);
			ui.say(t('matches.changed', { title: next.origin.title }));
		} catch (e) {
			ui.fail(e);
		} finally {
			busy = false;
		}
	}

	function remove() {
		const m = current;
		if (!m) return;
		ui.ask = {
			title: t('matches.removeQ', { title: m.origin.title }),
			message: t('matches.removeMsg', { service: SERVICE_NAME[m.service] }),
			label: t('common.remove'),
			run: async () => {
				try {
					await del(path(m));
					items = (items ?? []).filter((x) => !(x.song_id === m.song_id && x.service === m.service));
					show(null);
					ui.say(t('matches.removed', { title: m.origin.title }), 'trash');
				} catch (e) {
					ui.fail(e);
				}
			}
		};
	}
</script>

<Sheet bind:open={ui.matchesOpen} label={t('matches.title')} onclosed={() => show(null)}>
	{#snippet header()}
		{#if current}
			<SheetBar title={t('matches.one')} leading={{ label: t('common.back'), icon: 'chevronLeft', run: () => show(null) }} />
		{:else}
			<SheetBar title={t('matches.title')} leading={{ label: t('common.back'), icon: 'chevronLeft', run: () => (ui.matchesOpen = false) }} />
		{/if}
	{/snippet}
	<div class="wrap">
		{#if current}
			{@const m = current}
			<div class="origin">
				<span class="o-art">
					<Cover src={m.origin.artwork} seed={m.origin.id} size={88} track={m.origin} song={m.song_id} />
					<span class="o-hear"><HearButton track={m.origin} size={36} onArt /></span>
				</span>
				<div>
					<p class="t-title3">{m.origin.title}</p>
					<p class="t-sub t-2">{m.origin.artist}</p>
					<p class="t-foot t-2 o-meta">{[meta(m.origin), m.fades ? t('n.inFades', { n: m.fades }) : ''].filter(Boolean).join(' · ')}</p>
				</div>
			</div>
			<p class="explain t-foot t-2">{t('matches.explain', { service: SERVICE_NAME[m.service] })}</p>

			<section class="section">
				<div class="section-head">
					<span class="caps head"><ServiceMark service={m.service} size={16} /> {SERVICE_NAME[m.service]}</span>
					{#if m.exact}<span class="tag good">{t('song.same')}</span>{:else}<span class="tag warn">{m.notes.join(', ') || t('song.other')}</span>{/if}
				</div>
				<div class="group">
					<div class="cell" style:--inset="72px">
						<HearButton track={m.track} size={44} />
						<span class="grow">
							<span class="clip b">{m.track.title}</span>
							<span class="t-foot t-2 clip b">{m.track.artist}{meta(m.track) ? ` · ${meta(m.track)}` : ''} · {m.version}</span>
						</span>
					</div>
					{#if !picking}
						<button class="cell" onclick={choose}><Glyph name="refresh" size={20} weight={2} /><span class="grow tintc">{t('song.choose')}</span></button>
					{/if}
				</div>
				{#if picking}
					<div class="choose">
						{#if candidates === null}
							<p class="t-sub t-2 wait">{t('song.searching', { service: SERVICE_NAME[m.service] })}</p>
						{:else}
							<div class="group">
								{#each candidates as c (c.id)}
									<div class="cell" style:--inset="72px">
										<HearButton track={c} size={44} />
										<span class="grow">
											<span class="clip b">{c.title}</span>
											<span class="t-foot t-2 clip b">{c.artist}{meta(c) ? ` · ${meta(c)}` : ''}{c.version ? ` · ${c.version}` : ''}</span>
											{#if c.exact}<span class="tag good">{t('issue.exact')}</span>{:else if c.notes?.length}<span class="tag warn">{c.notes.join(', ')}</span>{/if}
										</span>
										<button class="gbtn glass lq take" disabled={busy} onclick={() => take(c.id)}>{t('song.take')}</button>
									</div>
								{:else}
									<div class="cell"><span class="grow t-2">{t('song.noOthers')}</span></div>
								{/each}
								<form class="cell" onsubmit={(e) => (e.preventDefault(), link.trim() && take(link.trim()))}>
									<Glyph name="link" size={20} weight={2} />
									<input class="grow in" bind:value={link} placeholder={t('issue.linkPh', { service: SERVICE_NAME[m.service] })} autocomplete="off" spellcheck="false" />
									<button class="gbtn glass lq take" disabled={busy || !link.trim()}>{t('song.take')}</button>
								</form>
							</div>
							<button class="text-btn cancel" onclick={() => (picking = false)}>{t('common.cancel')}</button>
						{/if}
					</div>
				{/if}
				<p class="section-foot">{t('matches.keepHint')}</p>
			</section>

			<div class="section">
				<div class="group">
					<button class="cell center" onclick={remove}><span class="grow danger">{t('matches.remove')}</span></button>
				</div>
			</div>
		{:else if items === null}
			<p class="wait t-sub t-2">{t('song.loading')}</p>
		{:else}
			<p class="intro t-sub t-2">{t('matches.intro')}</p>
			{#if items.length}
				<div class="section">
					<ul class="group">
						{#each items as m (`${m.song_id}:${m.service}`)}
							<li>
								<button class="cell" style:--inset="74px" onclick={() => show(m)}>
									<Cover src={m.track.artwork ?? m.origin.artwork} seed={m.track.id} size={44} />
									<span class="grow">
										<span class="b clip">{m.origin.title}</span>
										<span class="b t-foot t-2 clip">{m.origin.artist}</span>
										<span class="t-foot t-2 on"><ServiceMark service={m.service} size={14} /><span class="clip">{m.track.title}{m.version !== 'Original' ? ` · ${m.version}` : ''}</span></span>
									</span>
									<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
								</button>
							</li>
						{/each}
					</ul>
				</div>
			{:else}
				<p class="wait t-sub t-2">{t('matches.empty')}</p>
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
	.on {
		display: flex;
		align-items: center;
		gap: 5px;
		min-width: 0;
		margin-top: 1px;
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
