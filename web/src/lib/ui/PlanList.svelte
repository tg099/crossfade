<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { SERVICE_NAME } from '#lib/format.ts';
	import type { Plan, TrackInfo } from '#lib/types.ts';
	import Cover from './Cover.svelte';
	import Glyph from './Glyph.svelte';
	import HearButton from './HearButton.svelte';
	import { t } from './i18n.svelte.ts';

	// What a run did (or a preview would do), per collection: added, removed, still pending, and
	// deletions held back for your confirmation. Groups unfold on tap.
	let { plan, live = false }: { plan: Plan['plan']; live?: boolean } = $props();
	let open = $state<Record<string, boolean>>({});
	const toggle = (k: string) => (open[k] = !open[k]);
</script>

{#snippet tracks(list: TrackInfo[], sign: '+' | '−' | '…')}
	<ul class="songs">
		{#each list as x, i (x.service + x.id + i)}
			<li>
				<span class="mark" class:plus={sign === '+'} class:minus={sign === '−'} aria-hidden="true">{sign}</span>
				<Cover src={x.artwork} seed={x.id} size={36} track={x} />
				<span class="grow">
					<span class="b clip">{x.title}</span>
					<span class="b t-foot t-2 clip">{x.artist}{x.note ? ` · ${x.note}` : ''}</span>
				</span>
				<HearButton track={x} size={32} />
			</li>
		{/each}
	</ul>
{/snippet}

<div class="plan">
	{#if plan.held.length}
		<div class="g held">
			<button class="g-head" onclick={() => toggle('held')} aria-expanded={!!open.held}>
				<span class="tile-icon" style:background="var(--orange)"><Glyph name="trash" size={17} weight={2.2} /></span>
				<span class="grow"><span class="b">{t('plan.held', { n: plan.held.length })}</span><span class="b t-foot t-2">{t('plan.heldText')}</span></span>
				<span class="chev" class:up={open.held}><Glyph name="chevronDown" size={18} weight={2.4} /></span>
			</button>
			{#if open.held}{@render tracks(plan.held, '−')}{/if}
		</div>
	{/if}
	{#each plan.members as m (m.member_id)}
		{@const n = m.add.length + m.remove.length + m.pending.length}
		{@const k = String(m.member_id)}
		<div class="g">
			<button class="g-head" onclick={() => toggle(k)} aria-expanded={!!open[k]} disabled={!n}>
				<ServiceMark service={m.service} size={30} />
				<span class="grow">
					<span class="b clip">{m.name}</span>
					<span class="b t-foot t-2">{SERVICE_NAME[m.service]}</span>
				</span>
				{#if n}
					<span class="counts">
						{#if m.add.length}<span class="plus">+{m.add.length}</span>{/if}
						{#if m.remove.length}<span class="minus">−{m.remove.length}</span>{/if}
						{#if m.pending.length}<span class="wait" title={t('plan.pending')}>{m.pending.length}</span>{/if}
					</span>
					<span class="chev" class:up={open[k]}><Glyph name="chevronDown" size={18} weight={2.4} /></span>
				{:else}
					<span class="t-foot t-2">{live ? t('plan.nothingYet') : t('plan.unchanged')}</span>
				{/if}
			</button>
			{#if open[k]}
				{#if m.add.length}{@render tracks(m.add, '+')}{/if}
				{#if m.remove.length}{@render tracks(m.remove, '−')}{/if}
				{#if m.pending.length}
					<p class="pend t-foot t-2">{t('plan.pendingText')}</p>
					{@render tracks(m.pending, '…')}
				{/if}
			{/if}
		</div>
	{/each}
</div>

<style>
	/* one grouped list, like every other list: one row per collection, hairlines between */
	.plan {
		border-radius: var(--r-group);
		background: var(--cell-grouped);
		-webkit-backdrop-filter: blur(20px) saturate(1.6);
		backdrop-filter: blur(20px) saturate(1.6);
		box-shadow:
			var(--group-shadow),
			inset 0 0 0 0.5px var(--rim-edge),
			var(--group-rim);
		overflow: hidden;
	}
	.g {
		position: relative;
	}
	.g + .g::before {
		content: '';
		position: absolute;
		top: 0;
		left: 60px;
		right: 16px;
		border-top: 0.5px solid var(--sep);
	}
	.g-head {
		display: flex;
		align-items: center;
		gap: 12px;
		width: 100%;
		min-height: 62px;
		padding: 10px 16px;
		text-align: left;
	}
	.g-head:disabled {
		cursor: default;
	}
	.grow {
		flex: 1;
		min-width: 0;
	}
	.b {
		display: block;
	}
	.counts {
		display: flex;
		gap: 8px;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
	}
	.plus {
		color: var(--green-text);
	}
	.minus {
		color: var(--red-text);
	}
	.wait {
		color: var(--label-2);
	}
	.chev {
		color: var(--label-3);
		transition: transform 320ms cubic-bezier(0.25, 1.4, 0.45, 1);
	}
	.chev.up {
		transform: rotate(180deg);
	}
	.songs {
		margin: 0;
		padding: 0 16px 10px;
		list-style: none;
		animation: unfold 360ms cubic-bezier(0.25, 1.2, 0.45, 1);
	}
	@keyframes unfold {
		from {
			opacity: 0;
			transform: translateY(-6px);
		}
	}
	.songs li {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 6px 0;
		border-top: 0.5px solid var(--sep);
	}
	.mark {
		width: 16px;
		text-align: center;
		font-weight: 800;
		color: var(--label-2);
	}
	.mark.plus {
		color: var(--green-text);
	}
	.mark.minus {
		color: var(--red-text);
	}
	.pend {
		padding: 0 16px 4px;
	}
	@media (prefers-reduced-motion: reduce) {
		.songs {
			animation: none;
		}
	}
</style>
