<script lang="ts">
	import { goto } from '$app/navigation';
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { about, loadAbout } from '#lib/about.svelte.ts';
	import { api, post, put } from '#lib/api.ts';
	import { app } from '#lib/state.svelte.ts';
	import type { User } from '#lib/types.ts';
	import Glyph from './Glyph.svelte';
	import Segmented from './Segmented.svelte';
	import ServiceSheet from './ServiceSheet.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import ThemePicker from './ThemePicker.svelte';
	import { userHue } from './artcolor.ts';
	import { i18n, t, type LangPref } from './i18n.svelte.ts';
	import { ui } from './ui.svelte.ts';

	// Your account: profile, your services, appearance, language. Admins also manage users here.
	const me = $derived(app.user);
	let name = $state('');
	$effect(() => {
		if (!ui.accountOpen) return;
		name = app.user?.display_name ?? '';
		app.loadServices().catch(() => {});
		loadAbout();
	});

	async function saveName() {
		const n = name.trim();
		if (!n || n === me?.display_name) return (name = me?.display_name ?? '');
		try {
			const r = await put<{ user: User }>('/api/auth/me', { display_name: n });
			app.user = r.user;
			ui.say(t('toast.saved'));
		} catch (e) {
			ui.fail(e);
		}
	}

	let lang = $state<LangPref>(i18n.pref);
	$effect(() => {
		if (lang !== i18n.pref) ui.setLang(lang);
	});

	function signOut() {
		ui.ask = {
			title: t('account.signoutQ'),
			label: t('account.signout'),
			run: async () => {
				await post('/api/auth/logout').catch(() => {});
				ui.accountOpen = false;
				app.signedOut();
				goto('/login');
			}
		};
	}
</script>

