<script lang="ts">
	import { goto } from '$app/navigation';
	import { post, put } from '#lib/api.ts';
	import { app } from '#lib/state.svelte.ts';
	import AuthPage from '#lib/ui/AuthPage.svelte';
	import Field from '#lib/ui/Field.svelte';
	import { t } from '#lib/ui/i18n.svelte.ts';

	// After an admin created the account (or reset it): replace the start password first.
	let current = $state('');
	let next = $state('');
	let repeat = $state('');
	let error = $state('');
	let busy = $state(false);

	$effect(() => {
		if (app.user && !app.user.must_change_password) goto('/', { replaceState: true });
	});
	const pwError = $derived(next && next.length < 8 ? t('auth.pwShort') : repeat && repeat !== next ? t('auth.pwMismatch') : '');

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		error = '';
		try {
			await put('/api/auth/me/password', { current, new: next });
			await app.signedIn({ ...app.user!, must_change_password: false });
			goto('/');
		} catch (err) {
			error = (err as Error).message;
		} finally {
			busy = false;
		}
	}
	async function signOut() {
		await post('/api/auth/logout');
		app.signedOut();
		goto('/login');
	}
</script>

<svelte:head><title>{t('auth.newPw')} – Crossfade</title></svelte:head>

<AuthPage title={t('auth.newPwTitle', { name: app.user?.display_name ?? '' })} intro={t('auth.newPwText')}>
	<form class="form" onsubmit={submit}>
		<Field label={t('auth.startPw')} type="password" bind:value={current} autocomplete="current-password" />
		<Field label={t('auth.newPw')} type="password" bind:value={next} autocomplete="new-password" />
		<Field label={t('auth.repeat')} type="password" bind:value={repeat} autocomplete="new-password" error={pwError} />
		{#if error}<p class="err" role="alert">{error}</p>{/if}
		<button class="pb glass tint lq" disabled={busy || !current || next.length < 8 || repeat !== next}>{t('auth.newPwGo')}</button>
		<button type="button" class="text-btn out" onclick={signOut}>{t('account.signout')}</button>
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
	.out {
		justify-self: center;
		color: var(--label-2);
	}
</style>
