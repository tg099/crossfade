<script lang="ts">
	// The signed-in app: sidebar (desktop) or tab bar (phone), views per tab with their own stacks,
	// and the sheets. Every place has a URL: /, /fades, /fades/3, /library, /search.
	import { onMount, tick } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import Logo from '#lib/components/Logo.svelte';
	import { SERVICES } from '#lib/format.ts';
	import { app } from '#lib/state.svelte.ts';
	import type { Line, Service } from '#lib/types.ts';
	import Account from '#lib/ui/Account.svelte';
	import ActionSheet from '#lib/ui/ActionSheet.svelte';
	import AppsSheet from '#lib/ui/AppsSheet.svelte';
	import ContextMenu from '#lib/ui/ContextMenu.svelte';
	import Editor from '#lib/ui/Editor.svelte';
	import FadeBadge from '#lib/ui/FadeBadge.svelte';
	import FriendsSheet from '#lib/ui/FriendsSheet.svelte';
	import Glyph from '#lib/ui/Glyph.svelte';
	import Hearing from '#lib/ui/Hearing.svelte';
	import HistorySheet from '#lib/ui/HistorySheet.svelte';
	import JoinSheet from '#lib/ui/JoinSheet.svelte';
	import { moveKeys } from '#lib/ui/keys.ts';
	import PasswordSheet from '#lib/ui/PasswordSheet.svelte';
	import PlanSheet from '#lib/ui/PlanSheet.svelte';
	import ReviewSheet from '#lib/ui/ReviewSheet.svelte';
	import SongSheet from '#lib/ui/SongSheet.svelte';
	import UserSheet from '#lib/ui/UserSheet.svelte';
	import UsersSheet from '#lib/ui/UsersSheet.svelte';
	import { userHue } from '#lib/ui/artcolor.ts';
	import { fadeMenu, hold } from '#lib/ui/ctx.ts';
	import { fadeIcon } from '#lib/ui/fade.ts';
	import { t } from '#lib/ui/i18n.svelte.ts';
	import { gesture, project, Spring, VelocityTracker } from '#lib/ui/motion.ts';
	import { Reorder } from '#lib/ui/reorder.svelte.ts';
	import { nav, TABS, ui, type Tab, type View } from '#lib/ui/ui.svelte.ts';
	import FadeDetail from '#lib/ui/views/FadeDetail.svelte';
	import Fades from '#lib/ui/views/Fades.svelte';
	import Library from '#lib/ui/views/Library.svelte';
	import Overview from '#lib/ui/views/Overview.svelte';
	import Search from '#lib/ui/views/Search.svelte';

	let ready = $state(false);
	let W = $state(390);

	const SPOTIFY_ERRORS: Record<string, string> = {
		'spotify-cancelled': 'services.spotify.cancelled',
		'spotify-premium': 'services.spotify.premium'
	};

	onMount(() => {
		const mq = matchMedia('(max-width: 720px)');
		const set = () => (ui.phone = mq.matches);
		set();
		mq.addEventListener('change', set);
		// back from the Spotify sign-in (the server redirects to /services)
		const url = page.url;
		if (url.pathname.startsWith('/services')) {
			const ok = url.searchParams.get('connected');
			const err = url.searchParams.get('error');
			if (ok === 'spotify') {
				ui.say(t('services.connectedNamed', { service: 'Spotify' }));
				app.loadServices();
				app.loadLines();
			} else if (err) ui.say(t((SPOTIFY_ERRORS[err] ?? 'services.spotify.failed') as never), 'alert', 'fault');
			// a notification links to the service whose login needs renewing (/services?service=apple)
			const focus = url.searchParams.get('service') as Service | null;
			const back = (ok || err?.split('-')[0]) as Service | null;
			ui.openAccount(focus && SERVICES.includes(focus) ? focus : back && SERVICES.includes(back) ? back : null);
			goto('/', { replaceState: true }).then(() => nav.init('/'));
		} else nav.init(url.pathname);
		ready = true;
		return () => mq.removeEventListener('change', set);
	});

	// back / forward / swipe from the edge: bring tabs and stacks in line with the URL
	$effect(() => {
		const st = page.state;
		const path = page.url.pathname;
		if (ready) nav.sync(st as never, path);
	});

	const push = $derived(ui.sheets[0]?.p ?? 0);
	const titleOf = (v: View, tab: Tab) =>
		v.kind === 'fade' ? (app.line(v.id)?.name ?? t('fade.one')) : t(TABS.find((x) => x.id === tab)!.label);
	const keyOf = (v: View, i: number) => (v.kind === 'fade' ? `f${v.id}` : 'root') + i;
	// ---------------------------------------------------------------- interactive pop (phone)
	const token = Symbol('pop');
	const tracker = new VelocityTracker();
	let pan: { x: number; y: number; id: number; locked: boolean } | null = null;
	function pdown(e: PointerEvent) {
		if (!ui.phone || nav.stack.length < 2 || e.button !== 0) return;
		pan = { x: e.clientX, y: e.clientY, id: e.pointerId, locked: false };
		tracker.reset();
		tracker.add(e.clientX, 0);
	}
	function pmove(e: PointerEvent) {
		if (!pan || pan.id !== e.pointerId) return;
		const dx = e.clientX - pan.x;
		const dy = e.clientY - pan.y;
		if (!pan.locked) {
			if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) return void (pan = null);
			if (dx > 10 && dx > Math.abs(dy) * 1.3 && !gesture.busy() && gesture.claim(token)) {
				pan.locked = true;
				pan.x = e.clientX;
				(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
				nav.panStart();
			}
			return;
		}
		tracker.add(e.clientX, 0);
		nav.panMove(e.clientX - pan.x);
	}
	function pup() {
		if (!pan) return;
		const locked = pan.locked;
		pan = null;
		if (!locked) return;
		gesture.release(token);
		nav.panEnd(tracker.get().x);
	}

	// ---------------------------------------------------------------- tab bar: a liquid lens you can drag
	const BAR_TABS = TABS.filter((x) => x.id !== 'search');
	let lens = $state(0);
	let lensStretch = $state(0);
	let lensHeld = $state(false);
	const lensSpring = new Spring(
		0,
		(v) => {
			lens = v;
			if (!lensHeld) lensStretch = Math.min(0.32, Math.abs(lensSpring.velocity) * 0.06);
		},
		0.002
	);
	const tabIndex = $derived(BAR_TABS.findIndex((x) => x.id === nav.tab));
	let lensReady = false;
	$effect(() => {
		const i = tabIndex;
		if (i < 0) return;
		if (!lensReady) {
			lensSpring.set(i);
			lensReady = true;
		} else lensSpring.to(i, { response: 0.4, damping: 0.72 });
	});

	let barEl: HTMLElement | undefined = $state();
	const lensTracker = new VelocityTracker();
	let lensDrag: { x0: number; id: number; started: boolean } | null = null;
	let swallowClick = false;
	function slotAt(clientX: number) {
		const r = barEl!.getBoundingClientRect();
		const slot = (r.width - 8) / BAR_TABS.length;
		return { idx: (clientX - r.left - 4) / slot - 0.5, slot };
	}
	function bdown(e: PointerEvent) {
		if (ui.searching || e.button !== 0) return;
		lensDrag = { x0: e.clientX, id: e.pointerId, started: false };
		swallowClick = false;
		lensTracker.reset();
		lensTracker.add(e.clientX, 0);
	}
	function bmove(e: PointerEvent) {
		if (!lensDrag || lensDrag.id !== e.pointerId) return;
		if (!lensDrag.started) {
			if (Math.abs(e.clientX - lensDrag.x0) < 6) return;
			lensDrag.started = true;
			lensHeld = true;
			(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
		}
		lensTracker.add(e.clientX, 0);
		const { idx, slot } = slotAt(e.clientX);
		lensSpring.set(Math.max(-0.15, Math.min(BAR_TABS.length - 0.85, idx)));
		lensStretch = Math.min(0.32, Math.abs(lensTracker.get().x / slot) * 0.06);
	}
	function bup() {
		if (!lensDrag) return;
		const started = lensDrag.started;
		lensDrag = null;
		if (!started) return;
		swallowClick = true;
		lensHeld = false;
		const { slot } = slotAt(0);
		const v = lensTracker.get().x / slot;
		const target = Math.round(Math.max(0, Math.min(BAR_TABS.length - 1, lens + project(v * slot, 0.99) / slot)));
		lensSpring.velocity = v;
		if (BAR_TABS[target].id === nav.tab) lensSpring.to(target, { response: 0.4, damping: 0.72 });
		else nav.select(BAR_TABS[target].id);
	}
	function tabClick(tab: Tab) {
		if (swallowClick) return void (swallowClick = false);
		nav.select(tab);
	}

	// ---------------------------------------------------------------- search: type right away
	// The field already exists inside the search button, so focusing it in the tap itself opens
	// the keyboard immediately (iOS only allows that synchronously, inside the gesture).
	let dockInput: HTMLInputElement | undefined = $state();
	function startSearch() {
		dockInput?.focus();
		ui.searching = true;
		nav.select('search');
	}
	function leaveSearch() {
		dockInput?.blur();
		ui.query = '';
		nav.select(nav.lastTab);
	}
	function searchKey(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			if (ui.query) ui.query = '';
			else leaveSearch();
		}
	}
	let sideInput: HTMLInputElement | undefined = $state();
	function sideFocus() {
		if (nav.tab !== 'search') nav.select('search');
	}
	function globalKey(e: KeyboardEvent) {
		// ⌘K / Ctrl+K: search from anywhere (desktop)
		if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
			e.preventDefault();
			if (ui.phone) startSearch();
			else tick().then(() => sideInput?.focus());
		}
	}

	// ---------------------------------------------------------------- sidebar: drag your fades into your order;
	// friends' fades get a section of their own below and keep their place
	const ownFades = $derived(app.lines.filter((f) => f.mine));
	const friendFades = $derived(app.lines.filter((f) => !f.mine));
	const sideOrder = new Reorder(
		() => ownFades.map((f) => f.id),
		(from, to) => app.moveLine(from, to),
		6
	);

	const sidebarActive = (tab: Tab) => nav.tab === tab && !(tab === 'fades' && nav.stack.length > 1);
	const activeFade = $derived.by(() => {
		const top = nav.stacks.fades[nav.stacks.fades.length - 1];
		return nav.tab === 'fades' && top.kind === 'fade' ? top.id : null;
	});
	const backTab = $derived(TABS.find((x) => x.id === nav.lastTab)!);
	const services = $derived(app.services.filter((s) => s.connected).length);
