<script lang="ts">
	import { api, put } from '#lib/api.ts';
	import { app } from '#lib/state.svelte.ts';
	import Glyph from './Glyph.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import { t } from './i18n.svelte.ts';
	import { copyText, ui } from './ui.svelte.ts';

	// Admin only: the developer apps Spotify and the Google code sign-in need. Set once, valid for all.
	type Cred = { set: boolean; source: 'env' | 'ui' | null; value: string | null };
	type Apps = Record<'spotify_client_id' | 'spotify_client_secret' | 'google_client_id' | 'google_client_secret', Cred> & {
		spotify_redirect_uri: string;
	};
	let apps = $state<Apps | null>(null);
	let creds = $state({ spotify_client_id: '', spotify_client_secret: '', google_client_id: '', google_client_secret: '' });
	let busy = $state(false);

	async function load() {
		apps = await api<Apps>('/api/admin/apps');
		creds = {
			spotify_client_id: apps.spotify_client_id.value ?? '',
			spotify_client_secret: '',
			google_client_id: apps.google_client_id.value ?? '',
			google_client_secret: ''
		};
	}
	$effect(() => {
		if (ui.appsOpen) load().catch(ui.fail);
	});

	async function save(keys: (keyof typeof creds)[]) {
		busy = true;
		try {
			const body: Record<string, string> = {};
			for (const k of keys) if (creds[k] !== '' || k.endsWith('_id')) body[k] = creds[k];
			await put('/api/admin/apps', body);
			await Promise.all([load(), app.loadServices()]);
			ui.say(t('toast.saved'));
		} catch (e) {
			ui.fail(e);
		} finally {
			busy = false;
		}
	}
</script>

<Sheet bind:open={ui.appsOpen} label={t('account.apps')}>
	{#snippet header()}
		<SheetBar title={t('account.apps')} leading={{ label: t('common.back'), icon: 'chevronLeft', run: () => (ui.appsOpen = false) }} />
	{/snippet}
	<div class="wrap">
		<p class="intro t-sub t-2">{t('apps.intro')}</p>
		{#if apps}
			<div class="section">
				<div class="section-head"><span class="caps">Spotify</span></div>
				<ol class="steps t-sub">
					<li>{t('apps.sp.s1')} <a href="https://developer.spotify.com/dashboard" target="_blank" rel="noopener">Spotify Developer Dashboard</a></li>
					<li>{t('apps.sp.s2')}</li>
					<li>{t('apps.sp.s3')}</li>
				</ol>
				<div class="group">
					<button class="cell" onclick={() => copyText(apps!.spotify_redirect_uri)}>
						<span class="grow"><span class="b t-foot t-2">Redirect URI</span><span class="b clip mono uri">{apps.spotify_redirect_uri}</span></span>
						<Glyph name="copy" size={18} weight={2.2} />
					</button>
				</div>
				{#if apps.spotify_client_id.source === 'env'}
					<p class="section-foot">{t('apps.env', { vars: 'SPOTIFY_CLIENT_ID' })}</p>
				{:else}
					<form class="group form" onsubmit={(e) => (e.preventDefault(), save(['spotify_client_id', 'spotify_client_secret']))}>
						<label class="cell"><span class="lbl">Client ID</span><input class="in mono" bind:value={creds.spotify_client_id} autocomplete="off" /></label>
						<label class="cell">
							<span class="lbl">Secret</span>
							<input class="in mono" type="password" bind:value={creds.spotify_client_secret} placeholder={apps.spotify_client_secret.set ? t('apps.keep') : t('apps.optional')} autocomplete="off" />
						</label>
						<button class="cell" disabled={busy || !creds.spotify_client_id?.trim()}><span class="grow tintc">{t('common.save')}</span></button>
					</form>
					<p class="section-foot">{apps.spotify_client_id.set ? t('apps.ready') : t('apps.sp.hint')}</p>
				{/if}
			</div>

			<div class="section">
				<div class="section-head"><span class="caps">{t('apps.google')}</span></div>
				<ol class="steps t-sub">
					<li>{t('apps.g.s1')} <a href="https://console.cloud.google.com/" target="_blank" rel="noopener">Google Cloud Console</a></li>
					<li>{t('apps.g.s2')}</li>
					<li>{t('apps.g.s3')}</li>
				</ol>
				{#if apps.google_client_id.source === 'env'}
					<p class="section-foot">{t('apps.env', { vars: 'GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET' })}</p>
				{:else}
					<form class="group form" onsubmit={(e) => (e.preventDefault(), save(['google_client_id', 'google_client_secret']))}>
						<label class="cell"><span class="lbl">Client ID</span><input class="in mono" bind:value={creds.google_client_id} autocomplete="off" /></label>
						<label class="cell">
							<span class="lbl">Secret</span>
							<input class="in mono" type="password" bind:value={creds.google_client_secret} placeholder={apps.google_client_secret.set ? t('apps.keep') : ''} autocomplete="off" />
						</label>
						<button class="cell" disabled={busy}><span class="grow tintc">{t('common.save')}</span></button>
					</form>
				{/if}
				<p class="section-foot">{t('apps.g.hint')}</p>
			</div>
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
	.steps {
		margin: 0 16px 12px;
		padding-left: 22px;
		display: grid;
		gap: 6px;
		color: var(--label-2);
	}
	.steps a {
		color: var(--tint);
		font-weight: 600;
	}
	.form {
		margin-top: 12px;
	}
	.lbl {
		width: 5.5em !important;
	}
	.uri {
		font-size: var(--f-foot);
	}
</style>
