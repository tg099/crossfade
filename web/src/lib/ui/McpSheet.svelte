<script lang="ts">
	import { api, del, post, put } from '#lib/api.ts';
	import Glyph from './Glyph.svelte';
	import McpActionDetails from './McpActionDetails.svelte';
	import Segmented from './Segmented.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import Switch from './Switch.svelte';
	import { i18n, t, type Key } from './i18n.svelte.ts';
	import { ago, ui } from './ui.svelte.ts';

	type Token = { id: number; name: string; scopes: string[]; expires_at: number; last_used_at: number | null };
	type Action = { id: string; operation: string; state: string; destructive: number; has_result: boolean; error: string | null; created_at: number; expires_at: number };
	type Audit = { connection: string; tool: string; status: string; created_at: number };
	type Overview = { enabled: boolean; url: string; tokens: Token[]; actions: Action[]; audit: Audit[] };
	type Song = { id: number; title: string; artist: string; origin: string | null; annotation: { moods: string[]; genres: string[]; tags: string[]; language: string | null; year: number | null; energy: string | null; evidence: string; confidence: number } | null };
	let data = $state<Overview | null>(null);
	let tab = $state('connections');
	let busy = $state(false);
	let error = $state('');
	let name = $state('');
	let sync = $state(false);
	let manage = $state(false);
	let days = $state(90);
	let secret = $state('');
	let query = $state('');
	let songs = $state<Song[]>([]);
	let selected = $state<Song | null>(null);
	let moods = $state('');
	let genres = $state('');
	let tags = $state('');
	const operations: Record<string, Key> = {
		playlist: 'mcp.op.playlist', sync: 'mcp.op.sync', fade_create: 'mcp.op.fade_create',
		fade_update: 'mcp.op.fade_update', fade_delete: 'mcp.op.fade_delete', fade_pause: 'mcp.op.fade_pause',
		resolve_issue: 'mcp.op.resolve_issue', ignore_issue: 'mcp.op.ignore_issue', replace_version: 'mcp.op.replace_version',
		remove_songs: 'mcp.op.remove_songs', dedupe: 'mcp.op.dedupe', playlists: 'mcp.op.playlists', restore: 'mcp.op.restore'
	};
	const operation = (value: string) => operations[value] ? t(operations[value]) : value;
	const stateLabel = (value: string) => {
		const key = `mcp.state.${value}` as Key;
		return ['pending', 'queued', 'executing', 'ok', 'partial', 'error', 'stopped', 'cancelled', 'expired', 'blocked', 'auth_error'].includes(value) ? t(key) : value;
	};
	const date = (ts: number) => new Date(ts * 1000).toLocaleDateString(i18n.locale);
	async function load() { data = await api<Overview>('/api/mcp'); }
	$effect(() => {
		if (!ui.mcpOpen) return;
		load().catch(ui.fail);
		const timer = setInterval(() => load().catch(() => {}), 5000);
		return () => clearInterval(timer);
	});
	function closed() { secret = ''; error = ''; selected = null; songs = []; query = ''; }
	async function run(work: () => Promise<void>) {
		busy = true; error = '';
		try { await work(); } catch (e) { error = (e as Error).message; } finally { busy = false; }
	}
	function create() {
		run(async () => {
			const r = await post<{ token: string }>('/api/mcp/tokens', { name, days, scopes: ['read', ...(sync ? ['sync'] : []), ...(manage ? ['manage'] : [])] });
			secret = r.token; name = ''; await load();
		});
	}
	async function copy(text: string) {
		try { await navigator.clipboard.writeText(text); ui.say(t('common.copied')); }
		catch { ui.say(t('common.copyFailed'), 'alert'); }
	}
	function revoke(token: Token) {
		ui.ask = { title: t('mcp.revokeQ', { name: token.name }), label: t('mcp.revoke'), run: async () => {
			await del(`/api/mcp/tokens/${token.id}`); secret = ''; await load();
		} };
	}
	function apply(action: Action) {
		const execute = async () => { await post(`/api/mcp/actions/${action.id}/apply`); await load(); ui.say(t('mcp.applied')); };
		if (action.destructive) ui.ask = { title: t('mcp.applyQ', { action: operation(action.operation) }), label: t('mcp.apply'), run: () => run(execute) };
		else run(execute);
	}
	function search() { run(async () => { songs = (await api<{ songs: Song[] }>(`/api/mcp/songs?query=${encodeURIComponent(query)}&limit=100`)).songs; }); }
	function edit(song: Song) {
		selected = song;
		moods = song.annotation?.moods.join(', ') ?? '';
		genres = song.annotation?.genres.join(', ') ?? '';
		tags = song.annotation?.tags.join(', ') ?? '';
	}
	const split = (s: string) => s.split(',').map((v) => v.trim()).filter(Boolean);
	function save() {
		if (!selected) return;
		const song = selected;
		run(async () => {
			await put(`/api/mcp/annotations/${song.id}`, { song_id: song.id, moods: split(moods), genres: split(genres), tags: split(tags),
				language: song.annotation?.language ?? null, year: song.annotation?.year ?? null, energy: song.annotation?.energy ?? null,
				confidence: 1, evidence: t('mcp.personalEvidence') });
			selected = null; ui.say(t('toast.saved'));
			songs = (await api<{ songs: Song[] }>(`/api/mcp/songs?query=${encodeURIComponent(query)}&limit=100`)).songs;
		});
	}
