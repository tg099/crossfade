<script lang="ts">
	import { SERVICE_NAME } from '#lib/format.ts';
	import { del, post, put } from '#lib/api.ts';
	import { app } from '#lib/state.svelte.ts';
	import Glyph from './Glyph.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import Switch from './Switch.svelte';
	import { userHue } from './artcolor.ts';
	import { t } from './i18n.svelte.ts';
	import { copyText, ui } from './ui.svelte.ts';
	import { users } from './users.svelte.ts';

	// Create or manage one user (admins only). The server makes up the start password; it is shown
	// once, and the person has to replace it when they first sign in.
	const slug = (s: string) =>
		s
			.toLocaleLowerCase('de')
			.normalize('NFD')
			.replace(/\p{M}/gu, '')
			.replace(/ß/g, 'ss')
			.replace(/[^a-z0-9]+/g, '.')
			.replace(/^\.+|\.+$/g, '')
			.slice(0, 32);

	const editing = $derived(ui.userEditing);
	const user = $derived(editing ? users.list.find((u) => u.id === editing) : undefined);
	const isNew = $derived(editing === 0);
	const isMe = $derived(!!user && user.id === app.user?.id);

	let name = $state('');
	let username = $state('');
	let userTouched = $state(false);
	let admin = $state(false);
	/** a start password the server just made (new user or reset) */
	let startPw = $state<string | null>(null);
	let busy = $state(false);
	let tried = $state(false);
	let loadedFor: number | null = null;

	$effect(() => {
		if (!ui.userOpen || editing === null || loadedFor === editing) return;
		loadedFor = editing;
		name = user?.display_name ?? '';
		username = user?.username ?? '';
		userTouched = !!user;
		admin = user?.is_admin ?? false;
		startPw = null;
		tried = false;
	});
	$effect(() => {
		if (!userTouched && isNew && !startPw) username = slug(name);
	});

	const lastAdmin = $derived(!!user?.is_admin && users.list.filter((u) => u.is_admin).length === 1);
	const nameErr = $derived(name.trim() ? '' : t('users.err.name'));
	const userErr = $derived(
		!username
			? t('users.err.userMissing')
			: !/^[A-Za-z0-9._-]{2,32}$/.test(username)
				? t('users.err.userChars')
				: users.list.some((u) => u.username.toLowerCase() === username.toLowerCase() && u.id !== user?.id)
					? t('users.err.userTaken', { username })
					: ''
	);
	const valid = $derived(!nameErr && !userErr);
	const dirty = $derived(!user || name.trim() !== user.display_name || username !== user.username || admin !== user.is_admin);

	async function save() {
		tried = true;
		if (!valid || busy) return;
		if (startPw && isNew) return (ui.userOpen = false); // created already, this is "Done"
		busy = true;
		try {
			if (isNew) {
				const r = await post<{ id: number; password: string }>('/api/admin/users', { display_name: name.trim(), username, is_admin: admin });
				startPw = r.password;
				await users.load();
				ui.say(t('users.created', { name: name.trim() }), 'users');
			} else if (user) {
				if (name.trim() !== user.display_name || username !== user.username)
					await put(`/api/admin/users/${user.id}`, { display_name: name.trim(), username });
				if (admin !== user.is_admin && !lastAdmin && !isMe) await put(`/api/admin/users/${user.id}/admin`, { is_admin: admin });
				await users.load();
				if (isMe) await app.boot();
				ui.say(t('toast.saved'));
				ui.userOpen = false;
			}
		} catch (e) {
			ui.fail(e);
		} finally {
			busy = false;
		}
	}
	function reset() {
		if (!user || isMe) return;
		const u = user;
		ui.ask = {
			title: t('users.resetQ', { name: u.display_name }),
			message: t('users.resetMsg', { name: u.display_name }),
			label: t('users.resetPw'),
			run: async () => {
				try {
					const r = await post<{ password: string }>(`/api/admin/users/${u.id}/password`);
					startPw = r.password;
					await users.load();
				} catch (e) {
					ui.fail(e);
				}
			}
		};
	}
	function remove() {
		if (!user || isMe) return;
		const u = user;
		ui.ask = {
			title: t('users.deleteQ', { name: u.display_name }),
			message: t('users.deleteMsg', { name: u.display_name }),
			label: t('users.delete'),
			run: async () => {
				try {
					await del(`/api/admin/users/${u.id}`);
					await users.load();
					ui.userOpen = false;
					ui.say(t('users.deleted', { name: u.display_name }), 'trash');
				} catch (e) {
					ui.fail(e);
				}
			}
		};
	}
	const confirm = $derived(
		isNew
			? startPw
				? { label: t('common.done'), run: () => (ui.userOpen = false) }
				: { label: t('users.createShort'), run: save, disabled: !valid || busy }
			: { label: t('common.save'), run: save, disabled: !valid || busy || !dirty }
	);
