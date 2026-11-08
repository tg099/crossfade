<script lang="ts">
	import { api, del, post, put } from '#lib/api.ts';
	import { app } from '#lib/state.svelte.ts';
	import Glyph from './Glyph.svelte';
	import Segmented from './Segmented.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import Switch from './Switch.svelte';
	import { t, type Key } from './i18n.svelte.ts';
	import { ago, ui } from './ui.svelte.ts';

	// Your notification channels: ntfy, a webhook (Discord, Slack, Home Assistant …) or e-mail,
	// each with the events it should get. Everyone sets up their own.
	type Kind = 'ntfy' | 'webhook' | 'email';
	type Event = 'auth' | 'expiring' | 'confirm' | 'issues' | 'errors' | 'social' | 'update';
	type Channel = {
		id: number;
		kind: Kind;
		config: { server?: string; topic?: string; token_set?: boolean; url_hint?: string; to?: string };
		events: Event[];
		enabled: boolean;
		last_sent_at: number | null;
		last_error: string | null;
		flavor: 'discord' | 'slack' | 'json' | null;
	};
	type Overview = { channels: Channel[]; events: Event[]; email: boolean; ntfy_default: string };

	let data = $state<Overview | null>(null);
	/** null = the list, 0 = a new channel, else the channel being edited */
	let editing = $state<number | null>(null);
	let kind = $state<Kind>('ntfy');
	let form = $state({ server: '', topic: '', token: '', url: '', to: '' });
	let events = $state<Record<Event, boolean>>({ auth: true, expiring: true, confirm: true, issues: true, errors: true, social: true, update: true });
	let enabled = $state(true);
	let busy = $state(false);
	let error = $state('');

	const current = $derived(editing ? data?.channels.find((c) => c.id === editing) : undefined);
	// news about new releases only reach admins
	const EVENTS = $derived<Event[]>(['auth', 'expiring', 'confirm', 'issues', 'errors', 'social', ...(app.user?.is_admin ? (['update'] as Event[]) : [])]);

	async function load() {
		data = await api<Overview>('/api/notifications');
	}
	$effect(() => {
		if (ui.notifyOpen) load().catch(ui.fail);
	});
	function closed() {
		editing = null;
		error = '';
	}

	function open(c: Channel | null) {
		error = '';
		if (c) {
			kind = c.kind;
			form = { server: c.config.server ?? '', topic: c.config.topic ?? '', token: '', url: '', to: c.config.to ?? '' };
			events = Object.fromEntries(EVENTS.map((e) => [e, c.events.includes(e)])) as Record<Event, boolean>;
			enabled = c.enabled;
			editing = c.id;
		} else {
			kind = 'ntfy';
			form = { server: '', topic: '', token: '', url: '', to: '' };
			events = { auth: true, expiring: true, confirm: true, issues: true, errors: true, social: true, update: true };
			enabled = true;
			editing = 0;
		}
	}

	function config() {
		if (kind === 'ntfy') {
			const c: Record<string, string> = { server: form.server.trim() || data?.ntfy_default || '', topic: form.topic.trim() };
			if (form.token) c.token = form.token;
			return c;
		}
		if (kind === 'webhook') return form.url.trim() ? { url: form.url.trim() } : {};
		return { to: form.to.trim() };
	}
	const chosen = () => EVENTS.filter((e) => events[e]);
	const ready = $derived(
		kind === 'ntfy' ? !!form.topic.trim() : kind === 'webhook' ? !!form.url.trim() || !!editing : !!form.to.trim()
	);

	async function save() {
		busy = true;
		error = '';
		try {
			if (editing) await put(`/api/notifications/channels/${editing}`, { config: config(), events: chosen(), enabled });
			else await post('/api/notifications/channels', { kind, config: config(), events: chosen() });
			await load();
			ui.say(t('toast.saved'));
			editing = null;
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}
	async function test(c: Channel) {
		busy = true;
		try {
			const r = await post<{ ok: boolean; error: string | null }>(`/api/notifications/channels/${c.id}/test`);
			if (r.ok) ui.say(t('notify.testSent'), 'check');
			else ui.say(t('notify.testFailed', { error: r.error ?? '' }), 'alert', 'fault');
			await load();
		} catch (e) {
			ui.fail(e);
		} finally {
			busy = false;
		}
	}
	async function clearToken() {
		if (!editing) return;
		try {
			await put(`/api/notifications/channels/${editing}`, { config: { token: '' } });
			await load();
		} catch (e) {
			ui.fail(e);
		}
	}
	function remove(c: Channel) {
		ui.ask = {
			title: t('notify.removeQ'),
			label: t('common.remove'),
			run: async () => {
				try {
					await del(`/api/notifications/channels/${c.id}`);
					editing = null;
					await load();
				} catch (e) {
					ui.fail(e);
				}
			}
		};
	}

	function title(c: Channel) {
		if (c.kind === 'ntfy') return `ntfy · ${c.config.topic}`;
		if (c.kind === 'email') return c.config.to ?? t('notify.email');
		return c.flavor === 'discord' ? 'Discord' : c.flavor === 'slack' ? 'Slack' : t('notify.webhook');
	}
	function sub(c: Channel) {
		if (c.last_error) return t('notify.lastError', { error: c.last_error });
		if (!c.enabled) return t('notify.off');
		const n = c.events.length;
		return `${n === EVENTS.length ? t('notify.allEvents') : t('notify.nEvents', { n })} · ${c.last_sent_at ? t('notify.lastSent', { ago: ago(c.last_sent_at) }) : t('notify.neverSent')}`;
	}
	const ICON: Record<Kind, string> = { ntfy: '#34c759', webhook: '#5e5ce6', email: '#0a84ff' };
</script>

<Sheet bind:open={ui.notifyOpen} label={t('notify.title')} onclosed={closed}>
	{#snippet header()}
		{#if editing === null}
			<SheetBar title={t('notify.title')} leading={{ label: t('common.back'), icon: 'chevronLeft', run: () => (ui.notifyOpen = false) }} />
		{:else}
			<SheetBar
				title={editing ? title(current!) : t('notify.new')}
				leading={{ label: t('common.back'), icon: 'chevronLeft', run: () => (editing = null) }}
				confirm={{ label: t('common.save'), run: save, disabled: busy || !ready || !chosen().length }}
			/>
		{/if}
	{/snippet}
	<div class="wrap">
		{#if data && editing === null}
			<p class="intro t-sub t-2">{t('notify.intro')}</p>
			<div class="section">
				<ul class="group">
					{#each data.channels as c (c.id)}
						<li>
							<button class="cell" style:--inset="58px" onclick={() => open(c)}>
								<span class="tile-icon" style:background={ICON[c.kind]}><Glyph name={c.kind === 'email' ? 'mail' : c.kind === 'webhook' ? 'link' : 'bell'} size={18} weight={2.2} /></span>
								<span class="grow">
									<span class="b clip">{title(c)}</span>
									<span class="b t-foot clip" class:danger={!!c.last_error} class:t-2={!c.last_error}>{sub(c)}</span>
								</span>
								<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
							</button>
						</li>
					{/each}
					<li>
						<button class="cell" style:--inset="58px" onclick={() => open(null)}>
							<span class="tile-icon add"><Glyph name="plus" size={18} weight={2.4} /></span>
							<span class="grow tintc">{t('notify.add')}</span>
						</button>
					</li>
				</ul>
				<p class="section-foot">{t('notify.foot')}</p>
			</div>
		{:else if data}
			{#if !editing}
				<div class="section">
					<div class="way">
						<Segmented
							bind:value={kind}
							label={t('notify.kind')}
							options={[
								{ value: 'ntfy', label: 'ntfy' },
								{ value: 'webhook', label: t('notify.webhook') },
								{ value: 'email', label: t('notify.email') }
							]}
						/>
					</div>
				</div>
			{/if}

			<div class="section">
				{#if kind === 'ntfy'}
					<div class="group form">
						<label class="cell"><span class="lbl">{t('notify.topic')}</span><input class="in mono" bind:value={form.topic} placeholder="crossfade-…" autocomplete="off" /></label>
						<label class="cell"><span class="lbl">Server</span><input class="in mono" bind:value={form.server} placeholder={data.ntfy_default} autocomplete="off" inputmode="url" /></label>
						<label class="cell">
							<span class="lbl">Token</span>
							<input class="in mono" type="password" bind:value={form.token} placeholder={current?.config.token_set ? t('apps.keep') : t('apps.optional')} autocomplete="off" />
						</label>
					</div>
					<p class="section-foot">{t('notify.ntfyHint')}</p>
					{#if current?.config.token_set}
						<div class="group spaced"><button class="cell" onclick={clearToken}><span class="grow danger">{t('notify.clearToken')}</span></button></div>
					{/if}
				{:else if kind === 'webhook'}
					<div class="group form">
						<label class="cell"><span class="lbl">URL</span><input class="in mono" bind:value={form.url} placeholder={current?.config.url_hint ?? 'https://…'} autocomplete="off" inputmode="url" /></label>
					</div>
					<p class="section-foot">{editing ? t('notify.webhookKeep') : t('notify.webhookHint')}</p>
				{:else}
					<div class="group form">
						<label class="cell"><span class="lbl">{t('notify.address')}</span><input class="in" type="email" bind:value={form.to} placeholder="du@example.com" autocomplete="email" /></label>
					</div>
					<p class="section-foot">{data.email ? t('notify.emailHint') : t('notify.emailMissing')}</p>
				{/if}
			</div>

			<div class="section">
				<div class="section-head"><span class="caps">{t('notify.when')}</span></div>
				<ul class="group">
					{#each EVENTS as e (e)}
						<li class="cell">
							<span class="grow">
								<span class="b">{t(`notify.ev.${e}` as Key)}</span>
								<span class="b t-foot t-2">{t(`notify.ev.${e}.hint` as Key)}</span>
							</span>
							<Switch bind:checked={events[e]} label={t(`notify.ev.${e}` as Key)} />
						</li>
					{/each}
				</ul>
			</div>

			{#if current}
				<div class="section">
					<div class="group">
						<div class="cell">
							<span class="grow">{t('notify.enabled')}</span>
							<Switch bind:checked={enabled} label={t('notify.enabled')} />
						</div>
						<button class="cell" onclick={() => test(current)} disabled={busy}><span class="grow tintc">{t('notify.test')}</span></button>
					</div>
					{#if current.last_error}<p class="section-foot danger">{t('notify.lastError', { error: current.last_error })}</p>{/if}
				</div>
				<div class="section">
					<div class="group">
						<button class="cell center" onclick={() => remove(current)}><span class="grow danger">{t('notify.remove')}</span></button>
					</div>
				</div>
			{/if}
			{#if error}<p class="section-foot danger" role="alert">{error}</p>{/if}
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
	.way {
		margin: 0 16px;
	}
	.lbl {
		width: 5.5em !important;
	}
	.spaced {
		margin-top: 12px;
	}
	.tile-icon.add {
		background: var(--fill);
		color: var(--tint);
	}
</style>