</script>

<Sheet bind:open={ui.mcpOpen} label={t('mcp.title')} onclosed={closed}>
	{#snippet header()}
		<SheetBar title={t('mcp.title')} leading={{ label: t('common.back'), icon: 'chevronLeft', run: () => (ui.mcpOpen = false) }} confirm={{ label: t('mcp.refresh'), run: () => run(load) }} />
	{/snippet}
	<div class="wrap">
		<p class="intro t-sub t-2">{t('mcp.intro')}</p>
		<Segmented bind:value={tab} label={t('mcp.title')} options={[
			{ value: 'connections', label: t('mcp.connections') }, { value: 'actions', label: t('mcp.actions') },
			{ value: 'tags', label: t('mcp.tags') }, { value: 'audit', label: t('mcp.audit') }
		]} />
		{#if error}<p class="danger" role="alert">{error}</p>{/if}
		{#if data}
			{#if !data.enabled}<p class="section-foot">{t('mcp.disabled')}</p>{/if}
			{#if tab === 'connections'}
				<div class="section">
					<p class="caps">{t('mcp.endpoint')}</p>
					<div class="group padded"><code>{data.url}</code><button class="copy" onclick={() => copy(data!.url)} aria-label={t('mcp.copy')}><Glyph name="copy" size={18} /></button></div>
					<p class="section-foot">{t('mcp.setup')}</p>
				</div>
				{#if secret}
					<div class="section">
						<p class="caps">{t('mcp.newToken')}</p>
						<div class="group padded secret"><code>{secret}</code><button class="copy" onclick={() => copy(secret)} aria-label={t('mcp.copy')}><Glyph name="copy" size={18} /></button></div>
						<p class="section-foot">{t('mcp.once')}</p>
						<button class="cell" onclick={() => (secret = '')}>{t('common.done')}</button>
					</div>
				{:else if data.enabled}
					<form class="section group form" onsubmit={(e) => { e.preventDefault(); create(); }}>
						<label>{t('mcp.connectionName')}<input bind:value={name} maxlength="80" required placeholder={t('mcp.namePlaceholder')} /></label>
						<p class="t-foot t-2">{t('mcp.readHint')}</p>
						<div class="option"><span>{t('mcp.syncPermission')}</span><Switch bind:checked={sync} label={t('mcp.syncPermission')} /></div>
						<div class="option"><span>{t('mcp.managePermission')}</span><Switch bind:checked={manage} label={t('mcp.managePermission')} /></div>
						<p class="t-foot t-2">{t('mcp.manageHint')}</p>
						<label>{t('mcp.validFor')}<select bind:value={days}>{#each [7, 30, 90, 365] as n}<option value={n}>{t('mcp.days', { n })}</option>{/each}</select></label>
						<button class="cell tintc center" type="submit" disabled={busy || !name.trim()}>{t('mcp.create')}</button>
					</form>
				{/if}
				<div class="section"><p class="caps">{t('mcp.connections')}</p><ul class="group">
					{#each data.tokens as token (token.id)}
						<li class="connection"><div class="grow"><strong>{token.name}</strong><p class="t-foot t-2">{token.scopes.map((s) => t(`mcp.permission.${s}` as Key)).join(' · ')}</p><p class="t-foot t-2">{t('mcp.expires', { date: date(token.expires_at) })}{token.expires_at < Date.now() / 1000 ? ` · ${t('mcp.state.expired')}` : ''}</p><p class="t-foot t-2">{token.last_used_at ? ago(token.last_used_at) : t('mcp.neverUsed')}</p></div><button class="danger" disabled={busy} onclick={() => revoke(token)}>{t('mcp.revoke')}</button></li>
					{:else}<li class="padded t-2">{t('mcp.noConnections')}</li>{/each}
				</ul></div>
			{:else if tab === 'actions'}
				<p class="section-foot">{t('mcp.actionsHint')}</p>
				{#each data.actions as action (action.id)}
					<div class="section group form">
						<div><strong>{operation(action.operation)}</strong><p class="t-foot t-2">{stateLabel(action.state)} · {ago(action.created_at)}</p></div>
						<McpActionDetails {action} />
						{#if action.operation === 'playlist'}<p class="t-foot t-2">{t('mcp.estimates')}</p>{/if}
						{#if action.error}<p class="danger">{action.error}</p>{/if}
						{#if action.state === 'pending'}
							{#if action.destructive}<p class="t-foot danger">{t('mcp.destructive')}</p>{/if}
							<div class="buttons"><button class="cell center tintc" disabled={busy} onclick={() => apply(action)}>{t('mcp.apply')}</button><button class="cell center" disabled={busy} onclick={() => run(async () => { await del(`/api/mcp/actions/${action.id}`); await load(); })}>{t('common.cancel')}</button></div>
						{/if}
					</div>
				{:else}<p class="section-foot">{t('mcp.noActions')}</p>{/each}
			{:else if tab === 'tags'}
				<p class="section-foot">{t('mcp.tagsHint')}</p>
				<form class="section group form" onsubmit={(e) => { e.preventDefault(); search(); }}><label>{t('mcp.search')}<input bind:value={query} maxlength="300" /></label><button class="cell center tintc" disabled={busy}>{t('mcp.search')}</button></form>
				{#if selected}
					<form class="section group form" onsubmit={(e) => { e.preventDefault(); save(); }}>
						<strong>{selected.title} · {selected.artist}</strong>
						<label>{t('mcp.moods')}<input bind:value={moods} maxlength="1600" placeholder={t('mcp.moodPlaceholder')} /></label>
						<label>{t('mcp.genres')}<input bind:value={genres} maxlength="1600" /></label>
						<label>{t('mcp.tags')}<input bind:value={tags} maxlength="2400" /></label>
						<p class="t-foot t-2">{t('mcp.commas')}</p>
						<div class="buttons"><button class="cell center tintc" disabled={busy}>{t('common.save')}</button><button class="cell center" type="button" onclick={() => (selected = null)}>{t('common.cancel')}</button></div>
						{#if selected.annotation}<button class="cell center danger" type="button" disabled={busy} onclick={() => run(async () => { await del(`/api/mcp/annotations/${selected!.id}`); selected = null; songs = []; })}>{t('mcp.forget')}</button>{/if}
					</form>
				{/if}
				<ul class="group">{#each songs as song (song.id)}<li><button class="cell" onclick={() => edit(song)}><span class="grow"><span class="b">{song.title}</span><span class="b t-foot t-2">{song.artist}{song.annotation?.moods.length ? ` · ${song.annotation.moods.join(', ')}` : ''}</span>{#if song.origin === 'user'}<span class="b t-foot tintc">{t('mcp.corrected')}</span>{/if}</span><Glyph name="chevronRight" size={16} /></button></li>{/each}</ul>
			{:else}
				<ul class="group">{#each data.audit as entry}<li class="padded"><strong>{entry.connection}</strong><p class="t-foot">{entry.tool} · {entry.status}</p><p class="t-foot t-2">{ago(entry.created_at)}</p></li>{:else}<li class="padded t-2">{t('mcp.noAudit')}</li>{/each}</ul>
			{/if}
		{:else}<p class="section-foot">{t('mcp.loading')}</p>{/if}
	</div>
</Sheet>

<style>
	.wrap { padding: 8px 16px calc(var(--safe-bottom) + 32px); }
	.intro { margin: 0 0 20px; }
	.section { margin: 20px 0; }
	.padded { padding: 14px 16px; overflow-wrap: anywhere; }
	.group.padded { display: flex; align-items: center; gap: 10px; }
	code { flex: 1; min-width: 0; overflow-wrap: anywhere; user-select: text; font-size: var(--f-foot); }
	.copy { flex: none; padding: 8px; }
	.form { padding: 16px; display: grid; gap: 14px; }
	label { display: grid; gap: 6px; font-size: var(--f-sub); }
	input, select { width: 100%; border: 1px solid var(--separator); background: var(--fill-3); color: var(--label); border-radius: 10px; padding: 10px 12px; font: inherit; }
	.option, .connection, .buttons { display: flex; align-items: center; gap: 12px; }
	.connection { padding: 14px 16px; }
	.connection p { margin: 4px 0 0; }
	.buttons > button { flex: 1; }
	.secret { border: 1px solid var(--tint); }
</style>
