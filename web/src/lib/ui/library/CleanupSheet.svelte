<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { del, post, put } from '#lib/api.ts';
	import { SERVICE_NAME } from '#lib/format.ts';
	import { app } from '#lib/state.svelte.ts';
	import type { CleanupCopy, CleanupGroup, CleanupPlaylist, CleanupVariant, Release, Service, TrackInfo, YtFormat } from '#lib/types.ts';
	import { SvelteSet } from 'svelte/reactivity';
	import Cover from '../Cover.svelte';
	import Glyph, { type GlyphName } from '../Glyph.svelte';
	import HearButton from '../HearButton.svelte';
	import Segmented from '../Segmented.svelte';
	import Sheet from '../Sheet.svelte';
	import SheetBar from '../SheetBar.svelte';
	import { num, t, type Key } from '../i18n.svelte.ts';
	import { ago, mmss, ui } from '../ui.svelte.ts';
	import { catalog as c } from './catalog.svelte.ts';

	// Tidy up the library: suggestions from the last scan, grouped by kind. Nothing changes on a
	// service until a suggestion is confirmed; duplicates are resolved so that every collection keeps
	// the song (fades see no removal), and the last tidy-up can be undone.
	type Page = 'home' | 'dupes' | 'twice' | 'unusual' | 'playlists' | 'youtube';
	let page = $state<Page>('home');
	const k = $derived(c.cleanup);
	const job = $derived(app.catalogJob);
	const LIST = 60;

	const twiceGroups = $derived((k?.duplicates ?? []).filter((g) => g.confidence === 'same'));
	const likely = $derived((k?.duplicates ?? []).filter((g) => g.confidence === 'likely'));
	const check = $derived((k?.duplicates ?? []).filter((g) => g.confidence === 'check'));
	const twiceCount = $derived(twiceGroups.length + (k?.copies.length ?? 0));
	const yt = $derived(app.services.find((s) => s.service === 'ytm' && s.connected));
	const total = $derived(c.cleanupCount);

	const CLEAN_ACTIONS = ['dedupe', 'swap', 'playlists', 'undo'];
	const problems = $derived.by(() => {
		const f = app.catalogFinished;
		return f && CLEAN_ACTIONS.includes(f.action) && f.status === 'partial' ? ((f.result?.errors as string[]) ?? []) : [];
	});

	function closed() {
		page = 'home';
		picked.clear();
	}

	// ------------------------------------------------------------ helpers
	const src = (id: number) => c.sourceById.get(id);
	const srcName = (id: number) => src(id)?.name ?? '';
	const fadesOf = (id: number) => (k?.fades[String(id)] ?? []).map((f) => f.name);
	function hearable(v: CleanupVariant): TrackInfo {
		return { service: v.service, id: v.ref_id, title: v.title, artist: v.artist, album: v.album, duration: v.duration, isrc: null, url: null, artwork: v.artwork, explicit: v.explicit === null ? null : !!v.explicit, video_type: v.video ? 'MUSIC_VIDEO_TYPE_OMV' : null };
	}
	/** one entry in one collection, to listen into from its cover */
	function refTrack(x: { source_id: number; ref_id: string; title: string; artist: string; artwork: string | null }): TrackInfo {
		const service = c.sourceById.get(x.source_id)?.service ?? '';
		return { service, id: x.ref_id, title: x.title, artist: x.artist, album: '', duration: null, isrc: null, url: null, artwork: x.artwork };
	}
	/** collections that hold several entries of the group, by name */
	function twiceWhere(g: CleanupGroup) {
		const n = new Map<number, number>();
		for (const v of g.variants) for (const s of v.sources) n.set(s, (n.get(s) ?? 0) + 1);
		return [...n].filter(([, x]) => x > 1).map(([s]) => srcName(s)).join(', ');
	}

	// ------------------------------------------------------------ duplicates
	let choice = $state<Record<string, string>>({});
	const chosen = (g: CleanupGroup) => choice[g.key] ?? g.keep;
	function body(groups: CleanupGroup[]) {
		return groups.map((g) => {
			const v = g.variants.find((x) => x.id === chosen(g)) ?? g.variants[0];
			return { songs: g.songs, keep: [v.service, v.ref_id] };
		});
	}
	// album or single: which release the suggestion keeps (saved per user)
	let release = $state<Release>('album');
	$effect(() => {
		if (k?.release) release = k.release;
	});
	async function setRelease(r: Release) {
		if (r === k?.release) return;
		try {
			await put('/api/auth/me', { release_pref: r });
			if (app.user) app.user.release_pref = r;
			choice = {};
			await c.loadCleanup();
		} catch (e) {
			ui.fail(e);
			release = k?.release ?? 'album';
		}
	}
	const dedupe = (groups: CleanupGroup[], copies: CleanupCopy[] = []) =>
		c.start('/api/catalog/cleanup/dedupe', { groups: body(groups), copies: copies.map((x) => [x.source_id, x.ref_id]) });

	// ------------------------------------------------------------ unusual versions
	const picked = new SvelteSet<number>();
	function removePicked() {
		c.openRemove([...picked]);
		picked.clear();
	}

	// ------------------------------------------------------------ playlists
	const deletable = (id: number) => {
		const s = src(id);
		return !!s && s.kind === 'playlist' && s.editable && !s.lines.length;
	};
	function plAction(p: CleanupPlaylist): { kind: 'delete' | 'merge'; from: number; into?: number } | null {
		if (p.kind === 'empty') return deletable(p.sources[0]) ? { kind: 'delete', from: p.sources[0] } : null;
		const [a, b] = p.kind === 'same_name' ? [...p.sources].sort((x, y) => (src(x)?.item_count ?? 0) - (src(y)?.item_count ?? 0)) : p.sources;
		if (p.kind === 'identical' || p.kind === 'subset') {
			if (deletable(a)) return { kind: 'delete', from: a };
			if (p.kind === 'identical' && deletable(b)) return { kind: 'delete', from: b };
			return null;
		}
		return deletable(a) && src(b)?.editable ? { kind: 'merge', from: a, into: b } : null;
	}
	function blocked(p: CleanupPlaylist) {
		const s = src(p.sources[0]);
		if (!s) return '';
		if (s.lines.length) return t('clean.pl.inFade', { fades: fadesOf(s.id).join(', ') });
		if (!s.editable) return t('clean.pl.locked', { name: s.name });
		return '';
	}
	function runPl(a: NonNullable<ReturnType<typeof plAction>>) {
		const from = src(a.from)!;
		ui.ask =
			a.kind === 'delete'
				? {
						title: t('clean.pl.askDelete'),
						message: t('clean.pl.askDeleteMsg', { name: from.name, service: SERVICE_NAME[from.service] }),
						label: t('clean.pl.delete', { name: from.name }),
						run: () => c.start('/api/catalog/cleanup/playlists', { delete: [a.from] })
					}
				: {
						title: t('clean.pl.askMerge'),
						message: `${t('clean.pl.mergeSub', { from: from.name })} ${t('clean.pl.askDeleteMsg', { name: from.name, service: SERVICE_NAME[from.service] })}`,
						label: t('clean.pl.merge', { from: from.name, into: srcName(a.into!) }),
						run: () => c.start('/api/catalog/cleanup/playlists', { merge: [[a.from, a.into]] })
					};
	}

	// ------------------------------------------------------------ YouTube
	let format = $state<YtFormat>('song');
	$effect(() => {
		if (yt?.yt_format) format = yt.yt_format;
	});
	async function setFormat(f: YtFormat) {
		if (!yt || f === yt.yt_format) return;
		try {
			await put('/api/services/ytm/format', { format: f });
			await Promise.all([app.loadServices(), c.loadCleanup()]);
		} catch (e) {
			ui.fail(e);
			format = yt.yt_format ?? 'song';
		}
	}
	const swap = () => c.start('/api/catalog/cleanup/swap', { items: (k?.youtube?.candidates ?? []).map((x) => [x.source_id, x.ref_id]) });

	// ------------------------------------------------------------ misc
	const undo = () => c.start('/api/catalog/undo');
	async function showDismissed() {
		await del('/api/catalog/cleanup/dismissed').catch(ui.fail);
		await c.loadCleanup();
	}

	const TITLES: Record<Page, Key> = {
		home: 'clean.title',
		dupes: 'clean.cat.dupes',
		twice: 'clean.cat.twice',
		unusual: 'clean.cat.unusual',
		playlists: 'clean.cat.playlists',
		youtube: 'clean.cat.youtube'
	};
