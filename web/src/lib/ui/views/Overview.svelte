<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { SERVICE_NAME } from '#lib/format.ts';
	import { app } from '#lib/state.svelte.ts';
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
	import { i18n, num, t } from '../i18n.svelte.ts';
	import { tintVars } from '../tint.ts';
	import { BRAND, ago, nav, ui } from '../ui.svelte.ts';

	const hour = new Date().getHours();
	const first = $derived(app.user?.display_name.split(' ')[0] ?? '');
	const hello = $derived(t(hour < 11 ? 'home.morning' : hour < 18 ? 'home.day' : 'home.evening', { name: first }));
	const today = $derived(new Date().toLocaleDateString(i18n.locale, { weekday: 'long', day: 'numeric', month: 'long' }));

	type Att = { key: string; text: string; sub: string; tone: 'red' | 'orange' | 'tint' | 'friend'; icon: 'key' | 'note' | 'trash' | 'users'; run: () => void; fade?: { icon: string; color: string } };
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
		}
		// friends: an invitation into their fade, or someone asking to be friends
		for (const inv of app.friends.invites)
			out.push({ key: `v${inv.id}`, text: t('friends.invitedBy', { name: inv.from.display_name }), sub: inv.fade.name, tone: 'tint', icon: 'users', run: () => ui.join(inv), fade: inv.fade });
		const asking = app.friends.incoming;
		if (asking.length) {
			const text = asking.length === 1 ? t('friends.wants', { name: asking[0].user.display_name }) : t('friends.requestsN', { n: asking.length });
			const sub = asking.length === 1 ? `@${asking[0].user.username}` : asking.map((r) => r.user.display_name).join(', ');
			out.push({ key: 'fr', text, sub, tone: 'friend', icon: 'users', run: () => ((ui.accountOpen = true), (ui.friendsOpen = true)) });
		}
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
</script>

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

	{#if needServices}
		<!-- first steps: services, then a fade -->
		<section class="section">
			<div class="start glass strong">
				<p class="t-title3">{t('start.servicesTitle')}</p>
				<p class="t-sub t-2">{t('start.servicesText')}</p>
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
			</div>
		</section>
	{:else if app.linesLoaded && !app.lines.length}
		<section class="section" aria-labelledby="tpl-h">
			<div class="section-head"><h2 id="tpl-h">{t('start.fadeTitle')}</h2></div>
			<p class="t-sub t-2 lead">{t('start.fadeText')}</p>
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
