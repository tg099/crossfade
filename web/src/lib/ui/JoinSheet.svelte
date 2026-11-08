<script lang="ts">
	import { SERVICE_NAME } from '#lib/format.ts';
	import { api, del, post } from '#lib/api.ts';
	import { app } from '#lib/state.svelte.ts';
	import type { CollectionInfo, Friends, Kind, Line, Role, Service } from '#lib/types.ts';
	import FadeBadge from './FadeBadge.svelte';
	import Glyph from './Glyph.svelte';
	import Segmented from './Segmented.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import { fadeIcon } from './fade.ts';
	import { t } from './i18n.svelte.ts';
	import { tintVars } from './tint.ts';
	import { nav, ui } from './ui.svelte.ts';

	// Answering an invitation into a friend's fade. Joining is the confirmation: you pick one of
	// your own collections here, and only then does it (and the fade's content) become shared.
	const inv = $derived(ui.joining);
	const usable = $derived(app.connected.filter((s) => s.status !== 'error').map((s) => s.service));

	let svc = $state<Service>('ytm');
	let picked = $state<{ kind: Kind; id: string; name: string; create: boolean } | null>(null);
	let role = $state<Role>('both');
	let filter = $state('');
	let collections = $state<Partial<Record<Service, CollectionInfo[]>>>({});
	let loading = $state(false);
	let error = $state('');
	let busy = $state(false);

	let openedFor = 0;
	$effect(() => {
		if (!ui.joinOpen || !inv || openedFor === inv.id) return;
		openedFor = inv.id;
		picked = null;
		role = 'both';
		filter = '';
		svc = usable[0] ?? 'ytm';
	});
	$effect(() => {
		if (ui.joinOpen && usable.includes(svc)) load(svc);
	});
	let lastSvc: Service | null = null;
	$effect(() => {
		if (lastSvc && lastSvc !== svc) picked = null;
		lastSvc = svc;
	});

	async function load(s: Service) {
		if (collections[s]) return;
		loading = true;
		error = '';
		try {
			collections[s] = await api<CollectionInfo[]>(`/api/services/${s}/collections`);
		} catch (e) {
			error = (e as Error).message;
		} finally {
			loading = false;
		}
	}

	const shown = $derived.by(() => {
		const q = filter.trim().toLowerCase();
		return (collections[svc] ?? []).filter((c) => !q || c.name.toLowerCase().includes(q));
	});
	const newName = $derived(filter.trim() || inv?.fade.name || t('editor.newPlaylist'));
	const kindLabel = (k: Kind) => t(`kind.${k}`);
	const ROLE_OPTS = $derived<{ value: Role; label: string }[]>([
		{ value: 'send', label: t('role.send') },
		{ value: 'both', label: t('role.both') },
		{ value: 'receive', label: t('role.receive') }
	]);

	async function join() {
		if (!inv || !picked || busy) return;
		busy = true;
		try {
			const line = await post<Line>(`/api/invites/${inv.id}/accept`, {
				service: svc,
				kind: picked.kind,
				collection_id: picked.id,
				collection_name: picked.name,
				role,
				create: picked.create
			});
			await Promise.all([app.loadLines(), app.loadFriends()]);
			ui.joinOpen = false;
			ui.friendsOpen = false;
			ui.accountOpen = false;
			ui.say(t('join.joined', { name: line.name }), 'users');
			setTimeout(() => nav.openFade(line.id), 350);
		} catch (e) {
			ui.fail(e);
		} finally {
			busy = false;
		}
	}

	async function decline() {
		if (!inv) return;
		try {
			app.friends = await del<Friends>(`/api/invites/${inv.id}`);
			ui.joinOpen = false;
			ui.say(t('join.declined'), 'x', 'info');
		} catch (e) {
			ui.fail(e);
		}
	}
</script>

