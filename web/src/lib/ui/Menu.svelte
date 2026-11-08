<script lang="ts">
	import { tick, untrack } from 'svelte';
	import { Spring } from './motion.ts';

	// Pull-down menu that grows out of the control that opened it.
	let {
		open = $bindable(false),
		anchor,
		items,
		label
	}: {
		open?: boolean;
		anchor: HTMLElement | undefined;
		items: { label: string; checked?: boolean; run: () => void }[];
		label: string;
	} = $props();

	let mounted = $state(false);
	let p = $state(0);
	let pos = $state({ top: 0, right: 0, up: false });
	let menuEl: HTMLElement | undefined = $state();
	const spring = new Spring(0, (v) => (p = v), 0.002);

	$effect(() => {
		const o = open;
		untrack(async () => {
			if (o && anchor) {
				const r = anchor.getBoundingClientRect();
				const up = r.bottom + 320 > window.innerHeight;
				pos = { top: up ? r.top - 6 : r.bottom + 6, right: Math.max(8, window.innerWidth - r.right), up };
				mounted = true;
				await tick();
				(menuEl?.querySelector('[aria-checked="true"]') as HTMLElement | null)?.focus() ?? menuEl?.querySelector('button')?.focus();
				spring.to(1, { response: 0.3, damping: 0.88 });
			} else if (mounted) {
				if (await spring.to(0, { response: 0.22 })) {
					mounted = false;
					anchor?.focus({ preventScroll: true });
				}
			}
		});
	});

	function key(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			e.stopPropagation();
			open = false;
			return;
		}
		const d = e.key === 'ArrowDown' ? 1 : e.key === 'ArrowUp' ? -1 : 0;
		if (!d || !menuEl) return;
		e.preventDefault();
		const btns = [...menuEl.querySelectorAll<HTMLElement>('button')];
		const i = btns.indexOf(document.activeElement as HTMLElement);
		btns[(i + d + btns.length) % btns.length]?.focus();
	}
</script>

{#if mounted}
	<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
	<div class="catch" onclick={() => (open = false)}></div>
	<div
		bind:this={menuEl}
		class="menu glass strong"
		class:up={pos.up}
		role="menu"
		aria-label={label}
		tabindex="-1"
		style:top="{pos.top}px"
		style:right="{pos.right}px"
		style:--p={p}
		onkeydown={key}
	>
		{#each items as it}
			<button
				type="button"
				role="menuitemradio"
				aria-checked={!!it.checked}
				onclick={() => {
					open = false;
					it.run();
				}}
			>
				<span class="ck" aria-hidden="true">{#if it.checked}✓{/if}</span>
				{it.label}
			</button>
		{/each}
	</div>
{/if}

<style>
	.catch {
		position: fixed;
		inset: 0;
		z-index: 450;
	}
	.menu {
		position: fixed;
		z-index: 451;
		min-width: 230px;
		padding: 6px 0;
		border-radius: 24px;
		transform-origin: top right;
		opacity: min(1, calc(var(--p) * 2));
		transform: scale(calc(0.35 + var(--p) * 0.65));
	}
	.menu.up {
		transform-origin: bottom right;
		translate: 0 -100%;
	}
	button {
		display: flex;
		align-items: center;
		gap: 6px;
		width: 100%;
		min-height: 44px;
		padding: 0 16px 0 10px;
		text-align: left;
		font-size: var(--f-body);
	}
	button:active,
	button:focus-visible {
		background: var(--fill-3);
		outline: none;
		border-radius: 0;
	}
	.ck {
		width: 18px;
		font-weight: 600;
		text-align: center;
	}
	@media (prefers-reduced-motion: reduce) {
		.menu {
			transform: none;
			opacity: var(--p);
		}
	}
</style>
