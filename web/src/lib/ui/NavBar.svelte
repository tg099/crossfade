<script lang="ts">
	import { t } from './i18n.svelte.ts';
	import type { Snippet } from 'svelte';
	import Glyph from './Glyph.svelte';

	// No bar: glass controls float over the content. Once content slides underneath, a soft
	// scroll-edge blur fades in behind them and the title moves in.
	let {
		title = '',
		solid = false,
		back,
		actions
	}: {
		title?: string;
		solid?: boolean;
		back?: { label: string; run: () => void };
		actions?: Snippet;
	} = $props();
</script>

<div class="navbar" class:solid>
	<span class="edge" aria-hidden="true"></span>
	<div class="nb-row">
		<div class="lead">
			{#if back}
				<button class="gbtn round glass lq" onclick={back.run} aria-label={t('nav.back', { label: back.label })} title={t('nav.back', { label: back.label })}>
					<Glyph name="chevronLeft" size={22} weight={2.6} />
				</button>
			{/if}
		</div>
		<p class="title clip" aria-hidden={!solid}>{title}</p>
		<div class="trail">{@render actions?.()}</div>
	</div>
</div>

<style>
	.navbar {
		position: absolute;
		top: 0;
		left: var(--side);
		right: 0;
		z-index: 5;
		padding-top: var(--safe-top);
		pointer-events: none;
	}
	/* scroll edge: progressive blur + a wash of the page colour, no hard line */
	.edge {
		position: absolute;
		/* runs on under the floating sidebar: no hard edge where the page starts */
		inset: 0 0 -28px calc(-1 * var(--side));
		/* calm behind the title row (big bold text scrolling under it must not read through the title),
		   fading out only below the bar */
		background: linear-gradient(to bottom, color-mix(in srgb, var(--bg) 88%, transparent),
			color-mix(in srgb, var(--bg) 74%, transparent) calc(100% - 28px), transparent);
		-webkit-backdrop-filter: blur(10px) saturate(1.6);
		backdrop-filter: blur(10px) saturate(1.6);
		-webkit-mask-image: linear-gradient(to bottom, #000 calc(100% - 40px), transparent);
		mask-image: linear-gradient(to bottom, #000 calc(100% - 40px), transparent);
		opacity: 0;
		transition: opacity 260ms ease;
	}
	.solid .edge {
		opacity: 1;
	}
	.nb-row {
		position: relative;
		display: grid;
		grid-template-columns: 1fr auto 1fr;
		align-items: center;
		height: var(--bar);
		padding: 0 14px;
	}
	.lead,
	.trail {
		display: flex;
		align-items: center;
		gap: 8px;
		min-width: 0;
	}
	/* only the buttons take taps: an empty side must not cover what lies under the bar (the avatar
	   of a page whose large title starts right under the status bar) */
	.lead > :global(*),
	.trail > :global(*) {
		pointer-events: auto;
	}
	.trail {
		justify-content: flex-end;
	}
	.lead > :global(*),
	.trail > :global(*) {
		flex: none;
	}
	.title {
		max-width: calc(100vw - 260px);
		font-size: var(--f-body);
		font-weight: 700;
		opacity: 0;
		transform: translateY(8px) scale(0.96);
		transition:
			opacity 200ms ease,
			transform 420ms cubic-bezier(0.25, 1.3, 0.45, 1);
	}
	.solid .title {
		opacity: 1;
		transform: none;
	}
	@media (min-width: 721px) {
		.nb-row {
			padding: 14px 24px 0;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.title {
			transform: none;
		}
	}
</style>