</script>

<svelte:head><title>{nav.tab === 'fades' && activeFade ? `${app.line(activeFade)?.name ?? ''} – ` : ''}Crossfade</title></svelte:head>
<svelte:window bind:innerWidth={W} onkeydown={globalKey} />

{#if ready}
	<div class="stage" class:pushed={push > 0}>
		<div
			class="shell"
			inert={ui.sheets.length > 0}
			style:transform={ui.phone && push > 0 ? `translate3d(0, ${push * 10}px, 0) scale(${1 - push * 0.07})` : undefined}
			style:border-radius={ui.phone && push > 0 ? `${push * 38}px` : undefined}
			style:--chrome-bottom={ui.phone ? 'calc(var(--safe-bottom) + 110px)' : '100px'}
		>
			<main class="content">
				{#each TABS as tb (tb.id)}
					{@const stack = nav.stacks[tb.id]}
					<div class="tab" class:parked={nav.tab !== tb.id} data-tab={tb.id}>
						{#each stack as v, i (keyOf(v, i))}
							{@const depth = stack.length - 1 - i}
							{@const live = nav.tab === tb.id && nav.moving}
							<section
								class="view"
								class:top={depth === 0}
								class:parked={depth > 1 || (depth === 1 && !live)}
								aria-hidden={depth > 0 ? 'true' : undefined}
								inert={depth > 0}
								style:transform={live && depth === 0 && nav.x ? `translate3d(${nav.x}px,0,0)` : live && depth === 1 ? `translate3d(${-(W - nav.x) * 0.3}px,0,0)` : undefined}
								onpointerdown={depth === 0 && i > 0 ? pdown : undefined}
								onpointermove={depth === 0 && i > 0 ? pmove : undefined}
								onpointerup={depth === 0 && i > 0 ? pup : undefined}
								onpointercancel={depth === 0 && i > 0 ? pup : undefined}
							>
								{#if v.kind === 'root'}
									{#if tb.id === 'home'}<Overview />
									{:else if tb.id === 'fades'}<Fades />
									{:else if tb.id === 'library'}<Library />
									{:else}<Search />{/if}
								{:else}
									<FadeDetail id={v.id} back={ui.phone || i > 1 || tb.id !== 'fades' ? { label: titleOf(stack[i - 1], tb.id), run: () => nav.back() } : undefined} />
								{/if}
								{#if live && depth === 1}<span class="under-dim" style:opacity={(1 - nav.x / W) * 0.12}></span>{/if}
							</section>
						{/each}
					</div>
				{/each}
			</main>

			<!-- desktop: a glass sidebar floating over the content -->
			{#snippet sideFade(f: Line)}
				<FadeBadge icon={fadeIcon(f)} color={f.color} size={22} />
				<span class="clip grow">{f.name}</span>
				{#if f.members.some((m) => !m.account_connected || m.account_status === 'error')}<span class="sdot red"></span>{:else if f.issues || f.last_status === 'needs_confirm' || f.invites.some((x) => x.request)}<span class="sdot orange"></span>{:else if f.job}<span class="sdot run" style:background={f.color}></span>{/if}
			{/snippet}
			<aside class="sidebar glass" aria-label={t('nav.sidebar')}>
				<div class="brand"><Logo size={30} /> Crossfade</div>
				<label class="side-search" class:on={nav.tab === 'search'}>
					<Glyph name="search" size={16} weight={2.2} />
					<input bind:this={sideInput} bind:value={ui.query} onfocus={sideFocus} onkeydown={searchKey} placeholder={t('search.placeholder')} type="search" autocomplete="off" />
					<kbd>⌘K</kbd>
				</label>
				<nav>
					<button class="side-item" aria-current={sidebarActive('home') ? 'page' : undefined} onclick={() => nav.select('home')}>
						<Glyph name="home" size={20} />
						{t('tab.home')}
					</button>
					<p class="side-head">{t('tab.library')}</p>
					<button class="side-item" aria-current={sidebarActive('library') ? 'page' : undefined} onclick={() => nav.select('library')}>
						<Glyph name="library" size={20} />
						{t('lib.songs')}
					</button>
					<div class="side-head side-row">
						<span>{t('tab.fades')}</span>
						<button class="side-add" onclick={() => ui.edit(0)} aria-label={t('fades.new')}><Glyph name="plus" size={16} weight={2.4} /></button>
					</div>
					<button class="side-item" aria-current={sidebarActive('fades') ? 'page' : undefined} onclick={() => nav.select('fades')}>
						<Glyph name="fades" size={20} />
						{t('fades.all')}
					</button>
					<ul class="side-fades">
						{#each ownFades as f, i (f.id)}
							<li class:lifted={sideOrder.dragId === f.id} style:transform={sideOrder.style(f.id)}>
								<button
									class="side-item"
									aria-current={activeFade === f.id ? 'page' : undefined}
									onclick={() => nav.openFade(f.id)}
									onclickcapture={sideOrder.click}
									onpointerdown={(e) => sideOrder.down(e, i)}
									onpointermove={sideOrder.move}
									onpointerup={sideOrder.up}
									onpointercancel={sideOrder.up}
									onkeydown={(e) => e.altKey && sideOrder.key(e, i, (id) => `[data-side="${id}"]`)}
									data-side={f.id}
									title={t('fades.sideHint', { keys: moveKeys })}
									use:hold={() => fadeMenu(f.id)}
								>
									{@render sideFade(f)}
								</button>
							</li>
						{/each}
					</ul>
					{#if friendFades.length}
						<p class="side-head">{t('fades.friends')}</p>
						<ul class="side-fades">
							{#each friendFades as f (f.id)}
								<li>
									<button class="side-item" aria-current={activeFade === f.id ? 'page' : undefined} onclick={() => nav.openFade(f.id)} title={t('share.from', { name: f.owner.display_name })} use:hold={() => fadeMenu(f.id)}>
										{@render sideFade(f)}
									</button>
								</li>
							{/each}
						</ul>
					{/if}
				</nav>
				<button class="side-me" onclick={() => ui.openAccount()}>
					<span class="avatar uav" style:--uh={userHue(String(app.user?.id))}>{app.user?.display_name.charAt(0).toUpperCase()}</span>
					<span class="grow"><span class="b">{app.user?.display_name}</span><span class="b t-foot t-2">{t('account.sidebarServices', { n: services })}</span></span>
					{#if app.trouble}<span class="sdot red" title={t('account.openTrouble')}></span>{/if}
				</button>
			</aside>

			<!-- phone: floating glass tab bar; search turns the dock into a field -->
			<div class="dock" class:searching={ui.searching}>
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<nav class="tabs glass" aria-label={t('nav.tabs')} bind:this={barEl} style:--li={lens} style:--ls={lensStretch} onpointerdown={bdown} onpointermove={bmove} onpointerup={bup} onpointercancel={bup}>
					<span class="lens" class:held={lensHeld} class:gone={tabIndex < 0} aria-hidden="true"></span>
					{#each BAR_TABS as tb}
						<button class="tb" aria-current={nav.tab === tb.id ? 'page' : undefined} onclick={() => tabClick(tb.id)} tabindex={ui.searching ? -1 : 0}>
							<Glyph name={tb.icon} size={25} weight={nav.tab === tb.id ? 2.3 : 1.9} />
							<span>{t(tb.label)}</span>
						</button>
					{/each}
					<button class="tb-back" onclick={leaveSearch} aria-label={t('search.back', { tab: t(backTab.label) })} tabindex={ui.searching ? 0 : -1}>
						<Glyph name={backTab.icon} size={25} weight={2.3} />
					</button>
				</nav>
				<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
				<label class="search glass" class:on={ui.searching} onclick={() => !ui.searching && startSearch()}>
					<span class="s-ic"><Glyph name="search" size={ui.searching ? 19 : 24} weight={2.3} /></span>
					<input
						bind:this={dockInput}
						bind:value={ui.query}
						type="search"
						placeholder={t('search.placeholder')}
						aria-label={t('tab.search')}
						enterkeyhint="search"
						autocomplete="off"
						tabindex={ui.searching ? 0 : -1}
						onfocus={() => !ui.searching && startSearch()}
						onkeydown={searchKey}
					/>
					{#if ui.searching && ui.query}
						<button class="s-clear" onclick={() => ((ui.query = ''), dockInput?.focus())} aria-label={t('search.clear')}><Glyph name="x" size={12} weight={3} /></button>
					{/if}
				</label>
			</div>
		</div>
	</div>

	<Hearing />
	<Editor />
	<Account />
	<UsersSheet />
	<FriendsSheet />
	<JoinSheet />
	<UserSheet />
	<AppsSheet />
	<HistorySheet />
	<PasswordSheet />
	<PlanSheet />
	<ReviewSheet />
	<SongSheet />
	<ContextMenu />
	<ActionSheet
		bind:open={() => !!ui.ask, (v) => !v && (ui.ask = null)}
		title={ui.ask?.title ?? ''}
		message={ui.ask?.message ?? ''}
		actions={ui.ask ? [{ label: ui.ask.label, role: 'destructive', run: ui.ask.run }] : []}
	/>
{/if}

<style>
	.stage {
		position: fixed;
		inset: 0;
		background: var(--bg);
		overflow: hidden;
	}
	.stage.pushed {
		background: #000;
	}
	.shell {
		position: absolute;
		inset: 0;
		background: var(--bg);
		overflow: hidden;
		transform-origin: 50% 0;
	}

	/* ------------------------------------------------ views */
	.content {
		position: absolute;
		inset: 0;
		overflow: hidden;
	}
	.tab,
	.view {
		position: absolute;
		inset: 0;
	}
	.view {
		touch-action: pan-y;
		background: var(--bg);
	}
	.view.top:not(:first-child) {
		box-shadow: -10px 0 30px -14px rgb(0 0 0 / 0.3);
	}
	.parked {
		visibility: hidden;
		pointer-events: none;
	}
	.under-dim {
		position: absolute;
		inset: 0;
		z-index: 10;
		background: #000;
		pointer-events: none;
	}

	/* ------------------------------------------------ dock (phone) */
	.dock {
		--h: 64px;
		--m: 16px;
		position: fixed;
		z-index: 31;
		left: 0;
		right: 0;
		bottom: calc(var(--safe-bottom) + 10px);
		height: var(--h);
		pointer-events: none;
	}
	.tabs,
	.search {
		pointer-events: auto;
		position: absolute;
		bottom: 0;
		height: var(--h);
		border-radius: 999px;
		transition:
			width 520ms cubic-bezier(0.3, 1.25, 0.4, 1),
			left 520ms cubic-bezier(0.3, 1.25, 0.4, 1);
	}
	.tabs {
		left: var(--m);
		width: calc(100% - var(--m) * 2 - var(--h) - 10px);
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		padding: 4px;
		overflow: hidden;
		touch-action: pan-y;
		user-select: none;
		-webkit-user-select: none;
	}
	.search {
		left: calc(100% - var(--m) - var(--h));
		width: var(--h);
		display: flex;
		align-items: center;
		color: var(--label);
		overflow: hidden;
		cursor: text;
	}
	.s-ic {
		display: grid;
		place-items: center;
		flex: none;
		width: var(--h);
		height: 100%;
		transition: width 400ms cubic-bezier(0.3, 1.25, 0.4, 1);
	}
	.search input {
		flex: 1;
		min-width: 0;
		height: 100%;
		border: 0;
		outline: none;
		background: none;
		color: var(--label);
		font: inherit;
		font-size: var(--f-body);
		opacity: 0;
		transition: opacity 200ms ease;
	}
	.search input::-webkit-search-cancel-button {
		display: none;
	}
	.s-clear {
		display: grid;
		place-items: center;
		flex: none;
		width: 22px;
		height: 22px;
		margin-right: 18px;
		border-radius: 50%;
		background: var(--label-3);
		color: var(--bg);
	}
	/* searching: the tab bar shrinks to the tab you came from, the field takes the room */
	.searching .tabs {
		width: var(--h);
	}
	.searching .search {
		left: calc(var(--m) + var(--h) + 10px);
		width: calc(100% - var(--m) * 2 - var(--h) - 10px);
	}
	.searching .s-ic {
		width: 46px;
	}
	.searching .search input {
		opacity: 1;
	}
	.lens {
		position: absolute;
		top: 4px;
		bottom: 4px;
		left: 4px;
		width: calc((100% - 8px) / 3);
		border-radius: 999px;
		background: var(--lens);
		box-shadow:
			inset 0 1px 1px rgb(255 255 255 / 0.6),
			inset 0 0 0 0.5px rgb(255 255 255 / 0.35),
			0 2px 10px rgb(0 0 0 / 0.08);
		transform: translateX(calc(var(--li) * 100%)) scale(calc(1 + var(--ls)), calc(1 - var(--ls) * 0.35));
		transition:
			scale 340ms cubic-bezier(0.25, 1.6, 0.45, 1),
			opacity 200ms;
	}
	.lens.held {
		scale: 1.14 1.2;
		background: rgb(255 255 255 / 0.32);
		-webkit-backdrop-filter: blur(3px) saturate(1.8) brightness(1.1);
		backdrop-filter: blur(3px) saturate(1.8) brightness(1.1);
	}
	.lens.gone {
		opacity: 0;
	}
	.tb {
		position: relative;
		display: grid;
		justify-items: center;
		align-content: center;
		gap: 2px;
		color: var(--label);
		font-size: 0.62rem;
		font-weight: 600;
		letter-spacing: 0;
		border-radius: 999px;
		transition: opacity 220ms ease;
	}
	.tb[aria-current='page'] {
		font-weight: 700;
	}
	.tb-back {
		position: absolute;
		inset: 0;
		display: grid;
		place-items: center;
		color: var(--label);
		opacity: 0;
		pointer-events: none;
		transition: opacity 220ms ease;
	}
	.searching .tb,
	.searching .lens {
		opacity: 0;
		pointer-events: none;
	}
	.searching .tb-back {
		opacity: 1;
		pointer-events: auto;
	}

	/* ------------------------------------------------ sidebar (desktop) */
	.sidebar {
		display: none;
	}
	@media (min-width: 721px) {
		.dock {
			display: none;
		}
		.sidebar {
			position: absolute;
			z-index: 20;
			top: 10px;
			left: 10px;
			bottom: 10px;
			width: calc(var(--side) - 20px);
			display: flex;
			flex-direction: column;
			gap: 2px;
			padding: 16px 10px 10px;
			border-radius: 28px;
			background: var(--sidebar);
			--glass-blur: 30px;
			overflow-y: auto;
		}
		.sidebar nav {
			display: grid;
			gap: 1px;
		}
		.brand {
			display: flex;
			align-items: center;
			gap: 9px;
			padding: 2px 8px 14px;
			font-size: var(--f-body);
			font-weight: 800;
		}
		.side-search {
			display: flex;
			align-items: center;
			gap: 6px;
			height: 34px;
			margin: 0 2px 12px;
			padding: 0 6px 0 12px;
			border-radius: 999px;
			background: var(--well);
			box-shadow: var(--well-shadow);
			color: var(--label-2);
			font-size: var(--f-sub);
		}
		.side-search.on {
			background: var(--lens);
			color: var(--label);
		}
		.side-search input {
			flex: 1;
			min-width: 0;
			border: 0;
			outline: none;
			background: none;
			color: var(--label);
			font: inherit;
		}
		kbd {
			padding: 1px 6px;
			border-radius: 6px;
			background: var(--fill-3);
			color: var(--label-2);
			font: inherit;
			font-size: var(--f-cap);
		}
		.side-head {
			padding: 16px 12px 4px;
			font-size: var(--f-foot);
			font-weight: 700;
			color: var(--label-2);
		}
		.side-head.side-row {
			display: flex;
			align-items: center;
			justify-content: space-between;
			padding-right: 4px;
		}
		.side-add {
			display: grid;
			place-items: center;
			width: 26px;
			height: 26px;
			border-radius: 50%;
			color: var(--label-2);
		}
		.side-add:hover {
			background: var(--fill-3);
		}
		.side-fades {
			display: grid;
			gap: 1px;
			margin: 0;
			padding: 0;
			list-style: none;
		}
		.side-fades li {
			position: relative;
			border-radius: 999px;
		}
		.side-fades li.lifted {
			z-index: 2;
			background: var(--elevated);
			box-shadow: 0 8px 20px -8px rgb(0 0 0 / 0.35);
		}
		.side-fades .side-item {
			width: 100%;
			cursor: grab;
		}
		.side-item {
			display: flex;
			align-items: center;
			gap: 10px;
			min-height: 34px;
			padding: 0 12px;
			border-radius: 999px;
			color: var(--label);
			font-size: var(--f-sub);
			text-align: left;
			transition: background 160ms;
		}
		.side-item :global(.glyph) {
			color: var(--label-2);
		}
		.side-item:hover {
			background: var(--fill-3);
		}
		.side-item[aria-current='page'] {
			background: var(--lens);
			font-weight: 600;
			box-shadow:
				inset 0 1px 0 rgb(255 255 255 / 0.5),
				0 1px 4px rgb(0 0 0 / 0.06);
		}
		.grow {
			flex: 1;
			min-width: 0;
		}
		.sdot {
			flex: none;
			width: 8px;
			height: 8px;
			border-radius: 50%;
		}
		.sdot.red {
			background: var(--red);
		}
		.sdot.orange {
			background: var(--orange);
		}
		.sdot.run {
			animation: pulse 1.2s ease-in-out infinite alternate;
		}
		@keyframes pulse {
			to {
				opacity: 0.3;
			}
		}
		.side-me {
			display: flex;
			align-items: center;
			gap: 10px;
			margin-top: auto;
			padding: 8px 10px;
			border-radius: 20px;
			text-align: left;
			font-size: var(--f-sub);
		}
		.side-me:hover {
			background: var(--fill-3);
		}
		.avatar {
			display: grid;
			place-items: center;
			width: 32px;
			height: 32px;
			border-radius: 50%;
				font-weight: 700;
		}
		.b {
			display: block;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.lens {
			transform: translateX(calc(round(var(--li)) * 100%));
		}
		.tabs,
		.search,
		.s-ic {
			transition: none;
		}
	}
</style>
