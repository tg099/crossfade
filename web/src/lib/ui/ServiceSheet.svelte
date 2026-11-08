<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { del, post, put } from '#lib/api.ts';
	import type { YtFormat } from '#lib/types.ts';
	import { app } from '#lib/state.svelte.ts';
	import Glyph from './Glyph.svelte';
	import Segmented from './Segmented.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import { i18n, t } from './i18n.svelte.ts';
	import { devtoolsKeys } from './keys.ts';
	import { ago, ui } from './ui.svelte.ts';

	// One music service: connect it, renew the sign-in or disconnect. The ways to connect differ per
	// service (Spotify sign-in, a Google code or a pasted request for YouTube Music, a token for Apple).
	const s = $derived(app.services.find((x) => x.service === ui.serviceFocus));
	let busy = $state(false);
	let error = $state('');
	let ytmWay = $state<'device' | 'paste'>('device');
	let device = $state<{ flow: string; user_code: string; verification_url: string; interval: number } | null>(null);
	let pasteText = $state('');
	let appleToken = $state('');
	let arl = $state('');
	// YouTube: song or music video, picked before connecting and saved once the account exists
	let ytPick = $state<YtFormat>('song');
	let timer: ReturnType<typeof setTimeout> | null = null;
	let loadedFor = '';
	let hadDevice = false;

	$effect(() => {
		if (!ui.serviceOpen || !s) return;
		const hasDevice = s.methods.includes('device');
		if (loadedFor === s.service) {
			// The admin just set up the Google app from here: switch to the recommended code sign-in.
			if (hasDevice && !hadDevice) ytmWay = 'device';
			hadDevice = hasDevice;
			return;
		}
		loadedFor = s.service;
		hadDevice = hasDevice;
		error = '';
		device = null;
		pasteText = appleToken = arl = '';
		ytPick = s.yt_format ?? 'song';
		ytmWay = hasDevice ? 'device' : 'paste';
	});
	function closed() {
		loadedFor = '';
		device = null;
		if (timer) clearTimeout(timer);
	}

	async function run(fn: () => Promise<void>) {
		busy = true;
		error = '';
		try {
			await fn();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}
	async function connected() {
		const name = s?.name ?? '';
		if (s?.service === 'ytm' && !s.connected) await put('/api/services/ytm/format', { format: ytPick });
		await Promise.all([app.loadServices(), app.loadLines()]);
		ui.say(t('services.connectedNamed', { service: name }), 'link');
		ui.serviceOpen = false;
	}

	// Spotify and TIDAL: sign in on the service's own page, which comes back to /services
	const oauth = (svc: 'spotify' | 'tidal') =>
		run(async () => {
			const r = await post<{ url: string }>(`/api/services/${svc}/start`);
			window.location.href = r.url;
		});
	const spotify = () => oauth('spotify');
	const deezer = () =>
		run(async () => {
			await post('/api/services/deezer/arl', { arl });
			arl = '';
			await connected();
		});
	const startDevice = () =>
		run(async () => {
			device = await post('/api/services/ytm/device');
			poll();
		});
	function poll() {
		if (!device) return;
		timer = setTimeout(
			async () => {
				if (!device || !ui.serviceOpen) return;
				try {
					const r = await post<{ status: string }>('/api/services/ytm/device/poll', { flow: device.flow });
					if (r.status === 'connected') {
						device = null;
						await connected();
					} else poll();
				} catch (e) {
					error = (e as Error).message;
					device = null;
				}
			},
			(device.interval || 5) * 1000
		);
	}
	const paste = () =>
		run(async () => {
			await post('/api/services/ytm/paste', { text: pasteText });
			pasteText = '';
			await connected();
		});
	const apple = () =>
		run(async () => {
			await post('/api/services/apple/token', { token: appleToken });
			appleToken = '';
			await connected();
		});
	function disconnect() {
		if (!s) return;
		const svc = s;
		ui.ask = {
			title: t('services.disconnectQ', { service: svc.name }),
			message: svc.lines ? t('services.disconnectMsg', { n: svc.lines }) : undefined,
			label: t('services.disconnect'),
			run: () =>
				run(async () => {
					await del(`/api/services/${svc.service}`);
					await Promise.all([app.loadServices(), app.loadLines()]);
					ui.say(t('services.disconnected', { service: svc.name }), 'link');
					ui.serviceOpen = false;
				})
		};
	}
	const bad = $derived(!!s?.connected && s.status === 'error');
	const days = $derived(s?.connected && s.expires_at ? Math.floor((s.expires_at - Date.now() / 1000) / 86400) : null);

	// try the login right now (Crossfade also does this by itself twice a day)
	const check = () =>
		run(async () => {
			const svc = s!;
			const r = await post<{ result: 'ok' | 'error' | 'unreachable' }>(`/api/services/${svc.service}/check`);
			await app.loadServices();
			if (r.result === 'ok') ui.say(t('services.checkOk'), 'check');
			else if (r.result === 'error') ui.say(t('services.checkFail'), 'alert', 'fault');
			else ui.say(t('services.checkDown', { service: svc.name }), 'alert', 'fault');
		});
	const dateFmt = (ts: number) => new Date(ts * 1000).toLocaleDateString(i18n.locale, { day: 'numeric', month: 'long', year: 'numeric' });

	// YouTube: music video or audio track for everything Crossfade adds
	async function setFormat(f: YtFormat) {
		ytPick = f;
		if (!s?.connected) return;
		try {
			await put('/api/services/ytm/format', { format: f });
			await app.loadServices();
		} catch (e) {
			ui.fail(e);
		}
	}
</script>

<Sheet bind:open={ui.serviceOpen} label={s?.name ?? ''} onclosed={closed}>
	{#snippet header()}
		<SheetBar title={s?.name ?? ''} leading={{ label: t('common.back'), icon: 'chevronLeft', run: () => (ui.serviceOpen = false) }} />
	{/snippet}
	{#if s}
		<div class="wrap">
			<div class="hero">
				<ServiceMark service={s.service} size={64} />
				{#if s.connected}
					<p class="t-headline">{bad ? t('services.expiredShort') : t('services.as', { account: s.display_name ?? '' })}</p>
					<p class="t-sub t-2">
						{bad && s.status_message ? s.status_message : t('services.usage', { n: s.lines, ago: ago(s.updated_at) })}
					</p>
				{:else}
					<p class="t-headline">{t('services.notConnected')}</p>
					<p class="t-sub t-2">{t('services.why')}</p>
				{/if}
			</div>

			{#if s.service === 'ytm'}
				<div class="section">
					<div class="section-head"><span class="caps">{t('services.yt.format')}</span></div>
					<div class="group" role="radiogroup" aria-label={t('services.yt.format')}>
						{#each [{ v: 'song', title: 'services.yt.songTitle', sub: 'services.yt.songSub' }, { v: 'video', title: 'services.yt.videoTitle', sub: 'services.yt.videoSub' }] as const as o (o.v)}
							<button class="cell pick" role="radio" aria-checked={ytPick === o.v} onclick={() => setFormat(o.v)}>
								<span class="grow">
									<span class="b">{t(o.title)}</span>
									<span class="b t-foot t-2">{t(o.sub)}</span>
								</span>
								<span class="ck" aria-hidden="true">{#if ytPick === o.v}<Glyph name="check" size={20} weight={2.6} />{/if}</span>
							</button>
						{/each}
					</div>
					<p class="section-foot">{t(s.connected ? 'services.yt.formatHint' : 'services.yt.formatLater')}</p>
				</div>
			{/if}

			<div class="section">
				<div class="section-head"><span class="caps">{s.connected ? t('services.renew') : t('services.connect')}</span></div>
				{#if s.service === 'spotify'}
					{#if s.methods.includes('oauth')}
						<div class="group">
							<button class="cell" onclick={spotify} disabled={busy}>
								<span class="grow tintc">{t('services.spotify.go')}</span>
								<span class="chev"><Glyph name="external" size={17} weight={2.2} /></span>
							</button>
						</div>
						<p class="section-foot">{t('services.spotify.hint')}</p>
					{:else if app.user?.is_admin}
						<div class="group">
							<button class="cell" onclick={() => (ui.appsOpen = true)}>
								<span class="grow tintc">{t('services.spotify.setup')}</span>
								<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
							</button>
						</div>
						<p class="section-foot">{t('services.spotify.setupHint')}</p>
					{:else}
						<p class="section-foot">{t('services.spotify.askAdmin')}</p>
					{/if}
				{:else if s.service === 'ytm'}
					{#if s.methods.includes('device')}
						<div class="way">
							<Segmented
								bind:value={ytmWay}
								label={t('services.ytm.way')}
								options={[
									{ value: 'device', label: t('services.ytm.device') },
									{ value: 'paste', label: t('services.ytm.paste') }
								]}
							/>
						</div>
					{/if}
					{#if ytmWay === 'device' && s.methods.includes('device')}
						{#if device}
							<div class="code-box glass">
								<p class="t-sub">{t('services.ytm.open')} <a href={device.verification_url} target="_blank" rel="noopener">{device.verification_url.replace('https://', '')}</a></p>
								<p class="code mono" aria-label={t('services.ytm.code')}>{device.user_code}</p>
								<p class="t-foot t-2 waiting">{t('services.ytm.waiting')}</p>
							</div>
						{:else}
							<div class="group">
								<button class="cell" onclick={startDevice} disabled={busy}><span class="grow tintc">{t('services.ytm.getCode')}</span></button>
							</div>
							<p class="section-foot">{t('services.ytm.deviceHint')}</p>
						{/if}
					{:else}
						{#if !s.methods.includes('device')}
							{#if app.user?.is_admin}
								<div class="group">
									<button class="cell" onclick={() => (ui.appsOpen = true)}>
										<span class="grow tintc">{t('services.ytm.setup')}</span>
										<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
									</button>
								</div>
								<p class="section-foot">{t('services.ytm.setupHint')}</p>
							{:else}
								<p class="section-foot">{t('services.ytm.askAdmin')}</p>
							{/if}
							<div class="section-head alt"><span class="caps">{t('services.ytm.paste')}</span></div>
						{/if}
						<ol class="steps t-sub">
							<li>{t('services.ytm.s1')} <a href="https://music.youtube.com" target="_blank" rel="noopener">music.youtube.com</a></li>
							<li>{t('services.ytm.s2', { keys: devtoolsKeys })}</li>
							<li>{t('services.ytm.s3')}</li>
							<li>{t('services.ytm.s4')}</li>
						</ol>
						<div class="group">
							<textarea class="paste mono" bind:value={pasteText} placeholder="curl 'https://music.youtube.com/youtubei/v1/browse?…' -H '…' -b '…'" aria-label={t('services.ytm.curl')}></textarea>
							<button class="cell" onclick={paste} disabled={busy || pasteText.length < 20}><span class="grow tintc">{t('services.connect')}</span></button>
						</div>
						<p class="section-foot">{t('services.ytm.pasteHint')}</p>
					{/if}
				{:else if s.service === 'tidal'}
					{#if s.methods.includes('oauth')}
						<div class="group">
							<button class="cell" onclick={() => oauth('tidal')} disabled={busy}>
								<span class="grow tintc">{t('services.tidal.go')}</span>
								<span class="chev"><Glyph name="external" size={17} weight={2.2} /></span>
							</button>
						</div>
						<p class="section-foot">{t('services.tidal.hint')}</p>
					{:else if app.user?.is_admin}
						<div class="group">
							<button class="cell" onclick={() => (ui.appsOpen = true)}>
								<span class="grow tintc">{t('services.tidal.setup')}</span>
								<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
							</button>
						</div>
						<p class="section-foot">{t('services.tidal.setupHint')}</p>
					{:else}
						<p class="section-foot">{t('services.tidal.askAdmin')}</p>
					{/if}
				{:else if s.service === 'deezer'}
					<ol class="steps t-sub">
						<li>{t('services.deezer.s1')} <a href="https://www.deezer.com" target="_blank" rel="noopener">deezer.com</a></li>
						<li>{t('services.deezer.s2')}</li>
						<li>{t('services.deezer.s3')}</li>
					</ol>
					<div class="group">
						<label class="cell">
							<input class="in mono" type="password" bind:value={arl} placeholder="arl" autocomplete="off" />
						</label>
						<button class="cell" onclick={deezer} disabled={busy || arl.length < 20}><span class="grow tintc">{t('services.connect')}</span></button>
					</div>
					<p class="section-foot">{t('services.deezer.hint')}</p>
				{:else}
					<ol class="steps t-sub">
						<li>{t('services.apple.s1')} <a href="https://music.apple.com" target="_blank" rel="noopener">music.apple.com</a></li>
						<li>{t('services.apple.s2')}</li>
						<li>{t('services.apple.s3')}</li>
					</ol>
					<div class="group">
						<label class="cell">
							<input class="in mono" type="password" bind:value={appleToken} placeholder="media-user-token" autocomplete="off" />
						</label>
						<button class="cell" onclick={apple} disabled={busy || appleToken.length < 20}><span class="grow tintc">{t('services.connect')}</span></button>
					</div>
					<p class="section-foot">{t('services.apple.hint')}</p>
				{/if}
				{#if error}<p class="section-foot danger" role="alert">{error}</p>{/if}
			</div>

			{#if s.connected && !bad}
				<div class="section">
					<div class="group">
						<div class="cell">
							<span class="grow">
								<span class="b" class:warn={days !== null && days < 7}>
									{days !== null && days < 7 ? t('services.expiresIn', { n: days }) : s.expires_at ? t('services.expiresOn', { date: dateFmt(s.expires_at) }) : t('services.checkOk')}
								</span>
								<span class="b t-foot t-2">{t('services.checked', { ago: ago(s.checked_at) })}</span>
							</span>
						</div>
						<button class="cell" onclick={check} disabled={busy}><span class="grow tintc">{t('services.check')}</span></button>
					</div>
					<p class="section-foot">{t('services.checkHint')}</p>
				</div>
			{/if}

			{#if s.connected}
				<div class="section">
					<div class="group">
						<button class="cell center" onclick={disconnect} disabled={busy}><span class="grow danger">{t('services.disconnect')}</span></button>
					</div>
					<p class="section-foot">{t('services.disconnectHint')}</p>
				</div>
			{/if}
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
		gap: 6px;
		padding: 8px 24px 26px;
		text-align: center;
	}
	.hero :global(svg),
	.hero :global(img) {
		margin-bottom: 8px;
	}
	.way {
		margin: 0 16px 12px;
	}
	.warn {
		color: var(--orange-text);
	}
	.pick {
		text-align: left;
	}
	.ck {
		display: inline-flex;
		width: 22px;
		color: var(--tint);
	}
	.section-head.alt {
		margin-top: 22px;
	}
	.steps {
		margin: 0 16px 14px;
		padding-left: 22px;
		display: grid;
		gap: 6px;
		color: var(--label-2);
	}
	.steps a,
	.code-box a {
		color: var(--tint);
		font-weight: 600;
	}
	.paste {
		display: block;
		width: 100%;
		min-height: 110px;
		padding: 12px 16px;
		border: 0;
		outline: none;
		resize: vertical;
		background: none;
		color: var(--label);
		font-size: var(--f-foot);
	}
	.code-box {
		display: grid;
		gap: 10px;
		margin: 0 16px;
		padding: 20px;
		border-radius: var(--r-group);
		text-align: center;
	}
	.code {
		font-size: 2.4rem;
		font-weight: 700;
		letter-spacing: 0.12em;
		user-select: all;
	}
	.waiting {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 8px;
	}
	.waiting::before {
		content: '';
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: var(--tint);
		animation: blink 1.2s ease-in-out infinite;
	}
	@keyframes blink {
		50% {
			opacity: 0.25;
		}
	}
</style>
