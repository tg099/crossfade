<script lang="ts" generics="T extends string">
	import { onMount } from 'svelte';
	import { Spring } from './motion.ts';

	// Segmented control with a liquid lens: the thumb is a spring and stretches along its path
	// while it moves fast, like a drop of glass sliding over the track.
	let {
		value = $bindable(),
		options,
		label,
		small = false
	}: { value: T; options: { value: T; label: string }[]; label: string; small?: boolean } = $props();

	let x = $state(0);
	let stretch = $state(0);
	let ready = $state(false);
	const spring = new Spring(
		0,
		(v) => {
			x = v;
			stretch = Math.min(0.28, Math.abs(spring.velocity) * 0.05);
		},
		0.002
	);
	const idx = $derived(Math.max(0, options.findIndex((o) => o.value === value)));

	onMount(() => {
		spring.set(idx);
		ready = true;
	});
	$effect(() => {
		const i = idx;
		if (ready) spring.to(i, { response: 0.36, damping: 0.78 });
	});

	function key(e: KeyboardEvent) {
		const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
		if (!d) return;
		e.preventDefault();
		const n = options[(idx + d + options.length) % options.length];
		value = n.value;
		(e.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('button')[options.indexOf(n)]?.focus();
	}
</script>

<div class="segc" class:sm={small} role="radiogroup" aria-label={label} style:--n={options.length} style:--x={x} style:--st={stretch} tabindex="-1" onkeydown={key}>
	<span class="thumb" aria-hidden="true"></span>
	{#each options as o}
		<button
			type="button"
			role="radio"
			aria-checked={o.value === value}
			tabindex={o.value === value ? 0 : -1}
			class:on={o.value === value}
			onclick={() => (value = o.value)}>{o.label}</button
		>
	{/each}
</div>

<style>
	.segc {
		position: relative;
		display: grid;
		grid-template-columns: repeat(var(--n), minmax(0, 1fr));
		padding: 3px;
		border-radius: 999px;
		background: var(--well);
		box-shadow: var(--well-shadow);
		outline: none;
	}
	.thumb {
		position: absolute;
		top: 3px;
		bottom: 3px;
		left: 3px;
		width: calc((100% - 6px) / var(--n));
		border-radius: 999px;
		background: var(--thumb);
		box-shadow:
			0 2px 8px rgb(0 0 0 / 0.12),
			inset 0 1px 0 rgb(255 255 255 / 0.9),
			inset 0 0 0 0.5px rgb(0 0 0 / 0.04);
		transform: translateX(calc(var(--x) * 100%)) scale(calc(1 + var(--st)), calc(1 - var(--st) * 0.4));
	}
	button {
		position: relative;
		min-height: 34px;
		padding: 0 12px;
		font-size: var(--f-sub);
		font-weight: 500;
		border-radius: 999px;
		color: var(--label-2);
		/* never wider than its segment: a long label is cut, not pushed outside */
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		transition:
			color 160ms,
			opacity 120ms;
	}
	button.on {
		font-weight: 600;
		color: var(--label);
	}
	button:not(.on):active {
		opacity: 0.5;
	}
	.sm button {
		min-height: 30px;
		padding: 0 8px;
		font-size: var(--f-foot);
	}
</style>
