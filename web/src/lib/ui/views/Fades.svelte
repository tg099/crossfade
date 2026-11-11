<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { SERVICE_NAME } from '#lib/format.ts';
	import { app } from '#lib/state.svelte.ts';
	import type { Line } from '#lib/types.ts';
	import FadeBadge from '../FadeBadge.svelte';
	import Glyph from '../Glyph.svelte';
	import Menu from '../Menu.svelte';
	import Mosaic from '../Mosaic.svelte';
	import Page from '../Page.svelte';
	import Segmented from '../Segmented.svelte';
	import SwipeRow from '../SwipeRow.svelte';
	import { askLeaveFade, fadeMenu, hold } from '../ctx.ts';
	import { fadeCovers, fadeIcon, fadeStatus } from '../fade.ts';
	import { t } from '../i18n.svelte.ts';
	import { Reorder } from '../reorder.svelte.ts';
	import { BRAND, flowIcon, nav, ui } from '../ui.svelte.ts';

	// Your fades in your own order ("Reorder" shows handles; the first fade comes first everywhere),
	// and the fades friends share with you on a tab of their own.
	let ordering = $state(false);
	const own = $derived(app.lines.filter((f) => f.mine));
	const shared = $derived(app.lines.filter((f) => !f.mine));
	let tab = $state<'mine' | 'friends'>('mine');
	// the tab only shows while there is something on it
	$effect(() => {
		if (!shared.length && tab === 'friends') tab = 'mine';
	});
	$effect(() => {
		if (tab === 'friends') ordering = false;
	});
	// leaving the tab ends reordering
	$effect(() => {
		if (nav.tab !== 'fades') ordering = false;
	});
	const list = $derived(tab === 'mine' ? own : shared);
	const order = new Reorder(
		() => own.map((f) => f.id),
		(from, to) => app.moveLine(from, to)
	);
	// a new fade: from scratch, or from one of the common shapes (sorting likes into playlists …)
	let newBtn: HTMLButtonElement | undefined = $state();
	let newOpen = $state(false);
	const NEW = $derived([
		{ label: t('start.own'), run: () => ui.edit(0) },
		{ label: t('start.likes'), run: () => ui.edit(0, 'likes') },
		{ label: t('start.copy'), run: () => ui.edit(0, 'copy') },
		{ label: t('start.sort'), run: () => ui.edit(0, 'sort') }
	]);
</script>

