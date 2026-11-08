<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { post } from '#lib/api.ts';
	import { app } from '#lib/state.svelte.ts';
	import AuthPage from '#lib/ui/AuthPage.svelte';
	import Field from '#lib/ui/Field.svelte';
	import { t } from '#lib/ui/i18n.svelte.ts';

	let username = $state('');
	let password = $state('');
	let error = $state('');
	let busy = $state(false);

	$effect(() => {
		if (app.user && !app.user.must_change_password) goto(safeNext(), { replaceState: true });
	});

	function safeNext() {
		const n = page.url.searchParams.get('next') || '/';
		// only paths on this site: no //host or /\host (browsers read both as another host)
		return /^\/(?![/\\])/.test(n) ? n : '/';
	}

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		error = '';
		try {
			const r = await post('/api/auth/login', { username: username.trim(), password });
			await app.signedIn(r.user);
			goto(r.user.must_change_password ? '/password' : safeNext());
		} catch (err) {
			error = (err as Error).message;
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>{t('auth.signin')} – Crossfade</title></svelte:head>

<AuthPage title={t('auth.welcome')} intro={t('auth.welcomeText')}>
	<form class="form" onsubmit={submit}>
		<Field label={t('users.username')} bind:value={username} autocomplete="username" />
		<Field label={t('auth.password')} type="password" bind:value={password} autocomplete="current-password" />
		{#if error}<p class="err" role="alert">{error}</p>{/if}
		<button class="pb glass tint lq" disabled={busy || !username || !password}>{busy ? t('auth.signingIn') : t('auth.signin')}</button>
		<p class="t-foot t-2 note">{t('auth.noAccount')}</p>
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
	.note {
		text-align: center;
	}
</style>
