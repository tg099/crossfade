<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { api, put } from '#lib/api.ts';
	import { SERVICE_NAME } from '#lib/format.ts';
	import { app } from '#lib/state.svelte.ts';
	import { updates } from '#lib/updates.svelte.ts';
	import Avatar from '../Avatar.svelte';
	import Cover from '../Cover.svelte';
	import FadeBadge from '../FadeBadge.svelte';
	import Glyph from '../Glyph.svelte';
	import Mosaic from '../Mosaic.svelte';
	import Page from '../Page.svelte';
	import FriendsBoard from '../FriendsBoard.svelte';
	import WeekRecap from '../WeekRecap.svelte';
	import { fadeMenu, hold } from '../ctx.ts';
	import { fadeCovers, fadeIcon, fadeStatus, toneColor } from '../fade.ts';
	import { i18n, num, t, type Key } from '../i18n.svelte.ts';
	import { tintVars } from '../tint.ts';
	import { BRAND, ago, nav, ui } from '../ui.svelte.ts';

	const hour = new Date().getHours();
	const first = $derived(app.user?.display_name.split(' ')[0] ?? '');
	const hello = $derived(t(hour < 11 ? 'home.morning' : hour < 18 ? 'home.day' : 'home.evening', { name: first }));
	const today = $derived(new Date().toLocaleDateString(i18n.locale, { weekday: 'long', day: 'numeric', month: 'long' }));

	type Att = { key: string; text: string; sub: string; tone: 'red' | 'orange' | 'tint' | 'friend'; icon: 'key' | 'note' | 'trash' | 'users' | 'sparkle'; run: () => void; fade?: { icon: string; color: string } };
	// what needs a human, most important first
	const attention = $derived.by(() => {
		const out: Att[] = [];
		for (const s of app.services)
			if (s.connected && s.status === 'error')
				out.push({ key: `s${s.service}`, text: t('home.relogin', { service: s.name }), sub: s.lines ? t('n.inFades', { n: s.lines }) : '', tone: 'red', icon: 'key', run: () => ui.openAccount(s.service) });
		for (const f of app.lines) {
			if (f.last_status === 'needs_confirm' && !f.job && f.mine)
				out.push({ key: `h${f.id}`, text: t('status.held', { n: f.last_summary?.held ?? 0 }), sub: f.name, tone: 'orange', icon: 'trash', run: () => nav.openFade(f.id), fade: f });
			if (f.issues) out.push({ key: `i${f.id}`, text: t('home.waiting', { n: f.issues }), sub: f.name, tone: 'orange', icon: 'note', run: () => ui.review(f.id), fade: f });
			// a friend asked to join your fade
			for (const inv of f.invites)
				if (inv.request) out.push({ key: `q${inv.id}`, text: t('share.wants', { name: inv.user.display_name }), sub: f.name, tone: 'friend', icon: 'users', run: () => nav.openFade(f.id), fade: f });
		}
		// friends: an invitation into their fade (not yet answered), or someone asking to be friends
		for (const inv of app.friends.invites.filter((i) => !i.request))
			out.push({ key: `v${inv.id}`, text: t('friends.invitedBy', { name: inv.from.display_name }), sub: inv.fade.name, tone: 'tint', icon: 'users', run: () => ui.join(inv), fade: inv.fade });
		const asking = app.friends.incoming;
		if (asking.length) {
			const text = asking.length === 1 ? t('friends.wants', { name: asking[0].user.display_name }) : t('friends.requestsN', { n: asking.length });
			const sub = asking.length === 1 ? `@${asking[0].user.username}` : asking.map((r) => r.user.display_name).join(', ');
			out.push({ key: 'fr', text, sub, tone: 'friend', icon: 'users', run: () => ((ui.accountOpen = true), (ui.friendsOpen = true)) });
		}
		// admins: a new release, until its notes were opened from here
		if (app.user?.is_admin && updates.fresh)
			out.push({ key: 'upd', text: t('update.available', { version: (updates.state?.latest ?? '').toUpperCase() }), sub: t('update.whatsNew'), tone: 'tint', icon: 'sparkle', run: () => updates.open() });
		return out;
	});
	const TONE_BG: Record<Att['tone'], string> = { red: 'var(--red)', orange: 'var(--orange)', tint: 'var(--tint-fill)', friend: '#30b0c7' };
	// one honest sentence about right now: what is syncing, or how fresh everything is
	const status = $derived.by(() => {
		const parts: string[] = [];
		const busy = app.lines.filter((f) => f.job);
		const active = app.lines.filter((f) => !f.paused);
		if (busy.length) parts.push(t('home.syncing', { names: busy.map((f) => f.name).join(', ') }));
		else if (!active.length) parts.push(t('home.allPaused', { n: app.lines.length }));
		else {
			const last = Math.max(0, ...active.map((f) => f.last_run_at ?? 0));
			if (last) parts.push(t('home.lastSync', { ago: ago(last) }));
		}
		if (!attention.length) parts.push(t('home.allGood'));
		return parts.join(' · ');
	});
	const fresh = $derived(app.activity?.items ?? []);
	const needServices = $derived(app.connected.length < 2);

	// first steps: a checklist until everything is set up or put away (stored with the account)
	const showIntro = $derived(!!app.user && !app.user.intro_done && app.linesLoaded);
	let channels = $state<number | null>(null);
	$effect(() => {
		// again after the notification sheet closed: a channel may have been added
		if (!showIntro || ui.notifyOpen) return;
		api<{ channels: unknown[] }>('/api/notifications')
			.then((r) => (channels = r.channels.length))
			.catch(() => (channels = 0));
	});
	type Step = { id: 'services' | 'fade' | 'sync' | 'notify'; done: boolean; optional?: boolean };
	const steps = $derived.by<Step[]>(() => {
		// a friend's fade counts as a fade, and then one service of your own is enough
		const friends = app.lines.some((l) => !l.mine);
		return [
			{ id: 'services', done: app.connected.length >= 2 || (friends && app.connected.length >= 1) },
			{ id: 'fade', done: app.lines.length > 0 },
			{ id: 'sync', done: app.lines.some((l) => !!l.last_run_at) },
			{ id: 'notify', done: (channels ?? 0) > 0, optional: true }
		];
	});
	const step = $derived(steps.find((s) => !s.done)?.id ?? null);
	const stepsDone = $derived(steps.filter((s) => s.done).length);
	const firstFade = $derived(app.lines.find((l) => !l.last_run_at) ?? app.lines[0]);
	const STEP_TEXT: Record<Step['id'], [Key, Key]> = {
		services: ['intro.services', 'intro.servicesText'],
		fade: ['intro.fade', 'intro.fadeText'],
		sync: ['intro.sync', 'intro.syncText'],
		notify: ['intro.notify', 'intro.notifyText']
	};
	async function finishIntro() {
		if (!app.user) return;
		app.user.intro_done = true;
		try {
			await put('/api/auth/me', { intro_done: true });
		} catch {
			ui.say(t('err.notSaved'), 'alert', 'fault');
		}
	}