{#snippet row(f: Line)}
	{@const st = fadeStatus(f)}
	<span class="fcover">
		<Mosaic covers={fadeCovers(f)} seed={f.name} color={f.color} size={64} />
		<span class="badge"><FadeBadge icon={fadeIcon(f)} color={f.color} size={24} /></span>
	</span>
	<span class="grow">
		<span class="name clip">{f.name}</span>
		<span class="route" aria-label={f.members.map((s) => SERVICE_NAME[s.service]).join(', ')}>
			{#each f.members as s, i (s.id)}
				{#if i}<span aria-hidden="true"><Glyph name={flowIcon(f.members[i - 1], s)} size={13} weight={2.2} /></span>{/if}
				<ServiceMark service={s.service} size={16} title={false} />
			{/each}
		</span>
		<span class="t-foot clip st" class:red={st.tone === 'fault'} class:orange={st.tone === 'attention'}>{f.mine ? st.text : `${t('share.from', { name: f.owner.display_name })} · ${st.text}`}</span>
	</span>
{/snippet}

<Page title={t('tab.fades')} avatar glow={BRAND}>
	{#snippet actions()}
		{#if own.length > 1 && tab === 'mine'}
			<button class="gbtn glass lq" class:tint={ordering} onclick={() => (ordering = !ordering)} aria-pressed={ordering}>
				{ordering ? t('common.done') : t('fades.reorder')}
			</button>
		{/if}
		{#if !ordering}
			<button class="gbtn round glass lq" bind:this={newBtn} onclick={() => (newOpen = true)} aria-label={t('fades.new')} aria-haspopup="menu"><Glyph name="plus" size={22} weight={2.6} /></button>
		{/if}
	{/snippet}

	{#if shared.length}
		<div class="tabs">
			<Segmented
				bind:value={tab}
				label={t('fades.title')}
				options={[
					{ value: 'mine', label: t('fades.mine') },
					{ value: 'friends', label: t('fades.friends', { n: shared.length }) }
				]}
			/>
		</div>
	{/if}
	<ul class="group plain llist" class:ordering>
		{#each list as f, i (f.id)}
			<li class:lifted={order.dragId === f.id} style:transform={order.style(f.id)}>
				{#if ordering}
					<div class="cell" style:--inset="92px">
						{@render row(f)}
						{#if f.mine}<button
							class="grip"
							data-fgrip={f.id}
							aria-label={t('fades.move', { name: f.name, pos: i + 1, n: own.length })}
							onpointerdown={(e) => order.down(e, i)}
							onpointermove={order.move}
							onpointerup={order.up}
							onpointercancel={order.up}
							onkeydown={(e) => order.key(e, i, (id) => `[data-fgrip="${id}"]`)}
						>
							<Glyph name="grip" size={22} weight={1.8} />
						</button>{/if}
					</div>
				{:else}
					<SwipeRow
						actions={f.mine
							? [
									{ label: f.paused ? t('fade.resume') : t('fade.pause'), tone: 'orange', run: () => app.setPaused(f.id, !f.paused) },
									{ label: t('common.edit'), tone: 'gray', run: () => ui.edit(f.id) }
								]
							: [{ label: t('share.leave'), tone: 'red', run: () => askLeaveFade(f.id) }]}
					>
						<button class="cell" style:--inset="92px" onclick={() => nav.openFade(f.id)} use:hold={() => fadeMenu(f.id)}>
							{@render row(f)}
							<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
						</button>
					</SwipeRow>
				{/if}
			</li>
		{/each}
	</ul>
	{#if app.linesLoaded && !app.lines.length}
		<div class="empty">
			<span class="ic"><Glyph name="fades" size={34} weight={1.8} /></span>
			<p class="t-title3">{t('fades.emptyTitle')}</p>
			<p class="t-sub t-2">{t('fades.emptyText')}</p>
			<button class="pb glass tint lq" onclick={() => ui.edit(0)} disabled={app.connected.length < 2}><Glyph name="plus" size={18} weight={2.6} /> {t('fades.new')}</button>
			{#if app.connected.length < 2}<button class="text-btn" onclick={() => ui.openAccount()}>{t('start.servicesFirst')}</button>{/if}
		</div>
	{:else}
		{#if tab === 'mine' && !own.length}
			<div class="empty">
				<p class="t-sub t-2">{t('fades.noOwn')}</p>
				<button class="pb glass tint lq" onclick={() => ui.edit(0)} disabled={app.connected.length < 2}><Glyph name="plus" size={18} weight={2.6} /> {t('fades.new')}</button>
			</div>
		{/if}
		<p class="tip t-foot t-2">{ordering ? t('fades.orderHint') : tab === 'friends' ? t('fades.friendsHint') : t('fades.swipeHint')}</p>
	{/if}
</Page>

<Menu bind:open={newOpen} anchor={newBtn} label={t('fades.new')} items={NEW} />

<style>
	.llist {
		padding: 0 0 0 20px;
	}
	.tabs {
		margin: 4px 20px 12px;
	}
	.llist :global(.cell) {
		padding-right: 20px !important;
		gap: 14px;
	}
	.llist > li {
		position: relative;
		will-change: transform;
	}
	.lifted {
		z-index: 3;
		background: var(--elevated);
		border-radius: 20px;
		box-shadow: 0 14px 32px -10px rgb(0 0 0 / 0.3);
	}
	.fcover {
		position: relative;
		flex: none;
	}
	.badge {
		position: absolute;
		right: -6px;
		bottom: -6px;
		padding: 2px;
		border-radius: 10px;
		background: var(--bg);
	}
	.name {
		display: block;
		font-weight: 600;
	}
	.route {
		display: flex;
		align-items: center;
		gap: 3px;
		margin: 4px 0 3px;
		color: var(--label-3);
	}
	.st {
		display: block;
		color: var(--label-2);
	}
	.red {
		color: var(--red-text);
	}
	.orange {
		color: var(--orange-text);
	}
	.grip {
		display: grid;
		place-items: center;
		width: 44px;
		height: 48px;
		margin-right: -10px;
		color: var(--label-3);
		touch-action: none;
		cursor: grab;
	}
	.grip:active {
		cursor: grabbing;
		color: var(--label-2);
	}
	.ordering .cell {
		animation: wiggle-in 260ms cubic-bezier(0.25, 1.4, 0.45, 1);
	}
	@keyframes wiggle-in {
		from {
			transform: translateX(-6px);
		}
	}
	.tip {
		padding: 16px 20px;
	}
	.empty {
		display: grid;
		justify-items: center;
		gap: 8px;
		padding: 50px 32px;
		text-align: center;
	}
	.empty .ic {
		color: var(--label-3);
	}
	.empty .pb {
		margin-top: 10px;
	}
	@media (min-width: 721px) {
		.llist {
			padding-left: 36px;
			max-width: calc(var(--content-max) + 36px);
		}
		.tip {
			padding: 16px 36px;
		}
		.tabs {
			margin: 4px 36px 14px;
			max-width: 420px;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.ordering .cell {
			animation: none;
		}
	}
</style>