<Sheet bind:open={ui.joinOpen} label={t('join.title')} class="tinted" style={inv ? tintVars(inv.fade.color) : ''} onclosed={() => (openedFor = 0)}>
	{#snippet header()}
		<SheetBar title={t('join.title')} leading={{ label: t('common.close'), run: () => (ui.joinOpen = false) }} confirm={{ label: t('join.join'), run: join, disabled: !picked || busy }} />
	{/snippet}
	{#if inv}
		<div class="wrap">
			<div class="hero">
				<FadeBadge icon={fadeIcon(inv.fade)} color={inv.fade.color} size={64} />
				<p class="t-title3 ttl">{inv.fade.name}</p>
				<p class="t-sub">{t('join.text', { name: inv.from.display_name, fade: inv.fade.name })}</p>
				<p class="t-foot t-2 privacy">{t('join.privacy', { name: inv.from.display_name })}</p>
			</div>

			{#if !usable.length}
				<div class="section">
					<div class="group">
						<button class="cell" onclick={() => ((ui.joinOpen = false), ui.openAccount())}>
							<span class="grow">{t('join.needService')}</span>
							<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
						</button>
					</div>
				</div>
			{:else}
				<div class="section">
					<div class="section-head"><span class="caps">{t('join.pick')}</span></div>
					{#if usable.length > 1}
						<div class="svc">
							<Segmented
								bind:value={svc}
								label={t('join.pick')}
								options={usable.map((s) => ({ value: s, label: s === 'ytm' && usable.length > 2 ? 'YouTube' : SERVICE_NAME[s] }))}
							/>
						</div>
					{/if}
					<label class="field filter">
						<Glyph name="search" size={16} weight={2.2} />
						<input bind:value={filter} placeholder={t('editor.filter')} autocomplete="off" />
					</label>
					{#if error}<p class="section-foot warn">{error}</p>{/if}
					<div class="group list">
						{#if loading && !collections[svc]}
							<div class="cell"><span class="grow t-2">{t('editor.loadingCollections')}</span></div>
						{/if}
						{#each shown as c (c.kind + c.id)}
							{@const on = !!picked && !picked.create && picked.kind === c.kind && picked.id === c.id}
							<button class="cell" style:--inset="58px" aria-pressed={on} onclick={() => (picked = { kind: c.kind, id: c.id, name: c.name, create: false })}>
								<span class="tile-icon" style:background={c.kind === 'liked' ? '#ff2d55' : c.kind === 'library' ? '#8e8e93' : 'var(--tint-fill)'}>
									<Glyph name={c.kind === 'liked' ? 'heart' : c.kind === 'library' ? 'library' : 'note'} size={17} weight={2.2} />
								</span>
								<span class="grow">
									<span class="clip b">{c.name}</span>
									<span class="t-foot t-2 b">{kindLabel(c.kind)}{c.count != null ? ` · ${t('n.songs', { n: c.count })}` : ''}{!c.editable ? ` · ${t('editor.readOnly')}` : ''}</span>
								</span>
								{#if on}<span class="ok"><Glyph name="check" size={18} weight={2.6} /></span>{/if}
							</button>
						{/each}
						{#if collections[svc]}
							{@const on = !!picked?.create}
							<button class="cell" style:--inset="58px" aria-pressed={on} onclick={() => (picked = { kind: 'playlist', id: '', name: newName, create: true })}>
								<span class="add-ic"><Glyph name="plus" size={18} weight={2.6} /></span>
								<span class="grow">{t('editor.newPlaylistNamed', { name: on && picked ? picked.name : newName })}</span>
								{#if on}<span class="ok"><Glyph name="check" size={18} weight={2.6} /></span>{/if}
							</button>
						{/if}
					</div>
				</div>

				<div class="section">
					<div class="section-head"><span class="caps">{t('join.direction')}</span></div>
					<div class="group pad">
						<Segmented bind:value={role} options={ROLE_OPTS} label={t('join.direction')} />
					</div>
					<p class="section-foot">{t(`roleLong.${role}`)}</p>
				</div>
			{/if}

			<div class="section actions">
				<button class="pb pb-fill press" onclick={join} disabled={!picked || busy}>{t('join.join')}</button>
				<button class="text-btn danger" onclick={decline}>{t('join.decline')}</button>
			</div>
		</div>
	{/if}
</Sheet>

<style>
	.wrap {
		padding: 8px 0 calc(var(--safe-bottom) + 40px);
	}
	.hero {
		display: grid;
		justify-items: center;
		gap: 8px;
		margin: 8px 24px 28px;
		text-align: center;
	}
	.ttl {
		margin-top: 6px;
	}
	.privacy {
		max-width: 34em;
		line-height: 1.4;
	}
	.svc {
		margin-bottom: 10px;
	}
	.filter {
		margin-bottom: 10px;
	}
	.pad {
		padding: 10px;
	}
	.ok {
		color: var(--tint);
	}
	.add-ic {
		display: grid;
		place-items: center;
		flex: none;
		width: 30px;
		height: 30px;
		border-radius: 8px;
		background: var(--fill-3);
		color: var(--tint);
	}
	.actions {
		display: grid;
		justify-items: center;
		gap: 6px;
	}
	.actions .pb {
		width: 100%;
		max-width: 420px;
	}
</style>
