<script lang="ts">
	import { untrack } from 'svelte';
	import { api, del, post, put } from '#lib/api.ts';
	import { app } from '#lib/state.svelte.ts';
	import Glyph from './Glyph.svelte';
	import McpActionDetails from './McpActionDetails.svelte';
	import Segmented from './Segmented.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import Switch from './Switch.svelte';
	import { i18n, t, type Key } from './i18n.svelte.ts';
	import { ago, copyText, ui } from './ui.svelte.ts';

	// AI assistants (MCP): personal access keys, what assistants proposed (only deletions wait for
	// you), your corrections of how they sort your music, and what they did lately.
	type Token = { id: number; name: string; scopes: string[]; created_at: number; expires_at: number; last_used_at: number | null };
	type Action = { id: string; operation: string; state: string; destructive: number; has_result: boolean; error: string | null; created_at: number; connection: string | null };
	type Audit = { connection: string; tool: string; status: string; created_at: number };
	type Overview = { enabled: boolean; url: string; tokens: Token[]; actions: Action[]; audit: Audit[] };
	type Annotation = { moods: string[]; genres: string[]; tags: string[]; language: string | null; year: number | null; energy: string | null; evidence: string; confidence: number };
	type Song = { id: number; title: string; artist: string; origin: string | null; annotation: Annotation | null };
	type Label = { label: string; songs: number; corrected: number };
	type Labels = { moods: Label[]; genres: Label[]; tags: Label[]; annotated: number; corrected: number; unclassified: number };
	type Tab = 'connections' | 'actions' | 'tags' | 'audit';
	type Facet = 'mood' | 'genre' | 'tag';
	type Page = { kind: 'list' } | { kind: 'new' } | { kind: 'secret'; token: string } | { kind: 'token'; id: number } | { kind: 'song'; song: Song };

	let data = $state<Overview | null>(null);
	let tab = $state<Tab>('connections');
	let page = $state<Page>({ kind: 'list' });
	let busy = $state(false);
	let error = $state('');
	// a new access
	let name = $state('');
	let sync = $state(true);
	let manage = $state(true);
	let days = $state<'7' | '30' | '90' | '365'>('90');
	// tags
	let query = $state('');
	let filter = $state<{ facet: Facet; label: string } | null>(null);
	let songs = $state<Song[] | null>(null);
	let labels = $state<Labels | null>(null);
	let moods = $state('');
	let genres = $state('');
	let tags = $state('');

	const OPS = ['playlist', 'add_songs', 'sync', 'fade_create', 'fade_update', 'fade_delete', 'fade_pause', 'member_role', 'resolve_issue', 'ignore_issue', 'restore_issue', 'replace_version', 'remove_songs', 'dedupe', 'playlists', 'restore'];
	const STATES = ['pending', 'queued', 'executing', 'ok', 'partial', 'error', 'stopped', 'cancelled', 'expired', 'blocked', 'auth_error', 'needs_confirm'];
	const operation = (op: string) => (OPS.includes(op) ? t(`mcp.op.${op}` as Key) : op);
	const stateLabel = (s: string) => (STATES.includes(s) ? t(`mcp.state.${s}` as Key) : s);
	const stateTone = (s: string) => (s === 'ok' ? 'good' : s === 'error' ? 'bad' : ['partial', 'blocked', 'auth_error', 'needs_confirm'].includes(s) ? 'warn' : ['pending', 'queued', 'executing'].includes(s) ? 'accent' : '');
	const date = (ts: number) => new Date(ts * 1000).toLocaleDateString(i18n.locale, { day: 'numeric', month: 'long', year: 'numeric' });
	const scopes = (tk: Token) => tk.scopes.map((s) => t(`mcp.permission.${s}` as Key)).join(' · ');
	const expired = (tk: Token) => tk.expires_at < Date.now() / 1000;

	const waiting = $derived((data?.actions ?? []).filter((a) => a.state === 'pending' && a.destructive));
	const recent = $derived((data?.actions ?? []).filter((a) => !(a.state === 'pending' && a.destructive)));
	const token = $derived.by(() => {
		const p = page;
		return p.kind === 'token' ? data?.tokens.find((x) => x.id === p.id) : undefined;
	});

	async function load() {
		data = await api<Overview>('/api/mcp');
	}
	// opening the sheet, and every change to a proposal (live from the server), reloads it
	$effect(() => {
		if (!ui.mcpOpen) return;
		app.assistantTick;
		untrack(() => load().catch((e) => (data ? undefined : ui.fail(e))));
	});
	// the activity list has no live events of its own: refresh it while it is on screen
	$effect(() => {
		if (!ui.mcpOpen || tab !== 'audit') return;
		const timer = setInterval(() => load().catch(() => {}), 15000);
		return () => clearInterval(timer);
	});
	$effect(() => {
		if (ui.mcpOpen && tab === 'tags') untrack(() => (loadLabels(), findSongs()));
	});
	function closed() {
		page = { kind: 'list' };
		error = '';
		filter = null;
		query = '';
		songs = null;
	}

	async function run(work: () => Promise<void>) {
		busy = true;
		error = '';
		try {
			await work();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}

	// ------------------------------------------------------------ access
	function startNew() {
		name = '';
		sync = true;
		manage = true;
		days = '90';
		error = '';
		page = { kind: 'new' };
	}
	function create() {
		run(async () => {
			const r = await post<{ token: string }>('/api/mcp/tokens', {
				name: name.trim(),
				days: Number(days),
				scopes: ['read', ...(sync ? ['sync'] : []), ...(manage ? ['manage'] : [])]
			});
			page = { kind: 'secret', token: r.token };
			await load();
		});
	}
	function revoke(tk: Token) {
		ui.ask = {
			title: t('mcp.revokeQ', { name: tk.name }),
			message: t('mcp.revokeMsg'),
			label: t('mcp.revoke'),
			run: async () => {
				try {
					await del(`/api/mcp/tokens/${tk.id}`);
					page = { kind: 'list' };
					await load();
					app.loadAssistant();
					ui.say(t('mcp.revoked'), 'trash');
				} catch (e) {
					ui.fail(e);
				}
			}
		};
	}

	// ------------------------------------------------------------ proposals
	async function apply(a: Action) {
		busy = true;
		try {
			await post(`/api/mcp/actions/${encodeURIComponent(a.id)}/apply`);
			ui.say(t('mcp.applied'));
		} catch (e) {
			ui.fail(e);
		} finally {
			busy = false;
			await Promise.all([load().catch(() => {}), app.loadAssistant()]);
		}
	}
	async function discard(a: Action) {
		busy = true;
		try {
			await del(`/api/mcp/actions/${encodeURIComponent(a.id)}`);
			ui.say(t('mcp.discarded'), 'x', 'info');
		} catch (e) {
			ui.fail(e);
		} finally {
			busy = false;
			await Promise.all([load().catch(() => {}), app.loadAssistant()]);
		}
	}

	// ------------------------------------------------------------ tags
	async function loadLabels() {
		try {
			labels = await api<Labels>('/api/mcp/labels');
		} catch {
			labels = null;
		}
	}
	let searchSeq = 0;
	async function findSongs() {
		const seq = ++searchSeq;
		const p = new URLSearchParams({ query: query.trim(), limit: '100' });
		if (filter) p.set(filter.facet, filter.label);
		try {
			const r = await api<{ songs: Song[] }>(`/api/mcp/songs?${p}`);
			if (seq === searchSeq) songs = r.songs; // an older, slower answer never overwrites a newer one
		} catch (e) {
			if (seq === searchSeq) ui.fail(e);
		}
	}
	let typing = 0;
	function typed() {
		clearTimeout(typing);
		typing = window.setTimeout(findSongs, 250);
	}
	function pick(facet: Facet, label: string) {
		filter = filter?.facet === facet && filter.label === label ? null : { facet, label };
		findSongs();
	}
	function edit(song: Song) {
		moods = song.annotation?.moods.join(', ') ?? '';
		genres = song.annotation?.genres.join(', ') ?? '';
		tags = song.annotation?.tags.join(', ') ?? '';
		error = '';
		page = { kind: 'song', song };
	}
	const split = (s: string) => s.split(',').map((v) => v.trim()).filter(Boolean);
	function save() {
		if (page.kind !== 'song') return;
		const song = page.song;
		run(async () => {
			await put(`/api/mcp/annotations/${song.id}`, {
				song_id: song.id,
				moods: split(moods),
				genres: split(genres),
				tags: split(tags),
				language: song.annotation?.language ?? null,
				year: song.annotation?.year ?? null,
				energy: song.annotation?.energy ?? null,
				confidence: 1,
				evidence: t('mcp.personalEvidence')
			});
			page = { kind: 'list' };
			ui.say(t('toast.saved'));
			await Promise.all([findSongs(), loadLabels()]);
		});
	}
	function forget(song: Song) {
		run(async () => {
			await del(`/api/mcp/annotations/${song.id}`);
			page = { kind: 'list' };
			ui.say(t('mcp.forgotten'), 'undo', 'info');
			await Promise.all([findSongs(), loadLabels()]);
		});
	}

	const AUDIT_TONE: Record<string, string> = { ok: 'good', denied: 'warn', error: 'bad' };
</script>

{#snippet actionCard(a: Action)}
	<div class="section">
		<div class="group">
			<div class="cell">
				<span class="tile-icon" style:background={a.destructive ? 'var(--red)' : '#7c5ce6'}><Glyph name={a.destructive ? 'trash' : 'sparkle'} size={17} weight={2.2} /></span>
				<span class="grow">
					<span class="b">{operation(a.operation)}</span>
					<span class="b t-foot t-2 clip">{[a.connection ? t('mcp.by', { name: a.connection }) : '', ago(a.created_at)].filter(Boolean).join(' · ')}</span>
				</span>
				<span class="tag {stateTone(a.state)}">{stateLabel(a.state)}</span>
			</div>
			<McpActionDetails action={a} />
			{#if a.state === 'pending'}
				<button class="cell center" disabled={busy} onclick={() => apply(a)}><span class="grow tintc">{t('mcp.apply')}</span></button>
				<button class="cell center" disabled={busy} onclick={() => discard(a)}><span class="grow t-2">{t('mcp.discard')}</span></button>
			{/if}
		</div>
		{#if a.error}<p class="section-foot danger">{a.error}</p>
		{:else if a.operation === 'playlist'}<p class="section-foot">{t('mcp.estimates')}</p>{/if}
	</div>
{/snippet}

{#snippet chips(facet: Facet, list: Label[], head: Key)}
	{#if list.length}
		<div class="section">
			<div class="section-head"><span class="caps">{t(head)}</span></div>
			<div class="chips">
				{#each list.slice(0, 40) as l (l.label)}
					{@const on = filter?.facet === facet && filter.label === l.label}
					<button class="cell-pill chip" class:on aria-pressed={on} onclick={() => pick(facet, l.label)}>{l.label} <span class="n">{l.songs}</span></button>
				{/each}
			</div>
		</div>
	{/if}
{/snippet}

<Sheet bind:open={ui.mcpOpen} label={t('mcp.title')} onclosed={closed}>
	{#snippet header()}
		{#if page.kind === 'new'}
			<SheetBar title={t('mcp.new')} leading={{ label: t('common.back'), icon: 'chevronLeft', run: () => (page = { kind: 'list' }) }} confirm={{ label: t('mcp.new'), run: create, disabled: busy || !name.trim() || !data?.enabled }} />
		{:else if page.kind === 'secret'}
			<SheetBar title={t('mcp.newToken')} confirm={{ label: t('common.done'), run: () => (page = { kind: 'list' }) }} />
		{:else if page.kind === 'token'}
			<SheetBar title={token?.name ?? t('mcp.connections')} leading={{ label: t('common.back'), icon: 'chevronLeft', run: () => (page = { kind: 'list' }) }} />
		{:else if page.kind === 'song'}
			<SheetBar title={page.song.title} leading={{ label: t('common.back'), icon: 'chevronLeft', run: () => (page = { kind: 'list' }) }} confirm={{ label: t('common.save'), run: save, disabled: busy }} />
		{:else}
			<SheetBar title={t('mcp.title')} leading={{ label: t('common.back'), icon: 'chevronLeft', run: () => (ui.mcpOpen = false) }} />
		{/if}
	{/snippet}
	<div class="wrap">
		{#if !data}
			<p class="section-foot">{t('mcp.loading')}</p>
		{:else if page.kind === 'new'}
			<div class="section">
				<div class="group">
					<label class="cell"><span class="lbl">{t('mcp.connectionName')}</span><input class="in" bind:value={name} maxlength="80" placeholder={t('mcp.namePlaceholder')} autocomplete="off" /></label>
				</div>
			</div>
			<div class="section">
				<div class="section-head"><span class="caps">{t('mcp.permissions')}</span></div>
				<ul class="group">
					<li class="cell">
						<span class="grow"><span class="b">{t('mcp.permission.read')}</span><span class="b t-foot t-2">{t('mcp.readHint')}</span></span>
						<Glyph name="check" size={18} weight={2.6} />
					</li>
					<li class="cell">
						<span class="grow"><span class="b">{t('mcp.permission.sync')}</span><span class="b t-foot t-2">{t('mcp.syncHint')}</span></span>
						<Switch bind:checked={sync} label={t('mcp.permission.sync')} />
					</li>
					<li class="cell">
						<span class="grow"><span class="b">{t('mcp.managePermission')}</span><span class="b t-foot t-2">{t('mcp.manageHint')}</span></span>
						<Switch bind:checked={manage} label={t('mcp.managePermission')} />
					</li>
				</ul>
			</div>
			<div class="section">
				<div class="section-head"><span class="caps">{t('mcp.validFor')}</span></div>
				<div class="pad">
					<Segmented bind:value={days} label={t('mcp.validFor')} options={(['7', '30', '90', '365'] as const).map((n) => ({ value: n, label: t('mcp.days', { n }) }))} />
				</div>
				<p class="section-foot">{t('mcp.createHint')}</p>
			</div>
			{#if error}<p class="section-foot danger" role="alert">{error}</p>{/if}
		{:else if page.kind === 'secret'}
			<div class="section">
				<div class="group">
					<div class="cell"><code class="grow mono key">{page.token}</code><button class="cell-pill" onclick={() => page.kind === 'secret' && copyText(page.token)}>{t('mcp.copy')}</button></div>
				</div>
				<p class="section-foot">{t('mcp.once')}</p>
			</div>
			<div class="section">
				<div class="group">
					<div class="cell"><code class="grow mono key">{data.url}</code><button class="cell-pill" onclick={() => copyText(data!.url)}>{t('mcp.copy')}</button></div>
				</div>
				<p class="section-foot">{t('mcp.setup')}</p>
			</div>
		{:else if page.kind === 'token' && token}
			<div class="section">
				<ul class="group">
					<li class="cell"><span class="grow">{t('mcp.permissions')}</span><span class="value">{scopes(token)}</span></li>
					<li class="cell"><span class="grow">{t('mcp.validFor')}</span><span class="value" class:danger={expired(token)}>{expired(token) ? t('mcp.expired') : t('mcp.expires', { date: date(token.expires_at) })}</span></li>
					<li class="cell"><span class="grow">{t('mcp.audit')}</span><span class="value">{token.last_used_at ? t('mcp.lastUsed', { ago: ago(token.last_used_at) }) : t('mcp.neverUsed')}</span></li>
				</ul>
				<p class="section-foot">{t('mcp.created', { date: date(token.created_at) })}</p>
			</div>
			<div class="section">
				<div class="group"><button class="cell center" onclick={() => revoke(token)}><span class="grow danger">{t('mcp.revoke')}</span></button></div>
			</div>
		{:else if page.kind === 'song'}
			{@const song = page.song}
			<div class="section">
				<div class="group">
					<label class="cell"><span class="lbl">{t('mcp.moods')}</span><input class="in" bind:value={moods} maxlength="1600" placeholder={t('mcp.moodPlaceholder')} autocomplete="off" /></label>
					<label class="cell"><span class="lbl">{t('mcp.genres')}</span><input class="in" bind:value={genres} maxlength="1600" autocomplete="off" /></label>
					<label class="cell"><span class="lbl">{t('mcp.tags')}</span><input class="in" bind:value={tags} maxlength="2400" autocomplete="off" /></label>
				</div>
				<p class="section-foot">{t('mcp.commas')}</p>
				{#if song.annotation && song.origin !== 'user'}
					<p class="section-foot">{t('mcp.assistantSaid', { evidence: song.annotation.evidence, p: Math.round(song.annotation.confidence * 100) })}</p>
				{/if}
			</div>
			{#if song.annotation}
				<div class="section">
					<div class="group"><button class="cell center" disabled={busy} onclick={() => forget(song)}><span class="grow danger">{t('mcp.forget')}</span></button></div>
					<p class="section-foot">{t('mcp.forgetHint')}</p>
				</div>
			{/if}
			{#if error}<p class="section-foot danger" role="alert">{error}</p>{/if}
		{:else}
			<p class="intro t-sub t-2">{t('mcp.intro')}</p>
			<div class="section">
				<Segmented
					bind:value={tab}
					label={t('mcp.title')}
					options={[
						{ value: 'connections', label: t('mcp.connections') },
						{ value: 'actions', label: t('mcp.actions') },
						{ value: 'tags', label: t('mcp.tags') },
						{ value: 'audit', label: t('mcp.audit') }
					]}
				/>
			</div>
			{#if !data.enabled}<p class="section-foot warn spaced">{t('mcp.disabled')}</p>{/if}

			{#if tab === 'connections'}
				<div class="section">
					<div class="section-head"><span class="caps">{t('mcp.endpoint')}</span></div>
					<div class="group">
						<div class="cell"><code class="grow mono key">{data.url}</code><button class="cell-pill" onclick={() => copyText(data!.url)}>{t('mcp.copy')}</button></div>
					</div>
					<p class="section-foot">{t('mcp.setup')}</p>
				</div>
				<div class="section">
					<div class="section-head"><span class="caps">{t('mcp.connections')}</span></div>
					<ul class="group">
						{#each data.tokens as tk (tk.id)}
							<li>
								<button class="cell" style:--inset="58px" onclick={() => (page = { kind: 'token', id: tk.id })}>
									<span class="tile-icon" style:background={expired(tk) ? '#8e8e93' : '#7c5ce6'}><Glyph name="sparkle" size={18} weight={2.2} /></span>
									<span class="grow">
										<span class="b clip">{tk.name}</span>
										<span class="b t-foot t-2 clip">{scopes(tk)} · {expired(tk) ? t('mcp.expired') : tk.last_used_at ? t('mcp.lastUsed', { ago: ago(tk.last_used_at) }) : t('mcp.neverUsed')}</span>
									</span>
									<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
								</button>
							</li>
						{/each}
						{#if data.enabled}
							<li>
								<button class="cell" style:--inset="58px" onclick={startNew}>
									<span class="tile-icon add"><Glyph name="plus" size={18} weight={2.4} /></span>
									<span class="grow tintc">{t('mcp.add')}</span>
								</button>
							</li>
						{/if}
					</ul>
					{#if !data.tokens.length}<p class="section-foot">{t('mcp.noConnections')}</p>{/if}
				</div>
			{:else if tab === 'actions'}
				{#if waiting.length}
					<div class="section">
						<div class="group">
							<button class="cell" style:--inset="58px" onclick={() => ui.showApprovals()}>
								<span class="tile-icon" style:background="var(--red)"><Glyph name="trash" size={17} weight={2.2} /></span>
								<span class="grow">
									<span class="b">{t('mcp.waitingN', { n: waiting.length })}</span>
									<span class="b t-foot t-2">{t('mcp.onHome')}</span>
								</span>
								<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
							</button>
						</div>
					</div>
				{/if}
				{#if recent.length}
					<div class="section-head pushed"><span class="caps">{t('mcp.recent')}</span></div>
					{#each recent as a (a.id)}{@render actionCard(a)}{/each}
				{:else if !waiting.length}
					<p class="section-foot spaced">{t('mcp.noActions')}</p>
				{/if}
				<p class="section-foot spaced">{t('mcp.actionsHint')}</p>
			{:else if tab === 'tags'}
				<p class="section-foot top">{t('mcp.tagsHint')}</p>
				<div class="section">
					<label class="field">
						<Glyph name="search" size={16} weight={2.2} />
						<input bind:value={query} oninput={typed} placeholder={t('mcp.search')} maxlength="300" autocomplete="off" spellcheck="false" type="search" />
					</label>
				</div>
				{#if labels}
					{@render chips('mood', labels.moods, 'mcp.moods')}
					{@render chips('genre', labels.genres, 'mcp.genres')}
					{@render chips('tag', labels.tags, 'mcp.tags')}
				{/if}
				<div class="section">
					{#if filter}<div class="section-head"><span class="caps">{t('mcp.filtered', { label: filter.label })}</span></div>{/if}
					{#if songs === null}
						<p class="section-foot">{t('mcp.loading')}</p>
					{:else if !songs.length}
						<p class="section-foot">{t('mcp.noSongs')}</p>
					{:else}
						<ul class="group">
							{#each songs as song (song.id)}
								<li>
									<button class="cell" onclick={() => edit(song)}>
										<span class="grow">
											<span class="b clip">{song.title}</span>
											<span class="b t-foot t-2 clip">{song.artist}{song.annotation?.moods.length ? ` · ${song.annotation.moods.join(', ')}` : ''}</span>
										</span>
										{#if song.origin === 'user'}<span class="tag accent">{t('mcp.corrected')}</span>{/if}
										<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
									</button>
								</li>
							{/each}
						</ul>
					{/if}
					{#if labels}<p class="section-foot">{t('mcp.labelsFoot', { annotated: labels.annotated, corrected: labels.corrected, unclassified: labels.unclassified })}</p>{/if}
				</div>
			{:else}
				<div class="section">
					{#if data.audit.length}
						<ul class="group">
							{#each data.audit as entry, i (i)}
								<li class="cell">
									<span class="grow">
										<span class="b clip">{entry.connection}</span>
										<span class="b t-foot t-2 clip"><span class="mono">{entry.tool}</span> · {ago(entry.created_at)}</span>
									</span>
									<span class="tag {AUDIT_TONE[entry.status] ?? ''}">{t(`mcp.status.${entry.status}` as Key)}</span>
								</li>
							{/each}
						</ul>
					{:else}
						<p class="section-foot">{t('mcp.noAudit')}</p>
					{/if}
					<p class="section-foot">{t('mcp.auditHint')}</p>
				</div>
			{/if}
		{/if}
	</div>
</Sheet>

<style>
	.wrap {
		padding: 8px 0 calc(var(--safe-bottom) + 40px);
	}
	.intro {
		padding: 0 20px 18px;
	}
	.pad {
		padding: 0 4px;
	}
	.spaced {
		margin: 0 16px 24px;
	}
	.section-head.pushed {
		margin: 0 16px;
	}
	.section-foot.top {
		margin: -4px 16px 14px;
		padding-top: 0;
	}
	.key {
		min-width: 0;
		overflow-wrap: anywhere;
		user-select: all;
		-webkit-user-select: all;
		font-size: var(--f-foot);
	}
	.tile-icon.add {
		background: var(--fill);
		color: var(--tint);
	}
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		padding: 0 4px;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		color: var(--label);
		font-weight: 500;
	}
	.chip .n {
		color: var(--label-2);
		font-size: var(--f-foot);
	}
	.chip.on {
		background: var(--tint-fill);
		color: var(--on-tint);
	}
	.chip.on .n {
		color: inherit;
		opacity: 0.75;
	}
	.lbl {
		width: 7em !important;
	}
</style>
