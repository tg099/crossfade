<script lang="ts">
	import { goto } from '$app/navigation';
	import { post } from '#lib/api.ts';
	import { app } from '#lib/state.svelte.ts';
	import AuthPage from '#lib/ui/AuthPage.svelte';
	import Field from '#lib/ui/Field.svelte';
	import { t } from '#lib/ui/i18n.svelte.ts';

	// First start: create the first administrator.
	let name = $state('');
	let username = $state('');
	let password = $state('');
	let repeat = $state('');
	let error = $state('');
	let busy = $state(false);

	$effect(() => {
		if (app.ready && !app.setupNeeded) goto('/', { replaceState: true });
	});

	const pwError = $derived(password && password.length < 8 ? t('auth.pwShort') : repeat && repeat !== password ? t('auth.pwMismatch') : '');

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		error = '';
		try {
			const r = await post('/api/auth/setup', { username: username.trim(), display_name: name.trim(), password });
			app.setupNeeded = false;
			await app.signedIn(r.user);
			goto('/');
		} catch (err) {
			error = (err as Error).message;
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>{t('auth.setup')} – Crossfade</title></svelte:head>

<AuthPage title={t('auth.setupTitle')} intro={t('auth.setupText')}>
	<form class="form" onsubmit={submit}>
		<Field label={t('users.name')} bind:value={name} autocomplete="name" />
		<Field label={t('users.username')} bind:value={username} autocomplete="username" hint={t('auth.usernameHint')} />
		<Field label={t('auth.password')} type="password" bind:value={password} autocomplete="new-password" />
		<Field label={t('auth.repeat')} type="password" bind:value={repeat} autocomplete="new-password" error={pwError} />
		{#if error}<p class="err" role="alert">{error}</p>{/if}
		<button class="pb glass tint lq" disabled={busy || !name.trim() || !username.trim() || password.length < 8 || repeat !== password}>{t('auth.setupGo')}</button>
	</form>
</AuthPage>

<style>
	.form {
		display: grid;
		gap: 14px;
	}
	.err {
		color: var(--red-text);
		font-size: var(--f-sub);
	}
</style>