</script>

{#snippet releasePick()}
	<div class="section">
		<div class="section-head"><span class="caps">{t('clean.release.label')}</span></div>
		<div class="seg">
			<Segmented
				bind:value={() => release, (v) => ((release = v), setRelease(v))}
				label={t('clean.release.label')}
				options={[
					{ value: 'album', label: t('clean.release.album') },
					{ value: 'single', label: t('clean.release.single') }
				]}
			/>
		</div>
		<p class="section-foot">{t('clean.release.foot')}</p>
	</div>
{/snippet}

<Sheet bind:open={c.cleanupOpen} label={t('clean.title')} onclosed={closed}>
	{#snippet header()}
		<SheetBar
			title={t(TITLES[page])}
			leading={page === 'home' ? { label: t('common.close'), run: () => (c.cleanupOpen = false) } : { label: t('common.back'), icon: 'chevronLeft', run: () => (page = 'home') }}
		/>
	{/snippet}
	<div class="wrap">
		{#if !k}
			<p class="intro t-sub t-2">{t('lib.loading')}</p>
		{:else if page === 'home'}
			{#if total === 0 && !yt}
				<div class="hero">
					<span class="hero-ic glass"><Glyph name="sparkle" size={30} weight={1.9} /></span>
					<p class="t-headline">{t('clean.allClean')}</p>
					<p class="t-sub t-2">{t('clean.allCleanText')}</p>
				</div>
			{:else}
				<p class="intro t-sub t-2">{t('clean.intro')}</p>
			{/if}

			{#if problems.length}
				<div class="section">
					<div class="section-head"><span class="caps">{t('clean.problems')}</span></div>
					<div class="group">
						{#each problems.slice(0, 6) as p}<div class="cell"><span class="grow t-sub">{p}</span></div>{/each}
					</div>
				</div>
			{/if}

			{#if k.undo}
				<div class="section">
					<div class="group">
						<button class="cell" style:--inset="58px" onclick={undo} disabled={!!job}>
							<span class="tile-icon" style:background="#8e8e93"><Glyph name="undo" size={17} weight={2.2} /></span>
							<span class="grow">
								<span class="b tintc">{t('clean.undo')}</span>
								<span class="b t-foot t-2">{t('clean.undoSub', { n: k.undo.n, ago: ago(k.undo.at) })}</span>
							</span>
						</button>
					</div>
				</div>
			{/if}

			<div class="section">
				<div class="group">
					{#snippet cat(p: Page, icon: GlyphName, color: string, title: Key, sub: string, n: number)}
						<button class="cell" style:--inset="58px" onclick={() => (page = p)} disabled={!n && p !== 'youtube'}>
							<span class="tile-icon" style:background={color}><Glyph name={icon} size={17} weight={2.2} /></span>
							<span class="grow">
								<span class="b">{t(title)}</span>
								<span class="b t-foot t-2 clip">{sub}</span>
							</span>
							<span class="value">{n ? num(n) : '–'}</span>
							<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
						</button>
					{/snippet}
					{@render cat('dupes', 'copy', '#5e5ce6', 'clean.cat.dupes', t('clean.cat.dupesSub'), likely.length + check.length)}
					{@render cat('twice', 'refresh', 'var(--orange)', 'clean.cat.twice', t('clean.cat.twiceSub'), twiceCount)}
					{@render cat('unusual', 'ear', '#ff375f', 'clean.cat.unusual', t('clean.cat.unusualSub'), k.unusual.length)}
					{@render cat('playlists', 'library', '#30b0c7', 'clean.cat.playlists', t('clean.cat.playlistsSub'), k.playlists.length)}
					{#if yt && k.youtube}
						{@render cat('youtube', 'play', '#ff3b30', 'clean.cat.youtube', t(`clean.cat.youtubeSub.${k.youtube.format}`), k.youtube.candidates.length)}
					{/if}
				</div>
			</div>

			{#if k.dismissed}
				<div class="center-row"><button class="text-btn t-sub" onclick={showDismissed}>{t('clean.showDismissed', { n: k.dismissed })}</button></div>
			{/if}

			<!-- ======================================================= possible duplicates -->
		{:else if page === 'dupes'}
			{@render releasePick()}
			{#each [['likely', likely, 'clean.likely', 'clean.likelyFoot'], ['check', check, 'clean.check', 'clean.checkFoot']] as [id, groups, head, foot] (id)}
				{@const gs = groups as CleanupGroup[]}
				{#if gs.length}
					<div class="section">
						<div class="section-head"><span class="caps">{t(head as Key)}</span></div>
						<div class="bulk">
							<p class="t-foot t-2">{t(foot as Key)}</p>
							{#if id === 'likely' && gs.length > 1}<button class="pb glass tint lq pb-small" onclick={() => dedupe(gs)} disabled={!!job}>{t('clean.applyAll', { n: gs.length })}</button>{/if}
						</div>
						{#each gs.slice(0, LIST) as g (g.key)}
							{@const removes = g.plans[chosen(g)]?.length ?? 0}
							<div class="group dupe">
								<div class="cell head" style:--inset="68px">
									<Cover src={g.artwork} seed={g.key} size={44} track={hearable(g.variants[0])} />
									<span class="grow">
										<span class="b clip">{g.title}</span>
										<span class="b t-foot t-2 clip">{g.artist}</span>
									</span>
								</div>
								{#each g.variants as v (v.id)}
									{@const on = chosen(g) === v.id}
									<button class="cell variant" style:--inset="68px" onclick={() => (choice[g.key] = v.id)} aria-pressed={on} aria-label={t('clean.keepThis')}>
										<span class="radio" class:on aria-hidden="true">{#if on}<Glyph name="check" size={12} weight={3.2} />{/if}</span>
										<HearButton track={hearable(v)} size={30} />
										<span class="grow">
											<span class="b clip">
												<ServiceMark service={v.service as Service} size={14} title={false} />
												{v.video ? t('clean.video') : v.version}{v.release ? ` · ${t(`clean.release.${v.release}`)}` : ''}{v.explicit ? ' · E' : ''}{v.duration ? ` · ${mmss(v.duration)}` : ''}
											</span>
											<span class="b t-foot t-2 clip">{[v.album, ...v.sources.map(srcName)].filter(Boolean).join(' · ')}</span>
										</span>
									</button>
								{/each}
								<div class="foot">
									<p class="t-foot t-2 note">{t('clean.removes', { n: removes })}</p>
									<span class="grow"></span>
									<button class="text-btn t-sub" onclick={() => c.dismiss(g.key)}>{t('clean.different')}</button>
									<button class="pb glass tint lq pb-small" onclick={() => dedupe([g])} disabled={!!job}>{t('clean.apply')}</button>
								</div>
							</div>
						{/each}
						{#if gs.length > LIST}<p class="section-foot">{t('clean.more', { n: num(gs.length - LIST) })}</p>{/if}
					</div>
				{/if}
			{/each}
			<p class="section-foot">{t('clean.safe')}</p>

			<!-- ======================================================= double entries -->
		{:else if page === 'twice'}
			{@render releasePick()}
			<div class="section">
				<div class="section-head"><span class="caps">{t('clean.cat.twiceSub')}</span></div>
				<div class="bulk">
					<p class="t-foot t-2">{t('clean.twiceFoot')}</p>
					<button class="pb glass tint lq pb-small" onclick={() => dedupe(twiceGroups, k.copies)} disabled={!!job || !twiceCount}>{t('clean.fixAll', { n: twiceCount })}</button>
				</div>
				<div class="group">
					{#each k.copies.slice(0, LIST) as x (x.source_id + x.ref_id)}
						<div class="cell" style:--inset="68px">
							<Cover src={x.artwork} seed={x.ref_id} size={44} track={refTrack(x)} />
							<span class="grow">
								<span class="b clip">{x.title}</span>
								<span class="b t-foot t-2 clip">{x.artist} · {t('clean.twiceIn', { n: x.copies, where: srcName(x.source_id) })}</span>
							</span>
						</div>
					{/each}
					{#each twiceGroups.slice(0, LIST) as g (g.key)}
						<div class="cell" style:--inset="68px">
							<Cover src={g.artwork} seed={g.key} size={44} track={hearable(g.variants[0])} />
							<span class="grow">
								<span class="b clip">{g.title}</span>
								<span class="b t-foot t-2 clip">{g.artist} · {t('clean.bothIn', { where: twiceWhere(g) })}</span>
								<span class="b t-foot t-2 clip">{g.variants.map((v) => (v.video ? t('clean.video') : v.release ? t(`clean.release.${v.release}`) : v.version)).join(' + ')}</span>
							</span>
						</div>
					{/each}
				</div>
				<p class="section-foot">{t('clean.safe')}</p>
			</div>

			<!-- ======================================================= unusual versions -->
		{:else if page === 'unusual'}
			<p class="intro t-sub t-2">{t('clean.unusualIntro')}</p>
			<div class="section">
				<div class="group">
					{#each k.unusual as u (u.key)}
						{@const on = picked.has(u.song_id)}
						<div class="cell" style:--inset="104px">
							<button class="pick" onclick={() => (on ? picked.delete(u.song_id) : picked.add(u.song_id))} aria-pressed={on} aria-label={u.title}>
								<span class="radio" class:on aria-hidden="true">{#if on}<Glyph name="check" size={12} weight={3.2} />{/if}</span>
								<Cover src={u.artwork} seed={String(u.song_id)} size={44} song={u.song_id} />
								<span class="grow">
									<span class="b clip">{u.title}</span>
									<span class="b t-foot t-2 clip">{u.artist} · {u.sources.map(srcName).join(', ')}</span>
								</span>
								{#each u.tags as tag}<span class="tag warn">{tag}</span>{/each}
							</button>
							<button class="text-btn t-sub" onclick={() => (picked.delete(u.song_id), c.dismiss(u.key))}>{t('clean.keep')}</button>
						</div>
					{/each}
				</div>
			</div>
			<div class="section">
				<div class="group">
					<button class="cell center" onclick={removePicked} disabled={!picked.size || !!job}><span class="grow danger">{t('clean.removeSelected', { n: picked.size })}</span></button>
				</div>
			</div>

			<!-- ======================================================= playlists -->
		{:else if page === 'playlists'}
			{#each k.playlists as p (p.key)}
				{@const a = plAction(p)}
				<div class="section">
					<div class="section-head"><span class="caps">{t(`clean.pl.${p.kind}` as Key)}</span></div>
					<div class="group">
						{#each p.sources as id}
							{@const s = src(id)}
							{#if s}
								<div class="cell" style:--inset="52px">
									<ServiceMark service={s.service} size={24} title={false} />
									<span class="grow">
										<span class="b clip">{s.name}</span>
										<span class="b t-foot t-2 clip">{t('clean.pl.songs', { n: s.item_count ?? 0 })}{fadesOf(id).length ? ` · ${fadesOf(id).join(', ')}` : ''}</span>
									</span>
								</div>
							{/if}
						{/each}
						{#if a}
							<button class="cell center" onclick={() => runPl(a)} disabled={!!job}>
								<span class="grow" class:danger={a.kind === 'delete'} class:tintc={a.kind === 'merge'}>
									{a.kind === 'delete' ? t('clean.pl.delete', { name: srcName(a.from) }) : t('clean.pl.merge', { from: srcName(a.from), into: srcName(a.into!) })}
								</span>
							</button>
						{/if}
						<button class="cell center" onclick={() => c.dismiss(p.key)}><span class="grow tintc">{t('clean.keep')}</span></button>
					</div>
					{#if p.kind === 'similar' && p.only}
						<p class="section-foot">{t('clean.pl.similarSub', { common: p.common ?? 0, a: p.only[0], b: p.only[1] })}</p>
					{:else if a?.kind === 'merge'}
						<p class="section-foot">{t('clean.pl.mergeSub', { from: srcName(a.from) })}</p>
					{:else if !a && blocked(p)}
						<p class="section-foot warn">{blocked(p)}</p>
					{/if}
				</div>
			{/each}

			<!-- ======================================================= YouTube: video or song -->
		{:else if page === 'youtube' && k.youtube}
			{@const y = k.youtube}
			<p class="intro t-sub t-2">{t('clean.yt.intro')}</p>
			<div class="section">
				<div class="section-head"><span class="caps">{t('clean.yt.label')}</span></div>
				<div class="seg">
					<Segmented
						bind:value={() => format, (v) => ((format = v), setFormat(v))}
						label={t('clean.yt.label')}
						options={[
							{ value: 'song', label: t('clean.yt.song') },
							{ value: 'video', label: t('clean.yt.video') }
						]}
					/>
				</div>
				<p class="section-foot">{t('services.yt.formatHint')}</p>
			</div>
			<div class="section">
				<div class="section-head">
					<span class="caps">{y.candidates.length ? t(`clean.yt.candidates.${y.format}`, { n: num(y.candidates.length) }) : t('clean.yt.none')}</span>
				</div>
				{#if y.candidates.length}
					<div class="group">
						{#each y.candidates.slice(0, LIST) as x (x.source_id + x.ref_id)}
							<div class="cell" style:--inset="68px">
								<Cover src={x.artwork} seed={x.ref_id} size={44} track={refTrack(x)} />
								<span class="grow">
									<span class="b clip">{x.title}</span>
									<span class="b t-foot t-2 clip">{x.artist} · {srcName(x.source_id)}</span>
								</span>
							</div>
						{/each}
						<button class="cell center" onclick={swap} disabled={!!job}>
							<span class="grow tintc">{t(y.format === 'video' ? 'clean.yt.toVideo' : 'clean.yt.toSong', { n: num(y.candidates.length) })}</span>
						</button>
					</div>
					{#if y.candidates.length > LIST}<p class="section-foot">{t('clean.more', { n: num(y.candidates.length - LIST) })}</p>{/if}
				{/if}
				{#if y.without}<p class="section-foot">{t('clean.yt.without', { n: num(y.without) })}</p>{/if}
				<p class="section-foot">{t('clean.yt.foot')}</p>
			</div>
		{/if}
	</div>
</Sheet>

<style>
	.wrap {
		padding: 0 0 calc(var(--safe-bottom) + 40px);
	}
	.intro {
		padding: 0 20px 18px;
		text-align: center;
	}
	.hero {
		display: grid;
		justify-items: center;
		gap: 6px;
		padding: 8px 24px 24px;
		text-align: center;
	}
	.hero-ic {
		display: grid;
		place-items: center;
		width: 64px;
		height: 64px;
		margin-bottom: 8px;
		border-radius: 50%;
	}
	.section-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
	}
	.bulk {
		display: flex;
		align-items: center;
		gap: 12px;
		margin: -2px 0 12px;
		padding: 0 16px;
	}
	.bulk p {
		flex: 1;
		min-width: 0;
		margin: 0;
	}
	.center-row {
		display: flex;
		justify-content: center;
	}
	.group.dupe + .group.dupe {
		margin-top: 14px;
	}
	.foot {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 4px 10px;
		padding: 10px 12px 10px 16px;
		border-top: 0.5px solid var(--separator);
	}
	.foot .note {
		flex: 1 0 100%;
		margin: 0;
	}
	.foot .grow {
		flex: 1;
	}
	.variant .b {
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.radio {
		display: grid;
		place-items: center;
		flex: none;
		width: 22px;
		height: 22px;
		border-radius: 50%;
		border: 1.5px solid var(--label-3);
		color: var(--on-tint);
	}
	.radio.on {
		border-color: transparent;
		background: var(--tint-fill);
	}
	.pick {
		display: flex;
		align-items: center;
		gap: 12px;
		flex: 1;
		min-width: 0;
		text-align: start;
		color: inherit;
	}
	.pick .grow {
		flex: 1;
		min-width: 0;
	}
	.seg {
		padding: 0 4px;
	}
</style>
