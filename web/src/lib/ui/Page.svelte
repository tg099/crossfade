<script lang="ts">
	import type { Snippet } from 'svelte';
	import Avatar from './Avatar.svelte';
	import NavBar from './NavBar.svelte';
	import { ambient } from './ui.svelte.ts';

	// A page that scrolls edge to edge under floating glass. Large title (or a custom hero, its
	// title marked with data-title) hands over to the floating title as it scrolls away.
	let {
		title,
		large = true,
		back,
		actions,
		hero,
		backdrop,
		avatar = false,
		glow = [],
		children
	}: {
		title: string;
		large?: boolean;
		back?: { label: string; run: () => void };
		actions?: Snippet;
		hero?: Snippet;
		/** colour that stays put behind the scrolling content, so the glass always has something to bend */
		backdrop?: Snippet;
		avatar?: boolean;
		glow?: string[];
		children: Snippet;
	} = $props();

	let scroller: HTMLElement | undefined = $state();
	let solid = $state(false);

	// Scroll events fire far more often than frames are drawn: measure at most once per frame, and
	// keep what only changes with the layout (bar height, where the title starts) between frames.
	let frame = 0;
	let measured: { title: HTMLElement | null; bar: number; titleTop: number; titleH: number } | null = null;
	function measure(el: HTMLElement) {
		const title = el.querySelector<HTMLElement>('[data-title]');
		const bar = parseFloat(getComputedStyle(el).paddingTop) || 0;
		if (!title) return { title, bar, titleTop: 0, titleH: 0 };
		const r = title.getBoundingClientRect();
		return { title, bar, titleTop: r.top - el.getBoundingClientRect().top + el.scrollTop, titleH: r.height };
	}
	function update() {
		frame = 0;
		const el = scroller;
		if (!el) return;
		const y = el.scrollTop;
		if (y <= 2) {
			solid = false;
			return;
		}
		if (!measured || (measured.title && !measured.title.isConnected)) measured = measure(el);
		if (!measured.title) {
			solid = true;
			return;
		}
		// the title may start inside the bar (desktop: it shares the row with the buttons); it counts
		// as gone once it has scrolled past where it started, not as soon as it is under the bar
		const bottom = measured.titleTop + measured.titleH - y;
		solid = bottom < Math.min(measured.bar, Math.max(measured.titleTop, 0)) + 4;
	}
	function onscroll() {
		if (!frame) frame = requestAnimationFrame(update);
	}
	$effect(() => {
		const el = scroller;
		if (!el) return;
		// the layout changed (window size, Dynamic Type, the hero's content): measure again
		const ro = new ResizeObserver(() => {
			measured = null;
			onscroll();
		});
		ro.observe(el);
		const first = el.firstElementChild;
		if (first) ro.observe(first);
		return () => {
			ro.disconnect();
			cancelAnimationFrame(frame);
			frame = 0;
		};
	});
</script>

<div class="pg">
	<div class="backdrop" aria-hidden="true">
		{#if backdrop}{@render backdrop()}{:else if glow.length}<span class="ambient" style:background={ambient(glow)}></span>{/if}
	</div>
	<NavBar {title} solid={solid || (!large && !hero)} {back} {actions} />
	<div class="scroller" bind:this={scroller} {onscroll}>
		{#if hero}
			{@render hero()}
		{:else if large}
			<header class="large">
				<h1 class="t-large" data-title>{title}</h1>
				{#if avatar}<span class="av"><Avatar /></span>{/if}
			</header>
		{/if}
		{@render children()}
	</div>
</div>

<style>
	.pg {
		position: absolute;
		inset: 0;
		background: var(--bg);
	}
	/* fixed to the page, not the content: scrolling never runs out of colour */
	.backdrop {
		position: absolute;
		inset: 0;
		overflow: hidden;
		pointer-events: none;
	}
	.scroller {
		position: absolute;
		inset: 0;
		isolation: isolate;
		overflow-y: auto;
		overscroll-behavior-y: contain;
		padding-top: calc(var(--safe-top) + var(--bar));
		padding-left: var(--side);
		padding-bottom: var(--chrome-bottom, 24px);
		-webkit-overflow-scrolling: touch;
	}
	.large {
		display: flex;
		align-items: flex-end;
		justify-content: space-between;
		gap: 12px;
		padding: 0 20px 18px;
	}
	.av {
		margin-bottom: 2px;
	}
	@media (min-width: 721px) {
		/* desktop: the large title shares the row with the buttons on the right instead of
		   sitting under an empty bar */
		.large {
			/* centred on the row of the buttons and the sidebar's header */
			margin-top: calc(var(--bar) * -1 + 23px);
			padding: 0 36px 22px;
			align-items: center;
		}
		.av {
			display: none;
		}
	}
</style>