</script>

{#snippet serviceList()}
	<ul class="group svc">
		{#each app.services as s (s.service)}
			<li>
				<button class="cell" style:--inset="58px" onclick={() => ui.openAccount(s.service)}>
					<ServiceMark service={s.service} size={30} />
					<span class="grow">
						<span class="block">{s.name}</span>
						<span class="t-foot t-2 block">{s.connected ? t('services.as', { account: s.display_name ?? '' }) : t('services.notConnected')}</span>
					</span>
					{#if s.connected}<span class="ok"><Glyph name="check" size={18} weight={2.6} /></span>{:else}<span class="cell-pill">{t('services.connect')}</span>{/if}
				</button>
			</li>
		{/each}
	</ul>
{/snippet}

{#snippet templates()}
	<ul class="tpls">
		<li>
			<button class="tpl glass lq" onclick={() => ui.edit(0, 'likes')}>
				<span class="tile-icon" style:background="#C4007A"><Glyph name="heart" size={18} weight={2.3} /></span>
				<span class="b">{t('start.likes')}</span>
				<span class="t-foot t-2">{t('start.likesText', { services: app.connected.map((s) => s.name).join(' · ') })}</span>
			</button>
		</li>
		<li>
			<button class="tpl glass lq" onclick={() => ui.edit(0, 'copy')}>
				<span class="tile-icon" style:background="#0072CE"><Glyph name="right" size={18} weight={2.3} /></span>
				<span class="b">{t('start.copy')}</span>
				<span class="t-foot t-2">{t('start.copyText')}</span>
			</button>
		</li>
		<li>
			<button class="tpl glass lq" onclick={() => ui.edit(0)}>
				<span class="tile-icon" style:background="#8E8E93"><Glyph name="plus" size={18} weight={2.6} /></span>
				<span class="b">{t('start.own')}</span>
				<span class="t-foot t-2">{t('start.ownText')}</span>
			</button>
		</li>
	</ul>
{/snippet}

<Page title={t('tab.home')} large={false} glow={BRAND}>
	{#snippet hero()}
		<header class="hello">
			<div class="hello-top">
				<p class="date t-sub">{today}</p>
				<span class="av"><Avatar /></span>
			</div>
			<h1 class="t-large" data-title>{hello}</h1>
			{#if app.lines.length}
				<p class="summary t-sub">
					{status}
				</p>
			{/if}
		</header>
	{/snippet}

	{#if showIntro}
		<section class="section" aria-labelledby="intro-h">
			<div class="start glass strong">
				<div class="intro-head">
					<h2 id="intro-h" class="t-title3">{step ? t('intro.title') : t('intro.allDone')}</h2>
					<button class="text-btn" onclick={finishIntro}>{step ? t('intro.hide') : t('intro.finish')}</button>
				</div>
				<p class="t-sub t-2">{step ? t('intro.progress', { done: stepsDone, total: steps.length }) : t('intro.allDoneText')}</p>
				<div class="meter" role="progressbar" aria-valuemin="0" aria-valuemax={steps.length} aria-valuenow={stepsDone} aria-label={t('intro.title')}>
					<span style:width="{(stepsDone / steps.length) * 100}%"></span>
				</div>
				<ol class="steps">
					{#each steps as s, i (s.id)}
						<li class:done={s.done} class:now={step === s.id}>
							<span class="mark" aria-hidden="true">{#if s.done}<Glyph name="check" size={14} weight={3} />{:else}{i + 1}{/if}</span>
							<div class="step-body">
								<p class="step-t">
									{t(STEP_TEXT[s.id][0])}
									{#if s.optional}<span class="tag">{t('intro.optional')}</span>{/if}
								</p>
								{#if step === s.id}
									<p class="t-foot t-2">{t(STEP_TEXT[s.id][1])}</p>
									<div class="step-do">
										{#if s.id === 'services'}
											{@render serviceList()}
										{:else if s.id === 'fade'}
											{@render templates()}
										{:else if s.id === 'sync' && firstFade}
											<button class="pb glass tint lq pb-small" style={tintVars(firstFade.color)} onclick={() => nav.openFade(firstFade.id)}>{t('intro.syncGo', { name: firstFade.name })}</button>
										{:else if s.id === 'notify'}
											<button class="pb glass tint lq pb-small" onclick={() => ((ui.accountOpen = true), (ui.notifyOpen = true))}><Glyph name="bell" size={16} weight={2.3} />{t('intro.notifyGo')}</button>
										{/if}
									</div>
								{/if}
							</div>
						</li>
					{/each}
				</ol>
			</div>
		</section>
	{:else if needServices}
		<!-- first steps were put away: services, then a fade -->
		<section class="section">
			<div class="start glass strong">
				<p class="t-title3">{t('start.servicesTitle')}</p>
				<p class="t-sub t-2">{t('start.servicesText')}</p>
				{@render serviceList()}
			</div>
		</section>
	{:else if app.linesLoaded && !app.lines.length}
		<section class="section" aria-labelledby="tpl-h">
			<div class="section-head"><h2 id="tpl-h">{t('start.fadeTitle')}</h2></div>
			<p class="t-sub t-2 lead">{t('start.fadeText')}</p>
			{@render templates()}
		</section>
	{/if}


	{#if !app.lines.length}
	{#if attention.length}
		<section class="section" aria-label={t('home.attention')}>
			<ul class="att">
				{#each attention as a (a.key)}
					<li>
						<button class="att-card glass lq tinted" style={a.fade ? tintVars(a.fade.color) : ''} onclick={a.run}>
							<span class="tile-icon" style:background={TONE_BG[a.tone]}>
								<Glyph name={a.icon} size={18} weight={2.3} />
							</span>
							<span class="grow">
								<span class="block att-t">{a.text}</span>
								{#if a.sub}
									<span class="t-foot t-2 block att-f">{#if a.fade}<FadeBadge icon={fadeIcon(a.fade)} color={a.fade.color} size={16} />{/if} {a.sub}</span>
								{/if}
							</span>
							<span class="chev"><Glyph name="chevronRight" size={18} weight={2.6} /></span>
						</button>
					</li>
				{/each}
			</ul>
		</section>
	{/if}
	{:else}
		<div class="dash">
			<div class="main">
		{#if attention.length}
			<section class="section" aria-label={t('home.attention')}>
				<ul class="att">
					{#each attention as a (a.key)}
						<li>
							<button class="att-card glass lq tinted" style={a.fade ? tintVars(a.fade.color) : ''} onclick={a.run}>
								<span class="tile-icon" style:background={TONE_BG[a.tone]}>
									<Glyph name={a.icon} size={18} weight={2.3} />
								</span>
								<span class="grow">
									<span class="block att-t">{a.text}</span>
									{#if a.sub}
										<span class="t-foot t-2 block att-f">{#if a.fade}<FadeBadge icon={fadeIcon(a.fade)} color={a.fade.color} size={16} />{/if} {a.sub}</span>
									{/if}
								</span>
								<span class="chev"><Glyph name="chevronRight" size={18} weight={2.6} /></span>
							</button>
						</li>
					{/each}
				</ul>
			</section>
		{/if}
			<section class="section" aria-labelledby="fades-h">
				<div class="section-head">
					<h2 id="fades-h">{t('home.yourFades')}</h2>
					<button class="gbtn glass lq" onclick={() => nav.select('fades')}>{t('common.all')} <Glyph name="chevronRight" size={14} weight={2.8} /></button>
				</div>
				<!-- like the Home app's tiles: what it is, how it is, one tap in -->
				<ul class="group">
					{#each app.lines as f (f.id)}
						{@const raw = fadeStatus(f)}
						<!-- open songs already have their card above: the row tells how fresh the fade is -->
						{@const st = f.issues && raw.tone === 'attention' && f.last_run_at ? { tone: 'good' as const, text: t('status.current', { ago: ago(f.last_run_at) }) } : raw}
						<li>
							<button class="cell" style:--inset="74px" onclick={() => nav.openFade(f.id)} use:hold={() => fadeMenu(f.id)}>
								<span class="fcover">
									<Mosaic covers={fadeCovers(f)} seed={f.name} color={f.color} size={44} />
									<span class="fbadge"><FadeBadge icon={fadeIcon(f)} color={f.color} size={18} /></span>
								</span>
								<span class="grow">
									<span class="clip block f-name">{f.name}</span>
									<span class="c-meta t-foot t-2">
										<span class="dot" class:pulse={st.tone === 'running'} style:background={st.tone === 'running' ? f.color : toneColor[st.tone]}></span>
										<span class="clip">{st.text}</span>
									</span>
								</span>
								{#if f.issues}<span class="tag warn">{t('member.unmatched', { n: num(f.issues) })}</span>{/if}
								<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
							</button>
						</li>
					{/each}
				</ul>
			</section>
			</div>
			<!-- the numbers: one column of their own, every card the same width -->
			<div class="side">
				<WeekRecap />
				<FriendsBoard />
			</div>
		{#if fresh.length}
			<section class="section fresh" aria-labelledby="fresh-h">
				<div class="section-head"><h2 id="fresh-h">{t('home.fresh')}</h2></div>
				<ul class="shelf">
					{#each fresh as x (x.song_id)}
						<li>
							<button class="song press" onclick={() => nav.openFade(x.line_id)} title={t('home.freshVia', { name: x.line_name })}>
								<span class="scover">
									<Cover src={x.artwork} seed={String(x.song_id)} size="100%" radius={0.14} song={x.song_id} />
									{#if x.service}<span class="via glass"><ServiceMark service={x.service} size={18} title={false} /></span>{/if}
								</span>
								<span class="s-t clip">{x.title ?? x.label}</span>
								<span class="t-foot t-2 clip">{x.artist ?? ''}</span>
							</button>
						</li>
					{/each}
				</ul>
			</section>
		{/if}
		</div>
	{/if}

</Page>

<style>
	.hello {
		padding: 0 20px 26px;
	}
	/* the greeting stays on one line on a phone */
	.hello h1 {
		font-size: min(var(--f-large), 7.4vw);
	}
	.hello-top {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 6px;
		min-height: 40px;
	}
	.date {
		color: var(--label-2);
		font-weight: 600;
	}
	.summary {
		margin-top: 8px;
		color: var(--label-2);
	}
	.block,
	.b {
		display: block;
	}
	.b {
		font-weight: 700;
	}
	.start {
		display: grid;
		gap: 8px;
		padding: 20px 16px 16px;
		border-radius: 30px;
	}
	.start > p {
		padding: 0 4px;
	}
	.svc {
		margin-top: 8px;
	}
	.intro-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		padding: 0 4px;
	}
	.meter {
		height: 6px;
		margin: 6px 4px 4px;
		border-radius: 999px;
		background: var(--fill-3);
		overflow: hidden;
	}
	.meter span {
		display: block;
		height: 100%;
		border-radius: inherit;
		background: linear-gradient(90deg, #ff3b4e, #2e6bff);
		transition: width 0.4s cubic-bezier(0.2, 0.8, 0.2, 1);
	}
	.steps {
		display: grid;
		gap: 4px;
		margin: 6px 0 0;
		padding: 0;
		list-style: none;
	}
	.steps li {
		display: flex;
		align-items: flex-start;
		gap: 12px;
		padding: 8px 4px;
	}
	.mark {
		display: grid;
		flex: none;
		place-items: center;
		width: 26px;
		height: 26px;
		margin-top: -1px;
		border-radius: 50%;
		background: var(--fill-3);
		color: var(--label-2);
		font-size: var(--f-foot);
		font-weight: 700;
		font-variant-numeric: tabular-nums;
	}
	.now .mark {
		background: var(--label);
		color: var(--bg);
	}
	.done .mark {
		background: var(--green);
		color: #fff;
	}
	.step-body {
		flex: 1;
		min-width: 0;
		display: grid;
		gap: 4px;
	}
	.step-t {
		display: flex;
		align-items: center;
		gap: 8px;
		font-weight: 600;
	}
	.done .step-t {
		color: var(--label-2);
		font-weight: 500;
	}
	.step-do {
		margin-top: 6px;
	}
	.step-do .svc {
		margin-top: 0;
	}
	.step-do .pb {
		gap: 6px;
	}
	@media (prefers-reduced-motion: reduce) {
		.meter span {
			transition: none;
		}
	}
	.ok {
		color: var(--green);
	}
	.lead {
		padding: 0 6px 14px;
	}
	.tpls {
		display: grid;
		grid-auto-rows: 1fr;
		gap: 10px;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.tpl {
		display: grid;
		grid-template-columns: auto 1fr;
		column-gap: 12px;
		row-gap: 2px;
		align-content: start;
		align-items: center;
		width: 100%;
		height: 100%;
		padding: 14px 16px;
		border-radius: 24px;
		text-align: left;
	}
	.tpl .tile-icon {
		grid-row: span 2;
		align-self: start;
		margin-top: 2px;
	}
	.tpls li {
		display: flex;
	}
	.dash,
	.main {
		display: contents;
	}
	.att {
		display: grid;
		gap: 10px;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.att-card {
		display: flex;
		align-items: center;
		gap: 12px;
		width: 100%;
		min-height: 64px;
		padding: 12px 16px 12px 14px;
		border-radius: 26px;
		text-align: left;
		background: color-mix(in srgb, var(--glass-strong) 80%, var(--raw) 6%);
	}
	.att-t {
		font-weight: 600;
	}
	.att-f {
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.chev {
		color: var(--label-3);
	}
	.fcover {
		position: relative;
		flex: none;
	}
	.fbadge {
		position: absolute;
		right: -5px;
		bottom: -5px;
		display: flex;
		border-radius: 7px;
		box-shadow: 0 0 0 2px var(--cell-grouped-solid, var(--bg));
	}
	.f-name {
		font-weight: 600;
	}
	.c-meta {
		display: flex;
		align-items: center;
		gap: 6px;
		min-width: 0;
	}
	.dot {
		flex: none;
		width: 7px;
		height: 7px;
		border-radius: 50%;
	}
	.pulse {
		animation: pulse 1.4s ease-in-out infinite alternate;
	}
	@keyframes pulse {
		to {
			opacity: 0.3;
		}
	}
	.shelf {
		display: grid;
		grid-auto-flow: column;
		grid-auto-columns: 146px;
		gap: 14px;
		margin: 0 -16px;
		padding: 0 16px 4px;
		list-style: none;
		overflow-x: auto;
		scroll-snap-type: x mandatory;
		scroll-padding: 0 16px;
		scrollbar-width: none;
		overscroll-behavior-x: contain;
	}
	.shelf li {
		scroll-snap-align: start;
	}
	.song {
		display: grid;
		gap: 1px;
		width: 100%;
		text-align: left;
	}
	.scover {
		position: relative;
		margin-bottom: 8px;
		box-shadow: 0 10px 24px -12px rgb(0 0 0 / 0.4);
		border-radius: 20px;
	}
	.via {
		position: absolute;
		right: 7px;
		bottom: 7px;
		display: grid;
		place-items: center;
		width: 30px;
		height: 30px;
		border-radius: 50%;
	}
	.s-t {
		font-size: var(--f-sub);
		font-weight: 600;
	}
	@media (min-width: 721px) {
		.hello {
			margin-top: calc(var(--bar) * -1 + 24px);
			padding: 0 36px 30px;
		}
		.av {
			display: none;
		}
		/* one column on narrow windows; from 1100px what to do (left) and the week (right) side by
		   side, the left one taking the extra width */
		.dash {
			display: grid;
			grid-template-columns: minmax(0, 1fr);
			grid-template-areas: 'main' 'week' 'fresh';
			align-items: start;
			gap: 20px;
			margin: 0 36px 34px;
			max-width: var(--content-max);
		}
		.dash:empty {
			display: none;
		}
		@media (min-width: 1100px) {
			/* what to do and what is new on the left, the week alongside: no empty column */
			.dash {
				grid-template-columns: minmax(0, 3fr) minmax(340px, 2fr);
				grid-template-rows: auto 1fr;
				grid-template-areas: 'main week' 'fresh week';
				gap: 28px 28px;
			}
		}
		.main {
			display: grid;
			gap: 26px;
			align-content: start;
		}
		.main {
			grid-area: main;
		}
		.side {
			grid-area: week;
			align-self: start;
			display: grid;
			gap: 26px;
			min-width: 0;
		}
		/* side by side and the left column opens with a heading: start the week card level with the
		   list below that heading, not with the heading itself (34px button + 10px gap) */
		@media (min-width: 1100px) {
			.dash:has(> .main > .section:first-child > .section-head) > .side {
				margin-top: 44px;
			}
		}
		.fresh {
			grid-area: fresh;
		}
		.main > .section,
		.dash > .fresh,
		.side > :global(.week),
		.side > :global(.section) {
			margin: 0;
			max-width: none;
		}
		.fresh .shelf {
			margin: 0;
			padding: 0 0 4px;
			scroll-padding: 0;
		}
		.att,
		.tpls {
			grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
		}
		.shelf {
			margin: 0 -36px;
			padding: 0 36px 4px;
			scroll-padding: 0 36px;
			grid-auto-columns: 170px;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.pulse {
			animation: none;
		}
	}
</style>