<Sheet bind:open={ui.accountOpen} label={t('account.title')}>
	{#snippet header()}
		<SheetBar title={t('account.title')} confirm={{ label: t('common.done'), run: () => (ui.accountOpen = false) }} />
	{/snippet}
	{#if me}
		<div class="acc">
			<div class="me">
				<span class="avatar uav" aria-hidden="true" style:--uh={userHue(me.id)}>{(me.display_name || '?').charAt(0).toUpperCase()}</span>
				<div class="who">
					<input class="name t-title3" bind:value={name} onblur={saveName} onkeydown={(e) => e.key === 'Enter' && (e.currentTarget as HTMLInputElement).blur()} maxlength="60" aria-label={t('account.name')} />
					<p class="t-sub t-2">@{me.username} · {me.is_admin ? t('users.admin') : t('users.user')}</p>
				</div>
			</div>

			<div class="section">
				<div class="section-head"><span class="caps">{t('account.services')}</span></div>
				<ul class="group">
					{#each app.services as s (s.service)}
						{@const bad = s.connected && s.status === 'error'}
						<li>
							<button class="cell" style:--inset="58px" onclick={() => ui.openService(s.service)}>
								<ServiceMark service={s.service} size={30} />
								<span class="grow">
									<span class="b">{s.name}</span>
									{#if bad}
										<span class="b t-foot danger">{t('services.expired', { n: s.lines })}</span>
									{:else if s.connected}
										<span class="b t-foot t-2 clip">{s.display_name} · {t('n.inFades', { n: s.lines })}</span>
									{:else}
										<span class="b t-foot t-2">{t('services.notConnected')}</span>
									{/if}
								</span>
								{#if s.connected && !bad}
									<span class="ok" aria-label={t('services.connected')}><Glyph name="check" size={18} weight={2.6} /></span>
								{:else}
									<span class="cell-pill">{bad ? t('services.signin') : t('services.connect')}</span>
								{/if}
							</button>
						</li>
					{/each}
				</ul>
				<p class="section-foot">{t('services.privacy')}</p>
			</div>

			<div class="section">
				<div class="group">
					<button class="cell" style:--inset="58px" onclick={() => (ui.friendsOpen = true)}>
						<span class="tile-icon" style:background="#30b0c7"><Glyph name="users" size={19} weight={2.1} /></span>
						<span class="grow">
							<span class="b">{t('friends.title')}</span>
							{#if app.socialBadge}<span class="b t-foot tintc">{t('friends.badge', { n: app.socialBadge })}</span>{/if}
						</span>
						{#if app.socialBadge}<span class="count" aria-hidden="true">{app.socialBadge}</span>{/if}
						<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
					</button>
				</div>
			</div>

			<div class="section">
				<div class="section-head"><span class="caps">{t('account.appearance')}</span></div>
				<div class="group"><ThemePicker /></div>
				<p class="section-foot">{t('account.appearanceHint')}</p>
			</div>

			<div class="section">
				<div class="section-head"><span class="caps">{t('account.language')}</span></div>
				<div class="group pad">
					<Segmented
						bind:value={lang}
						label={t('account.language')}
						options={[
							{ value: 'system', label: t('lang.system') },
							{ value: 'de', label: 'Deutsch' },
							{ value: 'en', label: 'English' }
						]}
					/>
				</div>
				<p class="section-foot">{t('account.languageHint', { lang: i18n.system === 'de' ? 'Deutsch' : 'English' })}</p>
			</div>

			{#if me.is_admin}
				<div class="section">
					<div class="section-head"><span class="caps">{t('account.admin')}</span></div>
					<div class="group">
						<button class="cell" style:--inset="58px" onclick={() => (ui.usersOpen = true)}>
							<span class="tile-icon" style:background="#5e5ce6"><Glyph name="users" size={19} weight={2.1} /></span>
							<span class="grow">{t('users.title')}</span>
							<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
						</button>
						<button class="cell" style:--inset="58px" onclick={() => (ui.appsOpen = true)}>
							<span class="tile-icon" style:background="#636366"><Glyph name="shield" size={18} weight={2.2} /></span>
							<span class="grow">{t('account.apps')}</span>
							<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
						</button>
					</div>
					<p class="section-foot">{t('account.adminHint')}</p>
				</div>
			{/if}

			<div class="section">
				<div class="group">
					<button class="cell" style:--inset="58px" onclick={() => (ui.passwordOpen = true)}>
						<span class="tile-icon" style:background="#8e8e93"><Glyph name="key" size={18} weight={2.2} /></span>
						<span class="grow">{t('account.password')}</span>
						<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
					</button>
				</div>
			</div>

			<div class="section">
				<div class="group">
					<button class="cell center" onclick={signOut}><span class="grow danger">{t('account.signout')}</span></button>
				</div>
				<p class="section-foot version">
					Crossfade{about.version ? ` ${about.version}` : ''} ·
					<a href={about.source} target="_blank" rel="noopener noreferrer">{t('about.source')}</a>
				</p>
			</div>
		</div>
	{/if}
</Sheet>

<ServiceSheet />

<style>
	.acc {
		padding: 8px 0 calc(var(--safe-bottom) + 40px);
	}
	.me {
		display: flex;
		align-items: center;
		gap: 14px;
		margin: 4px 16px 28px;
		padding: 14px 16px;
		border-radius: var(--r-group);
		background: var(--cell-grouped);
		-webkit-backdrop-filter: blur(20px) saturate(1.6);
		backdrop-filter: blur(20px) saturate(1.6);
		box-shadow:
			var(--group-shadow),
			inset 0 0 0 0.5px var(--rim-edge),
			var(--group-rim);
	}
	.avatar {
		display: grid;
		flex: none;
		place-items: center;
		width: 56px;
		height: 56px;
		border-radius: 50%;
		box-shadow:
			0 6px 16px -6px rgb(0 0 0 / 0.3);
		font-size: var(--f-t2);
		font-weight: 600;
	}
	.who {
		flex: 1;
		min-width: 0;
	}
	.name {
		width: 100%;
		margin: -2px 0 0 -6px;
		padding: 2px 6px;
		border: 0;
		border-radius: 8px;
		outline: none;
		background: none;
		color: var(--label);
	}
	.name:focus {
		background: var(--fill-3);
	}
	.pad {
		padding: 10px;
	}
	.ok {
		color: var(--green);
	}
	.version {
		text-align: center;
	}
	.version a {
		color: inherit;
		text-decoration: underline;
		text-underline-offset: 2px;
	}
	.count {
		display: grid;
		place-items: center;
		min-width: 22px;
		height: 22px;
		padding: 0 6px;
		border-radius: 999px;
		background: var(--red);
		color: #fff;
		font-size: var(--f-foot);
		font-weight: 700;
	}
</style>