</script>

<Sheet bind:open={ui.userOpen} label={isNew ? t('users.create') : t('users.one')} onclosed={() => ((loadedFor = null), (ui.userEditing = null))}>
	{#snippet header()}
		<SheetBar title={isNew ? t('users.create') : (user?.display_name ?? t('users.one'))} leading={{ label: t('common.cancel'), run: () => (ui.userOpen = false) }} {confirm} />
	{/snippet}
	<div class="wrap">
		<div class="me">
			<span class="av uav" style:--uh={userHue(user?.id ?? (username || 'new'))}>{(name || '?').charAt(0).toUpperCase()}</span>
			{#if user}
				<p class="t-sub t-2">
					{user.must_change_password ? t('users.neverSignedIn') : t('n.fades', { n: user.lines })}{user.services.length
						? ` · ${user.services.map((s) => SERVICE_NAME[s]).join(', ')}`
						: ''}
				</p>
			{/if}
		</div>

		{#if startPw}
			<div class="section">
				<div class="section-head"><span class="caps">{t('users.startPw')}</span></div>
				<div class="group">
					<div class="cell">
						<code class="grow pw mono">{startPw}</code>
						<button class="gbtn round glass lq" onclick={() => copyText(startPw!)} aria-label={t('users.copyPw')}><Glyph name="copy" size={18} weight={2.2} /></button>
					</div>
				</div>
				<p class="section-foot">{t('users.startPwHint', { name: name.trim() })}</p>
			</div>
		{/if}

		{#if !(isNew && startPw)}
			<div class="section">
				<div class="group">
					<label class="cell">
						<span class="lbl">{t('users.name')}</span>
						<input class="in" bind:value={name} placeholder={t('users.namePh')} maxlength="60" autocomplete="off" />
					</label>
					<label class="cell">
						<span class="lbl">{t('users.username')}</span>
						<span class="at">@</span>
						<input class="in" bind:value={username} oninput={() => (userTouched = true)} maxlength="32" autocapitalize="off" autocomplete="off" spellcheck="false" />
					</label>
				</div>
				{#if (tried || name) && nameErr}<p class="section-foot warn">{nameErr}</p>{/if}
				{#if username && userErr}<p class="section-foot warn">{userErr}</p>{:else if !nameErr}<p class="section-foot">{t('users.usernameHint', { name: name.trim() })}</p>{/if}
			</div>

			<div class="section">
				<div class="group">
					<div class="cell">
						<span class="grow">{t('users.admin')}</span>
						{#if lastAdmin || isMe}
							<span class="value">{admin ? t('common.yes') : t('common.no')}</span>
						{:else}
							<Switch bind:checked={admin} label={t('users.admin')} />
						{/if}
					</div>
				</div>
				<p class="section-foot">{lastAdmin ? t('users.lastAdmin') : isMe ? t('users.adminSelf') : t('users.adminHint')}</p>
			</div>
		{/if}

		{#if isNew && !startPw}
			<p class="section-foot pre">{t('users.startPwBefore')}</p>
		{:else if user && !isMe}
			<div class="section">
				<div class="group">
					<button class="cell" onclick={reset}><span class="grow">{t('users.resetPw')}</span></button>
				</div>
				<p class="section-foot">{t('users.resetHint')}</p>
			</div>
			<div class="section">
				<div class="group">
					<button class="cell center" onclick={remove}><span class="grow danger">{t('users.delete')}</span></button>
				</div>
			</div>
		{:else if isMe}
			<p class="section-foot pre">{t('users.resetSelf')}</p>
		{/if}
	</div>
</Sheet>

<style>
	.wrap {
		padding: 4px 0 calc(var(--safe-bottom) + 40px);
	}
	.me {
		display: grid;
		justify-items: center;
		gap: 8px;
		padding: 4px 16px 24px;
		text-align: center;
	}
	.av {
		display: grid;
		place-items: center;
		width: 76px;
		height: 76px;
		border-radius: 50%;
		font-size: 2rem;
		font-weight: 700;
		box-shadow:
			0 10px 24px -10px rgb(0 0 0 / 0.35);
	}
	.at {
		color: var(--label-2);
		margin-right: -10px;
	}
	.pw {
		padding: 0;
		border: 0;
		background: none;
		font-size: var(--f-body);
		user-select: all;
	}
	.pre {
		margin: 0 16px;
	}
</style>
